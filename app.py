import streamlit as st
import pandas as pd
import plotly.express as px
from datetime import datetime
import gspread
from google.oauth2.service_account import Credentials
import uuid
import time

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

def get_spreadsheet():
    client = get_google_sheet_client()
    return client.open("my_expenses_db") # 請確保您的 Google Sheet 檔案名稱正確

# --- 3. 核心功能：讀取、寫入、更新 (支援分頁) ---

def get_data():
    """
    從 Google Sheet 的「所有分頁」讀取資料
    並合併成一個 DataFrame
    """
    sh = get_spreadsheet()
    all_worksheets = sh.worksheets()
    
    all_data = []
    expected_headers = ["date", "category", "amount", "note", "id"]

    for worksheet in all_worksheets:
        # 跳過非資料的 Sheet (如果有設定頁或其他頁可在此過濾)
        rows = worksheet.get_all_values()
        
        if len(rows) <= 1:
            continue # 空的或只有標題
            
        headers = rows[0]
        # 簡單檢查標題是否符合 (避免讀到不相關的頁面)
        if "id" not in headers or "date" not in headers:
            continue

        sheet_data = rows[1:]
        
        # 我們需要記錄這筆資料來自哪個 Sheet，方便後續更新時定位
        # 這裡利用 Python 的特性，暫存一個 _sheet_name 欄位
        for row in sheet_data:
            # 確保欄位數量一致 (避免有些行少填)
            if len(row) < len(headers):
                row += [""] * (len(headers) - len(row))
            # 建立字典
            row_dict = dict(zip(headers, row))
            row_dict['_sheet_name'] = worksheet.title # 記錄來源分頁
            all_data.append(row_dict)
            
    if not all_data:
        return pd.DataFrame(columns=expected_headers + ['_sheet_name'])

    df = pd.DataFrame(all_data)
    
    # 確保必要欄位存在
    for col in expected_headers:
        if col not in df.columns:
            df[col] = ""

    # 型別轉換
    df['amount'] = pd.to_numeric(df['amount'], errors='coerce').fillna(0)
    # 轉為 date 物件供編輯器使用
    df['date'] = pd.to_datetime(df['date'], errors='coerce').dt.date
    
    return df

def get_or_create_worksheet(sh, sheet_name):
    """取得指定名稱的分頁，若不存在則建立並寫入標題"""
    try:
        worksheet = sh.worksheet(sheet_name)
    except gspread.exceptions.WorksheetNotFound:
        # 建立新分頁
        worksheet = sh.add_worksheet(title=sheet_name, rows=100, cols=10)
        # 寫入標題
        worksheet.append_row(["date", "category", "amount", "note", "id"])
    return worksheet

def add_expense(date_obj, category, amount, note):
    """新增資料到對應月份的分頁"""
    sh = get_spreadsheet()
    
    # 根據日期決定 Sheet 名稱 (例如 "2024-05")
    sheet_name = date_obj.strftime("%Y-%m")
    worksheet = get_or_create_worksheet(sh, sheet_name)
    
    unique_id = str(uuid.uuid4())
    date_str = date_obj.strftime("%Y-%m-%d")
    
    worksheet.append_row([date_str, category, amount, note, unique_id])
    st.cache_data.clear()

def delete_expense(sheet_name, target_id):
    """從指定分頁刪除資料"""
    sh = get_spreadsheet()
    try:
        worksheet = sh.worksheet(sheet_name)
        cell = worksheet.find(target_id)
        worksheet.delete_rows(cell.row)
        st.cache_data.clear()
    except (gspread.exceptions.WorksheetNotFound, gspread.exceptions.CellNotFound):
        st.error(f"刪除失敗：在 {sheet_name} 找不到 ID {target_id}")

