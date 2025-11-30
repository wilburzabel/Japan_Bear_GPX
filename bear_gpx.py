import streamlit as st
import folium
from streamlit_folium import st_folium
import pandas as pd
import gpxpy
import requests
from geopy.distance import geodesic
from folium.plugins import MarkerCluster
from datetime import datetime, timedelta

st.set_page_config(page_title="日本熊出没 (云端版)", layout="wide")
st.title("🐻 日本熊出没地图 (云端部署版)")

# --- 1. 从 Secrets 读取 Cookie (更安全) ---
def get_headers_from_secrets():
    """从 Streamlit 后台配置读取 Cookie，防止代码泄露"""
    try:
        # 必须在 Streamlit Cloud 后台设置这些 secrets
        return {
            'cookies': {
                'XSRF-TOKEN': st.secrets["kumadas_cookies"]["XSRF_TOKEN"],
                '_session': st.secrets["kumadas_cookies"]["SESSION"],
                # 其他必要的 cookie...
            },
            'headers': {
                'x-csrf-token': st.secrets["kumadas_headers"]["CSRF_TOKEN"],
                'user-agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) ...',
                'content-type': 'application/json',
                'origin': 'https://kumadas.net',
                'referer': 'https://kumadas.net/'
            }
        }
    except Exception:
        return None

# --- 2. 数据抓取逻辑 ---
@st.cache_data(ttl=300)
def fetch_online_data(start_date, end_date):
    # 尝试读取 Secrets
    config = get_headers_from_secrets()
    
    if not config:
        st.error("❌ 未配置 Secrets！请在 Streamlit 后台填入 Cookie。")
        return None

    url = 'https://kumadas.net/api/ver1/sightings/post_list'
    
    json_data = {
        'lat': 38.00, 'lng': 137.00,
        'filter': {
            'radius': '3000',
            'info_type_ids': ['1', '2', '3', '4'],
            'animal_species_ids': ['1'],
            'municipality_ids': [],
            'startdate': start_date.strftime("%Y-%m-%d"),
            'enddate': end_date.strftime("%Y-%m-%d"),
        },
    }

    try:
        resp = requests.post(
            url, 
            cookies=config['cookies'], 
            headers=config['headers'], 
            json=json_data, 
            timeout=20
        )
        if resp.status_code == 200:
            items = resp.json()
            if isinstance(items, dict): items = items.get('data', [])
            
            cleaned = []
            for item in items:
                lat = item.get('lat') or item.get('latitude')
                lon = item.get('lng') or item.get('longitude')
                d_str = item.get('sighted_at') or item.get('created_at')
                if lat and lon and d_str:
                    cleaned.append({
                        "date": d_str, # 先存字符串，后面转
                        "lat": float(lat),
                        "lon": float(lon),
                        "desc": item.get('body', '无描述'),
                        "place": item.get('place_name', '')
                    })
            return pd.DataFrame(cleaned)
    except Exception as e:
        st.error(f"连接错误: {e}")
    return None

# --- 3. GPX 解析 ---
def parse_gpx(file):
    try:
        gpx = gpxpy.parse(file)
        return [(p.latitude, p.longitude) for t in gpx.tracks for s in t.segments for p in s.points]
    except: return []

# --- 4. 主界面逻辑 ---

# 侧边栏：选择数据源
st.sidebar.header("📡 数据源")
data_mode = st.sidebar.radio("选择模式", ["在线抓取 (需有效Cookie)", "上传历史备份 (离线)"])

df_bears = pd.DataFrame()

if data_mode == "在线抓取 (需有效Cookie)":
    s_date = st.sidebar.date_input("开始日期", datetime.now().date() - timedelta(days=30))
    e_date = st.sidebar.date_input("结束日期", datetime.now().date())
    
    if st.sidebar.button("开始抓取"):
        with st.spinner("正在连接 Kumadas..."):
            df_bears = fetch_online_data(s_date, e_date)
            
        if df_bears is not None and not df_bears.empty:
            st.success(f"✅ 成功抓取 {len(df_bears)} 条数据！")
            
            # ✨ 关键点：提供下载按钮来实现“持久化”
            csv = df_bears.to_csv(index=False).encode('utf-8')
            st.sidebar.download_button(
                label="💾 下载数据备份 (以便下次使用)",
                data=csv,
                file_name='kumadas_backup.csv',
                mime='text/csv',
            )

elif data_mode == "上传历史备份 (离线)":
    backup_file = st.sidebar.file_uploader("📂 上传之前的 kumadas_backup.csv", type=['csv'])
    if backup_file:
        df_bears = pd.read_csv(backup_file)
        st.sidebar.success(f"已加载离线数据: {len(df_bears)} 条")

# 统一处理数据
if not df_bears.empty:
    df_bears['date'] = pd.to_datetime(df_bears['date']).dt.date
    
    # 地图展示 (限制显示数量防止卡顿)
    m = folium.Map(location=[36.0, 138.0], zoom_start=5)
    mc = MarkerCluster().add_to(m)
    
    # 只显示最近的 1000 个点，避免浏览器崩溃
    for _, row in df_bears.head(1000).iterrows():
        folium.Marker(
            [row['lat'], row['lon']], 
            popup=f"{row['date']}\n{row['place']}",
            icon=folium.Icon(color='red', icon='paw', prefix='fa')
        ).add_to(mc)

    # GPX 上传与分析
    gpx_file = st.sidebar.file_uploader("上传 GPX 检测风险", type=['gpx'])
    safe_dist = st.sidebar.slider("风险半径 (km)", 0.5, 5.0, 1.0)
    
    if gpx_file:
        pts = parse_gpx(gpx_file)
        if pts:
            folium.PolyLine(pts, color="blue", weight=4).add_to(m)
            
            # 风险计算 (使用全部数据)
            risks = []
            sampled_route = pts[::20] if len(pts) > 50 else pts
            for _, b in df_bears.iterrows():
                b_loc = (b['lat'], b['lon'])
                for r in sampled_route:
                    if geodesic(b_loc, r).km <= safe_dist:
                        risks.append(b)
                        break
            
            if risks:
                risk_df = pd.DataFrame(risks)
                st.error(f"⚠️ 路线上发现 {len(risk_df)} 个风险点！")
                st.dataframe(risk_df)
                for _, r in risk_df.iterrows():
                    folium.Circle([r['lat'], r['lon']], radius=safe_dist*1000, color='crimson', fill=True).add_to(m)
            else:
                st.success("✅ 路线安全")

    st_folium(m, width="100%", height=600)

else:
    st.info("👈 请在左侧选择模式：如果Cookie有效则【在线抓取】，如果失效则【上传】之前的备份文件。")
