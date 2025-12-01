import streamlit as st
import pandas as pd
import requests
import json
import gpxpy
from shapely.geometry import Point, LineString
from streamlit_folium import st_folium
import folium
from folium.plugins import MarkerCluster
import datetime

# ==========================================
# 0. 页面基础配置
# ==========================================
st.set_page_config(
    page_title="山梨县熊出没安全地图", 
    layout="wide", 
    page_icon="🐻"
)

# ==========================================
# 1. 数据抽取层 (仅山梨县 API)
# ==========================================

@st.cache_data
def load_yamanashi_data():
    # 山梨县 CKAN API 地址
    url = "https://catalog.dataplatform-yamanashi.jp/api/action/datastore_search"
    params = {
        "resource_id": "b4eb262f-07e0-4417-b24f-6b15844b4ac1",
        "limit": 10000  # 获取 10000 条，确保覆盖全量
    }
    
    try:
        response = requests.get(url, params=params, timeout=15)
        data = response.json()
        
        if 'result' in data and 'records' in data['result']:
            df = pd.DataFrame(data['result']['records'])
            
            # 1. 字段名映射
            rename_map = {
                '緯度': 'latitude', 
                '経度': 'longitude', 
                '年月日': 'sighting_datetime'
            }
            df = df.rename(columns=rename_map)
            
            # 容错：如果 API 字段名变了，尝试其他可能
            if 'latitude' not in df.columns:
                for col in ['lat', 'Lat', 'LAT', '纬度']:
                    if col in df.columns:
                        df = df.rename(columns={col: 'latitude'})
                        break

            # 2. 类型转换
            df['latitude'] = pd.to_numeric(df['latitude'], errors='coerce')
            df['longitude'] = pd.to_numeric(df['longitude'], errors='coerce')
            df['sighting_datetime'] = pd.to_datetime(df['sighting_datetime'], errors='coerce')
            
            # 3. 智能拼接描述字段
            def make_description(row):
                muni = str(row.get('目撃市町村', ''))
                place = str(row.get('場所', ''))
                time = str(row.get('時間', ''))
                age = str(row.get('推定年齢', ''))
                count = str(row.get('目撃頭数', ''))
                
                desc = f"{muni} {place}".strip()
                details = []
                if time and time != 'nan': details.append(time)
                if age and age != 'nan': details.append(age)
                if count and count != 'nan': details.append(f"{count}頭")
                
                if details: desc += f" ({', '.join(details)})"
                return desc if desc else "API数据无描述"

            df['sighting_condition'] = df.apply(make_description, axis=1)
            
            # 4. 来源标签
            df['source'] = '山梨县 (API)'
            
            return df[['latitude', 'longitude', 'sighting_datetime', 'sighting_condition', 'source']].dropna(subset=['latitude', 'longitude'])
            
        return pd.DataFrame()
        
    except Exception as e:
        st.error(f"无法连接山梨县数据 API: {e}")
        return pd.DataFrame()

# ==========================================
# 2. 主程序逻辑
# ==========================================

st.title("🐻 山梨县熊出没安全地图")

# 加载数据
with st.spinner('正在连接山梨县政府数据库...'):
    all_bears = load_yamanashi_data()

if all_bears.empty:
    st.error("❌ 数据加载失败。请检查网络连接（是否需要关闭 VPN 或代理）。")
    st.stop()

# ==========================================
# 3. 侧边栏设置
# ==========================================
with st.sidebar:
    st.header("⚙️ 参数设置")
    
    # 预警距离滑块
    st.subheader("📏 安全预警范围")
    buffer_radius_m = st.slider(
        "路线两侧检测距离 (米)",
        min_value=100,
        max_value=3000,
        value=500,
        step=100,
        help="系统将检测路线周围这个距离内的熊出没记录。"
    )
    
    st.divider()
    
    # 时间筛选
    st.subheader("⏳ 时间筛选")
    valid_dates = all_bears['sighting_datetime'].dropna()
    if not valid_dates.empty:
        min_date = valid_dates.min().date()
        max_date = valid_dates.max().date()
        
        # 默认最近 1 年
        default_start = max_date - datetime.timedelta(days=365)
        if default_start < min_date: default_start = min_date

        date_range = st.date_input("选择日期范围", value=(default_start, max_date), min_value=min_date, max_value=max_date)
        
        if len(date_range) == 2:
            start_d, end_d = date_range
            filtered_df = all_bears[
                (all_bears['sighting_datetime'].dt.date >= start_d) & 
                (all_bears['sighting_datetime'].dt.date <= end_d)
            ].copy()
        else:
            filtered_df = all_bears.copy()
    else:
        filtered_df = all_bears.copy()

    st.write(f"📊 当前筛选记录数: {len(filtered_df)}")

# ==========================================
# 4. 地图核心逻辑
# ==========================================

col1, col2 = st.columns([3, 1])
with col1:
    uploaded_file = st.file_uploader("📂 上传 GPX 路线文件", type=['gpx'])

