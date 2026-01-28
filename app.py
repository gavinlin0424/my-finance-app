import streamlit as st
import pandas as pd
import plotly.express as px
from datetime import datetime
import gspread
from google.oauth2.service_account import Credentials
import uuid
import time

# --- 1. 設定頁面配置 ---
st.set_page_config(page_title="個人理財管家 Pro", page_icon="💰", layout="wide")

# --- 2. 連接 Google Sheets 設定 ---
@st.cache_resource
def get_google_sheet_client():
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive"
    ]
    # 從 Streamlit Secrets 讀取金鑰
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
        return client.open("my_expenses_db") # 請確保您的 Google Sheet 檔案名稱正確
    return None

# --- 3. 核心功能：讀取、寫入、更新 (支援分頁) ---

# 定義標準欄位順序
EXPECTED_HEADERS = ["date", "type", "category", "amount", "note", "id"]

def get_data():
    """
    從 Google Sheet 的「所有分頁」讀取資料
    並合併成一個 DataFrame
    """
    sh = get_spreadsheet()
    if not sh: return pd.DataFrame()

    all_worksheets = sh.worksheets()
    
    all_data = []

    for worksheet in all_worksheets:
        rows = worksheet.get_all_values()
        
        if len(rows) <= 1:
            continue 
            
        headers = rows[0]
        # 簡單檢查標題 (相容舊版)
        if "id" not in headers or "date" not in headers:
            continue

        sheet_data = rows[1:]
        
        # 處理每一列資料
        for row in sheet_data:
            # 補齊長度
            if len(row) < len(headers):
                row += [""] * (len(headers) - len(row))
            
            row_dict = dict(zip(headers, row))
            row_dict['_sheet_name'] = worksheet.title
            
            # --- 相容性處理 ---
            # 如果舊資料沒有 'type' 欄位，預設為 '支出'
            if 'type' not in row_dict:
                row_dict['type'] = '支出'
                
            all_data.append(row_dict)
            
    if not all_data:
        return pd.DataFrame(columns=EXPECTED_HEADERS + ['_sheet_name'])

    df = pd.DataFrame(all_data)
    
    # 確保所有標準欄位都存在
    for col in EXPECTED_HEADERS:
        if col not in df.columns:
            df[col] = "" # 若缺失則補空

    # 型別轉換
    df['amount'] = pd.to_numeric(df['amount'], errors='coerce').fillna(0)
    df['date'] = pd.to_datetime(df['date'], errors='coerce').dt.date
    
    return df

def get_or_create_worksheet(sh, sheet_name):
    """取得指定名稱的分頁，若不存在則建立並寫入標題"""
    try:
        worksheet = sh.worksheet(sheet_name)
    except gspread.exceptions.WorksheetNotFound:
        # 建立新分頁
        worksheet = sh.add_worksheet(title=sheet_name, rows=100, cols=10)
        # 寫入包含 'type' 的新標題
        worksheet.append_row(EXPECTED_HEADERS)
    return worksheet

def add_transaction(date_obj, record_type, category, amount, note):
    """新增交易 (收入或支出) 到對應月份的分頁"""
    sh = get_spreadsheet()
    if not sh: return

    sheet_name = date_obj.strftime("%Y-%m")
    worksheet = get_or_create_worksheet(sh, sheet_name)
    
    unique_id = str(uuid.uuid4())
    date_str = date_obj.strftime("%Y-%m-%d")
    
    # 依照 EXPECTED_HEADERS 順序寫入: date, type, category, amount, note, id
    row_data = [date_str, record_type, category, amount, note, unique_id]
    
    worksheet.append_row(row_data)
    st.cache_data.clear()

