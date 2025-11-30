import streamlit as st
import gpxpy
import json
import pandas as pd
from shapely.geometry import Point, LineString
from streamlit_folium import st_folium
import folium

# --- 页面基础配置 ---
st.set_page_config(page_title="熊出没路径检测器", layout="wide", page_icon="🐻")

# --- 核心函数：加载并清洗数据 ---
@st.cache_data
def load_bear_data(filepath):
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            raw_data = json.load(f)
        
        # 1. 关键修改：从 'result' 键中提取列表
        if 'result' not in raw_data:
            st.error("JSON 文件结构不正确：找不到 'result' 字段。")
            return pd.DataFrame()
            
        df = pd.DataFrame(raw_data['result'])
        
        # 2. 确保经纬度是数字类型 (防止 JSON 里偶尔混入字符串)
        df['latitude'] = pd.to_numeric(df['latitude'], errors='coerce')
        df['longitude'] = pd.to_numeric(df['longitude'], errors='coerce')
        
        # 删除经纬度无效的脏数据
        df = df.dropna(subset=['latitude', 'longitude'])
        
        return df
    except Exception as e:
        st.error(f"读取文件失败: {e}")
        return pd.DataFrame()

# --- 主界面逻辑 ---
st.title("🐻 熊出没路径检测器")
st.markdown("上传 GPX 轨迹文件，自动检测路径 **500米范围内** 的历史熊出没记录。")

# 加载数据 (确保文件名和你保存的一致，比如 bears.json)
bear_df = load_bear_data("bears.json") 

if not bear_df.empty:
    st.success(f"📚 本地数据库已加载：包含 {len(bear_df)} 条目击记录。")
else:
    st.stop() # 如果没数据，就暂停运行下面的代码

uploaded_file = st.file_uploader("📂 请上传 GPX 文件", type=['gpx'])

if uploaded_file is not None:
    # 1. 解析用户上传的 GPX
    gpx = gpxpy.parse(uploaded_file)
    points = []
    for track in gpx.tracks:
        for segment in track.segments:
            for point in segment.points:
                points.append((point.latitude, point.longitude))
    
    if points:
        # 构建路线和缓冲区
        route_line = LineString(points)
        buffer_distance_deg = 0.005  # 约 500米
        route_buffer = route_line.buffer(buffer_distance_deg)
        
        # 获取路线边界用于快速粗筛
        min_lon, min_lat, max_lon, max_lat = route_buffer.bounds
        
        # 2. 粗筛 (Bounding Box Filter) - 极大提升性能
        # 使用你提供的字段名：latitude, longitude
        candidates = bear_df[
            (bear_df['latitude'] >= min_lat) & (bear_df['latitude'] <= max_lat) &
            (bear_df['longitude'] >= min_lon) & (bear_df['longitude'] <= max_lon)
        ].copy()
        
        # 3. 精细几何检测
        dangerous_bears = []
        for idx, row in candidates.iterrows():
            bear_point = Point(row['latitude'], row['longitude'])
            if route_buffer.contains(bear_point):
                dangerous_bears.append(row)
        
        # 4. 地图可视化
        # 初始化地图中心为路线起点
        m = folium.Map(location=points[0], zoom_start=13)
        
        # 画路线
        folium.PolyLine(points, color="#3388ff", weight=4, opacity=0.8, tooltip="徒步路线").add_to(m)
        
        # 画危险点
        for bear in dangerous_bears:
            # 组合提示信息
            date_str = str(bear.get('sighting_datetime', '未知时间'))
            loc_str = str(bear.get('municipality_name', '')) + str(bear.get('address', ''))
            condition = str(bear.get('sighting_condition', '无详细描述'))
            
            # 弹窗内容 (支持 HTML 换行)
            popup_html = f"""
            <b>时间:</b> {date_str}<br>
            <b>地点:</b> {loc_str}<br>
            <b>详情:</b> {condition}
            """
            
            folium.Marker(
                [bear['latitude'], bear['longitude']],
                popup=folium.Popup(popup_html, max_width=300),
                icon=folium.Icon(color="red", icon="paw", prefix='fa') # 使用爪子图标
            ).add_to(m)
            
        st_folium(m, width=800)
        
        # 5. 结果展示
        if len(dangerous_bears) > 0:
            st.error(f"⚠️ 警告：在路线周边发现 {len(dangerous_bears)} 次目击记录！")
            
            # 整理一个漂亮的表格展示给用户
            display_df = pd.DataFrame(dangerous_bears)[
                ['sighting_datetime', 'municipality_name', 'address', 'sighting_condition']
            ]
            # 重命名列头，方便阅读
            display_df.columns = ['目击时间', '市町村', '详细地址', '目击详情']
            st.dataframe(display_df, hide_index=True)
        else:
            st.success("✅ 也就是两棵树，一棵没有熊，另一棵也没有熊。（路线周边暂无记录）")
            
    else:
        st.warning("GPX 文件中似乎没有路径点，请检查文件。")