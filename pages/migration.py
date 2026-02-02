import streamlit as st
import pandas as pd
import gspread
from google.oauth2.service_account import Credentials
from supabase import create_client, Client
import time

st.set_page_config(page_title="資料庫搬家工具", page_icon="🚚")

st.title("🚚 Google Sheets -> Supabase 搬家工具")
st.warning("⚠️ 此工具會讀取 Google Sheets 並寫入 Supabase。建議執行一次後就將此檔案刪除。")

# ==========================================
# 1. 初始化連線
# ==========================================
if st.button("🚀 開始執行搬家", type="primary"):
    log_container = st.container()
    
    with log_container:
        st.write("🔌 正在連線 Supabase...")
        try:
            # 從 Secrets 讀取 Supabase 設定
            supabase_url = st.secrets["supabase"]["url"]
            supabase_key = st.secrets["supabase"]["key"]
            supabase: Client = create_client(supabase_url, supabase_key)
            st.success("✅ Supabase 連線成功")
        except Exception as e:
            st.error(f"❌ Supabase 連線失敗: {e}")
            st.stop()

        st.write("🔌 正在連線 Google Sheets...")
        try:
            scopes = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
            # 從 Secrets 讀取 Google 設定
            creds = Credentials.from_service_account_info(st.secrets["gcp_service_account"], scopes=scopes)
            gc = gspread.authorize(creds)
            sh = gc.open("my_expenses_db")
            st.success("✅ Google Sheets 連線成功")
        except Exception as e:
            st.error(f"❌ Google Sheets 連線失敗: {e}")
            st.stop()

        # ==========================================
        # 2. 遷移交易記錄
        # ==========================================
        st.subheader("1️⃣ 正在遷移交易記錄 (Transactions)...")
        progress_bar = st.progress(0)
        status_text = st.empty()
        
        try:
            all_worksheets = sh.worksheets()
            transactions_to_upload = []

            for ws in all_worksheets:
                if ws.title == "app_settings": continue
                
                rows = ws.get_all_records()
                if not rows or 'date' not in rows[0]: continue
                
                status_text.text(f"正在讀取工作表: {ws.title}...")
                
                for row in rows:
                    # 資料清洗
                    try:
                        amt = float(str(row.get('amount', 0)).replace(',', ''))
                    except:
                        amt = 0
                    
                    date_val = row.get('date')
                    if not date_val: continue
                    
                    tags_val = row.get('tags', '')
                    if isinstance(tags_val, list): tags_val = ",".join(tags_val)

                    data = {
                        "date": date_val,
                        "cash_flow_date": row.get('cash_flow_date', date_val),
                        "type": row.get('type'),
                        "category": row.get('category'),
                        "amount": amt,
                        "payment_method": row.get('payment_method'),
                        "note": row.get('note', ''),
                        "tags": str(tags_val)
                    }
                    transactions_to_upload.append(data)
            
            st.info(f"共蒐集到 {len(transactions_to_upload)} 筆交易資料，準備寫入...")
            
            # 批次寫入
            batch_size = 100
            total_tx = len(transactions_to_upload)
            
            if total_tx > 0:
                for i in range(0, total_tx, batch_size):
                    batch = transactions_to_upload[i : i + batch_size]
                    supabase.table('transactions').insert(batch).execute()
                    
                    # 更新進度條
                    progress = min((i + batch_size) / total_tx, 1.0)
                    progress_bar.progress(progress)
                    time.sleep(0.1)
                st.success("🎉 交易記錄遷移完成！")
            else:
                st.warning("沒有發現交易資料。")

        except Exception as e:
            st.error(f"❌ 交易遷移發生錯誤: {e}")

        # ==========================================
        # 3. 遷移設定檔
        # ==========================================
        st.subheader("2️⃣ 正在遷移設定檔 (App Settings)...")
        try:
            ws_settings = sh.worksheet("app_settings")
            settings_rows = ws_settings.get_all_records()
            
            settings_to_upload = []
            for row in settings_rows:
                data = {
                    "section": row.get('section'),
                    "key_name": row.get('key'),
                    "value": str(row.get('value'))
                }
                settings_to_upload.append(data)
                
            if settings_to_upload:
                supabase.table('app_settings').insert(settings_to_upload).execute()
                st.success(f"🎉 設定檔遷移完成！共 {len(settings_to_upload)} 筆。")
        except gspread.exceptions.WorksheetNotFound:
            st.warning("找不到 app_settings 工作表。")
        except Exception as e:
            st.error(f"❌ 設定遷移發生錯誤: {e}")

        st.balloons()
        st.success("🚀 全部任務完成！請去 Supabase 檢查資料，並刪除此頁面。")
