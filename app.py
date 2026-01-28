import streamlit as st
import pandas as pd
import plotly.express as px
from datetime import datetime, timedelta
from dateutil.relativedelta import relativedelta # 需要 pip install python-dateutil
import gspread
from google.oauth2.service_account import Credentials
import uuid
import time
import random
import json

# --- 1. 設定頁面配置 ---
st.set_page_config(page_title="個人理財管家", page_icon="💎", layout="wide")

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
# ⚙️ 系統常數與設定
# ==========================================

# 擴充：加入「繳款日(後推天數)」的概念 (Gap Days)
# 例如：結帳日是 19 號，通常 +15~20 天是繳款截止日
CREDIT_CARDS_CONFIG = {
    "現金": {"cutoff": 0, "gap": 0, "color": "#00CC96"},
    "聯邦": {"cutoff": 19, "gap": 15, "color": "#636EFA"},
    "兆豐-LinePay": {"cutoff": 5, "gap": 15, "color": "#AB63FA"},
    "台新黑狗": {"cutoff": 2, "gap": 15, "color": "#EF553B"},
    "中信": {"cutoff": 12, "gap": 20, "color": "#FFA15A"},
    "銀行轉帳": {"cutoff": 0, "gap": 0, "color": "#7F7F7F"},
    "其他": {"cutoff": 0, "gap": 0, "color": "#BAB0AC"}
}

# 增加 tags 和 cash_flow_date 欄位
EXPECTED_HEADERS = ["date", "cash_flow_date", "type", "category", "amount", "payment_method", "tags", "note", "id"]

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

# ==========================================
# 🛠️ 進階功能：設定管理 (解決硬編碼與預算時空矛盾)
# ==========================================

def init_settings_sheet(sh):
    """初始化設定分頁，儲存類別與每月預算"""
    try:
        ws = sh.worksheet("app_settings")
    except gspread.exceptions.WorksheetNotFound:
        ws = sh.add_worksheet(title="app_settings", rows=100, cols=3)
        ws.append_row(["section", "key", "value"])
        # 預設類別
        default_cats = "飲食,交通,娛樂,購物,居住,醫療,投資,寵物,進修,其他"
        default_income_cats = "薪資,獎金,投資收益,退款,兼職,其他"
        ws.append_row(["categories", "expense", default_cats])
        ws.append_row(["categories", "income", default_income_cats])
        # 預設 2026-01 預算
        ws.append_row(["budget", "2026-01", "20000"])
    return ws

@st.cache_data(ttl=60)
def get_app_settings():
    """讀取所有設定：類別、預算"""
    sh = get_spreadsheet()
    if not sh: return {}, {}, {}
    
    ws = init_settings_sheet(sh)
    records = ws.get_all_records()
    
    expense_cats = []
    income_cats = []
    monthly_budgets = {}
    
    for row in records:
        if row['section'] == 'categories':
            if row['key'] == 'expense':
                expense_cats = row['value'].split(',')
            elif row['key'] == 'income':
                income_cats = row['value'].split(',')
        elif row['section'] == 'budget':
            monthly_budgets[row['key']] = float(row['value'])
            
    return expense_cats, income_cats, monthly_budgets

def update_monthly_budget(month_str, amount):
    """更新特定月份的預算"""
    sh = get_spreadsheet()
    ws = init_settings_sheet(sh)
    
    # 搜尋是否已有該月設定
    cell = ws.find(month_str)
    if cell:
        # 如果有，更新 C 欄 (Value)
        ws.update_cell(cell.row, 3, str(amount))
    else:
        # 如果沒有，新增一行
        ws.append_row(["budget", month_str, str(amount)])
    
    get_app_settings.clear()

def add_new_category(cat_type, new_cat):
    """新增類別"""
    sh = get_spreadsheet()
    ws = init_settings_sheet(sh)
    
    # 找到對應的列
    cell_key = ws.find(cat_type, in_column=2) # 找 key column
    if cell_key:
        # 讀取舊值
        current_val = ws.cell(cell_key.row, 3).value
        if new_cat not in current_val:
            new_val = current_val + "," + new_cat
            ws.update_cell(cell_key.row, 3, new_val)
            get_app_settings.clear()
            return True
    return False

# ==========================================
# 🧮 核心邏輯：現金流與日期計算
# ==========================================

