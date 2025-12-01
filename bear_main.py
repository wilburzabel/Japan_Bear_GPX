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
# 3. 核心处理 (轻量化渲染修复版)
# ==========================================
col1, col2 = st.columns([3, 1])

with col1:
    uploaded_file = st.file_uploader("📂 上传 GPX 文件", type=['gpx'])

# 准备地图中心
center_lat, center_lon = 35.6, 138.5
if not all_bears.empty:
    center_lat = all_bears['latitude'].mean()
    center_lon = all_bears['longitude'].mean()

# 创建地图对象
m = folium.Map(location=[center_lat, center_lon], zoom_start=10, tiles="OpenStreetMap")

# --- GPX 处理逻辑 ---
detected_danger = []
has_gpx = False

if uploaded_file is not None:
    try:
        gpx = gpxpy.parse(uploaded_file)
        folium_points = []  # (Lat, Lon)
        shapely_points = [] # (Lon, Lat)
        
        for track in gpx.tracks:
            for segment in track.segments:
                for point in segment.points:
                    folium_points.append((point.latitude, point.longitude))
                    shapely_points.append((point.longitude, point.latitude))
        
        # 兼容 routes
        if not folium_points:
            for route in gpx.routes:
                for point in route.points:
                    folium_points.append((point.latitude, point.longitude))
                    shapely_points.append((point.longitude, point.latitude))

        if len(folium_points) > 1:
            has_gpx = True
            
            # 1. 画路线 (深蓝色)
            folium.PolyLine(folium_points, color="blue", weight=4, opacity=0.8).add_to(m)
            
            # 2. 生成缓冲区
            deg_buffer = buffer_radius_m / 90000.0
            route_line = LineString(shapely_points)
            route_buffer = route_line.buffer(deg_buffer)
            
            # 3. 画预警范围 (橙色)
            folium.GeoJson(
                route_buffer,
                style_function=lambda x: {'fillColor': 'orange', 'color': 'orange', 'weight': 1, 'fillOpacity': 0.2}
            ).add_to(m)
            
            # 4. 缩放地图视野
            m.fit_bounds(route_line.bounds) 

            # 5. 碰撞检测
            min_x, min_y, max_x, max_y = route_buffer.bounds
            candidates = all_bears[
                (all_bears['longitude'] >= min_x) & (all_bears['longitude'] <= max_x) &
                (all_bears['latitude'] >= min_y) & (all_bears['latitude'] <= max_y)
            ]
            
            for idx, row in candidates.iterrows():
                bear_point = Point(row['longitude'], row['latitude'])
                if route_buffer.contains(bear_point):
                    detected_danger.append(row)
            
            # 6. 标记危险点 (红色高亮)
            for bear in detected_danger:
                date_str = str(bear['sighting_datetime'])[:10]
                folium.Marker(
                    [bear['latitude'], bear['longitude']],
                    popup=f"⚠️ {date_str}", # 简化 Popup 内容防止报错
                    icon=folium.Icon(color="red", icon="warning-sign"),
                    z_index_offset=1000
                ).add_to(m)

    except Exception as e:
        st.error(f"GPX 解析错误: {e}")

# --- 关键修改：背景点渲染策略 ---
# 如果没有上传 GPX，显示背景点；
# 如果上传了 GPX，为了保证地图能显示，我们【不显示】或【仅显示极少量】背景点
if not has_gpx:
    # 没上传文件时，显示聚合点供探索
    if not all_bears.empty:
        cluster = MarkerCluster(name="历史记录").add_to(m)
        # 限制显示 1000 个，防止浏览器卡死
        subset = all_bears.head(1000)
        for idx, row in subset.iterrows():
            folium.Marker(
                [row['latitude'], row['longitude']],
                icon=folium.Icon(color="lightgray", icon="info-sign"),
            ).add_to(cluster)
else:
    # 上传文件后，只显示危险点，保持地图清爽和流畅
    pass 

# --- 渲染地图 (关键参数修复) ---
with col1:
    # returned_objects=[] 是救命稻草！
    # 它禁止 Streamlit 回传点击数据，极大提升渲染成功率
    st_folium(m, width=800, height=600, returned_objects=[])

# --- 结果面板 ---
with col2:
    if has_gpx:
        st.subheader("🔍 检测报告")
        if detected_danger:
            st.error(f"🔴 发现 {len(detected_danger)} 个危险点！")
            
            # 整理显示数据
            res_df = pd.DataFrame(detected_danger)
            # 格式化时间
            if 'sighting_datetime' in res_df.columns:
                res_df['时间'] = res_df['sighting_datetime'].dt.strftime('%Y-%m-%d')
            else:
                res_df['时间'] = "未知"
                
            # 只展示关键列
            st.dataframe(
                res_df[['时间', 'sighting_condition']], 
                hide_index=True,
                height=400
            )
        else:
            st.success("🟢 路线周边安全")
            st.caption(f"在 {buffer_radius_m} 米范围内未发现记录。")
    else:
        st.info("👈 请上传 GPX 文件")
        st.caption("上传后地图将自动聚焦到路线区域。")
