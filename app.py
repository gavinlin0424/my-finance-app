import streamlit as st
import pandas as pd
import plotly.express as px
from datetime import datetime
import gspread
from google.oauth2.service_account import Credentials

# --- 設定頁面配置 ---
st.set_page_config(page_title="智能理財管家 (雲端版)", page_icon="☁️", layout="wide")

# --- 連接 Google Sheets ---
# 使用 st.cache_resource 來快取連線，避免每次操作都重新連線
@st.cache_resource
def get_google_sheet_client():
    # 定義需要的權限範圍
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive"
    ]
    # 從 Streamlit Secrets 讀取金鑰
    creds = Credentials.from_service_account_info(
        st.secrets["gcp_service_account"],
        scopes=scopes
    )
    return gspread.authorize(creds)

def get_data():
    """從 Google Sheet 讀取資料"""
    client = get_google_sheet_client()
    # 開啟試算表 (請確保名稱跟你的 Google Sheet 一模一樣)
    sheet = client.open("my_expenses_db").sheet1
    
    # 讀取所有資料
    data = sheet.get_all_records()
    
    # 如果是空的，回傳空的 DataFrame
    if not data:
        return pd.DataFrame(columns=["date", "category", "amount", "note"])
    
    return pd.DataFrame(data)

def add_expense_to_sheet(date, category, amount, note):
    """新增資料到 Google Sheet"""
    client = get_google_sheet_client()
    sheet = client.open("my_expenses_db").sheet1
    
    # 如果是第一筆資料，先寫入標題列 (Header)
    if not sheet.get_all_values():
        sheet.append_row(["date", "category", "amount", "note"])
    
    # 將日期轉為字串
    date_str = date.strftime("%Y-%m-%d")
    
    # 寫入一行新資料
    sheet.append_row([date_str, category, amount, note])
    
    # 強制清除快取，讓下次讀取時能看到新資料
    st.cache_data.clear()

# --- 側邊欄：設定與輸入 ---
st.sidebar.header("📝 新增支出")
with st.sidebar.form("expense_form", clear_on_submit=True):
    date = st.date_input("日期", datetime.now())
    category = st.selectbox("類別", ["飲食", "交通", "娛樂", "購物", "居住", "醫療", "投資", "寵物", "進修", "其他"])
    amount = st.number_input("金額", min_value=0.0, step=10.0, format="%.2f")
    note = st.text_input("備註 (選填)")
    submitted = st.form_submit_button("提交")

    if submitted:
        if amount > 0:
            with st.spinner("正在寫入雲端資料庫..."):
                add_expense_to_sheet(date, category, amount, note)
            st.sidebar.success("已儲存到 Google Sheet！")
            st.rerun() # 重新整理頁面以顯示最新資料
        else:
            st.sidebar.error("金額必須大於 0")

st.sidebar.markdown("---")
st.sidebar.header("⚙️ 預算設定")
budget = st.sidebar.number_input("本月預算上限", min_value=1000, value=10000, step=500)

# --- 主頁面 ---
st.title("☁️ 智能理財管家 (Google Sheets 連動版)")

# 讀取資料
df = get_data()

if not df.empty:
    # 資料處理
    df['date'] = pd.to_datetime(df['date'])
    current_month = datetime.now().strftime("%Y-%m")
    df['month'] = df['date'].dt.strftime("%Y-%m")
    current_month_df = df[df['month'] == current_month]
    
    # 統計數據
    total_spent = current_month_df['amount'].sum()
    remaining_budget = budget - total_spent
    usage_percentage = (total_spent / budget) * 100

    # 儀表板
    col1, col2, col3 = st.columns(3)
    col1.metric("本月總支出", f"NT${total_spent:,.0f}")
    col2.metric("剩餘預算", f"NT${remaining_budget:,.0f}", delta_color="normal" if remaining_budget > 0 else "inverse")
    
    bar_color = "green"
    if usage_percentage >= 100:
        bar_color = "red"
        st.error(f"⚠️ 警告：你已經超支了！ ({usage_percentage:.1f}%)")
    elif usage_percentage >= 80:
        bar_color = "orange"
        st.warning(f"⚠️ 注意：預算即將用盡 ({usage_percentage:.1f}%)")
    
    progress_value = min(usage_percentage / 100, 1.0)
    st.progress(progress_value)

    st.markdown("---")

    # 圖表
    c1, c2 = st.columns(2)
    with c1:
        st.subheader("📊 支出類別占比")
        if not current_month_df.empty:
            fig_pie = px.pie(current_month_df, values='amount', names='category', title=f'{current_month} 各類別花費')
            st.plotly_chart(fig_pie, use_container_width=True)
        else:
            st.info("本月尚無資料")

    with c2:
        st.subheader("📈 支出趨勢")
        daily_expense = df.groupby('date')['amount'].sum().reset_index()
        fig_line = px.line(daily_expense, x='date', y='amount', title='每日支出變化')
        st.plotly_chart(fig_line, use_container_width=True)

    # 詳細資料表
    st.subheader("📋 詳細記錄 (來自 Google Sheets)")
    st.dataframe(df.sort_values(by='date', ascending=False), use_container_width=True)

else:
    st.info("目前 Google Sheet 裡沒有資料，快新增第一筆吧！")