def calculate_cash_flow_info(date_obj, payment_method):
    """
    計算現金流日期 (Cash Flow Date) 與 繳款截止日
    """
    config = CREDIT_CARDS_CONFIG.get(payment_method, CREDIT_CARDS_CONFIG["其他"])
    cutoff = config['cutoff']
    gap = config['gap']
    
    if cutoff == 0:
        # 現金或即時扣款
        return date_obj, "當下結清"
    
    # 信用卡邏輯
    # 如果消費日 <= 結帳日，則歸屬「當月帳單」
    # 如果消費日 > 結帳日，則歸屬「下月帳單」
    if date_obj.day <= cutoff:
        billing_month = date_obj
    else:
        billing_month = date_obj + relativedelta(months=1)
        
    # 推算結帳日日期 (例如 1月19日)
    # 注意：需處理 2月沒有 30號的情況 (雖結帳日通常固定，但這裡簡化處理)
    try:
        billing_date = billing_month.replace(day=cutoff)
    except ValueError:
        # 如果該月沒有這一天 (例如2月沒有30號)，取該月最後一天
        billing_date = billing_month + relativedelta(day=31)
        
    # 現金流日期 (繳款日) = 結帳日 + Gap Days
    cash_flow_date = billing_date + timedelta(days=gap)
    
    return cash_flow_date, f"{billing_month.strftime('%Y-%m')} 帳單 (繳款日: {cash_flow_date.strftime('%m/%d')})"

# --- 3. 核心功能：讀取、寫入、更新 ---

@st.cache_data(ttl=60, show_spinner="正在同步雲端資料...")
def get_data():
    sh = get_spreadsheet()
    if not sh: return pd.DataFrame()

    try:
        all_worksheets = sh.worksheets()
    except Exception:
        return pd.DataFrame(columns=EXPECTED_HEADERS + ['_sheet_name'])

    all_data = []

    for worksheet in all_worksheets:
        # 跳過設定頁
        if worksheet.title == "app_settings": continue

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
            
            # 欄位補全
            if 'cash_flow_date' not in row_dict or not row_dict['cash_flow_date']:
                # 舊資料相容：如果沒有現金流日期，暫時用消費日代替
                row_dict['cash_flow_date'] = row_dict['date']
            if 'tags' not in row_dict: row_dict['tags'] = ""
                
            all_data.append(row_dict)
            
    if not all_data:
        return pd.DataFrame(columns=EXPECTED_HEADERS + ['_sheet_name'])

    df = pd.DataFrame(all_data)
    for col in EXPECTED_HEADERS:
        if col not in df.columns: df[col] = ""

    df['amount'] = pd.to_numeric(df['amount'], errors='coerce').fillna(0)
    df['date'] = pd.to_datetime(df['date'], errors='coerce').dt.date
    df['cash_flow_date'] = pd.to_datetime(df['cash_flow_date'], errors='coerce').dt.date
    
    return df

def get_or_create_worksheet(sh, sheet_name):
    try:
        worksheet = sh.worksheet(sheet_name)
        # 檢查是否需要更新標題 (如果新增了欄位)
        headers = worksheet.row_values(1)
        if "tags" not in headers:
             # 簡單處理：如果舊表沒新欄位，這裡不做 migrate，只在讀取時防呆
             # 若要嚴謹應在此時 append column
             pass
    except gspread.exceptions.WorksheetNotFound:
        time.sleep(1)
        worksheet = sh.add_worksheet(title=sheet_name, rows=100, cols=12)
        worksheet.append_row(EXPECTED_HEADERS)
    return worksheet

def add_transaction(date_obj, record_type, category, amount, payment_method, note, tags, installment_months=1):
    """
    新增交易，支援分期付款生成 (Installments)
    """
    sh = get_spreadsheet()
    if not sh: return

    # 計算單期金額 (四捨五入)
    monthly_amount = round(amount / installment_months)
    
    # 批次寫入資料準備
    operations = [] # (sheet_name, row_data)

    current_date = date_obj
    base_uuid = str(uuid.uuid4()) # 用來標記同一筆分期的 ID

    for i in range(installment_months):
        # 計算這一期的現金流日期
        cf_date, _ = calculate_cash_flow_info(current_date, payment_method)
        
        # 分期備註
        final_note = note
        final_tags = tags
        if installment_months > 1:
            final_note = f"{note} ({i+1}/{installment_months})"
            final_tags = f"{tags},#分期"
        
        # 決定分頁名稱 (依照消費日歸檔)
        sheet_name = current_date.strftime("%Y-%m")
        unique_id = str(uuid.uuid4())
        
        row_data = [
            current_date.strftime("%Y-%m-%d"),
            cf_date.strftime("%Y-%m-%d"), # Cash Flow Date
            record_type,
            category,
            monthly_amount,
            payment_method,
            final_tags,
            final_note,
            unique_id
        ]
        
        operations.append((sheet_name, row_data))
        
        # 日期推移到下個月
        current_date = current_date + relativedelta(months=1)

    # 執行寫入
    for sheet_name, row in operations:
        ws = get_or_create_worksheet(sh, sheet_name)
        ws.append_row(row)
        time.sleep(0.5) # 避免 Rate Limit

    get_data.clear()

