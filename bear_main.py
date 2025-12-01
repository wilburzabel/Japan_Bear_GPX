import streamlit as st
import streamlit.components.v1 as components 
import pandas as pd
import requests
import gpxpy
from shapely.geometry import Point, LineString, box
from shapely.ops import nearest_points
import folium
from folium.plugins import MarkerCluster
import datetime

# ==========================================
# 0. 页面配置
# ==========================================
st.set_page_config(page_title="熊出没地图 (诊断版)", layout="wide", page_icon="🐻")

# ==========================================
# 1. 数据抽取 (山梨县)
# ==========================================
@st.cache_data
def load_yamanashi_data():
    # 这里只获取山梨县数据。如果你的GPX不在山梨，这里将没有任何匹配。
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
st.title("🐻 熊出没安全地图 (强制显示诊断版)")
st.caption("🔴红色=危险(范围内) | ⚫灰色=附近数据(范围外) | 如果全是空白，说明该区域无数据")

all_bears = load_yamanashi_data()
if all_bears.empty:
    st.error("❌ 数据库加载失败，请检查网络。")
    st.stop()

with st.sidebar:
    st.header("⚙️ 设置")
    buffer_radius_m = st.slider("预警距离 (米)", 100, 5000, 500, 100)
    st.info(f"当前只检测【山梨县】数据。\n总记录数: {len(all_bears)}")

col1, col2 = st.columns([3, 1])
with col1:
    uploaded_file = st.file_uploader("📂 上传 GPX 文件", type=['gpx'])

# 默认中心
center_lat, center_lon = 35.6, 138.5
m = folium.Map(location=[center_lat, center_lon], zoom_start=10, tiles="OpenStreetMap")

# ==========================================
# 3. GPX 处理与诊断逻辑
# ==========================================
detected_danger = []
nearby_bears_count = 0
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
            # 1. GPX 基础信息
            lat_list = [p[0] for p in raw_points]
            lon_list = [p[1] for p in raw_points]
            min_lat, max_lat = min(lat_list), max(lat_list)
            min_lon, max_lon = min(lon_list), max(lon_list)
            
            debug_info.append(f"📍 GPX 纬度范围: {min_lat:.4f} ~ {max_lat:.4f}")
            debug_info.append(f"📍 GPX 经度范围: {min_lon:.4f} ~ {max_lon:.4f}")

            # 2. 画路线
            step = max(1, len(raw_points) // 500)
            folium_points = raw_points[::step]
            shapely_points = [(p[1], p[0]) for p in folium_points] # (Lon, Lat)

            folium.PolyLine(folium_points, color="blue", weight=4, opacity=0.8).add_to(m)
            
            # 3. 生成缓冲区
            deg_buffer = buffer_radius_m / 90000.0
            route_line = LineString(shapely_points)
            raw_buffer = route_line.buffer(deg_buffer)
            simplified_buffer = raw_buffer.simplify(tolerance=0.0005)

            folium.GeoJson(
                simplified_buffer,
                style_function=lambda x: {'fillColor': 'orange', 'color': 'orange', 'weight': 1, 'fillOpacity': 0.15}
            ).add_to(m)
            
            m.fit_bounds(route_line.bounds)

            # 4. 扩大搜索范围 (为了显示附近的灰色点)
            # 在路线周围扩大 0.1 度 (约10公里) 搜索所有数据
            search_box = box(min_lon - 0.1, min_lat - 0.1, max_lon + 0.1, max_lat + 0.1)
            
            # 筛选出 "视野内" 的所有熊
            candidates = all_bears[
                (all_bears['longitude'] >= search_box.bounds[0]) & 
                (all_bears['longitude'] <= search_box.bounds[2]) &
                (all_bears['latitude'] >= search_box.bounds[1]) & 
                (all_bears['latitude'] <= search_box.bounds[3])
            ]
            
            nearby_bears_count = len(candidates)
            debug_info.append(f"🔎 视野范围内(±10km)发现数据: {len(candidates)} 条")

            # 5. 遍历并分类绘制
            for idx, row in candidates.iterrows():
                bear_lon = float(row['longitude'])
                bear_lat = float(row['latitude'])
                bear_pt = Point(bear_lon, bear_lat)
                
                # 判断是否在 "橙色圈" 内 (危险!)
                is_danger = raw_buffer.contains(bear_pt)
                
                if is_danger:
                    detected_danger.append(row)
                    
                    # === 绘制危险点 (红 + 线) ===
                    # 计算最近连接线
                    nearest_pt_on_route = nearest_points(route_line, bear_pt)[0]
                    folium.PolyLine(
                        [[nearest_pt_on_route.y, nearest_pt_on_route.x], [bear_lat, bear_lon]],
                        color="red", weight=3, dash_array='5, 5'
                    ).add_to(m)
                    
                    folium.Marker(
                        [bear_lat, bear_lon],
                        popup=f"⚠️ {str(row['sighting_datetime'])[:10]}",
                        icon=folium.Icon(color="red", icon="exclamation-sign"),
                        z_index_offset=1000
                    ).add_to(m)
                else:
                    # === 绘制安全但附近的点 (灰) ===
                    # 强制显示出来，证明数据存在
                    folium.CircleMarker(
                        location=[bear_lat, bear_lon],
                        radius=5,
                        color="gray",
                        fill=True,
                        fill_color="gray",
                        fill_opacity=0.7,
                        popup="附近记录 (安全范围内)"
                    ).add_to(m)

    except Exception as e:
        st.error(f"处理失败: {e}")

# ==========================================
# 4. 渲染地图
# ==========================================
with col1:
    map_html = m._repr_html_()
    components.html(map_html, height=600)

# --- 诊断面板 ---
with col2:
    if uploaded_file:
        st.subheader("🛠 诊断面板")
        
        for msg in debug_info:
            st.text(msg)
            
        st.divider()
        
        if detected_danger:
            st.error(f"🔴 警报: {len(detected_danger)} 个危险点")
            # 列表展示
            res_df = pd.DataFrame(detected_danger).sort_values('sighting_datetime', ascending=False)
            st.dataframe(res_df[['sighting_datetime', 'sighting_condition']], hide_index=True)
        elif nearby_bears_count > 0:
            st.warning(f"🟡 附近有 {nearby_bears_count} 条记录，但在预警距离 ({buffer_radius_m}m) 外。")
            st.caption("尝试调大滑块距离，或检查地图上的灰色点。")
        else:
            st.info("⚪ 此区域完全无数据。")
            st.caption("请确认你的 GPX 路线是否位于【山梨县】境内。")
    else:
        st.info("👈 请上传 GPX 文件开始诊断")
