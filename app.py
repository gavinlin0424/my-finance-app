import streamlit as st
import pandas as pd
import plotly.express as px
from datetime import datetime, timedelta
from dateutil.relativedelta import relativedelta
import gspread
from google.oauth2.service_account import Credentials
import uuid
import time
import random
import json

# --- 1. 設定頁面配置 ---
st.set_page_config(page_title="個人理財管家 Ultimate", page_icon="💎", layout="wide")

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

CREDIT_CARDS_CONFIG = {
    "現金": {"cutoff": 0, "gap": 0, "color": "#00CC96"},
    "聯邦": {"cutoff": 19, "gap": 15, "color": "#636EFA"},
    "兆豐-LinePay": {"cutoff": 5, "gap": 15, "color": "#AB63FA"},
    "台新黑狗": {"cutoff": 2, "gap": 15, "color": "#EF553B"},
    "中信": {"cutoff": 12, "gap": 20, "color": "#FFA15A"},
    "銀行轉帳": {"cutoff": 0, "gap": 0, "color": "#7F7F7F"},
    "其他": {"cutoff": 0, "gap": 0, "color": "#BAB0AC"}
}

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
# 🛠️ 進階功能：設定管理 (類別、預算、訂閱)
# ==========================================

def init_settings_sheet(sh):
    """初始化設定分頁"""
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
        # 預設預算
        ws.append_row(["budget", "2026-01", "20000"])
    return ws

@st.cache_data(ttl=60)
def get_app_settings():
    """讀取所有設定：類別、預算、訂閱樣板"""
    sh = get_spreadsheet()
    if not sh: return {}, {}, {}, []
    
    ws = init_settings_sheet(sh)
    records = ws.get_all_records()
    
    expense_cats = []
    income_cats = []
    monthly_budgets = {}
    subscriptions = [] # 儲存訂閱樣板
    
    for row in records:
        section = row['section']
        if section == 'categories':
            if row['key'] == 'expense':
                expense_cats = row['value'].split(',')
            elif row['key'] == 'income':
                income_cats = row['value'].split(',')
        elif section == 'budget':
            monthly_budgets[row['key']] = float(row['value'])
        elif section == 'subscription':
            try:
                data = json.loads(row['value'])
                data['name'] = row['key']
                subscriptions.append(data)
            except:
                pass
            
    return expense_cats, income_cats, monthly_budgets, subscriptions

def update_monthly_budget(month_str, amount):
    """更新預算"""
    sh = get_spreadsheet()
    ws = init_settings_sheet(sh)
    cell = ws.find(month_str)
    if cell:
        ws.update_cell(cell.row, 3, str(amount))
    else:
        ws.append_row(["budget", month_str, str(amount)])
    get_app_settings.clear()

def add_new_category(cat_type, new_cat):
    """新增類別"""
    sh = get_spreadsheet()
    ws = init_settings_sheet(sh)
    cell_key = ws.find(cat_type, in_column=2)
    if cell_key:
        current_val = ws.cell(cell_key.row, 3).value
        if new_cat not in current_val:
            new_val = current_val + "," + new_cat
            ws.update_cell(cell_key.row, 3, new_val)
            get_app_settings.clear()
            return True
    return False

# 🔥 新增：訂閱/固定支出管理功能
def add_subscription_template(name, amount, category, payment_method, note):
    sh = get_spreadsheet()
    ws = init_settings_sheet(sh)
    
    value_data = {
        "amount": amount,
        "category": category,
        "payment_method": payment_method,
        "note": note
    }
    json_str = json.dumps(value_data, ensure_ascii=False)
    
    found = False
    records = ws.get_all_records()
    for i, row in enumerate(records):
        if row['section'] == 'subscription' and row['key'] == name:
            ws.update_cell(i+2, 3, json_str) # +2 因為 header=1, index 從 0 開始
            found = True
            break
            
    if not found:
        ws.append_row(["subscription", name, json_str])
        
    get_app_settings.clear()

def delete_subscription_template(name):
    sh = get_spreadsheet()
    ws = init_settings_sheet(sh)
    cell = ws.find(name)
    if cell and ws.cell(cell.row, 1).value == 'subscription':
        ws.delete_rows(cell.row)
        get_app_settings.clear()

