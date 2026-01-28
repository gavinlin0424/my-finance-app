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
st.set_page_config(page_title="個人理財管家 Pro Max", page_icon="💳", layout="wide")

# ==========================================
# 🔐 安全登入系統
# ==========================================
if 'logged_in' not in st.session_state:
    st.session_state.logged_in = False

def login():
    col1, col2, col3 = st.columns([1,2,1])
    with col2:
        st.title("🔒 請登入系統")
        password = st.text_input("請輸入密碼", type="password")
        if st.button("登入", use_container_width=True):
            if password == "pcgi1835":
                st.session_state.logged_in = True
                st.rerun()
            else:
                st.error("❌ 密碼錯誤")

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
# 這個函式負責連線，使用 cache_resource 保持連線物件
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

def get_spreadsheet():
    """取得 Spreadsheet 物件，包含重試機制"""
    client = get_google_sheet_client()
    if not client: return None
    
    for attempt in range(3):
        try:
            return client.open("my_expenses_db")
        except gspread.exceptions.APIError:
            time.sleep(2 + random.random())
            continue
        except Exception:
            return None
    return None

# --- 3. 核心功能：讀取、寫入、更新 ---

# 🔥 重點修正：加上 @st.cache_data (TTL=60秒)
# 這會把讀取到的資料暫存在記憶體 60 秒，避免你每動一下滑鼠就重新讀取一次 Google Sheet
@st.cache_data(ttl=60, show_spinner="正在從雲端下載資料...")
def get_data():
    sh = get_spreadsheet()
    if not sh: return pd.DataFrame()

    try:
        all_worksheets = sh.worksheets()
    except Exception:
        # 如果連線失敗，回傳空 DataFrame 避免程式崩潰
        return pd.DataFrame(columns=EXPECTED_HEADERS + ['_sheet_name'])

    all_data = []

    for worksheet in all_worksheets:
        try:
            rows = worksheet.get_all_values()
        except Exception:
            continue

        if len(rows) <= 1: continue 
            
        headers = rows[0]
        # 簡單檢查標題
        if "id" not in headers or "date" not in headers: continue

        sheet_data = rows[1:]
        
        for row in sheet_data:
            # 補齊欄位長度避免錯誤
            if len(row) < len(headers):
                row += [""] * (len(headers) - len(row))
            
            row_dict = dict(zip(headers, row))
            row_dict['_sheet_name'] = worksheet.title
            
            # 防呆預設值
            if 'type' not in row_dict: row_dict['type'] = '支出'
            if 'payment_method' not in row_dict: row_dict['payment_method'] = '現金'
            if 'category' not in row_dict: row_dict['category'] = '其他'
                
            all_data.append(row_dict)
            
    if not all_data:
        return pd.DataFrame(columns=EXPECTED_HEADERS + ['_sheet_name'])

    df = pd.DataFrame(all_data)
    
    # 確保所有必要欄位都在
    for col in EXPECTED_HEADERS:
        if col not in df.columns: df[col] = ""

    # 型別轉換
    df['amount'] = pd.to_numeric(df['amount'], errors='coerce').fillna(0)
    df['date'] = pd.to_datetime(df['date'], errors='coerce').dt.date
    
    return df

def get_or_create_worksheet(sh, sheet_name):
    try:
        worksheet = sh.worksheet(sheet_name)
    except gspread.exceptions.WorksheetNotFound:
        time.sleep(1)
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
    
    # 🔥 重要：寫入後清除快取，這樣下次讀取才會看到新資料
    get_data.clear()

def delete_transaction(sheet_name, target_id):
    sh = get_spreadsheet()
    if not sh: return
    try:
        worksheet = sh.worksheet(sheet_name)
        cell = worksheet.find(target_id)
        worksheet.delete_rows(cell.row)
        # 🔥 清除快取
        get_data.clear()
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
            
            time.sleep(0.5) 
            
            if needs_move:
                try:
                    old_ws = sh.worksheet(origin_sheet_name)
                    cell = old_ws.find(uid)
                    old_ws.delete_rows(cell.row)
                    time.sleep(0.5)
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
                    st.error(f"搬移失敗: {e}")
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
                    st.error(f"更新失敗: {e}")
        progress_bar.progress((i + 1) / total_rows)

    if changes_count > 0:
        st.success(f"✅ 成功更新 {changes_count} 筆資料！")
        # 🔥 清除快取並重整
        get_data.clear()
        time.sleep(1) 
        st.rerun()
    else:
        st.info("沒有檢測到任何變更。")

def calculate_billing_cycle(row):
    if row['type'] == '收入': return "N/A"
    pm = row.get('payment_method', '現金')
    date = row['date']
    # 防呆：確保 date 不是 NaT (非時間格式)
    if pd.isnull(date): return "日期錯誤"
    
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

# 這裡會使用 Cache，如果之前讀過就不會再連線，速度會變快
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

# 加入錯誤處理，避免因為資料格式錯誤導致側邊欄當機
try:
    if not df.empty and 'id' in df.columns:
        delete_df = df.sort_values(by='date', ascending=False).head(10)
        delete_options = {}
        for index, row in delete_df.iterrows():
            icon = "🔴" if row.get('type') == '支出' else "🟢"
            # 安全取得字串
            pm = str(row.get('payment_method', ''))
            pm_short = pm[:2] if pm else ""
            cat = str(row.get('category', ''))
            amt = row.get('amount', 0)
            
            label = f"{icon} {row['date']} {pm_short} - {cat} ${amt}"
            delete_options[label] = (row.get('_sheet_name'), row.get('id'))
        
        selected_label = st.sidebar.selectbox("選擇項目", options=list(delete_options.keys()))
        
        if st.sidebar.button("確認刪除"):
            if selected_label:
                target_sheet, target_id = delete_options[selected_label]
                with st.spinner("正在刪除..."):
                    delete_transaction(target_sheet, target_id)
                st.sidebar.success("刪除成功！")
                st.rerun()
