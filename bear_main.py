import streamlit as st
import pandas as pd
import requests
import json
import gpxpy
from shapely.geometry import Point, LineString
from streamlit_folium import st_folium
import folium
from folium.plugins import MarkerCluster, FastMarkerCluster
import datetime

# ==========================================
# 0. 页面基础配置
# ==========================================
st.set_page_config(
    page_title="日本熊出没安全地图 (秋田+山梨)", 
    layout="wide", 
    page_icon="🐻"
)

# ==========================================
# 1. 数据抽取与清洗层 (ETL)
# ==========================================

# --- A. 加载秋田县数据 (本地 bears.json) ---
@st.cache_data
def load_akita_data(filepath="bears.json"):
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            # 尝试解析 JSON
            try:
                raw_data = json.load(f)
            except json.JSONDecodeError:
                st.error("❌ `bears.json` 文件格式错误。请确保在 Charles 中使用的是 'Save Response Body'，而不是保存整个 Response。")
                return pd.DataFrame()
        
        # 检查数据结构 (适配 kumadas.net 的结构)
        if 'result' in raw_data:
            df = pd.DataFrame(raw_data['result'])
        else:
            st.warning("⚠️ `bears.json` 中找不到 'result' 字段，请检查数据源。")
            return pd.DataFrame()
            
        # 字段标准化 (目标: latitude, longitude, sighting_datetime, sighting_condition)
        # 假设 kumadas.net 返回的已经是标准字段，如果不是，需要在这里 rename
        # 这里做一点容错处理
        if 'latitude' not in df.columns and 'lat' in df.columns:
            df = df.rename(columns={'lat': 'latitude', 'lon': 'longitude', 'body': 'sighting_condition', 'date': 'sighting_datetime'})
            
        # 确保关键列存在
        required_cols = ['latitude', 'longitude']
        if not all(col in df.columns for col in required_cols):
            st.warning("⚠️ 秋田数据缺失经纬度字段。")
            return pd.DataFrame()

        # 数据类型清洗
        df['latitude'] = pd.to_numeric(df['latitude'], errors='coerce')
        df['longitude'] = pd.to_numeric(df['longitude'], errors='coerce')
        df['sighting_datetime'] = pd.to_datetime(df['sighting_datetime'], errors='coerce')
        
        # 补充缺失值
        if 'sighting_condition' not in df.columns:
            df['sighting_condition'] = "无详细描述"
        else:
            df['sighting_condition'] = df['sighting_condition'].fillna("无详细描述")

        # 添加来源标签
        df['source'] = '秋田县 (本地)'
        
        return df[['latitude', 'longitude', 'sighting_datetime', 'sighting_condition', 'source']].dropna(subset=['latitude', 'longitude'])
        
    except FileNotFoundError:
        st.error("❌ 找不到 `bears.json` 文件。请将 Charles 抓到的数据保存到项目根目录。")
        return pd.DataFrame()
    except Exception as e:
        st.error(f"❌ 秋田数据加载未知错误: {e}")
        return pd.DataFrame()

