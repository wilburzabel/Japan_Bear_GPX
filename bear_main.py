import streamlit as st
import pandas as pd
import requests
import gpxpy
from shapely.geometry import Point, LineString
from streamlit_folium import st_folium
import folium
from folium.plugins import MarkerCluster
import datetime

# ==========================================
# 0. 页面配置
# ==========================================
st.set_page_config(page_title="熊出没安全地图 (轻量版)", layout="wide", page_icon="🐻")

# ==========================================
# 1. 数据抽取 (山梨县 API)
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
                return " ".join(parts) if parts else "无位置描述"

            df['sighting_condition'] = df.apply(make_description, axis=1)
            return df
    except Exception:
        return pd.DataFrame()
    return pd.DataFrame()

# ==========================================
# 2. 主逻辑
# ==========================================
st.title("🐻 熊出没安全地图 (性能优化版)")

all_bears = load_yamanashi_data()
if all_bears.empty:
    st.error("❌ 数据库加载失败")
    st.stop()

with st.sidebar:
    st.header("⚙️ 设置")
    buffer_radius_m = st.slider("预警距离 (米)", 100, 3000, 500, 100)

col1, col2 = st.columns([3, 1])
with col1:
    uploaded_file = st.file_uploader("📂 上传 GPX 文件", type=['gpx'])

# 准备地图
center_lat, center_lon = 35.6, 138.5
if not all_bears.empty:
    center_lat, center_lon = all_bears['latitude'].mean(), all_bears['longitude'].mean()

m = folium.Map(location=[center_lat, center_lon], zoom_start=10, tiles="OpenStreetMap")

# ==========================================
# 3. GPX 处理 (核心优化部分)
# ==========================================
detected_danger = []
has_gpx = False

if uploaded_file is not None:
    try:
        gpx = gpxpy.parse(uploaded_file)
        raw_points = []
        
        # 1. 提取所有点
        for track in gpx.tracks:
            for segment in track.segments:
                for point in segment.points:
                    raw_points.append((point.latitude, point.longitude))
        
        # 兼容 routes
        if not raw_points:
            for route in gpx.routes:
                for point in route.points:
                    raw_points.append((point.latitude, point.longitude))

        if len(raw_points) > 1:
            has_gpx = True
            total_points = len(raw_points)
            
            # --- 🚀 性能优化步骤 1: 点位抽稀 (Downsampling) ---
            # 如果点太多，浏览器渲染几万个点会崩。我们限制最大点数为 500 个。
            # 这不会影响 500米的检测精度，但能极大提升渲染速度。
            step = 1
            if total_points > 500:
                step = total_points // 500
            
            # 抽稀后的点列表 (用于绘图和计算)
            # folium 需要 (Lat, Lon)
            optimized_folium_points = raw_points[::step]
            
            # shapely 需要 (Lon, Lat)
            optimized_shapely_points = [(p[1], p[0]) for p in optimized_folium_points]
            
            st.caption(f"ℹ️ 性能优化: 原始路径 {total_points} 点 -> 优化后 {len(optimized_folium_points)} 点")

            # 2. 画路线 (蓝色)
            folium.PolyLine(optimized_folium_points, color="blue", weight=4, opacity=0.8).add_to(m)
            
            # 3. 生成缓冲区
            deg_buffer = buffer_radius_m / 90000.0
            route_line = LineString(optimized_shapely_points)
            raw_buffer = route_line.buffer(deg_buffer)
            
            # --- 🚀 性能优化步骤 2: 几何简化 (Simplify) ---
            # 简化多边形边缘，减少顶点数量。0.001 度的精度约等于 100米，对于视觉显示足够了。
            # 如果不简化，这个 GeoJSON 可能有几十万个字符，导致地图消失。
            simplified_buffer = raw_buffer.simplify(tolerance=0.0005, preserve_topology=False)
            
            # 4. 画预警范围 (橙色)
            folium.GeoJson(
                simplified_buffer,
                style_function=lambda x: {'fillColor': 'orange', 'color': 'orange', 'weight': 1, 'fillOpacity': 0.2}
            ).add_to(m)
            
            # 5. 缩放地图
            m.fit_bounds(route_line.bounds)

            # 6. 碰撞检测 (使用简化后的 buffer 进行粗略检测，或者用 raw_buffer 也可以，这里用 raw_buffer 保证精度)
            min_x, min_y, max_x, max_y = raw_buffer.bounds
            candidates = all_bears[
                (all_bears['longitude'] >= min_x) & (all_bears['longitude'] <= max_x) &
                (all_bears['latitude'] >= min_y) & (all_bears['latitude'] <= max_y)
            ]
            
            for idx, row in candidates.iterrows():
                # 注意：Point 是 (Lon, Lat)
                if raw_buffer.contains(Point(row['longitude'], row['latitude'])):
                    detected_danger.append(row)
            
            # 7. 标记危险点
            for bear in detected_danger:
                date_str = str(bear['sighting_datetime'])[:10]
                folium.Marker(
                    [bear['latitude'], bear['longitude']],
                    popup=f"⚠️ {date_str}",
                    icon=folium.Icon(color="red", icon="warning-sign"),
                    z_index_offset=1000
                ).add_to(m)

    except Exception as e:
        st.error(f"GPX处理错误: {e}")

# --- 背景点 ---
if not has_gpx:
    if not all_bears.empty:
        cluster = MarkerCluster().add_to(m)
        # 没上传文件时，最多只显示 500 个点，防止还没开始就崩了
        for idx, row in all_bears.head(500).iterrows():
            folium.Marker(
                [row['latitude'], row['longitude']],
                icon=folium.Icon(color="lightgray", icon="info-sign"),
            ).add_to(cluster)

# --- 渲染地图 (禁止回传数据) ---
with col1:
    st_folium(m, width=800, height=600, returned_objects=[])

# --- 结果 ---
with col2:
    if has_gpx:
        st.subheader("📊 报告")
        if detected_danger:
            st.error(f"🔴 发现 {len(detected_danger)} 个危险点")
            res_df = pd.DataFrame(detected_danger)
            if 'sighting_datetime' in res_df.columns:
                res_df['时间'] = res_df['sighting_datetime'].dt.strftime('%Y-%m-%d')
            else:
                res_df['时间'] = "未知"
            st.dataframe(res_df[['时间', 'sighting_condition']], hide_index=True)
        else:
            st.success("🟢 路线周边安全")
            st.caption(f"范围: {buffer_radius_m}米")
    else:
        st.info("请上传 GPX")
