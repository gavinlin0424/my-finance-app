import streamlit as st
import pandas as pd
import plotly.express as px
from datetime import datetime, timedelta
import gspread
from google.oauth2.service_account import Credentials
import uuid
import time

# --- 1. 設定頁面配置 ---
st.set_page_config(page_title="個人理財管家 Pro Max", page_icon="💳", layout="wide")

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

# 信用卡選項與結帳日設定
CREDIT_CARDS = {
    "現金": 0,
    "聯邦 (結帳19日)": 19,
    "兆豐-linepay (結帳5日)": 5,
    "台新黑狗 (結帳2日)": 2,
    "中信 (結帳12日)": 12
}

# --- 🎨 新增：指定顏色配置 (Color Mapping) ---
# 這裡強制規定每一種付款方式的顏色，避免混淆
PAYMENT_COLORS = {
    "現金": "#00CC96",             # 綠色
    "聯邦 (結帳19日)": "#636EFA",   # 藍色
    "兆豐-linepay (結帳5日)": "#AB63FA", # 紫色
    "台新黑狗 (結帳2日)": "#EF553B", # 紅色
    "中信 (結帳12日)": "#FFA15A",   # 橘色
    "銀行轉帳": "#7F7F7F",         # 灰色
    "其他": "#BAB0AC"              # 淺灰
}

# 完整的欄位順序
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

def get_spreadsheet():
    client = get_google_sheet_client()
    if client:
        return client.open("my_expenses_db")
    return None

# --- 3. 核心功能：讀取、寫入、更新 ---

def get_data():
    sh = get_spreadsheet()
    if not sh: return pd.DataFrame()

    all_worksheets = sh.worksheets()
    all_data = []

    for worksheet in all_worksheets:
        rows = worksheet.get_all_values()
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
            
            if needs_move:
                try:
                    old_ws = sh.worksheet(origin_sheet_name)
                    cell = old_ws.find(uid)
                    old_ws.delete_rows(cell.row)
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
        time.sleep(1)
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
        st.sidebar.success("刪除成功！")
        st.rerun()

st.sidebar.markdown("---")
st.sidebar.header("⚙️ 設定")
budget = st.sidebar.number_input("本月支出預算", min_value=1000, value=20000, step=500)

# --- 主畫面儀表板 ---
st.title("💳 智慧理財管家 (信用卡版)")

if not df.empty:
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
            # 修改：加入 color 與 color_discrete_map 指定顏色
            fig_pie = px.pie(
                pay_stats, 
                values='amount', 
                names='payment_method', 
                title='錢都花哪張卡？', 
                hole=0.4,
                color='payment_method', # 指定顏色依據欄位
                color_discrete_map=PAYMENT_COLORS # 傳入我們定義好的顏色
            )
            st.plotly_chart(fig_pie, use_container_width=True)
        else:
            st.info("本月尚無支出")

    with c2:
        st.subheader(f"📈 {selected_month} 支出類別")
        if not expense_df.empty:
            # 修改：加入 color 與 color_discrete_map 指定顏色
            fig_bar = px.bar(
                expense_df, 
                x='category', 
                y='amount', 
                color='payment_method', # 堆疊顏色依據
                title='各類別花費與支付方式',
                color_discrete_map=PAYMENT_COLORS # 傳入我們定義好的顏色
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
            "billing_cycle": st.column_config.TextColumn("帳單歸屬 (自動推算)", disabled=True),
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

    if st.button("💾 儲存修改"):
        with st.spinner("正在更新..."):
            update_transaction_batch(edited_df, df)

else:
    st.info("💡 資料庫是空的，請開始記帳吧！")