def generate_subscriptions_for_month(date_obj, subs_list):
    """一鍵生成：將訂閱列表寫入當月帳務"""
    sh = get_spreadsheet()
    if not sh: return
    
    sheet_name = date_obj.strftime("%Y-%m")
    ws = get_or_create_worksheet(sh, sheet_name)
    
    rows_to_add = []
    
    for sub in subs_list:
        cf_date, _ = calculate_cash_flow_info(date_obj, sub['payment_method'])
        unique_id = str(uuid.uuid4())
        
        row_data = [
            date_obj.strftime("%Y-%m-%d"),
            cf_date.strftime("%Y-%m-%d"),
            "支出",
            sub['category'],
            sub['amount'],
            sub['payment_method'],
            "#固定支出", 
            f"{sub['name']} ({sub['note']})",
            unique_id
        ]
        rows_to_add.append(row_data)
        
    for row in rows_to_add:
        ws.append_row(row)
        time.sleep(0.3)
        
    get_data.clear()

# ==========================================
# 🧮 核心邏輯
# ==========================================

def calculate_cash_flow_info(date_obj, payment_method):
    config = CREDIT_CARDS_CONFIG.get(payment_method, CREDIT_CARDS_CONFIG["其他"])
    cutoff = config['cutoff']
    gap = config['gap']
    
    if cutoff == 0:
        return date_obj, "當下結清"
    
    if date_obj.day <= cutoff:
        billing_month = date_obj
    else:
        billing_month = date_obj + relativedelta(months=1)
        
    try:
        billing_date = billing_month.replace(day=cutoff)
    except ValueError:
        billing_date = billing_month + relativedelta(day=31)
        
    cash_flow_date = billing_date + timedelta(days=gap)
    return cash_flow_date, f"{billing_month.strftime('%Y-%m')} 帳單"

# --- 3. 讀取與寫入 ---

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
            
            if 'cash_flow_date' not in row_dict or not row_dict['cash_flow_date']:
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
    except gspread.exceptions.WorksheetNotFound:
        time.sleep(1)
        worksheet = sh.add_worksheet(title=sheet_name, rows=100, cols=12)
        worksheet.append_row(EXPECTED_HEADERS)
    return worksheet

def add_transaction(date_obj, record_type, category, amount, payment_method, note, tags, installment_months=1):
    sh = get_spreadsheet()
    if not sh: return

    monthly_amount = round(amount / installment_months)
    operations = [] 
    current_date = date_obj

    for i in range(installment_months):
        cf_date, _ = calculate_cash_flow_info(current_date, payment_method)
        final_note = note
        final_tags = tags
        if installment_months > 1:
            final_note = f"{note} ({i+1}/{installment_months})"
            final_tags = f"{tags},#分期"
        
        sheet_name = current_date.strftime("%Y-%m")
        unique_id = str(uuid.uuid4())
        
        row_data = [
            current_date.strftime("%Y-%m-%d"),
            cf_date.strftime("%Y-%m-%d"),
            record_type,
            category,
            monthly_amount,
            payment_method,
            final_tags,
            final_note,
            unique_id
        ]
        operations.append((sheet_name, row_data))
        current_date = current_date + relativedelta(months=1)

    for sheet_name, row in operations:
        ws = get_or_create_worksheet(sh, sheet_name)
        ws.append_row(row)
        time.sleep(0.5)

    get_data.clear()

def safe_update_transaction(edited_row, original_row, sh):
    uid = edited_row['id']
    origin_sheet_name = original_row['_sheet_name']
    new_sheet_name = edited_row['date'].strftime("%Y-%m")
    
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
        uid 
    ]

    try:
        if new_sheet_name == origin_sheet_name:
            ws = sh.worksheet(origin_sheet_name)
            cell = ws.find(uid)
            range_name = f"A{cell.row}:I{cell.row}"
            ws.update(range_name=range_name, values=[new_values])
        else:
            new_ws = get_or_create_worksheet(sh, new_sheet_name)
            new_ws.append_row(new_values)
            time.sleep(1)
            old_ws = sh.worksheet(origin_sheet_name)
            old_cell = old_ws.find(uid)
            old_ws.delete_rows(old_cell.row)
        return True
    except Exception as e:
        st.error(f"更新失敗 ID {uid}: {e}")
        return False

def delete_transaction(sheet_name, target_id):
    """刪除指定交易"""
    sh = get_spreadsheet()
    if not sh: return
    try:
        worksheet = sh.worksheet(sheet_name)
        cell = worksheet.find(target_id)
        if cell:
            worksheet.delete_rows(cell.row)
    except Exception as e:
        st.error(f"刪除失敗：{e}")

# --- 4. 主程式介面 ---

if st.sidebar.button("🔒 登出系統"):
    st.session_state.logged_in = False
    st.rerun()

# 讀取設定與資料
expense_cats, income_cats, monthly_budgets, subscriptions = get_app_settings()
df = get_data()

