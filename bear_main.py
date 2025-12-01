import streamlit as st
import streamlit.components.v1 as components 
import pandas as pd
import requests
import gpxpy
from shapely.geometry import Point, LineString
from shapely.ops import nearest_points
import folium
from folium.plugins import MarkerCluster
import datetime

# ==========================================
# 0. 页面配置
# ==========================================
st.set_page_config(page_title="熊出没安全地图 (高亮修复版)", layout="wide", page_icon="🐻")

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
    buffer_radius_m = st.slider("预警距离 (米)", 100, 3000, 500, 100)
    st.caption("调整滑块可改变黄色警戒区域的大小")

col1, col2 = st.columns([3, 1])
with col1:
    uploaded_file = st.file_uploader("📂 上传 GPX 文件", type=['gpx'])

# 地图默认中心
center_lat, center_lon = 35.6, 138.5
if not all_bears.empty:
    center_lat, center_lon = all_bears['latitude'].mean(), all_bears['longitude'].mean()

m = folium.Map(location=[center_lat, center_lon], zoom_start=10, tiles="OpenStreetMap")

# ==========================================
# 3. GPX 处理与高亮绘图
# ==========================================
detected_danger = []
has_gpx = False

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
            has_gpx = True
            
            # --- 抽稀 (性能优化) ---
            step = max(1, len(raw_points) // 500)
            folium_points = raw_points[::step]
            shapely_points = [(p[1], p[0]) for p in folium_points] # (Lon, Lat)

            # 1. 画路线 (蓝色)
            folium.PolyLine(folium_points, color="blue", weight=4, opacity=0.8).add_to(m)
            
            # 2. 缓冲区
            deg_buffer = buffer_radius_m / 90000.0
            route_line = LineString(shapely_points)
            raw_buffer = route_line.buffer(deg_buffer)
            simplified_buffer = raw_buffer.simplify(tolerance=0.0005)

            # 3. 画警戒区域 (橙色)
            folium.GeoJson(
                simplified_buffer,
                style_function=lambda x: {'fillColor': 'orange', 'color': 'orange', 'weight': 1, 'fillOpacity': 0.15}
            ).add_to(m)
            
            m.fit_bounds(route_line.bounds)

            # 4. 检测与高亮
            min_x, min_y, max_x, max_y = raw_buffer.bounds
            candidates = all_bears[
                (all_bears['longitude'] >= min_x) & (all_bears['longitude'] <= max_x) &
                (all_bears['latitude'] >= min_y) & (all_bears['latitude'] <= max_y)
            ]
            
            for idx, row in candidates.iterrows():
                # 坐标转为 float 确保不出错
                bear_lon = float(row['longitude'])
                bear_lat = float(row['latitude'])
                bear_pt = Point(bear_lon, bear_lat)
                
                if raw_buffer.contains(bear_pt):
                    detected_danger.append(row)
                    
                    # --- 计算指引线 ---
                    # 找到路线上最近的点
                    nearest_pt_on_route = nearest_points(route_line, bear_pt)[0]
                    # nearest_pt_on_route.x 是经度(Lon), .y 是纬度(Lat)
                    
                    # 连线坐标: [ [Lat1, Lon1], [Lat2, Lon2] ]
                    line_coords = [
                        [float(nearest_pt_on_route.y), float(nearest_pt_on_route.x)], 
                        [bear_lat, bear_lon]
                    ]
                    
                    # A. 画红色连接线 (加粗实线，确保看见)
                    folium.PolyLine(
                        line_coords,
                        color="red",
                        weight=3,        # 加粗
                        opacity=1,
                        dash_array='5, 5' # 虚线
                    ).add_to(m)
                    
                    # B. 画高亮图标 (使用默认图标，不加 prefix，最稳)
                    date_str = str(row['sighting_datetime'])[:10]
                    folium.Marker(
                        [bear_lat, bear_lon],
                        popup=f"⚠️ {date_str}<br>{row['sighting_condition']}",
                        # 使用标准 exclamation-sign，颜色 bright red
                        icon=folium.Icon(color="red", icon="exclamation-sign"), 
                        z_index_offset=1000
                    ).add_to(m)

    except Exception as e:
        st.error(f"GPX 解析失败: {e}")

# --- 背景点 (仅在未上传GPX时显示) ---
if not has_gpx and not all_bears.empty:
    cluster = MarkerCluster().add_to(m)
    for idx, row in all_bears.head(500).iterrows():
        folium.Marker(
            [row['latitude'], row['longitude']],
            icon=folium.Icon(color="lightgray", icon="info-sign"),
        ).add_to(cluster)

# ==========================================
# 4. 渲染地图
# ==========================================
with col1:
    map_html = m._repr_html_()
    components.html(map_html, height=600)

# --- 结果面板 ---
with col2:
    if has_gpx:
        st.subheader("📊 检测报告")
        if detected_danger:
            st.error(f"🔴 发现 {len(detected_danger)} 个危险点")
            
            res_df = pd.DataFrame(detected_danger).sort_values('sighting_datetime', ascending=False)
            
            for idx, row in res_df.iterrows():
                d_str = row['sighting_datetime'].strftime('%Y-%m-%d') if pd.notnull(row['sighting_datetime']) else "未知"
                with st.expander(f"⚠️ {d_str}", expanded=True):
                    st.write(f"**详情:** {row['sighting_condition']}")
        else:
            st.success("🟢 路线周边安全")
            st.caption(f"在 {buffer_radius_m} 米范围内未发现记录。")
    else:
        st.info("👈 请上传 GPX 文件")
