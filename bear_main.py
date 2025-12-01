import streamlit as st
import pandas as pd
import requests
import json
import gpxpy
import math
from shapely.geometry import Point, LineString
from streamlit_folium import st_folium
import folium
from folium.plugins import MarkerCluster
import datetime

# ==========================================
# 0. 页面基础配置
# ==========================================
st.set_page_config(
    page_title="山梨县熊出没安全地图 (修复版)", 
    layout="wide", 
    page_icon="🐻"
)

# ==========================================
# 1. 数据抽取层
# ==========================================
@st.cache_data
def load_yamanashi_data():
    url = "https://catalog.dataplatform-yamanashi.jp/api/action/datastore_search"
    params = {
        "resource_id": "b4eb262f-07e0-4417-b24f-6b15844b4ac1",
        "limit": 10000 
    }
    try:
        response = requests.get(url, params=params, timeout=10)
        data = response.json()
        if 'result' in data and 'records' in data['result']:
            df = pd.DataFrame(data['result']['records'])
            
            # 字段容错映射
            rename_map = {'緯度': 'latitude', '経度': 'longitude', '年月日': 'sighting_datetime'}
            df = df.rename(columns=rename_map)
            
            if 'latitude' not in df.columns:
                for col in ['lat', 'Lat', 'LAT', '纬度']:
                    if col in df.columns: df = df.rename(columns={col: 'latitude'}); break

            df['latitude'] = pd.to_numeric(df['latitude'], errors='coerce')
            df['longitude'] = pd.to_numeric(df['longitude'], errors='coerce')
            df['sighting_datetime'] = pd.to_datetime(df['sighting_datetime'], errors='coerce')
            
            # 描述拼接
            def make_description(row):
                parts = [str(row.get(c, '')) for c in ['目撃市町村', '場所'] if str(row.get(c, '')) != 'nan']
                details = [str(row.get(c, '')) for c in ['時間', '推定年齢', '目撃頭数'] if str(row.get(c, '')) != 'nan']
                desc = " ".join(parts)
                if details: desc += f" ({', '.join(details)})"
                return desc

            df['sighting_condition'] = df.apply(make_description, axis=1)
            df['source'] = '山梨县 (API)'
            return df[['latitude', 'longitude', 'sighting_datetime', 'sighting_condition', 'source']].dropna(subset=['latitude', 'longitude'])
    except Exception:
        return pd.DataFrame()
    return pd.DataFrame()

# ==========================================
# 2. 主逻辑与设置
# ==========================================
st.title("🐻 山梨县熊出没安全地图")

with st.spinner('正在获取最新数据...'):
    all_bears = load_yamanashi_data()

if all_bears.empty:
    st.error("❌ 数据加载失败，无法连接数据库。")
    st.stop()

# --- 侧边栏 ---
with st.sidebar:
    st.header("⚙️ 参数设置")
    
    st.subheader("📏 检测范围")
    buffer_radius_m = st.slider("安全预警距离 (米)", 100, 3000, 500, 100)
    
    st.divider()
    
    st.subheader("👀 地图显示过滤")
    st.info("注意：此处的日期筛选仅影响**地图上的圆点显示**。GPX 安全检测将始终扫描**全量历史数据**以确保安全。")
    
    valid_dates = all_bears['sighting_datetime'].dropna()
    min_date = valid_dates.min().date()
    max_date = valid_dates.max().date()
    # 默认显示全部，避免误解
    date_range = st.date_input("显示日期范围", value=(min_date, max_date), min_value=min_date, max_value=max_date)

    if len(date_range) == 2:
        start_d, end_d = date_range
        # 用于显示的 filtered_df
        display_df = all_bears[
            (all_bears['sighting_datetime'].dt.date >= start_d) & 
            (all_bears['sighting_datetime'].dt.date <= end_d)
        ]
    else:
        display_df = all_bears

# ==========================================
# 3. 地图核心逻辑
# ==========================================

col1, col2 = st.columns([3, 1])
with col1:
    uploaded_file = st.file_uploader("📂 上传 GPX 路线文件", type=['gpx'])

# 默认中心
center_lat, center_lon = (35.6, 138.5)
if not display_df.empty:
    center_lat, center_lon = display_df['latitude'].mean(), display_df['longitude'].mean()

m = folium.Map(location=[center_lat, center_lon], zoom_start=10, tiles="OpenStreetMap")

