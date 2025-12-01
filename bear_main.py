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
st.set_page_config(page_title="熊出没安全地图 (Debug版)", layout="wide", page_icon="🐻")

# ==========================================
# 1. 数据抽取 (山梨县 API)
# ==========================================
@st.cache_data
def load_yamanashi_data():
    # 使用山梨县 CKAN API
    url = "https://catalog.dataplatform-yamanashi.jp/api/action/datastore_search"
    params = {"resource_id": "b4eb262f-07e0-4417-b24f-6b15844b4ac1", "limit": 10000}
    
    try:
        response = requests.get(url, params=params, timeout=10)
        data = response.json()
        if 'result' in data and 'records' in data['result']:
            df = pd.DataFrame(data['result']['records'])
            
            # 1. 字段重命名
            rename_map = {'緯度': 'latitude', '経度': 'longitude', '年月日': 'sighting_datetime'}
            df = df.rename(columns=rename_map)
            
            # 容错处理
            if 'latitude' not in df.columns:
                for col in ['lat', 'Lat', 'LAT', '纬度']:
                    if col in df.columns: df = df.rename(columns={col: 'latitude'}); break

            # 2. 强制转为数字 (去除空值)
            df['latitude'] = pd.to_numeric(df['latitude'], errors='coerce')
            df['longitude'] = pd.to_numeric(df['longitude'], errors='coerce')
            df['sighting_datetime'] = pd.to_datetime(df['sighting_datetime'], errors='coerce')
            
            # 删除无效坐标
            df = df.dropna(subset=['latitude', 'longitude'])

            # 3. 描述拼接
            def make_description(row):
                parts = [str(row.get(c, '')) for c in ['目撃市町村', '場所'] if str(row.get(c, '')) != 'nan']
                return " ".join(parts) if parts else "无位置描述"

            df['sighting_condition'] = df.apply(make_description, axis=1)
            df['source'] = '山梨县API'
            
            return df
    except Exception as e:
        st.error(f"API 连接失败: {e}")
        return pd.DataFrame()
    return pd.DataFrame()

# ==========================================
# 2. 主逻辑
# ==========================================
st.title("🐻 熊出没安全地图 (修复坐标系问题)")

# 加载数据
with st.spinner('正在同步数据库...'):
    all_bears = load_yamanashi_data()

if all_bears.empty:
    st.error("❌ 数据库加载失败，无法继续。")
    st.stop()

# --- 侧边栏 ---
with st.sidebar:
    st.header("⚙️ 设置")
    buffer_radius_m = st.slider("预警距离 (米)", 100, 3000, 500, 100)
    
    st.divider()
    st.write(f"📚 数据库总记录: {len(all_bears)}")
    
    # 简单的日期筛选 (只影响显示，不影响检测)
    min_date = all_bears['sighting_datetime'].min().date()
    max_date = all_bears['sighting_datetime'].max().date()
    date_range = st.date_input("地图显示日期", value=(min_date, max_date))

# ==========================================
# 3. 核心处理
# ==========================================
col1, col2 = st.columns([3, 1])

with col1:
    uploaded_file = st.file_uploader("📂 上传 GPX 文件", type=['gpx'])

# 准备地图数据
center_lat, center_lon = 35.6, 138.5
if not all_bears.empty:
    center_lat = all_bears['latitude'].mean()
    center_lon = all_bears['longitude'].mean()

m = folium.Map(location=[center_lat, center_lon], zoom_start=10, tiles="OpenStreetMap")

# --- GPX 处理逻辑 (关键修复) ---
detected_danger = []
debug_msg = ""