except Exception as e:
    st.sidebar.error(f"刪除選單載入錯誤: {e}")

st.sidebar.markdown("---")
st.sidebar.header("⚙️ 設定")
budget = st.sidebar.number_input("本月支出預算", min_value=1000, value=20000, step=500)

# --- 主畫面儀表板 ---
st.title("💳 智慧理財管家 (信用卡版)")

if df is None:
    st.error("⚠️ 資料讀取發生錯誤，請稍後再試。")
elif df.empty:
    st.info("💡 目前還沒有任何資料，請從左側新增第一筆！")
else:
    # 正常顯示內容
    stats_df = df.copy()
    stats_df['date'] = pd.to_datetime(stats_df['date'])
    stats_df['month_str'] = stats_df['date'].dt.strftime("%Y-%m")
    
    if 'payment_method' not in stats_df.columns: stats_df['payment_method'] = '現金'
    if 'type' not in stats_df.columns: stats_df['type'] = '支出'

    stats_df['billing_cycle'] = stats_df.apply(calculate_billing_cycle, axis=1)

    current_month_str = datetime.now().strftime("%Y-%m")
    available_months = sorted(stats_df['month_str'].unique(), reverse=True)
    if current_month_str not in available_months:
        available_months.insert(0, current_month_str)
        
    selected_month = st.selectbox("📅 選擇分析月份", available_months, index=0)
    current_month_df = stats_df[stats_df['month_str'] == selected_month]
    
    income_df = current_month_df[current_month_df['type'] == '收入']
    expense_df = current_month_df[current_month_df['type'] == '支出']
    
    total_income = income_df['amount'].sum()
    total_expense = expense_df['amount'].sum()
    net_balance = total_income - total_expense
    remaining_budget = budget - total_expense
    
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("總收入", f"${total_income:,.0f}")
    col2.metric("總支出", f"${total_expense:,.0f}", delta=f"-{total_expense:,.0f}", delta_color="inverse")
    col3.metric("本月淨利", f"${net_balance:,.0f}", delta_color="normal" if net_balance >= 0 else "inverse")
    col4.metric("剩餘預算", f"${remaining_budget:,.0f}", delta_color="normal" if remaining_budget > 0 else "inverse")
    
    st.markdown("---")

    c1, c2 = st.columns(2)
    with c1:
        st.subheader(f"📊 {selected_month} 付款方式占比")
        if not expense_df.empty:
            pay_stats = expense_df.groupby('payment_method')['amount'].sum().reset_index()
            fig_pie = px.pie(
                pay_stats, 
                values='amount', 
                names='payment_method', 
                title='錢都花哪張卡？', 
                hole=0.4,
                color='payment_method', 
                color_discrete_map=PAYMENT_COLORS 
            )
            st.plotly_chart(fig_pie, use_container_width=True)
        else:
            st.info("本月尚無支出")

    with c2:
        st.subheader(f"📈 {selected_month} 支出類別")
        if not expense_df.empty:
            fig_bar = px.bar(
                expense_df, 
                x='category', 
                y='amount', 
                color='payment_method', 
                title='各類別花費與支付方式',
                color_discrete_map=PAYMENT_COLORS 
            )
            st.plotly_chart(fig_bar, use_container_width=True)
        else:
            st.info("本月尚無資料")

    st.markdown("---")
    st.subheader("📋 詳細記錄 & 帳單歸屬推算")
    st.caption("💡 系統會根據結帳日，自動推算這筆消費屬於哪個月的信用卡帳單")

    display_df = stats_df.sort_values(by='date', ascending=False)
    
    all_categories = [
        "飲食", "交通", "娛樂", "購物", "居住", "醫療", "投資", "寵物", "進修", 
        "薪資", "獎金", "投資收益", "退款", "兼職", "其他"
    ]
    all_payment_methods = list(CREDIT_CARDS.keys()) + ["銀行轉帳"]

    edited_df = st.data_editor(
        display_df,
        column_config={
            "id": None, 
            "_sheet_name": None,
            "billing_cycle": st.column_config.TextColumn("帳單歸屬", disabled=True),
            "date": st.column_config.DateColumn("日期", format="YYYY-MM-DD"),
            "type": st.column_config.SelectboxColumn("類型", options=["支出", "收入"], required=True, width="small"),
            "category": st.column_config.SelectboxColumn("類別", options=all_categories, required=True),
            "payment_method": st.column_config.SelectboxColumn("付款方式", options=all_payment_methods, required=True, width="medium"),
            "amount": st.column_config.NumberColumn("金額", format="$ %.0f"),
            "note": st.column_config.TextColumn("備註"),
        },
        use_container_width=True,
        num_rows="fixed",
        hide_index=True,
        key="data_editor"
    )

    if st.button("💾 儲存修改", use_container_width=True):
        with st.spinner("正在更新... (為確保穩定，動作會稍慢)"):
            update_transaction_batch(edited_df, df)