# --- GPX 处理 ---
gpx_valid = False
if uploaded_file is not None:
    try:
        gpx = gpxpy.parse(uploaded_file)
        points = []
        for track in gpx.tracks:
            for segment in track.segments:
                for point in segment.points:
                    points.append((point.latitude, point.longitude))
        
        if len(points) > 1:
            gpx_valid = True
            
            # --- 关键修正：缓冲区计算 (针对日本纬度的近似修正) ---
            # 纬度 1度 ≈ 111,000米
            # 经度 1度 ≈ 111,000 * cos(35度) ≈ 91,000米
            # 为了简化计算且保证安全，我们取较小值(经度跨度)作为除数，这样生成的 buffer 会略大一点，宁可误报不可漏报
            deg_per_meter = 1 / 91000 
            buffer_deg = buffer_radius_m * deg_per_meter
            
            route_line = LineString(points)
            route_buffer = route_line.buffer(buffer_deg)
            
            # 1. 画预警走廊
            folium.GeoJson(
                route_buffer,
                style_function=lambda x: {'fillColor': '#FFA500', 'color': '#FFA500', 'weight': 1, 'fillOpacity': 0.15},
                tooltip=f"预警范围 ({buffer_radius_m}米)"
            ).add_to(m)
            
            # 2. 画路线
            folium.PolyLine(points, color="blue", weight=4, opacity=0.8).add_to(m)
            
            # 3. 检测逻辑 (使用 all_bears 全量数据，而不是 filtered_df)
            min_lon, min_lat, max_lon, max_lat = route_buffer.bounds
            
            # 粗筛
            candidates = all_bears[
                (all_bears['latitude'] >= min_lat) & (all_bears['latitude'] <= max_lat) &
                (all_bears['longitude'] >= min_lon) & (all_bears['longitude'] <= max_lon)
            ]
            
            dangerous_bears = []
            for idx, row in candidates.iterrows():
                if route_buffer.contains(Point(row['latitude'], row['longitude'])):
                    dangerous_bears.append(row)
            
            # 4. 在地图上高亮危险点 (无论是否在当前时间筛选范围内，只要危险就显示)
            for bear in dangerous_bears:
                date_str = bear['sighting_datetime'].strftime('%Y-%m-%d') if pd.notnull(bear['sighting_datetime']) else "未知"
                popup_html = f"<div style='width:150px'><b>⚠️ 危险警告</b><br>{date_str}<br>{bear['sighting_condition']}</div>"
                folium.Marker(
                    [bear['latitude'], bear['longitude']],
                    popup=folium.Popup(popup_html, max_width=200),
                    icon=folium.Icon(color="red", icon="warning-sign"),
                    z_index_offset=1000 # 确保显示在最上层
                ).add_to(m)

            m.fit_bounds(route_line.bounds)

            # --- 结果输出 ---
            with col2:
                st.subheader("🔍 安全报告")
                st.caption(f"检测模式：全历史数据扫描\n检测范围：{buffer_radius_m}米")
                
                if dangerous_bears:
                    st.error(f"🔴 发现 {len(dangerous_bears)} 处历史记录！")
                    res_df = pd.DataFrame(dangerous_bears).sort_values('sighting_datetime', ascending=False)
                    st.dataframe(
                        res_df[['sighting_datetime', 'sighting_condition']],
                        hide_index=True,
                        column_config={"sighting_datetime": "时间", "sighting_condition": "详情"}
                    )
                else:
                    st.success("🟢 路线周边历史记录清零")
                    st.caption("在全量历史数据库中未发现威胁。")
        else:
            st.error("GPX 文件解析成功，但没有包含有效的路径点。")

    except Exception as e:
        st.error(f"GPX 文件解析出错: {e}")

# --- 背景点显示 (仅显示筛选范围内的数据) ---
# 使用 MarkerCluster，但不把 dangerous_bears 重复加进去
if not display_df.empty:
    cluster = MarkerCluster(name="其他历史记录").add_to(m)
    # 限制显示数量，优化性能
    limit_df = display_df.head(2000)
    for idx, row in limit_df.iterrows():
        folium.Marker(
            [row['latitude'], row['longitude']],
            popup=f"{row['sighting_datetime']}<br>{row['sighting_condition']}",
            icon=folium.Icon(color="gray", icon="info-sign", prefix='fa'), # 使用灰色以区分危险点
        ).add_to(cluster)

# 渲染地图
with col1:
    st_folium(m, width="100%", height=600)