def delete_transaction(sheet_name, target_id):
    """從指定分頁刪除資料"""
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
    """
    批次更新邏輯
    """
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
        
        # 檢查變更 (包含 type)
        has_changed = (
            row['date'] != orig['date'] or 
            row['type'] != orig['type'] or
            row['category'] != orig['category'] or 
            row['amount'] != orig['amount'] or 
            row['note'] != orig['note']
        )
        
        if has_changed:
            origin_sheet_name = orig['_sheet_name']
            new_sheet_name = row['date'].strftime("%Y-%m")
            
            # 判斷是否需要跨 Sheet 移動
            needs_move = (new_sheet_name != origin_sheet_name)
            
            if needs_move:
                try:
                    # A. 刪除舊資料
                    old_ws = sh.worksheet(origin_sheet_name)
                    cell = old_ws.find(uid)
                    old_ws.delete_rows(cell.row)
                    
                    # B. 寫入新分頁
                    new_ws = get_or_create_worksheet(sh, new_sheet_name)
                    new_ws.append_row([
                        row['date'].strftime("%Y-%m-%d"),
                        row['type'], # 寫入 type
                        row['category'],
                        float(row['amount']),
                        row['note'],
                        uid
                    ])
                    changes_count += 1
                except Exception as e:
                    st.error(f"搬移失敗 (ID: {uid}): {e}")
            
            else:
                # 原地更新
                try:
                    ws = sh.worksheet(origin_sheet_name)
                    cell = ws.find(uid)
                    row_num = cell.row
                    
                    # 依序列 date, type, category, amount, note
                    # 更新 A:E 欄 (因為 id 在 F)
                    new_values = [
                        row['date'].strftime("%Y-%m-%d"),
                        row['type'],
                        row['category'],
                        float(row['amount']),
                        row['note']
                    ]
                    ws.update(range_name=f"A{row_num}:E{row_num}", values=[new_values])
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

# --- 4. 主程式介面 ---

# 讀取資料
df = get_data()

# --- 側邊欄：新增交易 ---
st.sidebar.header("📝 新增交易")

# 新增：類型選擇
record_type = st.sidebar.radio("類型", ["支出", "收入"], horizontal=True)

with st.sidebar.form("expense_form", clear_on_submit=True):
    date = st.date_input("日期", datetime.now())
    
    # 動態調整類別選單 (這裡是 Sidebar 用的，分開比較乾淨)
    if record_type == "支出":
        cat_options = ["飲食", "交通", "娛樂", "購物", "居住", "醫療", "投資", "寵物", "進修", "其他"]
    else:
        cat_options = ["薪資", "獎金", "投資收益", "退款", "兼職", "其他"]
        
    category = st.selectbox("類別", cat_options)
    amount = st.number_input("金額", min_value=0.0, step=10.0, format="%.0f")
    note = st.text_input("備註 (選填)")
    submitted = st.form_submit_button("提交")

    if submitted:
        if amount > 0:
            with st.spinner("正在寫入雲端..."):
                add_transaction(date, record_type, category, amount, note)
            st.sidebar.success(f"已新增{record_type}！")
            st.rerun()
        else:
            st.sidebar.error("金額必須大於 0")

st.sidebar.markdown("---")
st.sidebar.header("🗑️ 快速刪除")

if not df.empty and 'id' in df.columns:
    # 顯示最近 10 筆，包含類型
    delete_df = df.sort_values(by='date', ascending=False).head(10)
    delete_options = {}
    for index, row in delete_df.iterrows():
        # 加上 icon 區分
        icon = "🔴" if row.get('type') == '支出' else "🟢"
        label = f"{icon} {row['date']} - {row['category']} ${row['amount']} ({row['note']})"
        delete_options[label] = (row['_sheet_name'], row['id'])
    
    selected_label = st.sidebar.selectbox("選擇項目", options=list(delete_options.keys()))
    
    if st.sidebar.button("確認刪除"):
        target_sheet, target_id = delete_options[selected_label]
        with st.spinner("正在刪除..."):
            delete_transaction(target_sheet, target_id)
        st.sidebar.success("刪除成功！")
        st.rerun()

st.sidebar.markdown("---")
# 預算只針對「支出」設定比較合理
st.sidebar.header("⚙️ 設定")
budget = st.sidebar.number_input("本月支出預算", min_value=1000, value=20000, step=500)


# --- 主畫面儀表板 ---
st.title("💰 個人雲端理財管家 (收支版)")

