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
st.set_page_config(page_title="熊出没地图 (终极版)", layout="wide", page_icon="🐻")

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
    buffer_radius_m = st.slider("预警距离 (米)", 100, 5000, 500, 100)

col1, col2 = st.columns([3, 1])
with col1:
    uploaded_file = st.file_uploader("📂 上传 GPX 文件", type=['gpx'])

center_lat, center_lon = 35.6, 138.5
m = folium.Map(location=[center_lat, center_lon], zoom_start=10, tiles="OpenStreetMap")

# ==========================================
# 3. GPX 处理
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
            # 1. 画蓝色路线
            # 抽稀防止卡顿
            step = max(1, len(raw_points) // 500)
            folium_points = raw_points[::step]
            shapely_points = [(p[1], p[0]) for p in folium_points]

            folium.PolyLine(folium_points, color="#3388ff", weight=5, opacity=0.8).add_to(m)
            
            # 2. 计算缓冲区 (仅用于数学计算，不画在地图上，防止崩溃)
            deg_buffer = buffer_radius_m / 90000.0
            route_line = LineString(shapely_points)
            raw_buffer = route_line.buffer(deg_buffer)
            
            # 3. 调整地图视野
            m.fit_bounds(route_line.bounds)

            # 4. 暴力扫描 + 绘制红点
            # 先缩小范围提升速度
            min_x, min_y, max_x, max_y = raw_buffer.bounds
            candidates = all_bears[
                (all_bears['longitude'] >= min_x - 0.05) & 
                (all_bears['longitude'] <= max_x + 0.05) &
                (all_bears['latitude'] >= min_y - 0.05) & 
                (all_bears['latitude'] <= max_y + 0.05)
            ]

            for idx, row in candidates.iterrows():
                b_lon = float(row['longitude'])
                b_lat = float(row['latitude'])
                bear_pt = Point(b_lon, b_lat)
                
                # 判定是否在圈内
                if raw_buffer.contains(bear_pt):
                    detected_danger.append(row)
                    
                    # === 绘制逻辑 (只有这里画图) ===
                    
                    # 1. 计算连接线
                    nearest = nearest_points(route_line, bear_pt)[0]
                    line_coords = [[nearest.y, nearest.x], [b_lat, b_lon]]
                    
                    # 画红线
                    folium.PolyLine(
                        line_coords,
                        color="#FF0000", # 纯红 Hex
                        weight=3,
                        dash_array='5, 5',
                        opacity=1.0
                    ).add_to(m)
                    
                    # 画大红点 (CircleMarker 绝对稳)
                    folium.CircleMarker(
                        location=[b_lat, b_lon],
                        radius=8,
                        color="#FF0000",
                        fill=True,
                        fill_color="#FF0000",
                        fill_opacity=1.0,
                        stroke=True,
                        weight=2,
                        popup="DANGER", # 简单文本
                        z_index_offset=1000
                    ).add_to(m)

    except Exception as e:
        st.error(f"处理出错: {e}")

# ==========================================
# 4. 渲染地图
# ==========================================
with col1:
    # 静态渲染
    map_html = m._repr_html_()
    components.html(map_html, height=600)

# --- 结果面板 (保留你需要的列表) ---
with col2:
    if uploaded_file:
        st.subheader("📊 详细危险点列表")
        
        if detected_danger:
            st.error(f"🔴 共发现 {len(detected_danger)} 处威胁")
            
            # 整理数据
            res_df = pd.DataFrame(detected_danger).sort_values('sighting_datetime', ascending=False)
            
            # 循环展示详情卡片
            for idx, row in res_df.iterrows():
                # 处理时间格式
                if pd.notnull(row['sighting_datetime']):
                    d_str = row['sighting_datetime'].strftime('%Y-%m-%d')
                else:
                    d_str = "时间未知"
                
                with st.expander(f"⚠️ {d_str}", expanded=True):
                    st.write(f"**地点:** {row['sighting_condition']}")
                    # 这里显示坐标方便核对
                    st.caption(f"坐标: {row['latitude']:.4f}, {row['longitude']:.4f}")
        else:
            st.success("🟢 路线周边安全")
            st.caption(f"检测范围: {buffer_radius_m} 米")
    else:
        st.info("👈 请先上传 GPX 文件")
