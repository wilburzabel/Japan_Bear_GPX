import streamlit as st
import pandas as pd
import requests
import json
import gpxpy
from shapely.geometry import Point, LineString
from streamlit_folium import st_folium
import folium
from folium.plugins import FastMarkerCluster
import datetime

# --- 页面基础配置 ---
st.set_page_config(page_title="日本熊出没综合看板", layout="wide", page_icon="🐻")

# ==========================================
# 1. 数据抽取与清洗层 (ETL)
# ==========================================

# --- A. 加载秋田县数据 (本地 JSON) ---
@st.cache_data
def load_akita_data(filepath="bears.json"):
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            raw_data = json.load(f)
        
        # 秋田数据在 'result' 列表中
        if 'result' not in raw_data:
            return pd.DataFrame()
            
        df = pd.DataFrame(raw_data['result'])
        
        # 标准化字段名
        # 目标格式: latitude, longitude, sighting_datetime, sighting_condition, source
        # 秋田源字段已经是 latitude, longitude，无需改名
        
        # 清洗数据
        df['latitude'] = pd.to_numeric(df['latitude'], errors='coerce')
        df['longitude'] = pd.to_numeric(df['longitude'], errors='coerce')
        df['sighting_datetime'] = pd.to_datetime(df['sighting_datetime'], errors='coerce')
        
        # 补充来源标签
        df['source'] = '秋田县 (本地库)'
        
        # 确保有描述字段
        if 'sighting_condition' not in df.columns:
            df['sighting_condition'] = '无详细描述'
            
        # 选取标准列
        return df[['latitude', 'longitude', 'sighting_datetime', 'sighting_condition', 'source']].dropna()
        
    except Exception as e:
        st.error(f"秋田数据加载失败: {e}")
        return pd.DataFrame()

# --- B. 加载山梨县数据 (远程 CKAN API) ---
@st.cache_data
def load_yamanashi_data():
    url = "https://catalog.dataplatform-yamanashi.jp/api/action/datastore_search"
    params = {
        "resource_id": "b4eb262f-07e0-4417-b24f-6b15844b4ac1",
        "limit": 5000 # 获取 5000 条
    }
    
    try:
        response = requests.get(url, params=params, timeout=10)
        data = response.json()
        
        if 'result' in data and 'records' in data['result']:
            df = pd.DataFrame(data['result']['records'])
            
            # --- 关键：字段名映射 ---
            # 这里是根据常见的日本开放数据字段进行的猜测
            # 如果不显示数据，请先查看页面上打印的 "原始字段名"
            rename_map = {
                '緯度': 'latitude',
                '纬度': 'latitude', # 容错
                'Lat': 'latitude',
                
                '経度': 'longitude',
                '经度': 'longitude',
                'Lon': 'longitude',
                
                '発生日時': 'sighting_datetime',
                '月日': 'sighting_datetime', # 某些表可能只有月日
                
                '出没状況': 'sighting_condition',
                '状況': 'sighting_condition',
                '摘要': 'sighting_condition'
            }
            
            df = df.rename(columns=rename_map)
            
            # 清洗数据
            df['latitude'] = pd.to_numeric(df['latitude'], errors='coerce')
            df['longitude'] = pd.to_numeric(df['longitude'], errors='coerce')
            
            # 时间处理可能需要更复杂的逻辑，这里先简单尝试
            df['sighting_datetime'] = pd.to_datetime(df['sighting_datetime'], errors='coerce')
            
            # 补充来源标签
            df['source'] = '山梨县 (Live API)'
            
            # 填充缺失的描述
            if 'sighting_condition' not in df.columns:
                 # 尝试合并地址作为描述
                 possible_desc = ['場所', '市町村名', '住所', 'address']
                 for col in possible_desc:
                     if col in df.columns:
                         df['sighting_condition'] = df[col]
                         break
                 else:
                     df['sighting_condition'] = "API数据无描述"

            # 选取标准列 (如果 API 缺少某些列，这里可能会报错，所以加个检测)
            required_cols = ['latitude', 'longitude', 'sighting_datetime', 'sighting_condition', 'source']
            for col in required_cols:
                if col not in df.columns:
                    df[col] = None # 补全缺失列
            
            return df[required_cols].dropna(subset=['latitude', 'longitude'])
            
        return pd.DataFrame()
        
    except Exception as e:
        # 为了不影响主程序运行，API 失败只打印警告
        st.warning(f"山梨县 API 连接失败或解析错误: {e}")
        return pd.DataFrame()

# ==========================================
# 2. 主程序逻辑
# ==========================================

st.title("🐻 日本熊出没综合检测看板")
st.caption("数据源融合：秋田县 (JSON文件) + 山梨县 (实时API)")

# --- 1. 并行加载数据 ---
with st.spinner('正在融合多源数据...'):
    df_akita = load_akita_data()
    df_yamanashi = load_yamanashi_data()
    
    # 合并数据表
    all_bears = pd.concat([df_akita, df_yamanashi], ignore_index=True)

# 检查数据是否为空
if all_bears.empty:
    st.error("❌ 未能加载任何数据，请检查 bears.json 文件位置或 API 连接。")
    st.stop()