def safe_update_transaction(edited_row, original_row, sh):
    """
    安全性更新：先寫入新資料，確認無誤後刪除舊資料 (Atomic-like)
    """
    uid = edited_row['id']
    origin_sheet_name = original_row['_sheet_name']
    new_sheet_name = edited_row['date'].strftime("%Y-%m")
    
    # 1. 計算新的 Cash Flow Date
    cf_date, _ = calculate_cash_flow_info(edited_row['date'], edited_row['payment_method'])
    
    new_values = [
        edited_row['date'].strftime("%Y-%m-%d"),
        cf_date.strftime("%Y-%m-%d"),
        edited_row['type'],
        edited_row['category'],
        float(edited_row['amount']),
        edited_row['payment_method'],
        edited_row['tags'],
        edited_row['note'],
        uid # ID 保持不變
    ]

    try:
        # A. 寫入新位置 (如果是同一個 Sheet，其實可以直接 Update，但為了統一邏輯，視為移動)
        # 如果 Sheet 沒變，我們用 Update Cell，如果變了，用 Append + Delete
        
        if new_sheet_name == origin_sheet_name:
            ws = sh.worksheet(origin_sheet_name)
            cell = ws.find(uid)
            # 更新 A:I (假設 ID 在 I)
            # 注意：gspread update 範圍需要精確
            # 這裡簡單作法：更新整列
            range_name = f"A{cell.row}:I{cell.row}"
            ws.update(range_name=range_name, values=[new_values])
        else:
            # 跨表移動：風險較高，採用兩段式
            new_ws = get_or_create_worksheet(sh, new_sheet_name)
            new_ws.append_row(new_values)
            
            # 確認寫入沒報錯後，刪除舊的
            time.sleep(1)
            old_ws = sh.worksheet(origin_sheet_name)
            old_cell = old_ws.find(uid)
            old_ws.delete_rows(old_cell.row)
            
        return True
    except Exception as e:
        st.error(f"更新失敗 ID {uid}: {e}")
        return False

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

# --- 4. 主程式介面 ---

if st.sidebar.button("🔒 登出系統"):
    st.session_state.logged_in = False
    st.rerun()

# 讀取設定與資料
expense_cats, income_cats, monthly_budgets = get_app_settings()
df = get_data()

# --- 側邊欄：新增交易 (功能升級) ---
st.sidebar.header("📝 新增交易")
record_type = st.sidebar.radio("類型", ["支出", "收入"], horizontal=True)

with st.sidebar.form("expense_form", clear_on_submit=True):
    date = st.date_input("交易日期", datetime.now())
    
    if record_type == "支出":
        cat_options = expense_cats
        payment_method = st.selectbox("付款方式", options=list(CREDIT_CARDS_CONFIG.keys()))
    else:
        cat_options = income_cats
        payment_method = st.selectbox("入帳方式", ["現金", "銀行轉帳"])
        
    category = st.selectbox("類別", cat_options)
    amount = st.number_input("金額", min_value=0.0, step=10.0, format="%.0f")
    note = st.text_input("備註")
    tags = st.text_input("標籤 (Tag)", placeholder="例如: #日本旅遊, #專案A")
    
    # 🔥 進階功能：分期付款
    is_installment = False
    installment_months = 1
    if record_type == "支出" and payment_method != "現金":
        is_installment = st.checkbox("設定分期付款 (自動生成未來帳務)")
        if is_installment:
            installment_months = st.number_input("分期期數", min_value=2, max_value=36, value=3)
    
    submitted = st.form_submit_button("提交")

    if submitted:
        if amount > 0:
            with st.spinner("正在寫入雲端..."):
                add_transaction(date, record_type, category, amount, payment_method, note, tags, installment_months)
            st.sidebar.success("已新增！")
            time.sleep(1)
            st.rerun()
        else:
            st.sidebar.error("金額必須大於 0")

