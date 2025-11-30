import streamlit as st
import pandas as pd
import requests
import json
from datetime import datetime, timedelta

st.set_page_config(page_title="调试模式", layout="wide")
st.title("🐞 熊地图 - 故障排查模式")

# --- 调试函数 ---
def debug_log(msg):
    st.write(f"👉 {msg}")

# --- 1. 检查 Secrets ---
st.subheader("1. 检查配置 (Secrets)")
try:
    if "kumadas_cookies" in st.secrets and "kumadas_headers" in st.secrets:
        st.success("✅ Secrets 已检测到")
        
        # 尝试读取具体字段 (只显示前几位，防止泄露)
        xsrf = st.secrets["kumadas_cookies"].get("XSRF_TOKEN", "")
        session = st.secrets["kumadas_cookies"].get("SESSION", "")
        csrf = st.secrets["kumadas_headers"].get("CSRF_TOKEN", "")
        
        st.code(f"""
        XSRF-TOKEN: {xsrf[:10]}... (长度: {len(xsrf)})
        SESSION: {session[:10]}... (长度: {len(session)})
        CSRF-TOKEN: {csrf[:10]}... (长度: {len(csrf)})
        """)
        
        if len(xsrf) < 10 or len(session) < 10:
            st.error("❌ Cookie 看起来太短了，可能是复制错了？")
    else:
        st.error("❌ 未找到 secrets！请在 Streamlit 后台 Settings -> Secrets 中配置。")
        st.stop() # 停止运行
except Exception as e:
    st.error(f"❌ 读取 Secrets 时发生严重错误: {e}")
    st.stop()

# --- 2. 构造请求 ---
st.subheader("2. 尝试连接服务器")

if st.button("开始测试抓取"):
    url = 'https://kumadas.net/api/ver1/sightings/post_list'
    
    # 构造 Headers
    cookies = {
        'XSRF-TOKEN': st.secrets["kumadas_cookies"]["XSRF_TOKEN"],
        '_session': st.secrets["kumadas_cookies"]["SESSION"],
    }
    headers = {
        'x-csrf-token': st.secrets["kumadas_headers"]["CSRF_TOKEN"],
        'user-agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'content-type': 'application/json',
        'origin': 'https://kumadas.net',
        'referer': 'https://kumadas.net/'
    }
    
    # 构造 Body
    json_data = {
        'lat': 38.00, 'lng': 137.00,
        'filter': {
            'radius': '3000',
            'info_type_ids': ['1', '2', '3', '4'],
            'animal_species_ids': ['1'],
            'municipality_ids': [],
            'startdate': (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d"),
            'enddate': datetime.now().strftime("%Y-%m-%d"),
        },
    }

    st.write("正在发送 POST 请求...")
    
    try:
        # ⚠️ 这里去掉了 try-except 的静音保护，让错误直接爆出来
        resp = requests.post(url, cookies=cookies, headers=headers, json=json_data, timeout=30)
        
        st.write(f"📡 HTTP 状态码: **{resp.status_code}**")
        
        if resp.status_code == 200:
            st.success("✅ 连接成功！服务器返回了 200 OK")
            try:
                data = resp.json()
                st.write("数据预览 (Raw JSON):")
                st.json(data if isinstance(data, list) else data.get('data', [])[:3]) # 只看前3条
                st.balloons()
            except Exception as e:
                st.error(f"❌ JSON 解析失败: {e}")
                st.write("返回的原始内容是:")
                st.text(resp.text[:500])
        
        elif resp.status_code == 419:
            st.error("❌ 错误 419 (Page Expired)")
            st.warning("原因：CSRF Token 或 Cookie 已过期/不匹配。")
            st.info("解决：请重新去浏览器 F12 抓取最新的 Cookie 和 Token，并更新 Streamlit Secrets。")
            
        elif resp.status_code == 403:
            st.error("❌ 错误 403 (Forbidden)")
            st.warning("原因：服务器拒绝访问。通常是 User-Agent 不对，或者 IP 被封了。")
            
        elif resp.status_code == 401:
            st.error("❌ 错误 401 (Unauthorized)")
            st.warning("原因：未授权。Cookie 无效。")
            
        else:
            st.error(f"❌ 未知错误: {resp.status_code}")
            st.text(resp.text[:1000]) # 打印出服务器具体的报错文字
            
    except Exception as e:
        st.error(f"❌ 发生程序级错误: {e}")
