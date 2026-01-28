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

# 🔥 新增：取得或建立設定分頁 (用來存預算)
def get_settings_worksheet(sh):
    try:
        ws = sh.worksheet("settings")
    except gspread.exceptions.WorksheetNotFound:
        # 如果沒有 settings 分頁，就建立一個，並寫入預設值
        ws = sh.add_worksheet(title="settings", rows=20, cols=2)
        ws.append_row(["key", "value"])
        ws.append_row(["budget", "20000"])
    return ws

# 🔥 新增：讀取預算 (有 Cache)
@st.cache_data(ttl=300) # 設定 5 分鐘快取，不需要頻繁讀取
def get_budget_setting():
    sh = get_spreadsheet()
    if not sh: return 20000.0
    
    try:
        ws = get_settings_worksheet(sh)
        # 讀取所有設定
        records = ws.get_all_records()
        # 尋找 key 為 budget 的那一行
        for item in records:
            if item.get('key') == 'budget':
                return float(item.get('value', 20000))
    except Exception:
        pass
    
    return 20000.0 # 預設值

# 🔥 新增：更新預算
def update_budget_setting(new_budget):
    sh = get_spreadsheet()
    if not sh: return
    
    try:
        ws = get_settings_worksheet(sh)
        # 找到 'budget' 所在的儲存格
        cell = ws.find("budget")
        # 更新它右邊那一格 (B欄) 的值
        ws.update_cell(cell.row, cell.col + 1, str(new_budget))
        
        # 清除讀取快取，確保下次讀到新的
        get_budget_setting.clear()
    except Exception as e:
        st.error(f"預算儲存失敗: {e}")

@st.cache_data(ttl=60, show_spinner="正在從雲端下載資料...")
def get_data():
    sh = get_spreadsheet()
    if not sh: return pd.DataFrame()

    try:
        all_worksheets = sh.worksheets()
    except Exception:
        return pd.DataFrame(columns=EXPECTED_HEADERS + ['_sheet_name'])

    all_data = []

    for worksheet in all_worksheets:
        # 跳過 settings 分頁，不要把它當成帳務資料讀進來
        if worksheet.title == "settings":
            continue

        try:
            rows = worksheet.get_all_values()
        except Exception:
            continue

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
            if 'category' not in row_dict: row_dict['category'] = '其他'
                
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
    get_data.clear()

def delete_transaction(sheet_name, target_id):
    sh = get_spreadsheet()
    if not sh: return
    try:
        worksheet = sh.worksheet(sheet_name)
        cell = worksheet.find(target_id)
        worksheet.delete_rows(cell.row)
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
        get_data.clear()
        time.sleep(1) 
        st.rerun()
    else:
        st.info("沒有檢測到任何變更。")

def calculate_billing_cycle(row):
    if row['type'] == '收入': return "N/A"
    pm = row.get('payment_method', '現金')
    date = row['date']
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

try:
    if not df.empty and 'id' in df.columns:
        delete_df = df.sort_values(by='date', ascending=False).head(10)
        delete_options = {}
        for index, row in delete_df.iterrows():
            icon = "🔴" if row.get('type') == '支出' else "🟢"
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

# 🔥 修正：從雲端讀取目前的預算 (而不是寫死 20000)
current_budget_setting = get_budget_setting()

# 讓使用者輸入新預算
new_budget_input = st.sidebar.number_input(
    "本月支出預算", 
    min_value=1000.0, 
    value=float(current_budget_setting), 
    step=500.0,
    format="%.0f"
)

# 🔥 增加一個按鈕來儲存預算，避免每次打字都觸發 API 導致卡頓
if st.sidebar.button("💾 更新預算設定"):
    if new_budget_input != current_budget_setting:
        with st.spinner("正在儲存新預算..."):
            update_budget_setting(new_budget_input)
        st.sidebar.success(f"預算已更新為 ${new_budget_input:,.0f}")
        time.sleep(1)
        st.rerun()
    else:
        st.sidebar.info("預算未變更")

# 設定變數給下方使用
budget = new_budget_input

# --- 主畫面儀表板 ---
st.title("💳 智慧理財管家 (信用卡版)")

if df is None:
    st.error("⚠️ 資料讀取發生錯誤，請稍後再試。")
elif df.empty:
    st.info("💡 目前還沒有任何資料，請從左側新增第一筆！")
else:
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

    # ==========================================
    # 🔥 新增功能區塊：多週期趨勢分析
    # ==========================================
    st.markdown("---")
    st.subheader("📈 長期收支趨勢分析")
    
    # 1. 週期選擇器
    trend_period = st.radio("選擇統計週期", ["日", "週", "月", "季"], horizontal=True, key="trend_period")
    
    # 2. 資料準備
    trend_df = stats_df.copy()
    # 確保是 timestamp 格式以便進行 Resample
    trend_df['date'] = pd.to_datetime(trend_df['date'])
    
    freq_map = {"日": "D", "週": "W-MON", "月": "MS", "季": "QS"}
    freq = freq_map[trend_period]
    
    # 3. 聚合計算 (Grouping)
    try:
        # 依照選擇的頻率 (freq) 和 類型 (type) 進行加總
        trend_grouped = trend_df.groupby([pd.Grouper(key='date', freq=freq), 'type'])['amount'].sum().reset_index()
        trend_grouped = trend_grouped.sort_values('date')
        
        # 產生顯示用的日期字串
        if trend_period == "日":
            trend_grouped['date_str'] = trend_grouped['date'].dt.strftime('%Y-%m-%d')
        elif trend_period == "週":
            trend_grouped['date_str'] = trend_grouped['date'].dt.strftime('%Y-%m-%d (週)')
        elif trend_period == "月":
            trend_grouped['date_str'] = trend_grouped['date'].dt.strftime('%Y-%m')
        elif trend_period == "季":
            trend_grouped['date_str'] = trend_grouped['date'].apply(lambda x: f"{x.year}-Q{(x.month-1)//3 + 1}")

        # 4. 繪製趨勢圖
        fig_trend = px.bar(
            trend_grouped, 
            x='date_str', 
            y='amount', 
            color='type', 
            barmode='group',
            title=f'各{trend_period}收支總額統計',
            labels={'date_str': '時間區間', 'amount': '金額', 'type': '類型'},
            color_discrete_map={'支出': '#EF553B', '收入': '#00CC96'}
        )
        st.plotly_chart(fig_trend, use_container_width=True)
        
        # 5. 詳細報表表格
        with st.expander(f"📊 查看 {trend_period} 詳細報表"):
            # 轉置表格：日期為列，收入/支出為欄
            pivot_df = trend_grouped.pivot(index='date_str', columns='type', values='amount').fillna(0)
            # 計算淨利
            pivot_df['淨利 (Net)'] = pivot_df.get('收入', 0) - pivot_df.get('支出', 0)
            # 排序：最新的在上面
            pivot_df = pivot_df.sort_index(ascending=False)
            
            # 美化表格顯示
            st.dataframe(pivot_df.style.format("{:,.0f}").background_gradient(subset=['淨利 (Net)'], cmap="RdYlGn", vmin=-5000, vmax=5000))
            
    except Exception as e:
        st.info("資料不足以進行此週期的趨勢分析。")

    # ==========================================
    # 結束新增區塊
    # ==========================================

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