# --- 2. 侧边栏：全局时间筛选 ---
with st.sidebar:
    st.header("⏳ 筛选设置")
    
    # 移除空时间（防止报错）
    valid_dates = all_bears['sighting_datetime'].dropna()
    if not valid_dates.empty:
        min_date = valid_dates.min().date()
        max_date = valid_dates.max().date()
        
        # 默认看最近 2 年
        default_start = max_date - datetime.timedelta(days=730)
        if default_start < min_date: default_start = min_date

        date_range = st.date_input(
            "选择日期范围",
            value=(default_start, max_date),
            min_value=min_date,
            max_value=max_date
        )
        
        if len(date_range) == 2:
            start_d, end_d = date_range
            # 执行筛选
            filtered_df = all_bears[
                (all_bears['sighting_datetime'].dt.date >= start_d) & 
                (all_bears['sighting_datetime'].dt.date <= end_d)
            ].copy()
        else:
            filtered_df = all_bears.copy()
    else:
        filtered_df = all_bears.copy()
        st.warning("数据中未检测到有效的时间字段，显示全部数据。")

    # 数据源统计
    st.divider()
    st.write("📊 数据源统计:")
    source_counts = filtered_df['source'].value_counts()
    st.write(source_counts)

# --- 3. 地图与分析逻辑 ---

uploaded_file = st.file_uploader("📂 上传 GPX 路线进行安全检测", type=['gpx'])

# 确定地图中心
if not filtered_df.empty:
    center_lat = filtered_df['latitude'].mean()
    center_lon = filtered_df['longitude'].mean()
else:
    center_lat, center_lon = 36.2, 138.2 # 日本中心大概位置

m = folium.Map(location=[center_lat, center_lon], zoom_start=7)

# === 场景 A: 路线检测模式 ===
if uploaded_file is not None:
    gpx = gpxpy.parse(uploaded_file)
    points = []
    for track in gpx.tracks:
        for segment in track.segments:
            for point in segment.points:
                points.append((point.latitude, point.longitude))
    
    if points:
        folium.PolyLine(points, color="blue", weight=4, opacity=0.7).add_to(m)
        
        # 空间计算
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
        
        # 渲染危险点
        for bear in dangerous_bears:
            # 根据来源设置不同颜色
            icon_color = "red" if "秋田" in bear['source'] else "orange"
            
            popup_html = f"""
            <b>来源:</b> {bear['source']}<br>
            <b>时间:</b> {bear['sighting_datetime']}<br>
            <b>详情:</b> {bear['sighting_condition']}
            """
            folium.Marker(
                [bear['latitude'], bear['longitude']],
                popup=folium.Popup(popup_html, max_width=250),
                icon=folium.Icon(color=icon_color, icon="paw", prefix='fa')
            ).add_to(m)
            
        m.fit_bounds(route_line.bounds)
        
        if dangerous_bears:
            st.error(f"⚠️ 在路线周边发现 {len(dangerous_bears)} 次目击记录！")
            st.dataframe(pd.DataFrame(dangerous_bears)[['sighting_datetime', 'source', 'sighting_condition']])
        else:
            st.success("✅ 路线周边暂无记录。")

# === 场景 B: 全景探索模式 ===
# --- B. 加载山梨县数据 (远程 CKAN API - 适配版) ---
@st.cache_data
def load_yamanashi_data():
    # 注意：URL 和 Resource ID 保持不变
    url = "https://catalog.dataplatform-yamanashi.jp/api/action/datastore_search"
    params = {
        "resource_id": "b4eb262f-07e0-4417-b24f-6b15844b4ac1",
        "limit": 5000 
    }
    
    try:
        response = requests.get(url, params=params, timeout=10)
        data = response.json()
        
        if 'result' in data and 'records' in data['result']:
            df = pd.DataFrame(data['result']['records'])
            
            # 1. 字段名映射 (根据你提供的样本修改)
            rename_map = {
                '緯度': 'latitude',
                '経度': 'longitude',
                '年月日': 'sighting_datetime' # 使用这个 ISO 格式的日期
            }
            df = df.rename(columns=rename_map)
            
            # 2. 数据类型转换
            df['latitude'] = pd.to_numeric(df['latitude'], errors='coerce')
            df['longitude'] = pd.to_numeric(df['longitude'], errors='coerce')
            df['sighting_datetime'] = pd.to_datetime(df['sighting_datetime'], errors='coerce')
            
            # 3. 关键修改：构建“目击详情”字段
            # 因为原始数据没有单一的“详情”列，我们将多个字段拼接起来，做成一个易读的字符串
            def make_description(row):
                # 获取各个字段，如果为空则显示空字符串
                muni = str(row.get('目撃市町村', ''))
                place = str(row.get('場所', ''))
                time = str(row.get('時間', ''))
                age = str(row.get('推定年齢', ''))
                count = str(row.get('目撃頭数', ''))
                
                # 拼接成类似: "早川町 千須和地内 (19:00, コドモ, 1頭)"
                desc = f"{muni} {place}"
                details = []
                if time and time != 'nan': details.append(time)
                if age and age != 'nan': details.append(age)
                if count and count != 'nan': details.append(f"{count}頭")
                
                if details:
                    desc += f" ({', '.join(details)})"
                return desc

            # 应用拼接函数
            df['sighting_condition'] = df.apply(make_description, axis=1)
            
            # 4. 补充来源标签
            df['source'] = '山梨县 (Live API)'
            
            # 5. 选取标准列
            required_cols = ['latitude', 'longitude', 'sighting_datetime', 'sighting_condition', 'source']
            return df[required_cols].dropna(subset=['latitude', 'longitude'])
            
        return pd.DataFrame()
        
    except Exception as e:
        st.warning(f"山梨县 API 数据处理失败: {e}")
        return pd.DataFrame()

# --- 调试区：如果有山梨数据但没显示，查看这里 ---
with st.expander("🛠 开发者工具：查看原始数据字段"):
    if not df_yamanashi.empty:
        st.write("山梨县数据预览 (前3行):", df_yamanashi.head(3))
    else:
        st.write("山梨县数据为空 (请检查 API 或 字段映射)")
