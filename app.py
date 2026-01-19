import streamlit as st
import pandas as pd
import plotly.express as px
from datetime import datetime
import gspread
from google.oauth2.service_account import Credentials

# --- 1. 設定頁面配置 ---
st.set_page_config(page_title="個人理財管家", page_icon="💰", layout="wide")

# --- 2. 連接 Google Sheets 設定 ---
@st.cache_resource
def get_google_sheet_client():
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

# --- 3. 讀取與寫入功能 (含刪除邏輯) ---

def get_data():
    """從 Google Sheet 讀取資料，並加上行號以便刪除"""
    client = get_google_sheet_client()
    sheet = client.open("my_expenses_db").sheet1
    
    # 讀取整張表 (包含空白行)
    all_rows = sheet.get_all_values()
    
    # 如果只有標題或沒資料
    if len(all_rows) <= 1:
        return pd.DataFrame(columns=["row_id", "date", "category", "amount", "note"])
    
    # 轉換成 DataFrame (第一行是標題)
    # 標題應該是: date, category, amount, note
    headers = all_rows[0]
    data = all_rows[1:]
    
    df = pd.DataFrame(data, columns=headers)
    
    # 【關鍵】加上原始行號 (Row ID)
    # Google Sheet 資料從第 2 行開始 (第 1 行是標題)
    # 所以 index 0 的資料其實是 Sheet 的第 2 行
    df['row_id'] = [i + 2 for i in range(len(df))]
    
    # 確保金額是數字格式
    # 如果有空值或非數字，強制轉為 0
    df['amount'] = pd.to_numeric(df['amount'], errors='coerce').fillna(0)
    
    return df

def add_expense(date, category, amount, note):
    """新增一筆資料"""
    client = get_google_sheet_client()
    sheet = client.open("my_expenses_db").sheet1
    
    # 如果是第一筆，先寫入標題
    if not sheet.get_all_values():
        sheet.append_row(["date", "category", "amount", "note"])
    
    date_str = date.strftime("%Y-%m-%d")
    sheet.append_row([date_str, category, amount, note])
    
    # 清除快取，讓介面更新
    st.cache_data.clear()

def delete_expense(row_id):
    """根據行號刪除資料"""
    client = get_google_sheet_client()
    sheet = client.open("my_expenses_db").sheet1
    
    sheet.delete_rows(int(row_id))
    st.cache_data.clear()

# --- 4. 主程式介面 ---

# 讀取資料
df = get_data()

# --- 側邊欄：新增與刪除 ---
st.sidebar.header("📝 新增支出")
with st.sidebar.form("expense_form", clear_on_submit=True):
    date = st.date_input("日期", datetime.now())
    category = st.selectbox("類別", ["飲食", "交通", "娛樂", "購物", "居住", "醫療", "投資", "寵物", "進修", "其他"])
    amount = st.number_input("金額", min_value=0.0, step=10.0, format="%.0f")
    note = st.text_input("備註 (選填)")
    submitted = st.form_submit_button("提交")

    if submitted:
        if amount > 0:
            with st.spinner("正在寫入雲端..."):
                add_expense(date, category, amount, note)
            st.sidebar.success("已儲存！")
            st.rerun()
        else:
            st.sidebar.error("金額必須大於 0")

st.sidebar.markdown("---")
st.sidebar.header("🗑️ 刪除/管理")

# 準備刪除選單 (顯示最近 5 筆)
if not df.empty:
    # 排序：最新的在最上面
    delete_df = df.sort_values(by='row_id', ascending=False).head(5)
    
    # 製作選項標籤
    delete_options = {
        f"{row['date']} - {row['category']} ${row['amount']} ({row['note']})": row['row_id']
        for index, row in delete_df.iterrows()
    }
    
    selected_label = st.sidebar.selectbox(
        "選擇要刪除哪一筆 (最近5筆)", 
        options=list(delete_options.keys())
    )
    
    if st.sidebar.button("確認刪除此筆"):
        target_row_id = delete_options[selected_label]
        with st.spinner("正在刪除中..."):
            delete_expense(target_row_id)
        st.sidebar.success("刪除成功！")
        st.rerun()
else:
    st.sidebar.info("目前沒有資料可刪除")

st.sidebar.markdown("---")
st.sidebar.header("⚙️ 預算設定")
budget = st.sidebar.number_input("本月預算上限", min_value=1000, value=20000, step=500)


# --- 主畫面儀表板 ---
st.title("💰 個人雲端理財管家")

if not df.empty:
    # 資料處理
    df['date'] = pd.to_datetime(df['date'])
    current_month = datetime.now().strftime("%Y-%m")
    df['month'] = df['date'].dt.strftime("%Y-%m")
    
    # 篩選本月資料
    current_month_df = df[df['month'] == current_month]
    
    # 計算統計
    total_spent = current_month_df['amount'].sum()
    remaining_budget = budget - total_spent
    usage_percentage = (total_spent / budget) * 100 if budget > 0 else 0

    # 1. 關鍵指標
    col1, col2, col3 = st.columns(3)
    col1.metric("本月總支出", f"NT$ {total_spent:,.0f}")
    col2.metric("剩餘預算", f"NT$ {remaining_budget:,.0f}", delta_color="normal" if remaining_budget > 0 else "inverse")
    
    # 2. 進度條
    if usage_percentage >= 100:
        st.error(f"⚠️ 警告：本月已超支！ ({usage_percentage:.1f}%)")
        bar_color = "red"
    elif usage_percentage >= 80:
        st.warning(f"⚠️ 注意：預算即將用盡 ({usage_percentage:.1f}%)")
        bar_color = "orange"
    else:
        st.success(f"目前控制良好 ({usage_percentage:.1f}%)")
        bar_color = "green"
        
    st.progress(min(usage_percentage / 100, 1.0))

    st.markdown("---")

    # 3. 圖表區
    c1, c2 = st.columns(2)
    with c1:
        st.subheader("📊 本月花費類別")
        if not current_month_df.empty:
            fig_pie = px.pie(current_month_df, values='amount', names='category', title=f'{current_month} 類別占比', hole=0.4)
            st.plotly_chart(fig_pie, use_container_width=True)
        else:
            st.info("本月尚無支出資料")

    with c2:
        st.subheader("📈 每日支出趨勢")
        # 每日加總
        daily_expense = df.groupby('date')['amount'].sum().reset_index()
        fig_line = px.line(daily_expense, x='date', y='amount', title='支出變化趨勢', markers=True)
        st.plotly_chart(fig_line, use_container_width=True)

    # 4. 詳細資料表 (隱藏 row_id 欄位，不需要給使用者看)
    st.subheader("📋 詳細記錄")
    st.dataframe(
        df.drop(columns=['row_id', 'month']).sort_values(by='date', ascending=False), 
        use_container_width=True
    )

else:
    st.info("💡 目前還沒有任何資料，請從左側側邊欄「新增支出」！")