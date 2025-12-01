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
st.set_page_config(page_title="熊出没地图 (全量数据版)", layout="wide", page_icon="🐻")
st.title("🐻 熊出没安全地图 (2022-2025 全量数据)")

# ==========================================
# 1. 数据抽取 (合并三个年度)
# ==========================================
@st.cache_data
def load_yamanashi_data():
    url = "https://catalog.dataplatform-yamanashi.jp/api/action/datastore_search"
    
    # 这里的列表包含了你提供的所有 ID
    resource_ids = [
        "b4eb262f-07e0-4417-b24f-6b15844b4ac1", # 2024-2025 (最新)
        "62796404-c80f-47d6-ae88-222f844ee958", # 2023 (历史)
        "89d2478e-e29e-46e3-9ad3-19bf44822d4d"  # 2022 (历史)
    ]
    
    all_frames = []
    
    # 循环获取所有 ID 的数据
    for rid in resource_ids:
        params = {"resource_id": rid, "limit": 10000} # 确保拿全
        try:
            response = requests.get(url, params=params, timeout=10)
            data = response.json()
            
            if 'result' in data and 'records' in data['result']:
                df = pd.DataFrame(data['result']['records'])
                
                # 字段名映射 (涵盖不同年份可能的写法)
                rename_map = {
                    '緯度': 'latitude', '纬度': 'latitude', 'Lat': 'latitude', 'LAT': 'latitude',
                    '経度': 'longitude', '经度': 'longitude', 'Lon': 'longitude', 'LON': 'longitude',
                    '年月日': 'sighting_datetime', '発生日時': 'sighting_datetime', 'Date': 'sighting_datetime'
                }
                df = df.rename(columns=rename_map)
                
                # 必须有经纬度
                if 'latitude' in df.columns and 'longitude' in df.columns:
                    df['latitude'] = pd.to_numeric(df['latitude'], errors='coerce')
                    df['longitude'] = pd.to_numeric(df['longitude'], errors='coerce')
                    df = df.dropna(subset=['latitude', 'longitude'])
                    
                    # 时间转换
                    if 'sighting_datetime' in df.columns:
                        df['sighting_datetime'] = pd.to_datetime(df['sighting_datetime'], errors='coerce')
                    else:
                        df['sighting_datetime'] = pd.NaT

                    # 描述字段构建
                    def make_description(row):
                        parts = []
                        # 不同年份字段名可能不同，尝试所有可能性
                        possible_cols = ['目撃市町村', '場所', '住所', '詳細', '状況', 'Municipality', 'Place']
                        for col in possible_cols:
                            val = str(row.get(col, ''))
                            if val and val != 'nan':
                                parts.append(val)
                        return " ".join(parts) if parts else "无描述"
                    
                    df['sighting_condition'] = df.apply(make_description, axis=1)
                    
                    # 统一列结构
                    clean_df = df[['latitude', 'longitude', 'sighting_datetime', 'sighting_condition']]
                    all_frames.append(clean_df)
                    
        except Exception as e:
            print(f"ID {rid} 加载失败: {e}")
            continue

    if all_frames:
        final_df = pd.concat(all_frames, ignore_index=True)
        return final_df
    else:
        return pd.DataFrame()

# 加载数据
all_bears = load_yamanashi_data()
if all_bears.empty:
    st.error("❌ 数据库加载失败")
    st.stop()

# ==========================================
# 2. 界面布局
# ==========================================
col1, col2 = st.columns([3, 1])

with col1:
    uploaded_file = st.file_uploader("📂 上传 GPX 路线文件", type=['gpx'])

with col2:
    st.subheader("⚙️ 检测设置")
    buffer_radius_m = st.slider("预警距离 (米)", 100, 5000, 500, 100)
    
    # 显示数据统计
    if not all_bears.empty:
        min_date = all_bears['sighting_datetime'].min().strftime('%Y-%m')
        max_date = all_bears['sighting_datetime'].max().strftime('%Y-%m')
        st.info(f"📚 数据库覆盖: {min_date} 至 {max_date}\n总记录数: {len(all_bears)}")
    
    st.divider()

# ==========================================
# 3. 处理逻辑
# ==========================================
map_html = ""
danger_list = []
points_count = 0

if uploaded_file:
    try:
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
            # 地图中心
            start_lat, start_lon = points[0]
            m = folium.Map(location=[start_lat, start_lon], zoom_start=12, tiles="OpenStreetMap")
            
            # 画路线 (蓝线)
            folium.PolyLine(points, color="blue", weight=5, opacity=0.7).add_to(m)
            
            # 缓冲区计算
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
            
            # 精筛与绘图
            for idx, row in candidates.iterrows():
                b_lat = float(row['latitude'])
                b_lon = float(row['longitude'])
                bear_pt = Point(b_lon, b_lat)
                
                if route_buffer.contains(bear_pt):
                    danger_list.append(row)
                    
                    # 红点高亮
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
        st.error(f"处理报错: {
