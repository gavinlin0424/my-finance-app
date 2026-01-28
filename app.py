import streamlit as st
import pandas as pd
import plotly.express as px
from datetime import datetime, timedelta
import gspread
from google.oauth2.service_account import Credentials
import uuid
import time
import random

# --- 1. 設定頁面配置 ---
st.set_page_config(page_title="個人理財管家", page_icon="💳", layout="wide")

# ==========================================
# 🔐 安全登入系統
# ==========================================
if 'logged_in' not in st.session_state:
    st.session_state.logged_in = False

def login():
    st.title("🔒 請登入系統")
    password = st.text_input("請輸入密碼", type="password")
    if st.button("登入"):
        if password == "pcgi1835":
            st.session_state.logged_in = True
            st.rerun()
        else:
            st.error("密碼錯誤")

if not st.session_state.logged_in:
    login()
    st.stop() 

# ==========================================
# ⚙️ 系統設定與常數
# ==========================================

CREDIT_CARDS = {
    "現金": 0,
    "聯邦 (結帳19日)": 19,
    "兆豐-linepay (結帳5日)": 5,
    "台新黑狗 (結帳2日)": 2,
    "中信 (結帳12日)": 12
}

PAYMENT_COLORS = {
    "現金": "#00CC96",             
    "聯邦 (結帳19日)": "#636EFA",   
    "兆豐-linepay (結帳5日)": "#AB63FA", 
    "台新黑狗 (結帳2日)": "#EF553B", 
    "中信 (結帳12日)": "#FFA15A",   
    "銀行轉帳": "#7F7F7F",         
    "其他": "#BAB0AC"              
}

EXPECTED_HEADERS = ["date", "type", "category", "amount", "payment_method", "note", "id"]

# --- 2. 連接 Google Sheets 設定 ---
@st.cache_resource
def get_google_sheet_client():
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive"
    ]
    try:
        creds = Credentials.from_service_account_info(
            st.secrets["gcp_service_account"],
            scopes=scopes
        )
        return gspread.authorize(creds)
    except Exception as e:
        st.error(f"無法連接 Google Sheet，請檢查 Secrets 設定: {e}")
        return None

# 🔥 修改重點：增加重試機制的連線函式 🔥
def get_spreadsheet():
    """取得 Spreadsheet 物件，包含重試機制以避免 API Rate Limit"""
    client = get_google_sheet_client()
    if not client: return None
    
    # 嘗試連線最多 3 次
    for attempt in range(3):
        try:
            return client.open("my_expenses_db")
        except gspread.exceptions.APIError:
            # 如果遇到 API 錯誤，等待 2~4 秒後重試
            time.sleep(2 + random.random() * 2)
            continue
        except Exception as e:
            st.error(f"連線發生未預期錯誤: {e}")
            return None
            
    st.error("⚠️ 系統忙碌中 (API 請求過多)，請稍後再重新整理頁面。")
    return None

# --- 3. 核心功能：讀取、寫入、更新 ---

def get_data():
    sh = get_spreadsheet()
    if not sh: return pd.DataFrame()

    try:
        all_worksheets = sh.worksheets()
    except gspread.exceptions.APIError:
        time.sleep(2)
        all_worksheets = sh.worksheets() # 簡單重試一次

    all_data = []

    for worksheet in all_worksheets:
        try:
            rows = worksheet.get_all_values()
        except Exception:
            continue # 跳過讀取失敗的分頁

        if len(rows) <= 1: continue 
            
        headers = rows[0]
        if "id" not in headers or "date" not in headers: continue

        sheet_data = rows[1:]
        
        for row in sheet_data:
            if len(row) < len(headers):
                row += [""] * (len(headers) - len(row))
            
            row_dict = dict(zip(headers, row))
            row_dict['_sheet_name'] = worksheet.title
            
            if 'type' not in row_dict: row_dict['type'] = '支出'
            if 'payment_method' not in row_dict: row_dict['payment_method'] = '現金'
                
            all_data.append(row_dict)
            
    if not all_data:
        return pd.DataFrame(columns=EXPECTED_HEADERS + ['_sheet_name'])

    df = pd.DataFrame(all_data)
    for col in EXPECTED_HEADERS:
        if col not in df.columns: df[col] = ""

    df['amount'] = pd.to_numeric(df['amount'], errors='coerce').fillna(0)
    df['date'] = pd.to_datetime(df['date'], errors='coerce').dt.date
    
    return df

def get_or_create_worksheet(sh, sheet_name):
    try:
        worksheet = sh.worksheet(sheet_name)
    except gspread.exceptions.WorksheetNotFound:
        time.sleep(1) # 建立分頁前稍作緩衝
        worksheet = sh.add_worksheet(title=sheet_name, rows=100, cols=10)
        worksheet.append_row(EXPECTED_HEADERS)
    return worksheet

def add_transaction(date_obj, record_type, category, amount, payment_method, note):
    sh = get_spreadsheet()
    if not sh: return

    sheet_name = date_obj.strftime("%Y-%m")
    worksheet = get_or_create_worksheet(sh, sheet_name)
    unique_id = str(uuid.uuid4())
    date_str = date_obj.strftime("%Y-%m-%d")
    
    row_data = [date_str, record_type, category, amount, payment_method, note, unique_id]
    worksheet.append_row(row_data)
    st.cache_data.clear()

def delete_transaction(sheet_name, target_id):
    sh = get_spreadsheet()
    if not sh: return
    try:
        worksheet = sh.worksheet(sheet_name)
        cell = worksheet.find(target_id)
        worksheet.delete_rows(cell.row)
        st.cache_data.clear()
    except Exception as e:
        st.error(f"刪除失敗：{e}")