# --- 側邊欄：新增交易 ---
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
    tags = st.text_input("標籤 (Tag)", placeholder="例如: #日本旅遊")
    
    is_installment = False
    installment_months = 1
    if record_type == "支出" and payment_method != "現金":
        is_installment = st.checkbox("設定分期付款")
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

# 🔥 側邊欄：訂閱與固定支出管理
with st.sidebar.expander("🔄 訂閱/固定支出管家"):
    st.caption("設定房租、Netflix等固定開銷，每月可一鍵生成。")
    
    # 新增樣板
    sub_name = st.text_input("名稱 (如: Netflix)")
    sub_amt = st.number_input("金額", min_value=0.0, step=10.0)
    sub_cat = st.selectbox("類別", expense_cats, key="sub_cat")
    sub_pm = st.selectbox("扣款方式", list(CREDIT_CARDS_CONFIG.keys()), key="sub_pm")
    
    if st.button("➕ 新增固定支出樣板"):
        if sub_name and sub_amt > 0:
            add_subscription_template(sub_name, sub_amt, sub_cat, sub_pm, "固定支出")
            st.success(f"已新增 {sub_name}")
            st.rerun()
    
    st.markdown("---")
    st.write("📋 現有樣板：")
    for sub in subscriptions:
        c1, c2 = st.columns([3, 1])
        c1.text(f"{sub['name']} ${sub['amount']}")
        if c2.button("❌", key=f"del_{sub['name']}"):
            delete_subscription_template(sub['name'])
            st.rerun()
            
    st.markdown("---")
    # 一鍵生成按鈕
    gen_date = st.date_input("生成日期 (通常選每月1號)", datetime.now().replace(day=1))
    if st.button("⚡ 一鍵生成本月固定支出"):
        if subscriptions:
            with st.spinner(f"正在生成 {len(subscriptions)} 筆資料..."):
                generate_subscriptions_for_month(gen_date, subscriptions)
            st.success("生成完成！")
            st.rerun()
        else:
            st.warning("請先新增樣板")

# --- 主畫面 ---
st.title("💎 個人理財管家 Ultimate")

if df.empty:
    st.info("💡 目前沒有資料，請初始化您的第一筆帳務！")