if not df.empty:
    stats_df = df.copy()
    stats_df['date'] = pd.to_datetime(stats_df['date'])
    stats_df['month_str'] = stats_df['date'].dt.strftime("%Y-%m")
    
    # 確保 type 欄位存在 (防呆)
    if 'type' not in stats_df.columns:
        stats_df['type'] = '支出'

    current_month_str = datetime.now().strftime("%Y-%m")
    available_months = sorted(stats_df['month_str'].unique(), reverse=True)
    if current_month_str not in available_months:
        available_months.insert(0, current_month_str)
        
    selected_month = st.selectbox("📅 選擇分析月份", available_months, index=0)
    
    # 篩選該月資料
    current_month_df = stats_df[stats_df['month_str'] == selected_month]
    
    # --- 計算收支 ---
    income_df = current_month_df[current_month_df['type'] == '收入']
    expense_df = current_month_df[current_month_df['type'] == '支出']
    
    total_income = income_df['amount'].sum()
    total_expense = expense_df['amount'].sum()
    net_balance = total_income - total_expense
    
    remaining_budget = budget - total_expense
    usage_percentage = (total_expense / budget) * 100 if budget > 0 else 0

    # 1. 關鍵指標
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("總收入 (Income)", f"${total_income:,.0f}", delta_color="normal")
    col2.metric("總支出 (Expense)", f"${total_expense:,.0f}", delta=f"-{total_expense:,.0f}", delta_color="inverse")
    col3.metric("本月淨利", f"${net_balance:,.0f}", delta_color="normal" if net_balance >= 0 else "inverse")
    col4.metric("剩餘預算", f"${remaining_budget:,.0f}", delta_color="normal" if remaining_budget > 0 else "inverse")
    
    st.caption(f"支出預算使用率: {usage_percentage:.1f}%")
    st.progress(min(usage_percentage / 100, 1.0))
    
    st.markdown("---")

    # 2. 圖表區
    c1, c2 = st.columns(2)
    with c1:
        st.subheader(f"📊 {selected_month} 支出分佈")
        if not expense_df.empty:
            fig_pie = px.pie(expense_df, values='amount', names='category', title='支出類別占比', hole=0.4)
            st.plotly_chart(fig_pie, use_container_width=True)
        else:
            st.info("本月尚無支出")

    with c2:
        st.subheader(f"📈 {selected_month} 收支趨勢")
        if not current_month_df.empty:
            # 依日期與類型加總
            daily_trend = current_month_df.groupby(['date', 'type'])['amount'].sum().reset_index()
            # 指定顏色：支出紅色，收入綠色
            fig_bar = px.bar(daily_trend, x='date', y='amount', color='type', 
                             title='每日收支', barmode='group',
                             color_discrete_map={'支出': '#EF553B', '收入': '#00CC96'})
            st.plotly_chart(fig_bar, use_container_width=True)
        else:
            st.info("本月尚無資料")

    # 3. 詳細記錄 (可編輯)
    st.markdown("---")
    st.subheader("📋 詳細記錄")
    
    display_df = df.sort_values(by='date', ascending=False)
    
    # --- 修正重點：這裡列出所有可能的類別，確保資料庫有值的都能顯示 ---
    all_possible_categories = [
        "飲食", "交通", "娛樂", "購物", "居住", "醫療", "投資", "寵物", "進修", # 支出
        "薪資", "獎金", "投資收益", "退款", "兼職", # 收入
        "其他"
    ]

    # 編輯器設定
    edited_df = st.data_editor(
        display_df,
        column_config={
            "id": None, 
            "_sheet_name": None,
            "date": st.column_config.DateColumn("日期", format="YYYY-MM-DD"),
            "type": st.column_config.SelectboxColumn("類型", options=["支出", "收入"], required=True, width="small"),
            "category": st.column_config.SelectboxColumn(
                "類別", 
                options=all_possible_categories, # 修正：使用完整清單
                required=True
            ),
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
    st.info("💡 資料庫是空的，請從左側新增第一筆收入或支出！")