if uploaded_file is not None:
    try:
        gpx = gpxpy.parse(uploaded_file)
        
        # 1. 提取点位 (支持 tracks, routes 和 waypoints)
        folium_points = []  # 用于画图: (Lat, Lon)
        shapely_points = [] # 用于计算: (Lon, Lat) -> 注意这里顺序不同！
        
        # 遍历 tracks
        for track in gpx.tracks:
            for segment in track.segments:
                for point in segment.points:
                    folium_points.append((point.latitude, point.longitude))
                    shapely_points.append((point.longitude, point.latitude)) # X=Lon, Y=Lat
        
        # 如果 tracks 没数据，试试 routes
        if not folium_points:
            for route in gpx.routes:
                for point in route.points:
                    folium_points.append((point.latitude, point.longitude))
                    shapely_points.append((point.longitude, point.latitude))

        # 2. 检查提取结果
        if len(folium_points) > 1:
            # 调试信息
            debug_msg += f"✅ 成功解析 {len(folium_points)} 个路径点。\n"
            debug_msg += f"📍 起点: {folium_points[0]}, 终点: {folium_points[-1]}\n"

            # 3. 画路线 (蓝色)
            folium.PolyLine(folium_points, color="blue", weight=4, opacity=0.7).add_to(m)
            
            # 4. 生成缓冲区 (使用 Shapely)
            # 转换距离：1度 ≈ 90km (取保守值) -> 1米 ≈ 1/90000 度
            deg_buffer = buffer_radius_m / 90000.0
            
            route_line = LineString(shapely_points) # 使用 (Lon, Lat) 构建
            route_buffer = route_line.buffer(deg_buffer)
            
            # 5. 画预警范围 (橙色)
            # Folium 需要 GeoJSON，GeoJSON 标准是 (Lon, Lat)，Shapely 也是，所以直接转换
            # 但要注意：folium.GeoJson 自动处理 GeoJSON 格式，所以这里不需要手动反转
            folium.GeoJson(
                route_buffer,
                style_function=lambda x: {'fillColor': 'orange', 'color': 'orange', 'weight': 1, 'fillOpacity': 0.2}
            ).add_to(m)
            
            m.fit_bounds(route_line.bounds) # 缩放地图

            # 6. 碰撞检测 (全量扫描)
            min_x, min_y, max_x, max_y = route_buffer.bounds # (min_lon, min_lat, ...)
            
            # 粗筛：利用 Pandas 快速过滤
            # 注意：all_bears['longitude'] 是 x, ['latitude'] 是 y
            candidates = all_bears[
                (all_bears['longitude'] >= min_x) & (all_bears['longitude'] <= max_x) &
                (all_bears['latitude'] >= min_y) & (all_bears['latitude'] <= max_y)
            ]
            
            debug_msg += f"🔍 粗筛范围内候选点: {len(candidates)} 个\n"

            # 精筛：几何判断
            for idx, row in candidates.iterrows():
                # 关键修复：Point 必须是 (Lon, Lat)
                bear_point = Point(row['longitude'], row['latitude']) 
                if route_buffer.contains(bear_point):
                    detected_danger.append(row)
            
            # 7. 标记危险点 (红色)
            for bear in detected_danger:
                date_str = str(bear['sighting_datetime'])[:10]
                folium.Marker(
                    [bear['latitude'], bear['longitude']], # 画图用 (Lat, Lon)
                    popup=f"⚠️ {date_str}<br>{bear['sighting_condition']}",
                    icon=folium.Icon(color="red", icon="warning-sign"),
                    z_index_offset=1000
                ).add_to(m)

        else:
            st.error("GPX 解析成功，但未找到坐标点。请检查 GPX 是否为空。")

    except Exception as e:
        st.error(f"GPX 处理崩溃: {e}")
        # 打印详细报错方便排查
        import traceback
        st.code(traceback.format_exc())

# --- 画背景点 (仅显示部分，灰色) ---
if not all_bears.empty:
    cluster = MarkerCluster(name="历史记录").add_to(m)
    # 限制显示 2000 个点防止卡顿
    subset = all_bears.head(2000)
    for idx, row in subset.iterrows():
        folium.Marker(
            [row['latitude'], row['longitude']],
            popup=f"{str(row['sighting_datetime'])[:10]}",
            icon=folium.Icon(color="lightgray", icon="info-sign"),
        ).add_to(cluster)

# 渲染地图
st_folium(m, width="100%", height=600)

# --- 右侧结果面板 ---
with col2:
    if uploaded_file:
        st.subheader("📊 检测报告")
        
        # 显示调试信息
        with st.expander("🛠 调试信息 (为何没显示?)"):
            st.text(debug_msg)
        
        if detected_danger:
            st.error(f"🔴 发现 {len(detected_danger)} 个危险点！")
            res = pd.DataFrame(detected_danger).sort_values('sighting_datetime', ascending=False)
            st.dataframe(res[['sighting_datetime', 'sighting_condition']], hide_index=True)
        else:
            st.success("🟢 路线周边安全")
            st.caption("注：如果在'粗筛'中有数据但这里没有，说明点在矩形框内但没在缓冲区圆圈内。")
    else:
        st.info("请上传 GPX")
