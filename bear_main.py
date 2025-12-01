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
st.set_page_config(page_title="熊出没地图 (绝对渲染版)", layout="wide", page_icon="🐻")

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

            # 强制转为 float，并删除空值
            df['latitude'] = pd.to_numeric(df['latitude'], errors='coerce')
            df['longitude'] = pd.to_numeric(df['longitude'], errors='coerce')
            df = df.dropna(subset=['latitude', 'longitude'])
            
            # 处理日期
            df['sighting_datetime'] = pd.to_datetime(df['sighting_datetime'], errors='coerce')

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
st.title("🐻 熊出没安全地图")

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

# 临时初始化，稍后会根据 GPX 覆盖
m = folium.Map(location=[35.6, 138.5], zoom_start=10, tiles="OpenStreetMap")

# ==========================================
# 3. GPX 处理与绘图
# ==========================================
detected_danger = []

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
            # --- 1. 路线处理 ---
            step = max(1, len(raw_points) // 500)
            folium_points = raw_points[::step]
            # 这里的转换非常关键：shapely 需要 (Lon, Lat)
            shapely_points = [(float(p[1]), float(p[0])) for p in folium_points]

            # 画蓝色路线
            folium.PolyLine(folium_points, color="blue", weight=5, opacity=0.7).add_to(m)
            
            # --- 2. 橙色缓冲区 (恢复显示) ---
            deg_buffer = buffer_radius_m / 90000.0
            route_line = LineString(shapely_points)
            raw_buffer = route_line.buffer(deg_buffer)
            
            # 简化后绘制橙色区域
            simplified_buffer = raw_buffer.simplify(tolerance=0.0005)
            folium.GeoJson(
                simplified_buffer,
                style_function=lambda x: {'fillColor': '#FFA500', 'color': '#FFA500', 'weight': 1, 'fillOpacity': 0.2}
            ).add_to(m)
            
            # 强制调整地图视野到路线位置
            m.fit_bounds(route_line.bounds)

            # --- 3. 检测危险 ---
            min_x, min_y, max_x, max_y = raw_buffer.bounds
            candidates = all_bears[
                (all_bears['longitude'] >= min_x - 0.05) & 
                (all_bears['longitude'] <= max_x + 0.05) &
                (all_bears['latitude'] >= min_y - 0.05) & 
                (all_bears['latitude'] <= max_y + 0.05)
            ]

            for idx, row in candidates.iterrows():
                # 【关键修复】强制转换为 Python 原生 float
                b_lon = float(row['longitude'])
                b_lat = float(row['latitude'])
                bear_pt = Point(b_lon, b_lat)
                
                if raw_buffer.contains(bear_pt):
                    detected_danger.append(row)
                    
                    # --- 绘制危险点 (使用物理大圆圈) ---
                    # 计算最近连接点
                    nearest = nearest_points(route_line, bear_pt)[0]
                    
                    # 画红线
                    folium.PolyLine(
                        [[nearest.y, nearest.x], [b_lat, b_lon]],
                        color="red", weight=3, dash_array='5, 5', opacity=1
                    ).add_to(m)
                    
                    # 画大红圈 (folium.Circle)
                    # radius=200 表示半径200米，这个圆在地图上会非常巨大，不可能看不见
                    folium.Circle(
                        location=[b_lat, b_lon],
                        radius=200,          # 物理半径 200米
                        color="red",
                        fill=True,
                        fill_color="red",
                        fill_opacity=0.8,
                        popup="DANGER",
                        tooltip="点击查看详情"
                    ).add_to(m)

    except Exception as e:
        st.error(f"处理失败: {e}")

# ==========================================
# 4. 渲染
# ==========================================
with col1:
    map_html = m._repr_html_()
    components.html(map_html, height=600)

with col2:
    if uploaded_file:
        st.subheader("📊 危险列表")
        if detected_danger:
            st.error(f"🔴 发现 {len(detected_danger)} 个危险点")
            
            res_df = pd.DataFrame(detected_danger).sort_values('sighting_datetime', ascending=False)
            for idx, row in res_df.iterrows():
                d_str = row['sighting_datetime'].strftime('%Y-%m-%d') if pd.notnull(row['sighting_datetime']) else "未知"
                with st.expander(f"⚠️ {d_str}", expanded=True):
                    st.write(f"**详情:** {row['sighting_condition']}")
                    # 显示坐标，方便你在地图上找
                    st.caption(f"坐标: {row['latitude']:.4f}, {row['longitude']:.4f}")
        else:
            st.success("🟢 路线周边安全")
    else:
        st.info("👈 请上传 GPX")
