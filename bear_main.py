import streamlit as st
import streamlit.components.v1 as components 
import pandas as pd
import requests
import gpxpy
from shapely.geometry import Point, LineString
import folium
import datetime

# ==========================================
# 0. 页面配置
# ==========================================
st.set_page_config(page_title="熊出没地图 (布局调整版)", layout="wide", page_icon="🐻")
st.title("🐻 熊出没安全地图")

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
            df = df.dropna(subset=['latitude', 'longitude'])
            df['sighting_datetime'] = pd.to_datetime(df['sighting_datetime'], errors='coerce')

            def make_description(row):
                parts = [str(row.get(c, '')) for c in ['目撃市町村', '場所'] if str(row.get(c, '')) != 'nan']
                return " ".join(parts)
            df['sighting_condition'] = df.apply(make_description, axis=1)
            return df
    except Exception:
        return pd.DataFrame()
    return pd.DataFrame()

all_bears = load_yamanashi_data()
if all_bears.empty:
    st.error("❌ 数据库加载失败")
    st.stop()

# ==========================================
# 2. 布局定义 (关键修改)
# ==========================================
# 先定义两列，方便把控件放进去
col1, col2 = st.columns([3, 1])

# --- 左侧 (col1): 上传控件 ---
with col1:
    uploaded_file = st.file_uploader("📂 第一步: 上传 GPX 路线文件", type=['gpx'])

# --- 右侧 (col2): 设置控件 (移到这里了) ---
with col2:
    st.subheader("⚙️ 检测设置")
    buffer_radius_m = st.slider("预警距离 (米)", 100, 5000, 500, 100)
    st.divider() # 加一条分割线，区分设置和结果

# ==========================================
# 3. 处理逻辑
# ==========================================
map_html = ""
danger_list = []
points_count = 0

if uploaded_file:
    try:
        # --- A. 解析 GPX ---
        gpx = gpxpy.parse(uploaded_file)
        points = []
        for track in gpx.tracks:
            for segment in track.segments:
                for point in segment.points:
                    points.append((point.latitude, point.longitude))
        
        if not points:
            for route in gpx.routes:
                for point in route.points:
                    points.append((point.latitude, point.longitude))
        
        points_count = len(points)
        
        if points_count > 0:
            # --- B. 准备地图 ---
            start_lat, start_lon = points[0]
            m = folium.Map(location=[start_lat, start_lon], zoom_start=12, tiles="OpenStreetMap")
            
            # --- C. 画路线 ---
            folium.PolyLine(points, color="blue", weight=5, opacity=0.7).add_to(m)
            
            # --- D. 计算危险点 ---
            line_points = [(p[1], p[0]) for p in points]
            route_line = LineString(line_points)
            
            deg_buffer = buffer_radius_m / 90000.0
            route_buffer = route_line.buffer(deg_buffer)
            
            # 粗筛
            min_x, min_y, max_x, max_y = route_buffer.bounds
            candidates = all_bears[
                (all_bears['longitude'] >= min_x - 0.05) & 
                (all_bears['longitude'] <= max_x + 0.05) &
                (all_bears['latitude'] >= min_y - 0.05) & 
                (all_bears['latitude'] <= max_y + 0.05)
            ]
            
            # 精筛
            for idx, row in candidates.iterrows():
                b_lat = float(row['latitude'])
                b_lon = float(row['longitude'])
                bear_pt = Point(b_lon, b_lat)
                
                if route_buffer.contains(bear_pt):
                    danger_list.append(row)
                    
                    folium.Marker(
                        location=[b_lat, b_lon],
                        popup=f"⚠️ {str(row['sighting_datetime'])[:10]}",
                        icon=folium.Icon(color='red', icon='info-sign')
                    ).add_to(m)
            
            m.fit_bounds(route_line.bounds)
            map_html = m._repr_html_()
            
        else:
            st.warning("GPX 解析成功但无坐标点。")
            
    except Exception as e:
        st.error(f"处理报错: {e}")

# ==========================================
# 4. 渲染输出
# ==========================================

# 左侧：显示地图
with col1:
    if map_html:
        components.html(map_html, height=600)
    else:
        # 空地图占位
        m_empty = folium.Map(location=[35.6, 138.5], zoom_start=10)
        components.html(m_empty._repr_html_(), height=600)

# 右侧：显示结果列表 (在滑块下方)
with col2:
    if uploaded_file:
        if points_count > 0:
            # 这里的 margin-top 是为了稍微好看一点
            st.markdown("#### 📊 检测报告") 
            
            if danger_list:
                st.error(f"🔴 发现 {len(danger_list)} 个危险点")
                res_df = pd.DataFrame(danger_list).sort_values('sighting_datetime', ascending=False)
                
                # 列表显示
                st.dataframe(
                    res_df[['sighting_datetime', 'sighting_condition']],
                    hide_index=True,
                    height=500
                )
            else:
                st.success(f"🟢 安全")
                st.caption("路线周边未发现记录。")
    else:
        st.info("👈 请先上传 GPX")