def update_transaction_batch(edited_df, original_df):
    sh = get_spreadsheet()
    if not sh: return
    
    original_map = original_df.set_index('id').to_dict('index')
    changes_count = 0
    progress_bar = st.progress(0)
    total_rows = len(edited_df)
    
    for i, (index, row) in enumerate(edited_df.iterrows()):
        uid = row['id']
        if uid not in original_map: continue
            
        orig = original_map[uid]
        has_changed = (
            row['date'] != orig['date'] or 
            row['type'] != orig['type'] or
            row['category'] != orig['category'] or 
            row['amount'] != orig['amount'] or 
            row['payment_method'] != orig['payment_method'] or
            row['note'] != orig['note']
        )
        
        if has_changed:
            origin_sheet_name = orig['_sheet_name']
            new_sheet_name = row['date'].strftime("%Y-%m")
            needs_move = (new_sheet_name != origin_sheet_name)
            
            # 🔥 增加緩衝：每次寫入前等待 0.5 秒，避免觸發 Rate Limit
            time.sleep(0.5) 
            
            if needs_move:
                try:
                    old_ws = sh.worksheet(origin_sheet_name)
                    cell = old_ws.find(uid)
                    old_ws.delete_rows(cell.row)
                    
                    time.sleep(0.5) # 再次緩衝

                    new_ws = get_or_create_worksheet(sh, new_sheet_name)
                    new_ws.append_row([
                        row['date'].strftime("%Y-%m-%d"),
                        row['type'],
                        row['category'],
                        float(row['amount']),
                        row['payment_method'],
                        row['note'],
                        uid
                    ])
                    changes_count += 1
                except Exception as e:
                    st.error(f"搬移失敗 (ID: {uid}): {e}")
            else:
                try:
                    ws = sh.worksheet(origin_sheet_name)
                    cell = ws.find(uid)
                    row_num = cell.row
                    new_values = [
                        row['date'].strftime("%Y-%m-%d"),
                        row['type'],
                        row['category'],
                        float(row['amount']),
                        row['payment_method'],
                        row['note']
                    ]
                    ws.update(range_name=f"A{row_num}:F{row_num}", values=[new_values])
                    changes_count += 1
                except Exception as e:
                    st.error(f"更新失敗 (ID: {uid}): {e}")
        progress_bar.progress((i + 1) / total_rows)

    if changes_count > 0:
        st.success(f"✅ 成功更新 {changes_count} 筆資料！")
        st.cache_data.clear()
        # 🔥 延長等待時間至 2 秒，確保 API 冷卻
        time.sleep(2) 
        st.rerun()
    else:
        st.info("沒有檢測到任何變更。")

def calculate_billing_cycle(row):
    if row['type'] == '收入': return "N/A"
    pm = row['payment_method']
    date = row['date']
    cutoff_day = CREDIT_CARDS.get(pm, 0)
    
    if cutoff_day == 0: return "當下結清"
    
    if date.day <= cutoff_day:
        return f"{date.year}-{date.month:02d}月帳單"
    else:
        next_month_date = date.replace(day=1) + timedelta(days=32)
        return f"{next_month_date.year}-{next_month_date.month:02d}月帳單"

# --- 4. 主程式介面 ---

if st.sidebar.button("🔒 登出系統"):
    st.session_state.logged_in = False
    st.rerun()

df = get_data()

# --- 側邊欄 ---
st.sidebar.header("📝 新增交易")
record_type = st.sidebar.radio("類型", ["支出", "收入"], horizontal=True)

with st.sidebar.form("expense_form", clear_on_submit=True):
    date = st.date_input("日期", datetime.now())
    
    if record_type == "支出":
        cat_options = ["飲食", "交通", "娛樂", "購物", "居住", "醫療", "投資", "寵物", "進修", "其他"]
        payment_method = st.selectbox("付款方式", options=list(CREDIT_CARDS.keys()))
    else:
        cat_options = ["薪資", "獎金", "投資收益", "退款", "兼職", "其他"]
        payment_method = st.selectbox("入帳方式", ["現金", "銀行轉帳"])
        
    category = st.selectbox("類別", cat_options)
    amount = st.number_input("金額", min_value=0.0, step=10.0, format="%.0f")
    note = st.text_input("備註 (選填)")
    submitted = st.form_submit_button("提交")

    if submitted:
        if amount > 0:
            with st.spinner("正在寫入雲端..."):
                add_transaction(date, record_type, category, amount, payment_method, note)
            st.sidebar.success(f"已新增！")
            st.rerun()
        else:
            st.sidebar.error("金額必須大於 0")

st.sidebar.markdown("---")
st.sidebar.header("🗑️ 快速刪除")

if not df.empty and 'id' in df.columns:
    delete_df = df.sort_values(by='date', ascending=False).head(10)
    delete_options = {}
    for index, row in delete_df.iterrows():
        icon = "🔴" if row.get('type') == '支出' else "🟢"
        pm_short = row.get('payment_method', '')[:2] 
        label = f"{icon} {row['date']} {pm_short} - {row['category']} ${row['amount']}"
        delete_options[label] = (row['_sheet_name'], row['id'])
    
    selected_label = st.sidebar.selectbox("選擇項目", options=list(delete_options.keys()))
    
    if st.sidebar.button("確認刪除"):
        target_sheet, target_id = delete_options[selected_label]
        with st.spinner("正在刪除..."):
            delete_transaction(target_sheet, target_id)
        st.sidebar.success