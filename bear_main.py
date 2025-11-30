import streamlit as st
import gpxpy
import json
import pandas as pd
from shapely.geometry import Point, LineString
from streamlit_folium import st_folium
import folium
from folium.plugins import FastMarkerCluster
import datetime

# --- 页面基础配置 ---
st.set_page_config(page_title="熊出没可视化地图", layout="wide", page_icon="🐻")

# --- 核心函数：加载并清洗数据 ---
@st.cache_data
def load_bear_data(filepath):
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            raw_data = json.load(f)
        
        if 'result' not in raw_data:
            st.error("数据结构错误：找不到 'result' 字段。")
            return pd.DataFrame()
            
        df = pd.DataFrame(raw_data['result'])
        
        # 1. 经纬度清洗
        df['latitude'] = pd.to_numeric(df['latitude'], errors='coerce')
        df['longitude'] = pd.to_numeric(df['longitude'], errors='coerce')
        df = df.dropna(subset=['latitude', 'longitude'])
        
        # 2. 时间解析 (这是新增的关键步骤)
        # 将字符串转换为 datetime 对象，以便进行日期比较
        df['sighting_datetime'] = pd.to_datetime(df['sighting_datetime'], errors='coerce')
        
        return df
    except Exception as e:
        st.error(f"读取文件失败: {e}")
        return pd.DataFrame()

# --- 主逻辑 ---

# 1. 加载全量数据
bear_df = load_bear_data("bears.json")

if bear_df.empty:
    st.stop()

# --- 侧边栏：全局过滤器 ---
with st.sidebar:
    st.header("🔍 筛选条件")
    
    # 获取数据中的最早和最晚时间
    min_date = bear_df['sighting_datetime'].min().date()
    max_date = bear_df['sighting_datetime'].max().date()
    
    # 时间范围选择器 (默认显示最近一年的数据，避免数据量过大干扰视线)
    default_start = max_date - datetime.timedelta(days=365)
    
    date_range = st.date_input(
        "选择目击时间范围",
        value=(default_start, max_date),
        min_value=min_date,
        max_value=max_date
    )
    
    # 简单的容错处理，防止用户只选了一个日期报错
    if len(date_range) == 2:
        start_date, end_date = date_range
    else:
        start_date, end_date = date_range[0], date_range[0]

# --- 根据时间筛选数据 ---
# 这一步过滤了全量数据，后续的所有地图展示都基于这个 filtered_df
filtered_df = bear_df[
    (bear_df['sighting_datetime'].dt.date >= start_date) & 
    (bear_df['sighting_datetime'].dt.date <= end_date)
].copy()

st.title("🐻 熊出没可视化地图")
st.markdown(f"当前显示 **{start_date}** 至 **{end_date}** 期间的 **{len(filtered_df)}** 条记录。")

# GPX 上传组件
uploaded_file = st.file_uploader("📂 上传 GPX 路线进行碰撞检测 (可选)", type=['gpx'])

# --- 地图生成逻辑 ---

# 默认中心点：如果没有 GPX，就用筛选后数据的平均位置；如果没有数据，就定在东京
if not filtered_df.empty:
    map_center = [filtered_df['latitude'].mean(), filtered_df['longitude'].mean()]
else:
    map_center = [35.6895, 139.6917] 

m = folium.Map(location=map_center, zoom_start=6 if uploaded_file is None else 12)

# 情况 A: 用户上传了 GPX (进入详细检测模式)
if uploaded_file is not None:
    gpx = gpxpy.parse(uploaded_file)
    points = []
    for track in gpx.tracks:
        for segment in track.segments:
            for point in segment.points:
                points.append((point.latitude, point.longitude))
    
    if points:
        # 1. 画路线
        folium.PolyLine(points, color="#3388ff", weight=4, opacity=0.8, tooltip="徒步路线").add_to(m)
        
        # 2. 空间计算 (只计算时间筛选后的数据)
        route_line = LineString(points)
        route_buffer = route_line.buffer(0.005) # 500m
        min_lon, min_lat, max_lon, max_lat = route_buffer.bounds
        
        # 粗筛
        candidates = filtered_df[
            (filtered_df['latitude'] >= min_lat) & (filtered_df['latitude'] <= max_lat) &
            (filtered_df['longitude'] >= min_lon) & (filtered_df['longitude'] <= max_lon)
        ]
        
        dangerous_bears = []
        for idx, row in candidates.iterrows():
            if route_buffer.contains(Point(row['latitude'], row['longitude'])):
                dangerous_bears.append(row)
        
        # 3. 标记危险点 (红色高亮)
        for bear in dangerous_bears:
            date_str = bear['sighting_datetime'].strftime('%Y-%m-%d %H:%M')
            popup_html = f"<b>{date_str}</b><br>{bear.get('sighting_condition', '')}"
            folium.Marker(
                [bear['latitude'], bear['longitude']],
                popup=folium.Popup(popup_html, max_width=250),
                icon=folium.Icon(color="red", icon="paw", prefix='fa')
            ).add_to(m)
            
        # 调整地图视野以适应路线
        m.fit_bounds(route_line.bounds)
        
        if dangerous_bears:
            st.error(f"⚠️ 在路线周边发现 {len(dangerous_bears)} 条记录！")
        else:
            st.success("✅ 该时间段内，路线周边无记录。")

# 情况 B: 用户没有上传 GPX (进入全景探索模式)
else:
    # 使用 FastMarkerCluster 进行聚合显示，防止浏览器卡顿
    if not filtered_df.empty:
        # 提取经纬度列表
        locations = filtered_df[['latitude', 'longitude']].values.tolist()
        
        # 这里的 callback 可以自定义点击聚合点时的行为，这里我们直接显示聚合
        FastMarkerCluster(data=locations).add_to(m)
        
        st.info("💡 提示：地图显示的是全区域数据，上传 GPX 文件可进行路线周边的精确检测。")

# 最后渲染地图
st_folium(m, width=1000, height=600)

# 如果有危险记录，显示详情列表
if uploaded_file is not None and 'dangerous_bears' in locals() and dangerous_bears:
    st.subheader("📋 详细记录")
    display_df = pd.DataFrame(dangerous_bears)[
        ['sighting_datetime', 'municipality_name', 'address', 'sighting_condition']
    ]
    st.dataframe(display_df, hide_index=True)