# --- B. 加载山梨县数据 (远程 CKAN API) ---
@st.cache_data
def load_yamanashi_data():
    url = "https://catalog.dataplatform-yamanashi.jp/api/action/datastore_search"
    params = {
        "resource_id": "b4eb262f-07e0-4417-b24f-6b15844b4ac1",
        "limit": 5000 
    }
    
    try:
        response = requests.get(url, params=params, timeout=15) # 设置超时防止卡死
        data = response.json()
        
        if 'result' in data and 'records' in data['result']:
            df = pd.DataFrame(data['result']['records'])
            
            # 1. 字段名映射 (基于你提供的样本)
            rename_map = {
                '緯度': 'latitude',
                '経度': 'longitude',
                '年月日': 'sighting_datetime' # 样本显示是这个字段
            }
            df = df.rename(columns=rename_map)
            
            # 如果映射后没有找到关键列，说明 API 字段名变了，打印出来调试
            if 'latitude' not in df.columns:
                # 尝试查找其他可能的列名
                possible_lats = ['lat', 'Lat', 'LAT', '纬度']
                for col in possible_lats:
                    if col in df.columns:
                        df = df.rename(columns={col: 'latitude'})
                        break
            
            # 2. 类型转换
            df['latitude'] = pd.to_numeric(df['latitude'], errors='coerce')
            df['longitude'] = pd.to_numeric(df['longitude'], errors='coerce')
            df['sighting_datetime'] = pd.to_datetime(df['sighting_datetime'], errors='coerce')
            
            # 3. 智能构建描述字段 (拼接多个字段)
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
                
                if details:
                    desc += f" ({', '.join(details)})"
                
                return desc if desc else "API数据无描述"

            df['sighting_condition'] = df.apply(make_description, axis=1)
            
            # 4. 来源标签
            df['source'] = '山梨县 (API)'
            
            return df[['latitude', 'longitude', 'sighting_datetime', 'sighting_condition', 'source']].dropna(subset=['latitude', 'longitude'])
            
        return pd.DataFrame()
        
    except Exception as e:
        st.warning(f"⚠️ 山梨县 API 连接失败 (可能是网络原因): {e}")
        return pd.DataFrame()

# ==========================================
# 2. 主逻辑控制器
# ==========================================

st.title("🐻 日本熊出没安全地图")
st.markdown("融合 **秋田县 (本地库)** 与 **山梨县 (实时API)** 数据，提供全方位的徒步安全检测。")

# --- 加载数据 ---
with st.spinner('正在融合多源数据...'):
    df_akita = load_akita_data()
    df_yamanashi = load_yamanashi_data()
    
    # 合并
    all_bears = pd.concat([df_akita, df_yamanashi], ignore_index=True)

# --- 全局检查 ---
if all_bears.empty:
    st.error("❌ 所有数据源均加载失败。请检查：1. bears.json 是否存在且格式正确；2. 网络是否能访问山梨县 API。")
    st.stop()
else:
    st.success(f"✅ 成功加载 {len(all_bears)} 条记录 (秋田: {len(df_akita)}, 山梨: {len(df_yamanashi)})")

# ==========================================
# 3. 侧边栏：时间过滤器
# ==========================================
with st.sidebar:
    st.header("⏳ 筛选设置")
    
    # 过滤掉无效时间
    valid_dates = all_bears['sighting_datetime'].dropna()
    
    if not valid_dates.empty:
        min_date = valid_dates.min().date()
        max_date = valid_dates.max().date()
        
        # 默认显示最近 1 年
        default_start = max_date - datetime.timedelta(days=365)
        if default_start < min_date: default_start = min_date

        date_range = st.date_input(
            "选择目击时间范围",
            value=(default_start, max_date),
            min_value=min_date,
            max_value=max_date
        )
        
        if len(date_range) == 2:
            start_d, end_d = date_range
            filtered_df = all_bears[
                (all_bears['sighting_datetime'].dt.date >= start_d) & 
                (all_bears['sighting_datetime'].dt.date <= end_d)
            ].copy()
        else:
            filtered_df = all_bears.copy()
    else:
        st.warning("数据中缺少时间字段，无法筛选。")
        filtered_df = all_bears.copy()

    st.divider()
    st.caption("Developed with Streamlit")

# ==========================================
# 4. 地图可视化核心
# ==========================================

# 页面主要布局
col1, col2 = st.columns([3, 1])

with col1:
    uploaded_file = st.file_uploader("📂 上传 GPX 路线文件 (开启精准检测)", type=['gpx'])

# 确定地图默认中心 (优先显示筛选后的数据中心，否则显示日本中心)
if not filtered_df.empty:
    center_lat = filtered_df['latitude'].mean()
    center_lon = filtered_df['longitude'].mean()
else:
    center_lat, center_lon = 36.2048, 138.2529

m = folium.Map(location=[center_lat, center_lon], zoom_start=7, tiles="OpenStreetMap")