else:
    stats_df = df.copy()
    stats_df['month_str'] = stats_df['date'].apply(lambda x: x.strftime("%Y-%m"))
    
    current_month_str = datetime.now().strftime("%Y-%m")
    available_months = sorted(stats_df['month_str'].unique(), reverse=True)
    if current_month_str not in available_months: available_months.insert(0, current_month_str)
    
    # 🔥 自動切換到本月邏輯
    try:
        default_index = available_months.index(current_month_str)
    except ValueError:
        default_index = 0

    col_filter1, col_filter2 = st.columns([1, 2])
    with col_filter1:
        # 加入 index 參數
        selected_month = st.selectbox("📅 選擇月份", available_months, index=default_index)
    with col_filter2:
        tag_filter = st.text_input("🔍 標籤搜尋", "")

    current_month_df = stats_df[stats_df['month_str'] == selected_month]
    if tag_filter:
        current_month_df = current_month_df[current_month_df['tags'].astype(str).str.contains(tag_filter)]

    budget = monthly_budgets.get(selected_month, 20000)

    total_income = current_month_df[current_month_df['type'] == '收入']['amount'].sum()
    total_expense = current_month_df[current_month_df['type'] == '支出']['amount'].sum()
    net_balance = total_income - total_expense
    remaining = budget - total_expense
    
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("總收入", f"${total_income:,.0f}")
    c2.metric("總支出", f"${total_expense:,.0f}", delta=f"-{total_expense:,.0f}", delta_color="inverse")
    c3.metric("本月淨利", f"${net_balance:,.0f}", delta_color="normal" if net_balance >= 0 else "inverse")
    c4.metric(f"預算 ({selected_month})", f"${remaining:,.0f}", delta=f"預算 ${budget:,.0f}")
    
    with st.expander("✏️ 修改本月預算"):
        new_budget_val = st.number_input("設定金額", value=float(budget), step=1000.0)
        if st.button("更新預算"):
            update_monthly_budget(selected_month, new_budget_val)
            st.success("預算已更新！")
            st.rerun()

    st.markdown("---")

    tab1, tab2, tab3 = st.tabs(["📊 收支概況", "💳 現金流分析", "🏷️ 專案/標籤分析"])
    
    with tab1:
        cc1, cc2 = st.columns(2)
        with cc1:
            if not current_month_df[current_month_df['type']=='支出'].empty:
                fig = px.pie(current_month_df[current_month_df['type']=='支出'], values='amount', names='category', title='支出類別占比', hole=0.4)
                st.plotly_chart(fig, use_container_width=True)
            else:
                st.info("無支出資料")
        with cc2:
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
        st.caption("💡 這裡顯示的是『實際扣款日』，而非消費日。")
        cf_df = current_month_df.copy()
        fig_cf = px.bar(cf_df[cf_df['type']=='支出'], x='cash_flow_date', y='amount', color='payment_method', 
                        title='未來30天現金流出預測',
                        labels={'cash_flow_date': '預計扣款日', 'amount': '扣款金額'})
        st.plotly_chart(fig_cf, use_container_width=True)

    with tab3:
        tags_series = current_month_df['tags'].str.split(',').explode().str.strip()
        tags_series = tags_series[tags_series != ""]
        if not tags_series.empty:
            tag_counts = tags_series.value_counts().reset_index()
            tag_counts.columns = ['tag', 'count']
            tag_amounts = {}
            for tag in tag_counts['tag']:
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
    
    # ==========================================
    # 🔥 核心修改區域：詳細記錄 (支援編輯與刪除)
    # ==========================================
    st.subheader("📋 詳細記錄 (可編輯與刪除)")
    
    all_cats = expense_cats + income_cats + ["其他"]
    all_pm = list(CREDIT_CARDS_CONFIG.keys())

    # 設定 Data Editor，開啟 dynamic 模式以允許刪除
    # 並強制定義欄位格式 (DateColumn, NumberColumn) 解決格式跑掉問題
    edited_df = st.data_editor(
        current_month_df.sort_values('date', ascending=False),
        column_config={
            "id": None,  # 隱藏 ID
            "_sheet_name": None, # 隱藏工作表名稱
            "date": st.column_config.DateColumn("消費日期", format="YYYY-MM-DD", required=True),
            "cash_flow_date": st.column_config.DateColumn("現金流/繳款日", disabled=True), 
            "type": st.column_config.SelectboxColumn("類型", options=["支出", "收入"], required=True, width="small"),
            "category": st.column_config.SelectboxColumn("類別", options=all_cats, required=True),
            "payment_method": st.column_config.SelectboxColumn("付款方式", options=all_pm, required=True),
            "amount": st.column_config.NumberColumn("金額", format="$ %.0f", required=True),
            "tags": st.column_config.TextColumn("標籤"),
            "note": st.column_config.TextColumn("備註"),
        },
        use_container_width=True,
        num_rows="dynamic", # 🔥 允許新增與刪除列
        hide_index=True,
        key="data_editor_main"
    )

    if st.button("💾 儲存變更"):
        with st.spinner("正在同步雲端資料庫..."):
            sh = get_spreadsheet()
            
            # 建立原始資料的索引地圖
            original_map = current_month_df.set_index('id').to_dict('index')
            
            # 取得編輯後的 ID 列表與原始 ID 列表
            current_ids = set(row['id'] for i, row in edited_df.iterrows() if row['id'])
            original_ids = set(original_map.keys())
            
            changes_count = 0
            delete_count = 0

            # --- A. 處理刪除 ---
            deleted_ids = original_ids - current_ids
            for uid in deleted_ids:
                sheet_name = original_map[uid]['_sheet_name']
                delete_transaction(sheet_name, uid)
                delete_count += 1

            # --- B. 處理修改 ---
            progress_bar = st.progress(0)
            total_rows = len(edited_df)
            
            for i, (idx, row) in enumerate(edited_df.iterrows()):
                uid = row['id']
                if not uid or uid not in original_map: 
                    continue # 略過新增的行 (建議使用左側欄位新增)
                
                orig = original_map[uid]
                
                # 檢查欄位變更
                has_changed = (
                    str(row['date']) != str(orig['date']) or 
                    row['type'] != orig['type'] or 
                    row['category'] != orig['category'] or 
                    float(row['amount']) != float(orig['amount']) or 
                    row['payment_method'] != orig['payment_method'] or
                    str(row['tags']) != str(orig['tags']) or
                    str(row['note']) != str(orig['note'])
                )
                
                if has_changed:
                    if safe_update_transaction(row, orig, sh):
                        changes_count += 1
                
                if total_rows > 0:
                    progress_bar.progress((i + 1) / total_rows)
            
            if changes_count > 0 or delete_count > 0:
                st.success(f"✅ 同步完成！更新 {changes_count} 筆，刪除 {delete_count} 筆。")
                get_data.clear()
                time.sleep(1.5)
                st.rerun()
            else:
                st.info("沒有偵測到任何變更。")