def update_expense_batch(edited_df, original_df):
    """
    【修正版】批次更新
    1. 使用 ID Map 進行精確比對 (解決排序問題)
    2. 支援跨月移動 (若修改日期，自動換 Sheet)
    """
    sh = get_spreadsheet()
    
    # 將原始資料轉為 Dict 方便用 ID 快速查找
    # key: id, value: row_series
    original_map = original_df.set_index('id').to_dict('index')
    
    changes_count = 0
    
    # 進度條 (若資料多時會很有感)
    progress_bar = st.progress(0)
    total_rows = len(edited_df)
    
    for i, (index, row) in enumerate(edited_df.iterrows()):
        uid = row['id']
        
        # 如果這個 ID 不在原始資料中，代表是新創的 (但在 data_editor 我們通常禁止新增，只允許修改)
        if uid not in original_map:
            continue
            
        orig = original_map[uid]
        
        # 1. 檢查是否有變更
        # 注意：介面上的 date 是 datetime.date，原始資料讀進來也是 datetime.date (在 get_data 轉過了)
        has_changed = (
            row['date'] != orig['date'] or 
            row['category'] != orig['category'] or 
            row['amount'] != orig['amount'] or 
            row['note'] != orig['note']
        )
        
        if has_changed:
            origin_sheet_name = orig['_sheet_name']
            
            # 2. 檢查是否需要跨表移動 (月份是否改變)
            new_sheet_name = row['date'].strftime("%Y-%m")
            # 舊的分頁名稱通常是 "YYYY-MM"，但也許舊資料在 "Sheet1"，所以我們要比對
            # 如果原始分頁名稱 與 新日期的月份不同，就需要搬移
            
            # 若原始資料在 Sheet1，我們也視為需要搬移到正確的月分頁
            needs_move = (new_sheet_name != origin_sheet_name)
            
            if needs_move:
                # --- 搬移邏輯：刪除舊的 -> 新增新的 ---
                try:
                    # A. 刪除舊資料
                    old_ws = sh.worksheet(origin_sheet_name)
                    cell = old_ws.find(uid)
                    old_ws.delete_rows(cell.row)
                    
                    # B. 寫入新分頁 (保持原本的 ID)
                    new_ws = get_or_create_worksheet(sh, new_sheet_name)
                    new_ws.append_row([
                        row['date'].strftime("%Y-%m-%d"),
                        row['category'],
                        float(row['amount']),
                        row['note'],
                        uid # 保持 ID 不變
                    ])
                    changes_count += 1
                except Exception as e:
                    st.error(f"搬移資料失敗 (ID: {uid}): {e}")
            
            else:
                # --- 原地更新邏輯 ---
                try:
                    ws = sh.worksheet(origin_sheet_name)
                    cell = ws.find(uid)
                    row_num = cell.row
                    
                    # 準備更新的值
                    new_values = [
                        row['date'].strftime("%Y-%m-%d"),
                        row['category'],
                        float(row['amount']),
                        row['note']
                    ]
                    # 更新 A:D 欄
                    ws.update(range_name=f"A{row_num}:D{row_num}", values=[new_values])
                    changes_count += 1
                except Exception as e:
                    st.error(f"更新資料失敗 (ID: {uid}): {e}")
        
        # 更新進度條
        progress_bar.progress((i + 1) / total_rows)

    if changes_count > 0:
        st.success(f"✅ 成功更新 {changes_count} 筆資料！")
        st.cache_data.clear()
        time.sleep(1) # 稍等一下讓使用者看到成功訊息
        st.rerun()
    else:
        st.info("沒有檢測到任何變更。")

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
            with st.spinner("正在寫入雲端 (自動歸檔到對應月份)..."):
                add_expense(date, category, amount, note)
            st.sidebar.success("已儲存！")
            st.rerun()
        else:
            st.sidebar.error("金額必須大於 0")

st.sidebar.markdown("---")
st.sidebar.header("🗑️ 刪除/管理")

# 準備刪除選單
if not df.empty and 'id' in df.columns:
    delete_df = df.sort_values(by='date', ascending=False).head(10)
    delete_options = {
        f"{row['date']} - {row['category']} ${row['amount']} ({row['note']})": (row['_sheet_name'], row['id'])
        for index, row in delete_df.iterrows()
    }
    
    selected_label = st.sidebar.selectbox("快速刪除 (最近10筆)", options=list(delete_options.keys()))
    
    if st.sidebar.button("確認刪除此筆"):
        target_sheet, target_id = delete_options[selected_label]
        with st.spinner("正在刪除中..."):
            delete_expense(target_sheet, target_id)
        st.sidebar.success("刪除成功！")
        st.rerun()
else:
    st.sidebar.info("目前沒有資料可刪除")

