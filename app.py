import streamlit as st
import pandas as pd
import plotly.express as px
from datetime import datetime
import gspread
from google.oauth2.service_account import Credentials
import uuid

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

# --- 3. 讀取與寫入功能 (含修改與刪除) ---

def get_data():
    """從 Google Sheet 讀取資料"""
    client = get_google_sheet_client()
    sheet = client.open("my_expenses_db").sheet1
    all_rows = sheet.get_all_values()
    
    expected_headers = ["date", "category", "amount", "note", "id"]

    if len(all_rows) <= 1:
        return pd.DataFrame(columns=expected_headers)
    
    headers = all_rows[0]
    data = all_rows[1:]
    
    df = pd.DataFrame(data, columns=headers)
    
    if "id" not in df.columns:
        st.error("⚠️ 資料表結構版本過舊，缺少 'id' 欄位。請清空 Google Sheet 後重新整理。")
        return pd.DataFrame(columns=expected_headers)
    
    # 轉換型別，確保編輯器能正確顯示
    df['amount'] = pd.to_numeric(df['amount'], errors='coerce').fillna(0)
    
    # 將日期轉為 datetime 物件，方便編輯器顯示日曆
    # 注意：寫回 Sheet 時要轉回字串
    df['date'] = pd.to_datetime(df['date'], errors='coerce').dt.date
    
    return df

def add_expense(date, category, amount, note):
    """新增一筆資料"""
    client = get_google_sheet_client()
    sheet = client.open("my_expenses_db").sheet1
    unique_id = str(uuid.uuid4())
    
    if not sheet.get_all_values():
        sheet.append_row(["date", "category", "amount", "note", "id"])
    
    date_str = date.strftime("%Y-%m-%d")
    sheet.append_row([date_str, category, amount, note, unique_id])
    st.cache_data.clear()

def delete_expense(target_id):
    """根據 UUID 刪除資料"""
    client = get_google_sheet_client()
    sheet = client.open("my_expenses_db").sheet1
    try:
        cell = sheet.find(target_id)
        sheet.delete_rows(cell.row)
        st.cache_data.clear()
    except gspread.exceptions.CellNotFound:
        st.error("找不到該筆資料，可能已被刪除。")
    except Exception as e:
        st.error(f"刪除失敗: {e}")

def update_expense_batch(edited_df, original_df):
    """
    【新增功能】批次更新修改過的資料
    比較新舊 DataFrame，找出變更的行並更新 Google Sheet
    """
    client = get_google_sheet_client()
    sheet = client.open("my_expenses_db").sheet1
    
    # 找出有變動的 row (根據 id 比對)
    # 這裡我們簡單做：直接檢查每一列是否與原始資料不同
    # 為了效能，真實場景通常會只更新變動的 cell，但這裡我們更新整行以確保一致性
    
    # 確保索引對齊
    edited_df = edited_df.reset_index(drop=True)
    original_df = original_df.reset_index(drop=True)
    
    changes_count = 0
    
    for index, row in edited_df.iterrows():
        original_row = original_df.iloc[index]
        
        # 檢查關鍵欄位是否有變動
        if (row['date'] != original_row['date'] or 
            row['category'] != original_row['category'] or 
            row['amount'] != original_row['amount'] or 
            row['note'] != original_row['note']):
            
            target_id = row['id']
            
            try:
                # 1. 在 Sheet 中找到這筆資料的位置
                cell = sheet.find(target_id)
                row_num = cell.row
                
                # 2. 準備更新的資料 (注意日期要轉字串)
                new_values = [
                    row['date'].strftime("%Y-%m-%d"),
                    row['category'],
                    float(row['amount']), # 確保是數字
                    row['note']
                ]
                
                # 3. 更新該行的前 4 欄 (A 到 D)
                # Google Sheet API 的範圍是 A{row}:D{row}
                sheet.update(range_name=f"A{row_num}:D{row_num}", values=[new_values])
                changes_count += 1
                
            except gspread.exceptions.CellNotFound:
                st.warning(f"ID {target_id} 找不到，可能已被刪除，跳過更新。")
            except Exception as e:
                st.error(f"更新失敗: {e}")
                
    if changes_count > 0:
        st.success(f"成功更新 {changes_count} 筆資料！")
        st.cache_data.clear() # 清除快取以顯示最新狀態
        st.rerun() # 重新整理頁面
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
            with st.spinner("正在寫入雲端..."):
                add_expense(date, category, amount, note)
            st.sidebar.success("已儲存！")
            st.rerun()
        else:
            st.sidebar.error("金額必須大於 0")