# 🔥 側邊欄：新增類別 (解決硬編碼)
with st.sidebar.expander("⚙️ 管理類別"):
    new_cat_name = st.text_input("新增類別名稱")
    new_cat_type = st.radio("新增至", ["支出", "收入"], horizontal=True)
    if st.button("新增類別"):
        key = "expense" if new_cat_type == "支出" else "income"
        if add_new_category(key, new_cat_name):
            st.success(f"已新增 {new_cat_name}")
            st.rerun()
        else:
            st.warning("類別已存在")

# --- 主畫面 ---
st.title("💎 個人理財管家 Ultimate")

if df.empty:
    st.info("💡 目前沒有資料，請初始化您的第一筆帳務！(初次使用請稍等設定檔建立)")
else:
    stats_df = df.copy()
    stats_df['month_str'] = stats_df['date'].apply(lambda x: x.strftime("%Y-%m"))
    
    # 選擇月份
    current_month_str = datetime.now().strftime("%Y-%m")
    available_months = sorted(stats_df['month_str'].unique(), reverse=True)
    if current_month_str not in available_months: available_months.insert(0, current_month_str)
    
    col_filter1, col_filter2 = st.columns([1, 2])
    with col_filter1:
        selected_month = st.selectbox("📅 選擇月份", available_months)
    with col_filter2:
        tag_filter = st.text_input("🔍 標籤搜尋 (例如輸入 '旅遊')", "")

    # 資料篩選
    current_month_df = stats_df[stats_df['month_str'] == selected_month]
    if tag_filter:
        current_month_df = current_month_df[current_month_df['tags'].astype(str).str.contains(tag_filter)]

    # 取得當月預算 (解決時空矛盾)
    budget = monthly_budgets.get(selected_month, 20000) # 預設 20000

    # 計算統計
    total_income = current_month_df[current_month_df['type'] == '收入']['amount'].sum()
    total_expense = current_month_df[current_month_df['type'] == '支出']['amount'].sum()
    net_balance = total_income - total_expense
    remaining = budget - total_expense
    
    # 💰 指標顯示
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("總收入", f"${total_income:,.0f}")
    c2.metric("總支出", f"${total_expense:,.0f}", delta=f"-{total_expense:,.0f}", delta_color="inverse")
    c3.metric("本月淨利", f"${net_balance:,.0f}", delta_color="normal" if net_balance >= 0 else "inverse")
    c4.metric(f"預算 ({selected_month})", f"${remaining:,.0f}", delta=f"預算 ${budget:,.0f}")
    
    # 修改預算按鈕
    with st.expander("✏️ 修改本月預算"):
        new_budget_val = st.number_input("設定金額", value=float(budget), step=1000.0)
        if st.button("更新預算"):
            update_monthly_budget(selected_month, new_budget_val)
            st.success("預算已更新！")
            st.rerun()

    st.markdown("---")

    # 📊 圖表分析
    tab1, tab2, tab3 = st.tabs(["📊 收支概況", "💳 現金流分析 (New)", "🏷️ 專案/標籤分析"])
    
    with tab1:
        cc1, cc2 = st.columns(2)
        with cc1:
            if not current_month_df[current_month_df['type']=='支出'].empty:
                fig = px.pie(current_month_df[current_month_df['type']=='支出'], values='amount', names='category', title='支出類別占比', hole=0.4)
                st.plotly_chart(fig, use_container_width=True)
            else:
                st.info("無支出資料")
        with cc2:
            # 趨勢圖 (日/週/月)
            period = st.radio("趨勢週期", ["日", "週"], horizontal=True, key='trend_p')
            trend_df = current_month_df.copy()
            trend_df['date'] = pd.to_datetime(trend_df['date'])
            freq = 'D' if period == '日' else 'W-MON'
            
            try:
                g_df = trend_df.groupby([pd.Grouper(key='date', freq=freq), 'type'])['amount'].sum().reset_index()
                fig_trend = px.bar(g_df, x='date', y='amount', color='type', barmode='group', 
                                   color_discrete_map={'支出': '#EF553B', '收入': '#00CC96'})
                st.plotly_chart(fig_trend, use_container_width=True)
            except:
                st.info("資料不足")

    with tab2:
        st.caption("💡 這裡顯示的是『實際扣款日』，而非消費日。這能幫助你預判月底要準備多少現金繳卡費。")
        # 以 cash_flow_date 進行統計
        cf_df = current_month_df.copy()
        cf_df['day'] = pd.to_datetime(cf_df['cash_flow_date']).dt.day
        
        # 繪製現金流甘特圖概念或長條圖
        fig_cf = px.bar(cf_df[cf_df['type']=='支出'], x='cash_flow_date', y='amount', color='payment_method', 
                        title='未來30天現金流出預測 (依繳款日)',
                        labels={'cash_flow_date': '預計扣款日', 'amount': '扣款金額'})
        st.plotly_chart(fig_cf, use_container_width=True)

    with tab3:
        # 標籤雲與標籤統計
        tags_series = current_month_df['tags'].str.split(',').explode().str.strip()
        tags_series = tags_series[tags_series != ""] # 去除空標籤
        
        if not tags_series.empty:
            tag_counts = tags_series.value_counts().reset_index()
            tag_counts.columns = ['tag', 'count']
            
            # 計算每個 tag 的總花費
            tag_amounts = {}
            for tag in tag_counts['tag']:
                # 簡單模糊搜尋
                mask = current_month_df['tags'].astype(str).str.contains(tag)
                amt = current_month_df[mask & (current_month_df['type']=='支出')]['amount'].sum()
                tag_amounts[tag] = amt
            
            tag_counts['total_spent'] = tag_counts['tag'].map(tag_amounts)
            
            st.dataframe(tag_counts, use_container_width=True)
            fig_tag = px.bar(tag_counts, x='tag', y='total_spent', title='各專案/標籤總支出')
            st.plotly_chart(fig_tag, use_container_width=True)
        else:
            st.info("本月尚無設定標籤的交易")

    st.markdown("---")
    
    # 📋 資料編輯器
    st.subheader("📋 詳細記錄")
    
    # 準備編輯器的 Options
    all_cats = expense_cats + income_cats + ["其他"]
    all_pm = list(CREDIT_CARDS_CONFIG.keys())

    edited_df = st.data_editor(
        current_month_df.sort_values('date', ascending=False),
        column_config={
            "id": None, 
            "_sheet_name": None,
            "date": st.column_config.DateColumn("消費日期", format="YYYY-MM-DD"),
            "cash_flow_date": st.column_config.DateColumn("現金流/繳款日", disabled=True), # 自動計算，不給改
            "type": st.column_config.SelectboxColumn("類型", options=["支出", "收入"], required=True, width="small"),
            "category": st.column_config.SelectboxColumn("類別", options=all_cats, required=True),
            "payment_method": st.column_config.SelectboxColumn("付款方式", options=all_pm, required=True),
            "amount": st.column_config.NumberColumn("金額", format="$ %.0f"),
            "tags": st.column_config.TextColumn("標籤"),
            "note": st.column_config.TextColumn("備註"),
        },
        use_container_width=True,
        num_rows="fixed",
        hide_index=True,
        key="data_editor_main"
    )

    if st.button("💾 儲存變更 (Save Changes)"):
        with st.spinner("正在安全更新中..."):
            sh = get_spreadsheet()
            original_map = current_month_df.set_index('id').to_dict('index')
            changes = 0
            
            progress = st.progress(0)
            total = len(edited_df)
            
            for i, (idx, row) in enumerate(edited_df.iterrows()):
                uid = row['id']
                if uid not in original_map: continue
                orig = original_map[uid]
                
                # 檢查是否變更
                has_changed = (
                    row['date'] != orig['date'] or 
                    row['type'] != orig['type'] or 
                    row['category'] != orig['category'] or 
                    row['amount'] != orig['amount'] or 
                    row['payment_method'] != orig['payment_method'] or
                    row['tags'] != orig['tags'] or
                    row['note'] != orig['note']
                )
                
                if has_changed:
                    if safe_update_transaction(row, orig, sh):
                        changes += 1
                        
                progress.progress((i+1)/total)
                
            if changes > 0:
                st.success(f"成功更新 {changes} 筆資料")
                get_data.clear()
                time.sleep(1)
                st.rerun()
            else:
                st.info("無資料變更")