st.sidebar.markdown("---")
st.sidebar.header("⚙️ 預算設定")
budget = st.sidebar.number_input("本月預算上限", min_value=1000, value=20000, step=500)


# --- 主畫面儀表板 ---
st.title("💰 個人雲端理財管家 (月分頁版)")

if not df.empty:
    # 統計分析資料準備
    stats_df = df.copy()
    # 確保 date 是 datetime 格式以便計算
    stats_df['date'] = pd.to_datetime(stats_df['date'])
    stats_df['month_str'] = stats_df['date'].dt.strftime("%Y-%m")
    
    current_month_str = datetime.now().strftime("%Y-%m")
    
    # 讓使用者選擇要查看的月份 (預設本月)
    # 找出資料庫中所有的月份
    available_months = sorted(stats_df['month_str'].unique(), reverse=True)
    if current_month_str not in available_months:
        available_months.insert(0, current_month_str)
        
    selected_month = st.selectbox("📅 選擇分析月份", available_months, index=0)
    
    # 篩選該月資料
    current_month_df = stats_df[stats_df['month_str'] == selected_month]
    
    total_spent = current_month_df['amount'].sum()
    remaining_budget = budget - total_spent
    usage_percentage = (total_spent / budget) * 100 if budget > 0 else 0

    # 1. 關鍵指標
    col1, col2, col3 = st.columns(3)
    col1.metric(f"{selected_month} 總支出", f"NT$ {total_spent:,.0f}")
    col2.metric("剩餘預算", f"NT$ {remaining_budget:,.0f}", delta_color="normal" if remaining_budget > 0 else "inverse")
    
    if usage_percentage >= 100:
        st.error(f"⚠️ 警告：本月已超支！ ({usage_percentage:.1f}%)")
    elif usage_percentage >= 80:
        st.warning(f"⚠️ 注意：預算即將用盡 ({usage_percentage:.1f}%)")
    else:
        st.success(f"目前控制良好 ({usage_percentage:.1f}%)")
    st.progress(min(usage_percentage / 100, 1.0))

    st.markdown("---")

    # 2. 圖表區
    c1, c2 = st.columns(2)
    with c1:
        st.subheader(f"📊 {selected_month} 花費類別")
        if not current_month_df.empty:
            fig_pie = px.pie(current_month_df, values='amount', names='category', title='類別占比', hole=0.4)
            st.plotly_chart(fig_pie, use_container_width=True)
        else:
            st.info("本月尚無支出資料")

    with c2:
        st.subheader(f"📈 {selected_month} 每日趨勢")
        if not current_month_df.empty:
            daily_expense = current_month_df.groupby('date')['amount'].sum().reset_index()
            fig_line = px.line(daily_expense, x='date', y='amount', title='支出變化', markers=True)
            st.plotly_chart(fig_line, use_container_width=True)
        else:
            st.info("本月尚無資料")

    # 3. 詳細記錄 (可編輯版)
    st.markdown("---")
    st.subheader("📋 全月份詳細記錄 (可修改)")
    st.caption("💡 修改日期會自動移動到對應的月份分頁")
    
    # 在這裡我們顯示所有資料，方便查找歷史紀錄
    # 預設排序：日期新 -> 舊
    display_df = df.sort_values(by='date', ascending=False)
    
    edited_df = st.data_editor(
        display_df,
        column_config={
            "id": None, # 隱藏 ID
            "_sheet_name": None, # 隱藏來源 Sheet 名稱
            "date": st.column_config.DateColumn("日期", format="YYYY-MM-DD"),
            "category": st.column_config.SelectboxColumn("類別", options=["飲食", "交通", "娛樂", "購物", "居住", "醫療", "投資", "寵物", "進修", "其他"], required=True),
            "amount": st.column_config.NumberColumn("金額", format="$ %.0f"),
            "note": st.column_config.TextColumn("備註"),
        },
        use_container_width=True,
        num_rows="fixed",
        hide_index=True,
        key="data_editor"
    )

    if st.button("💾 儲存修改 (修改表格後請點此)"):
        with st.spinner("正在智慧更新 (自動比對變更)..."):
            # 傳入 編輯後的 df 與 原始 df
            update_expense_batch(edited_df, df)

else:
    st.info("💡 目前還沒有任何資料，請從左側側邊欄「新增支出」！")