st.sidebar.markdown("---")
st.sidebar.header("🗑️ 刪除/管理")

# 準備刪除選單
if not df.empty and 'id' in df.columns:
    delete_df = df.sort_values(by='date', ascending=False).head(5)
    delete_options = {
        f"{row['date']} - {row['category']} ${row['amount']} ({row['note']})": row['id']
        for index, row in delete_df.iterrows()
    }
    
    selected_label = st.sidebar.selectbox("快速刪除 (最近5筆)", options=list(delete_options.keys()))
    
    if st.sidebar.button("確認刪除此筆"):
        target_id = delete_options[selected_label]
        with st.spinner("正在刪除中..."):
            delete_expense(target_id)
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
    # 為了顯示統計，先建立一份處理過的 df
    stats_df = df.copy()
    stats_df['date'] = pd.to_datetime(stats_df['date']) # 轉為 datetime 才能計算月份
    current_month = datetime.now().strftime("%Y-%m")
    stats_df['month'] = stats_df['date'].dt.strftime("%Y-%m")
    
    current_month_df = stats_df[stats_df['month'] == current_month]
    
    total_spent = current_month_df['amount'].sum()
    remaining_budget = budget - total_spent
    usage_percentage = (total_spent / budget) * 100 if budget > 0 else 0

    # 1. 關鍵指標
    col1, col2, col3 = st.columns(3)
    col1.metric("本月總支出", f"NT$ {total_spent:,.0f}")
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
        st.subheader("📊 本月花費類別")
        if not current_month_df.empty:
            fig_pie = px.pie(current_month_df, values='amount', names='category', title=f'{current_month} 類別占比', hole=0.4)
            st.plotly_chart(fig_pie, use_container_width=True)
        else:
            st.info("本月尚無支出資料")

    with c2:
        st.subheader("📈 每日支出趨勢")
        daily_expense = stats_df.groupby('date')['amount'].sum().reset_index()
        fig_line = px.line(daily_expense, x='date', y='amount', title='支出變化趨勢', markers=True)
        st.plotly_chart(fig_line, use_container_width=True)

    # 3. 詳細記錄 (可編輯版)
    st.markdown("---")
    st.subheader("📋 詳細記錄 (直接點擊表格即可修改)")
    
    # 使用 st.data_editor 讓表格可編輯
    # 設定 column_config 隱藏 id，並設定其他欄位的顯示方式
    edited_df = st.data_editor(
        df.sort_values(by='date', ascending=False),
        column_config={
            "id": None, # 隱藏 ID 欄位，不讓使用者看見
            "date": st.column_config.DateColumn("日期", format="YYYY-MM-DD"),
            "category": st.column_config.SelectboxColumn("類別", options=["飲食", "交通", "娛樂", "購物", "居住", "醫療", "投資", "寵物", "進修", "其他"], required=True),
            "amount": st.column_config.NumberColumn("金額", format="$ %.0f"),
            "note": st.column_config.TextColumn("備註"),
        },
        use_container_width=True,
        num_rows="fixed", # 暫時不開放直接在表格新增列，避免邏輯複雜化
        hide_index=True,
        key="data_editor"
    )

    # 4. 儲存按鈕
    # 只有當資料有變動時，我們才需要執行更新檢查
    # 但因為 Streamlit 的機制，我們直接提供一個按鈕讓使用者確認
    if st.button("💾 儲存修改 (修改表格後請點此)"):
        with st.spinner("正在更新雲端資料..."):
            # 這裡傳入的是尚未排序的原始 df 與 編輯後的 edited_df
            # 注意：edited_df 經過排序操作，我們需要確保比對邏輯正確
            # 簡單做法：我們比對 ID，只更新有變動的
            update_expense_batch(edited_df, df)

else:
    st.info("💡 目前還沒有任何資料，請從左側側邊欄「新增支出」！")