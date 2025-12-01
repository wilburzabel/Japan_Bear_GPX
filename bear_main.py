import streamlit as st
import streamlit.components.v1 as components  # <--- 关键变化：引入原生组件库
import pandas as pd
import requests
import gpxpy
from shapely.geometry import Point, LineString
import folium
from folium.plugins import MarkerCluster
import datetime

# ==========================================
# 0. 页面配置
# ==========================================
st.set_page_config(page_title="熊出没安全地图 (静态渲染版)", layout="wide", page_icon="🐻")

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
            
            # 容错：查找经纬度列
            if 'latitude' not in df.columns:
                for col in ['lat', 'Lat', 'LAT', '纬度']:
                    if col in df.columns: df = df.rename(columns={col: 'latitude'}); break

            # 强转数字
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

col1, col2 = st.columns([3, 1])
with col1:
    uploaded_file = st.file_uploader("📂 上传 GPX 文件", type=['gpx'])

# 准备地图中心
center_lat, center_lon = 35.6, 138.5
if not all_bears.empty:
    center_lat, center_lon = all_bears['latitude'].mean(), all_bears['longitude'].mean()

# 创建地图对象
m = folium.Map(location=[center_lat, center_lon], zoom_start=10, tiles="OpenStreetMap")

# ==========================================
# 3. GPX 处理逻辑
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
                    raw_points.append((point.latitude, point.longitude)) # (Lat, Lon)
        
        # 兼容 routes
        if not raw_points:
            for route in gpx.routes:
                for point in route.points:
                    raw_points.append((point.latitude, point.longitude))

        if len(raw_points) > 1:
            has_gpx = True
            
            # --- 抽稀 (保证地图不卡) ---
            step = max(1, len(raw_points) // 500)
            folium_points = raw_points[::step]
            shapely_points = [(p[1], p[0]) for p in folium_points] # (Lon, Lat)

            # 1. 画路线
            folium.PolyLine(folium_points, color="blue", weight=4, opacity=0.8).add_to(m)
            
            # 2. 缓冲区
            deg_buffer = buffer_radius_m / 90000.0
            route_line = LineString(shapely_points)
            raw_buffer = route_line.buffer(deg_buffer)
            # 简化多边形 (防止 HTML 过大)
            simplified_buffer = raw_buffer.simplify(tolerance=0.0005)

            # 3. 画橙色范围
            folium.GeoJson(
                simplified_buffer,
                style_function=lambda x: {'fillColor': 'orange', 'color': 'orange', 'weight': 1, 'fillOpacity': 0.2}
            ).add_to(m)
            
            m.fit_bounds(route_line.bounds)

            # 4. 检测
            min_x, min_y, max_x, max_y = raw_buffer.bounds
            candidates = all_bears[
                (all_bears['longitude'] >= min_x) & (all_bears['longitude'] <= max_x) &
                (all_bears['latitude'] >= min_y) & (all_bears['latitude'] <= max_y)
            ]
            
            for idx, row in candidates.iterrows():
                # 必须用原始 buffer 做包含判断，保证精度
                if raw_buffer.contains(Point(row['longitude'], row['latitude'])):
                    detected_danger.append(row)

            # 5. 标记危险点
            for bear in detected_danger:
                date_str = str(bear['sighting_datetime'])[:10]
                folium.Marker(
                    [bear['latitude'], bear['longitude']],
                    popup=f"⚠️ {date_str}",
                    icon=folium.Icon(color="red", icon="warning-sign"),
                ).add_to(m)

    except Exception as e:
        st.error(f"GPX 解析失败: {e}")

# --- 背景点 ---
if not has_gpx and not all_bears.empty:
    cluster = MarkerCluster().add_to(m)
    for idx, row in all_bears.head(500).iterrows():
        folium.Marker(
            [row['latitude'], row['longitude']],
            icon=folium.Icon(color="lightgray", icon="info-sign"),
        ).add_to(cluster)

# ==========================================
# 4. 渲染地图 (关键变化点!)
# ==========================================
with col1:
    # 彻底放弃 st_folium，改用静态 HTML 渲染
    # 这种方式非常稳定，几乎不会因为数据量或重新加载而崩溃
    map_html = m._repr_html_()
    components.html(map_html, height=600)

# --- 结果面板 ---
with col2:
    if has_gpx:
        st.subheader("📊 检测报告")
        if detected_danger:
            st.error(f"🔴 发现 {len(detected_danger)} 个危险点")
            res_df = pd.DataFrame(detected_danger)
            if 'sighting_datetime' in res_df.columns:
                res_df['时间'] = res_df['sighting_datetime'].dt.strftime('%Y-%m-%d')
            else:
                res_df['时间'] = "未知"
            st.dataframe(res_df[['时间', 'sighting_condition']], hide_index=True, height=400)
        else:
            st.success("🟢 路线周边安全")
    else:
        st.info("👈 请上传 GPX 文件")
