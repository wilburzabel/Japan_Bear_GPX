import streamlit as st
import streamlit.components.v1 as components 
import pandas as pd
import requests
import gpxpy
from shapely.geometry import Point, LineString, box
from shapely.ops import nearest_points
import folium
import datetime

# ==========================================
# 0. 页面配置
# ==========================================
st.set_page_config(page_title="熊出没地图 (纯几何版)", layout="wide", page_icon="🐻")

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
st.title("🐻 熊出没安全地图 (几何渲染版)")
st.caption("🔴大红圆点 = 危险警告 | ⚫灰色小点 = 附近数据 | 🟦蓝色 = 你的路线")

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

# 地图初始化
center_lat, center_lon = 35.6, 138.5
m = folium.Map(location=[center_lat, center_lon], zoom_start=10, tiles="OpenStreetMap")

# ==========================================
# 3. GPX 处理与绘图
# ==========================================
detected_danger = []
debug_info = []

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
            # 1. 抽稀路线
            step = max(1, len(raw_points) // 500)
            folium_points = raw_points[::step]
            shapely_points = [(p[1], p[0]) for p in folium_points] # (Lon, Lat)

            # 2. 画路线 (蓝色)
            folium.PolyLine(folium_points, color="blue", weight=4, opacity=0.8).add_to(m)
            
            # 3. 生成缓冲区
            deg_buffer = buffer_radius_m / 90000.0
            route_line = LineString(shapely_points)
            raw_buffer = route_line.buffer(deg_buffer)
            
            # 简化并绘制橙色区域
            simplified_buffer = raw_buffer.simplify(tolerance=0.0005)
            folium.GeoJson(
                simplified_buffer,
                style_function=lambda x: {'fillColor': 'orange', 'color': 'orange', 'weight': 1, 'fillOpacity': 0.15}
            ).add_to(m)
            
            # 4. 强制缩放视野
            m.fit_bounds(route_line.bounds)

            # 5. 查找视野内所有数据 (±0.1度范围)
            # 使用 GPX 的边界，而不是 buffer 的边界，确保视野覆盖全
            gpx_lats = [p[0] for p in raw_points]
            gpx_lons = [p[1] for p in raw_points]
            search_box = box(min(gpx_lons)-0.1, min(gpx_lats)-0.1, max(gpx_lons)+0.1, max(gpx_lats)+0.1)
            
            candidates = all_bears[
                (all_bears['longitude'] >= search_box.bounds[0]) & 
                (all_bears['longitude'] <= search_box.bounds[2]) &
                (all_bears['latitude'] >= search_box.bounds[1]) & 
                (all_bears['latitude'] <= search_box.bounds[3])
            ]
            
            debug_info.append(f"视野内数据量: {len(candidates)}")

            # 6. 遍历绘制 (纯几何图形，不依赖图标)
            for idx, row in candidates.iterrows():
                bear_lon = float(row['longitude'])
                bear_lat = float(row['latitude'])
                bear_pt = Point(bear_lon, bear_lat)
                
                # 判断是否危险
                is_danger = raw_buffer.contains(bear_pt)
                
                date_str = str(row['sighting_datetime'])[:10]
                popup_html = f"{date_str}<br>{row['sighting_condition']}"

                if is_danger:
                    detected_danger.append(row)
                    
                    # === 危险点：大红圆 + 连接线 ===
                    
                    # 画连接线
                    nearest_pt_on_route = nearest_points(route_line, bear_pt)[0]
                    folium.PolyLine(
                        [[nearest_pt_on_route.y, nearest_pt_on_route.x], [bear_lat, bear_lon]],
                        color="red", weight=3, dash_array='5, 5', opacity=0.9
                    ).add_to(m)
                    
                    # 画大红圆 (CircleMarker)
                    #这是矢量图形，浏览器直接画，绝对不会加载失败
                    folium.CircleMarker(
                        location=[bear_lat, bear_lon],
                        radius=8,          # 大一点
                        color="red",       # 边框红
                        fill=True,
                        fill_color="red",  # 填充红
                        fill_opacity=1.0,
                        popup=f"⚠️ {popup_html}",
                        z_index_offset=1000
                    ).add_to(m)
                    
                else:
                    # === 安全点：小灰圆 ===
                    folium.CircleMarker(
                        location=[bear_lat, bear_lon],
                        radius=4,            # 小一点
                        color="gray",
                        fill=True,
                        fill_color="gray",
                        fill_opacity=0.6,
                        popup=popup_html
                    ).add_to(m)

    except Exception as e:
        st.error(f"处理失败: {e}")

# ==========================================
# 4. 渲染地图
# ==========================================
with col1:
    map_html = m._repr_html_()
    components.html(map_html, height=600)

# --- 结果面板 ---
with col2:
    if uploaded_file:
        st.subheader("📊 检测报告")
        for msg in debug_info:
            st.caption(msg)
            
        if detected_danger:
            st.error(f"🔴 发现 {len(detected_danger)} 个危险点")
            res_df = pd.DataFrame(detected_danger).sort_values('sighting_datetime', ascending=False)
            
            for idx, row in res_df.iterrows():
                d_str = row['sighting_datetime'].strftime('%Y-%m-%d') if pd.notnull(row['sighting_datetime']) else "未知"
                with st.expander(f"⚠️ {d_str}", expanded=True):
                    st.write(f"{row['sighting_condition']}")
        else:
            st.success("🟢 路线周边安全")
            st.caption(f"视野内有 {len(candidates) if 'candidates' in locals() else 0} 个灰色记录点，但均在安全距离外。")
    else:
        st.info("👈 请上传 GPX 文件")
