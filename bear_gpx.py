import streamlit as st
import folium
from streamlit_folium import st_folium
import pandas as pd
import gpxpy
import requests
import re
from datetime import datetime
from geopy.distance import geodesic
from folium.plugins import MarkerCluster

# --- 页面配置 ---
st.set_page_config(page_title="日本熊出没地图 (TV Asahi版)", layout="wide")

st.title("🐻 日本熊出没地图 - 2025特别版")
st.markdown("数据来源：[朝日电视台 熊出没专题](https://news.tv-asahi.co.jp/special/202506bear/) | 自动同步最新 JSON 数据")

# --- 1. 数据获取与解析 ---

@st.cache_data(ttl=3600)  # 缓存1小时
def load_tvasahi_data():
    url = "https://news.tv-asahi.co.jp/special/202506bear/sys/data.json"
    try:
        response = requests.get(url, timeout=10)
        if response.status_code != 200:
            st.error("无法连接到数据源")
            return pd.DataFrame()
        
        json_data = response.json()
        markers = json_data.get('marker', [])
        
        data = []
        # 正则表达式用于从标题提取日期：例如 "【2025年8月25日】..."
        date_pattern = re.compile(r"【(\d+)年(\d+)月(\d+)日】")

        for item in markers:
            # 1. 过滤无效数据 (调整用Pin通常纬度很高或标题含特定词)
            if "調整用" in item.get('title', '') or float(item.get('latitude', 0)) > 80:
                continue

            # 2. 提取日期
            title = item.get('title', '')
            match = date_pattern.search(title)
            if match:
                try:
                    year, month, day = map(int, match.groups())
                    date_obj = datetime(year, month, day).date()
                except:
                    date_obj = None
            else:
                date_obj = None

            # 3. 整理数据
            data.append({
                "date": date_obj,
                "title": title,
                "desc": item.get('description', ''),
                "lat": float(item.get('latitude')),
                "lon": float(item.get('longitude')),
                "url": item.get('link_url', '')
            })
            
        df = pd.DataFrame(data)
        # 删除没有日期的脏数据
        df = df.dropna(subset=['date'])
        return df

    except Exception as e:
        st.error(f"数据解析错误: {e}")
        return pd.DataFrame()

def parse_gpx(uploaded_file):
    """解析GPX文件"""
    if uploaded_file is not None:
        try:
            gpx = gpxpy.parse(uploaded_file)
            points = []
            for track in gpx.tracks:
                for segment in track.segments:
                    for point in segment.points:
                        points.append((point.latitude, point.longitude))
            return points
        except:
            st.error("GPX文件解析失败")
    return []

def check_proximity(route_points, bear_df, threshold_km=1.0):
    """检测路线风险"""
    dangers = []
    # 抽样检查路线点以提高速度 (每50个点查一次)
    sampled_route = route_points[::50] 
    
    # 如果路线点太少，就全部检查
    if len(route_points) < 50:
        sampled_route = route_points

    for _, bear in bear_df.iterrows():
        bear_loc = (bear['lat'], bear['lon'])
        for route_pt in sampled_route:
            if geodesic(bear_loc, route_pt).km <= threshold_km:
                dangers.append(bear)
                break
    return pd.DataFrame(dangers)

# --- 2. 程序主逻辑 ---

# 加载数据
with st.spinner("正在从朝日电视台服务器获取最新数据..."):
    df_bears = load_tvasahi_data()

if df_bears.empty:
    st.warning("未能获取到数据，请检查网络连接。")
    st.stop()

# 侧边栏控制
st.sidebar.header("📅 筛选与设置")

# 日期滑块
min_date = df_bears['date'].min()
max_date = df_bears['date'].max()

start_date, end_date = st.sidebar.date_input(
    "选择时间范围",
    [min_date, max_date],
    min_value=min_date,
    max_value=max_date
)

# 根据日期过滤
filtered_data = df_bears[
    (df_bears['date'] >= start_date) & 
    (df_bears['date'] <= end_date)
]

st.sidebar.success(f"显示记录: {len(filtered_data)} / {len(df_bears)} 条")

# GPX 上传
uploaded_file = st.sidebar.file_uploader("📂 上传GPX路线文件", type=['gpx'])
safe_distance = st.sidebar.slider("🔴 警戒半径 (km)", 0.5, 5.0, 1.0)

# --- 3. 地图绘制 ---

# 默认中心设为最新的一个点，或者日本中心
if not filtered_data.empty:
    center_lat = filtered_data.iloc[0]['lat']
    center_lon = filtered_data.iloc[0]['lon']
else:
    center_lat, center_lon = 36.2048, 138.2529 # 日本大概中心

m = folium.Map(location=[center_lat, center_lon], zoom_start=6, tiles="OpenStreetMap")

# 绘制熊点 (使用聚类插件防止卡顿)
marker_cluster = MarkerCluster().add_to(m)

for _, row in filtered_data.iterrows():
    # 构建弹出内容
    popup_html = f"""
    <b>日期:</b> {row['date']}<br>
    <b>地点:</b> {row['title']}<br>
    <div style='width:200px; white-space:normal;'>{row['desc']}</div>
    """
    
    folium.Marker(
        location=[row['lat'], row['lon']],
        popup=folium.Popup(popup_html, max_width=300),
        icon=folium.Icon(color="red", icon="paw", prefix='fa')
    ).add_to(marker_cluster)

# GPX 路线与风险分析
danger_bears = pd.DataFrame()

if uploaded_file:
    route_points = parse_gpx(uploaded_file)
    if route_points:
        # 画路线
        folium.PolyLine(route_points, color="blue", weight=5, opacity=0.7).add_to(m)
        
        # 调整视角到路线起点
        m.location = route_points[0]
        m.zoom_start = 12
        
        # 计算风险
        danger_bears = check_proximity(route_points, filtered_data, safe_distance)
        
        # 高亮危险熊点
        if not danger_bears.empty:
            for _, row in danger_bears.iterrows():
                folium.Circle(
                    location=[row['lat'], row['lon']],
                    radius=safe_distance * 1000,
                    color="crimson",
                    fill=True,
                    fill_opacity=0.3,
                    popup="⚠️ 警戒：路线上有熊"
                ).add_to(m)

# --- 4. 显示界面 ---

col1, col2 = st.columns([3, 1])

with col1:
    st_folium(m, width="100%", height=700)

with col2:
    st.subheader("📊 风险分析报告")
    
    if uploaded_file:
        if not danger_bears.empty:
            st.error(f"⚠️ 警告！路线上发现 {len(danger_bears)} 处风险记录！")
            st.markdown(f"**警戒半径 {safe_distance}km 内的目击记录：**")
            
            for _, row in danger_bears.iterrows():
                with st.expander(f"{row['date']} - {row['title'][:10]}..."):
                    st.write(row['desc'])
                    if row['url']:
                        st.markdown(f"[查看新闻链接]({row['url']})")
        else:
            st.success("✅ 您的路线在所选时间段内相对安全。")
    else:
        st.info("👈 请在左侧上传 GPX 文件以检测路线安全。")
        
    st.markdown("---")
    st.markdown("### 最近5条全境记录")
    # 显示最近的几条记录供参考
    recent = filtered_data.sort_values(by='date', ascending=False).head(5)
    for _, row in recent.iterrows():
        st.text(f"{row['date']} {row['title'][:15]}...")