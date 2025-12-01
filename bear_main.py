import streamlit as st
import streamlit.components.v1 as components 
import pandas as pd
import requests
import gpxpy
from shapely.geometry import Point, LineString
from shapely.ops import nearest_points
import folium
import datetime

# ==========================================
# 0. 页面配置
# ==========================================
st.set_page_config(page_title="熊出没地图 (排错版)", layout="wide", page_icon="🐻")

# ==========================================
# 1. 数据抽取
# ==========================================
@st.cache_data
def load_yamanashi_data():
    url = "https://catalog.dataplatform-yamanashi.jp/api/action/datastore_search"
    params = {"resource_id": "b4eb262f-07e0-4417-b24f-6b15844b4ac1", "limit": 10000}
    try:
        response = requests.get(url, params=params, timeout=10)
        data = response.json()
        if 'result' in data and 'records' in data['result']:
            df = pd.DataFrame(data['result']['records'])
            rename_map = {'緯度': 'latitude', '経度': 'longitude', '年月日': 'sighting_datetime'}
            df = df.rename(columns=rename_map)
            
            if 'latitude' not in df.columns:
                for col in ['lat', 'Lat', 'LAT', '纬度']:
                    if col in df.columns: df = df.rename(columns={col: 'latitude'}); break

            df['latitude'] = pd.to_numeric(df['latitude'], errors='coerce')
            df['longitude'] = pd.to_numeric(df['longitude'], errors='coerce')
            df['sighting_datetime'] = pd.to_datetime(df['sighting_datetime'], errors='coerce')
            df = df.dropna(subset=['latitude', 'longitude'])

            # 简化描述，防止特殊字符报错
            def make_description(row):
                parts = [str(row.get(c, '')) for c in ['目撃市町村', '場所'] if str(row.get(c, '')) != 'nan']
                return " ".join(parts)
            df['sighting_condition'] = df.apply(make_description, axis=1)
            return df
    except Exception:
        return pd.DataFrame()
    return pd.DataFrame()

# ==========================================
# 2. 主逻辑
# ==========================================
st.title("🐻 熊出没安全地图 (排错版)")
st.caption("如果能看到地图中心的【绿色正方形】，说明绘图引擎正常。如果看不到红点，说明数据过滤太严格。")

all_bears = load_yamanashi_data()
if all_bears.empty:
    st.error("❌ 数据库加载失败")
    st.stop()

with st.sidebar:
    st.header("⚙️ 设置")
    buffer_radius_m = st.slider("预警距离 (米)", 100, 5000, 500, 100)

col1, col2 = st.columns([3, 1])
with col1:
    uploaded_file = st.file_uploader("📂 上传 GPX 文件", type=['gpx'])

center_lat, center_lon = 35.6, 138.5
if not all_bears.empty:
    center_lat, center_lon = all_bears['latitude'].mean(), all_bears['longitude'].mean()

m = folium.Map(location=[center_lat, center_lon], zoom_start=10, tiles="OpenStreetMap")

# --- 🟢 测试点：证明地图能画图 ---
# 在地图中心画一个显眼的绿色正方形
folium.RegularPolygonMarker(
    location=[center_lat, center_lon],
    number_of_sides=4,
    radius=15,
    color="green",
    fill_color="green",
    popup="测试点 (Test Marker)"
).add_to(m)

# ==========================================
# 3. GPX 处理
# ==========================================
detected_danger = []
debug_msg = []

if uploaded_file is not None:
    try:
        gpx = gpxpy.parse(uploaded_file)
        raw_points = []
        for track in gpx.tracks:
            for segment in track.segments:
                for point in segment.points:
                    raw_points.append((point.latitude, point.longitude))
        
        if not raw_points:
            for route in gpx.routes:
                for point in route.points:
                    raw_points.append((point.latitude, point.longitude))

        if len(raw_points) > 1:
            # 1. 路线处理
            step = max(1, len(raw_points) // 500)
            folium_points = raw_points[::step]
            shapely_points = [(p[1], p[0]) for p in folium_points]

            folium.PolyLine(folium_points, color="blue", weight=4, opacity=0.8).add_to(m)
            
            # 2. 缓冲区
            deg_buffer = buffer_radius_m / 90000.0
            route_line = LineString(shapely_points)
            raw_buffer = route_line.buffer(deg_buffer)
            
            # 绘制橙色区域 (简化版)
            simplified_buffer = raw_buffer.simplify(tolerance=0.0005)
            folium.GeoJson(
                simplified_buffer,
                style_function=lambda x: {'fillColor': 'orange', 'color': 'orange', 'weight': 1, 'fillOpacity': 0.1}
            ).add_to(m)
            
            m.fit_bounds(route_line.bounds)

            # 3. 全局搜索 (不再依赖 box，直接暴力循环所有数据，确保不漏)
            # 为了性能，还是得先缩小范围，但是放宽一点
            min_x, min_y, max_x, max_y = raw_buffer.bounds
            # 扩大搜索框
            candidates = all_bears[
                (all_bears['longitude'] >= min_x - 0.05) & 
                (all_bears['longitude'] <= max_x + 0.05) &
                (all_bears['latitude'] >= min_y - 0.05) & 
                (all_bears['latitude'] <= max_y + 0.05)
            ]
            
            debug_msg.append(f"🔍 粗筛候选点数: {len(candidates)}")

            for idx, row in candidates.iterrows():
                # 强制类型转换，确保万无一失
                b_lon = float(row['longitude'])
                b_lat = float(row['latitude'])
                bear_pt = Point(b_lon, b_lat)
                
                # 判定
                is_danger = raw_buffer.contains(bear_pt)
                
                # 准备坐标 (Lat, Lon)
                loc = [b_lat, b_lon]

                if is_danger:
                    detected_danger.append(row)
                    
                    # === 绘制危险点 (纯几何图形，不含文本) ===
                    # 1. 红线
                    nearest = nearest_points(route_line, bear_pt)[0]
                    folium.PolyLine(
                        [[nearest.y, nearest.x], loc],
                        color="red", weight=3, opacity=1
                    ).add_to(m)
                    
                    # 2. 红点 (CircleMarker) - 放在最上层
                    folium.CircleMarker(
                        location=loc,
                        radius=8,
                        color="#FF0000",      # 纯红
                        fill=True,
                        fill_color="#FF0000",
                        fill_opacity=1.0,
                        popup="DANGER",       # 纯英文 Popup，防止乱码
                        tooltip="Danger",
                        z_index_offset=9999   # 强制置顶
                    ).add_to(m)
                    
                else:
                    # === 绘制附近安全点 ===
                    folium.CircleMarker(
                        location=loc,
                        radius=4,
                        color="gray",
                        fill=True,
                        fill_color="gray",
                        fill_opacity=0.6,
                        popup="Safe"
                    ).add_to(m)

    except Exception as e:
        st.error(f"Error: {e}")

# ==========================================
# 4. 渲染
# ==========================================
with col1:
    map_html = m._repr_html_()
    components.html(map_html, height=600)

with col2:
    if uploaded_file:
        st.subheader("🛠 调试信息")
        for m in debug_msg:
            st.write(m)
            
        if detected_danger:
            st.error(f"🔴 发现 {len(detected_danger)} 个危险点 (已尝试绘制)")
        else:
            st.success("🟢 暂无危险")