# --- 场景 A: 路线检测模式 (用户上传了 GPX) ---
if uploaded_file is not None:
    try:
        gpx = gpxpy.parse(uploaded_file)
        points = []
        for track in gpx.tracks:
            for segment in track.segments:
                for point in segment.points:
                    points.append((point.latitude, point.longitude))
        
        if points:
            # 1. 绘制路线
            folium.PolyLine(points, color="blue", weight=4, opacity=0.7, tooltip="徒步路线").add_to(m)
            
            # 2. 空间计算 (Buffer)
            route_line = LineString(points)
            buffer_dist = 0.005 # 约 500m
            route_buffer = route_line.buffer(buffer_dist)
            min_lon, min_lat, max_lon, max_lat = route_buffer.bounds
            
            # 3. 粗筛 (极大提升性能)
            candidates = filtered_df[
                (filtered_df['latitude'] >= min_lat) & (filtered_df['latitude'] <= max_lat) &
                (filtered_df['longitude'] >= min_lon) & (filtered_df['longitude'] <= max_lon)
            ]
            
            # 4. 精确检测
            dangerous_bears = []
            for idx, row in candidates.iterrows():
                if route_buffer.contains(Point(row['latitude'], row['longitude'])):
                    dangerous_bears.append(row)
            
            # 5. 渲染危险点
            for bear in dangerous_bears:
                # 颜色区分：秋田(红), 山梨(橙)
                color = "red" if "秋田" in bear['source'] else "orange"
                
                date_str = bear['sighting_datetime'].strftime('%Y-%m-%d %H:%M') if pd.notnull(bear['sighting_datetime']) else "未知时间"
                
                popup_html = f"""
                <div style="font-family:sans-serif; width:200px;">
                    <b>{bear['source']}</b><br>
                    <span style="color:red;">⚠️ {date_str}</span><br>
                    <hr style="margin:5px 0;">
                    {bear['sighting_condition']}
                </div>
                """
                folium.Marker(
                    [bear['latitude'], bear['longitude']],
                    popup=folium.Popup(popup_html, max_width=250),
                    icon=folium.Icon(color=color, icon="paw", prefix='fa')
                ).add_to(m)
                
            m.fit_bounds(route_line.bounds)
            
            # 结果提示
            if dangerous_bears:
                st.error(f"⚠️ 警告：在路线 500米 范围内发现 {len(dangerous_bears)} 条熊出没记录！")
                with st.expander("查看详细列表", expanded=True):
                    st.dataframe(pd.DataFrame(dangerous_bears)[['sighting_datetime', 'source', 'sighting_condition']])
            else:
                st.success("✅ 该时间段内，路线周边安全（无记录）。")
        else:
            st.warning("GPX 文件中未解析到路径点。")
            
    except Exception as e:
        st.error(f"GPX 解析失败: {e}")

# --- 场景 B: 全景探索模式 (默认) ---
else:
    if not filtered_df.empty:
        # 使用 MarkerCluster 处理大量数据
        marker_cluster = MarkerCluster(name="熊出没聚合点").add_to(m)
        
        # 限制显示数量防止浏览器崩溃 (如果超过 5000 条)
        limit = 5000
        if len(filtered_df) > limit:
            st.info(f"💡 数据量较大，地图仅显示最近的 {limit} 条记录。请使用侧边栏筛选缩短时间范围。")
            display_data = filtered_df.sort_values('sighting_datetime', ascending=False).head(limit)
        else:
            display_data = filtered_df
            
        for idx, row in display_data.iterrows():
            color = "red" if "秋田" in row['source'] else "orange"
            date_str = row['sighting_datetime'].strftime('%Y-%m-%d') if pd.notnull(row['sighting_datetime']) else ""
            
            # 简化的 Popup
            popup_content = f"<b>{date_str}</b><br>{row['sighting_condition']}"
            
            folium.Marker(
                location=[row['latitude'], row['longitude']],
                popup=folium.Popup(popup_content, max_width=200),
                icon=folium.Icon(color=color, icon="info-sign"),
            ).add_to(marker_cluster)

# 渲染地图
st_folium(m, width="100%", height=600)
