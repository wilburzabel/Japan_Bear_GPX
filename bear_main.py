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
st.set_page_config(page_title="熊出没地图 (基础稳健版)", layout="wide", page_icon="🐻")
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

            # 强转 float，删除空值
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
# 2. 界面布局
# ==========================================
with st.sidebar:
    st.header("⚙️ 设置")
    buffer_radius_m = st.slider("预警距离 (米)", 100, 5000, 500, 100)

uploaded_file = st.file_uploader("📂 上传 GPX 文件", type=['gpx'])

# ==========================================
# 3. 处理逻辑 (含文本诊断)
# ==========================================
map_html = ""
danger_list = []
debug_text = ""

if uploaded_file:
    try:
        # --- A. 解析 GPX ---
        gpx = gpxpy.parse(uploaded_file)
        points = []
        for track in gpx.tracks:
            for segment in track.segments:
                for point in segment.points:
                    points.append((point.latitude, point.longitude))
        
        # 如果 tracks 为空，尝试 routes
        if not points:
            for route in gpx.routes:
                for point in route.points:
                    points.append((point.latitude, point.longitude))
        
        # 文本诊断 1
        st.info(f"📍 GPX 解析状态: 成功读取到 {len(points)} 个坐标点。")
        
        if len(points) > 0:
            # --- B. 准备地图 ---
            # 既然有点，就强制地图中心定在起跑点
            start_lat, start_lon = points[0]
            m = folium.Map(location=[start_lat, start_lon], zoom_start=12, tiles="OpenStreetMap")
            
            # --- C. 画路线 (不做任何抽稀，原样画) ---
            folium.PolyLine(points, color="blue", weight=5, opacity=0.7).add_to(m)

            # ... (在 "folium.PolyLine(...).add_to(m)" 这行代码的下面插入) ...

            # === [可选功能] 绘制橙色预警范围 ===
            # 1. 计算缓冲区几何图形
            # 注意：这里需要先把 points 转为 (Lon, Lat) 给 Shapely 计算
            shapely_line_points = [(p[1], p[0]) for p in points]
            route_line_geom = LineString(shapely_line_points)
            
            # 简单估算：1度 ≈ 90,000米 (日本纬度)
            deg_buffer = buffer_radius_m / 90000.0
            raw_buffer = route_line_geom.buffer(deg_buffer)
            
            # 2. 关键步骤：简化多边形 (防止让地图变卡/消失)
            # tolerance=0.0005 约等于 50米精度，视觉上看不出区别，但能极大减少数据量
            simplified_buffer = raw_buffer.simplify(tolerance=0.0005)
            
            # 3. 画到地图上
            folium.GeoJson(
                simplified_buffer,
                style_function=lambda x: {
                    'fillColor': 'orange', 
                    'color': 'orange', 
                    'weight': 1, 
                    'fillOpacity': 0.15 # 很淡的橙色，不遮挡视线
                }
            ).add_to(m)
            
            # ... (接着是 "# --- D. 计算危险点 ---") ...
                    
            # --- D. 计算危险点 ---
            # 准备 Shapely 线段用于计算
            # 注意：Shapely 用 (Lon, Lat)
            line_points = [(p[1], p[0]) for p in points]
            route_line = LineString(line_points)
            
            # 计算简单的缓冲区
            deg_buffer = buffer_radius_m / 90000.0
            route_buffer = route_line.buffer(deg_buffer)
            
            # 暴力循环检查所有熊
            # 先用矩形框快速过滤一下，提升速度
            min_x, min_y, max_x, max_y = route_buffer.bounds
            candidates = all_bears[
                (all_bears['longitude'] >= min_x - 0.05) & 
                (all_bears['longitude'] <= max_x + 0.05) &
                (all_bears['latitude'] >= min_y - 0.05) & 
                (all_bears['latitude'] <= max_y + 0.05)
            ]
            
            st.info(f"🔎 粗筛检测: 路线附近发现 {len(candidates)} 条记录，正在进行精确判定...")
            
            for idx, row in candidates.iterrows():
                b_lat = float(row['latitude'])
                b_lon = float(row['longitude'])
                bear_pt = Point(b_lon, b_lat)
                
                if route_buffer.contains(bear_pt):
                    danger_list.append(row)
                    
                    # --- E. 画红点 (使用最原始的 Marker) ---
                    # 不用 CircleMarker，不用 SVG，就用最普通的红色图钉
                    folium.Marker(
                        location=[b_lat, b_lon],
                        popup="DANGER",
                        icon=folium.Icon(color='red', icon='info-sign')
                    ).add_to(m)
            
            # 调整缩放
            m.fit_bounds(route_line.bounds)
            
            # 生成 HTML
            map_html = m._repr_html_()
            
        else:
            st.warning("GPX 文件里没有找到路径点 (points is empty)。请检查文件内容。")
            
    except Exception as e:
        st.error(f"处理过程报错: {e}")

# ==========================================
# 4. 渲染输出
# ==========================================
col1, col2 = st.columns([3, 1])

with col1:
    if map_html:
        # 静态渲染
        components.html(map_html, height=600)
    else:
        # 如果没有 map_html，显示一个空地图占位
        m_empty = folium.Map(location=[35.6, 138.5], zoom_start=10)
        components.html(m_empty._repr_html_(), height=600)

with col2:
    if uploaded_file:
        if danger_list:
            st.error(f"🔴 最终确认: {len(danger_list)} 个危险点")
            res_df = pd.DataFrame(danger_list).sort_values('sighting_datetime', ascending=False)
            
            # 简单列表
            st.dataframe(
                res_df[['sighting_datetime', 'sighting_condition']],
                hide_index=True,
                height=500
            )
        else:
            if len(points) > 0:
                st.success("🟢 安全: 路线 500米 内无记录")
    else:
        st.info("👈 等待上传 GPX")