# 确定地图中心
if not filtered_df.empty:
    center_lat = filtered_df['latitude'].mean()
    center_lon = filtered_df['longitude'].mean()
else:
    center_lat, center_lon = 35.66, 138.56 # 山梨县大致中心

m = folium.Map(location=[center_lat, center_lon], zoom_start=9, tiles="OpenStreetMap")

# --- 场景 A: 路线检测模式 ---
if uploaded_file is not None:
    try:
        gpx = gpxpy.parse(uploaded_file)
        points = []
        for track in gpx.tracks:
            for segment in track.segments:
                for point in segment.points:
                    points.append((point.latitude, point.longitude))
        
        if points:
            # 1. 计算缓冲区 (简单估算: 1度 ≈ 111km)
            buffer_deg = buffer_radius_m / 111111 
            
            # 2. 生成几何对象
            route_line = LineString(points)
            route_buffer = route_line.buffer(buffer_deg)
            
            # 3. 画出“预警走廊” (浅橙色)
            folium.GeoJson(
                route_buffer,
                style_function=lambda x: {
                    'fillColor': '#FFA500', 'color': '#FFA500', 'weight': 1, 'fillOpacity': 0.2
                },
                tooltip=f"预警范围 ({buffer_radius_m}米)"
            ).add_to(m)
            
            # 4. 画出路线 (深蓝色)
            folium.PolyLine(
                points, color="blue", weight=4, opacity=0.8, tooltip="徒步路线"
            ).add_to(m)
            
            # 5. 空间碰撞检测
            min_lon, min_lat, max_lon, max_lat = route_buffer.bounds
            
            # 粗筛
            candidates = filtered_df[
                (filtered_df['latitude'] >= min_lat) & (filtered_df['latitude'] <= max_lat) &
                (filtered_df['longitude'] >= min_lon) & (filtered_df['longitude'] <= max_lon)
            ]
            
            # 精筛
            dangerous_bears = []
            for idx, row in candidates.iterrows():
                if route_buffer.contains(Point(row['latitude'], row['longitude'])):
                    dangerous_bears.append(row)
            
            # 6. 标记危险点
            for bear in dangerous_bears:
                date_str = bear['sighting_datetime'].strftime('%Y-%m-%d %H:%M') if pd.notnull(bear['sighting_datetime']) else "未知时间"
                
                popup_html = f"""
                <div style="font-family:sans-serif; width:200px;">
                    <span style="color:red; font-weight:bold;">⚠️ {date_str}</span><br>
                    <hr style="margin:5px 0;">
                    {bear['sighting_condition']}
                </div>
                """
                folium.Marker(
                    [bear['latitude'], bear['longitude']],
                    popup=folium.Popup(popup_html, max_width=250),
                    icon=folium.Icon(color="red", icon="paw", prefix='fa')
                ).add_to(m)
            
            m.fit_bounds(route_line.bounds)
            
            # --- 结果面板 ---
            with col2:
                st.subheader("🔍 检测报告")
                st.info(f"检测半径: **{buffer_radius_m} 米**")
                
                if dangerous_bears:
                    st.error(f"🔴 发现 **{len(dangerous_bears)}** 处风险！")
                    # 按时间倒序展示
                    res_df = pd.DataFrame(dangerous_bears).sort_values('sighting_datetime', ascending=False)
                    for idx, row in res_df.iterrows():
                        date_display = row['sighting_datetime'].strftime('%m-%d')
                        with st.expander(f"{date_display} - {row['sighting_condition'][:8]}...", expanded=False):
                            st.write(f"**时间:** {row['sighting_datetime']}")
                            st.write(f"**详情:** {row['sighting_condition']}")
                else:
                    st.success("🟢 路线周边安全")
                    st.caption("未发现历史记录。")
                    
        else:
            st.warning("GPX 解析失败：未找到路径点。")
    except Exception as e:
        st.error(f"GPX 处理出错: {e}")

# --- 场景 B: 全景模式 ---
else:
    if not filtered_df.empty:
        marker_cluster = MarkerCluster(name="熊出没聚合点").add_to(m)
        limit = 3000
        display_data = filtered_df.sort_values('sighting_datetime', ascending=False).head(limit)
            
        for idx, row in display_data.iterrows():
            date_str = row['sighting_datetime'].strftime('%Y-%m-%d') if pd.notnull(row['sighting_datetime']) else ""
            folium.Marker(
                location=[row['latitude'], row['longitude']],
                popup=f"<b>{date_str}</b><br>{row['sighting_condition']}",
                icon=folium.Icon(color="orange", icon="info-sign"),
            ).add_to(marker_cluster)
            
    with col2:
        st.info("👈 上传 GPX 文件以开启路线检测。")
        st.write(f"全图显示最近 {len(display_data) if 'display_data' in locals() else 0} 条记录")

# 渲染地图
with col1:
    st_folium(m, width="100%", height=600)
