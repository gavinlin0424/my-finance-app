import os
from supabase import create_client, Client

# 從環境變數讀取憑證（在 GitHub Secrets 中設定）
url: str = os.environ.get("https://wlcjcoplkomctivyrmbp.supabase.co")
key: str = os.environ.get("eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6IndsY2pjb3Bsa29tY3RpdnlybWJwIiwicm9sZSI6ImFub24iLCJpYXQiOjE3Njk5NDUwMDEsImV4cCI6MjA4NTUyMTAwMX0.l243q-uuhZpBfOdpEJIv-VhlJKZatPG3EWz5AFGwA3I")
supabase: Client = create_client(url, key)

try:
    # 執行一次極輕量的查詢，證明我們還活著
    response = supabase.table('app_settings').select("id").limit(1).execute()
    print("✅ 成功連接 Supabase，保持活躍狀態！", response.data)
except Exception as e:
    print(f"❌ 連線失敗: {e}")