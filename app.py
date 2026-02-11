import streamlit as st
import pandas as pd
import plotly.express as px
from datetime import datetime, timedelta, date
from dateutil.relativedelta import relativedelta
from supabase import create_client, Client
import uuid
import time
import json

# --- 1. 設定頁面配置 ---
st.set_page_config(page_title="個人理財管家 Pro (Supabase版)", page_icon="💎", layout="wide")

# --- 初始化 Supabase 連線 ---
@st.cache_resource
def init_supabase():
    try:
        url = st.secrets["supabase"]["url"]
        key = st.secrets["supabase"]["key"]
        return create_client(url, key)
    except Exception as e:
        st.error(f"Supabase 連線失敗，請檢查 secrets 設定: {e}")
        return None

supabase = init_supabase()

# ==========================================
# ⚙️ 系統核心配置
# ==========================================

@st.cache_data(ttl=300)
def get_system_config():
    """從資料庫讀取信用卡設定與系統密碼"""
    if not supabase: return {}, "pcgi1835"

    default_cards = {
        "現金": {"cutoff": 0, "gap": 0, "color": "#00CC96"},
        "其他": {"cutoff": 0, "gap": 0, "color": "#BAB0AC"}
    }
    default_pw = "pcgi1835"

    try:
        response = supabase.table('app_settings').select("*").eq("section", "system").execute()
        for row in response.data:
            if row['key_name'] == 'credit_cards_config':
                default_cards = json.loads(row['value'])
            elif row['key_name'] == 'admin_password':
                default_pw = row['value']
    except Exception:
        pass
        
    return default_cards, default_pw

CREDIT_CARDS_CONFIG, ADMIN_PASSWORD = get_system_config()

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
            if password == ADMIN_PASSWORD:
                st.session_state.logged_in = True
                st.rerun()
            else:
                st.error("❌ 密碼錯誤")

if not st.session_state.logged_in:
    login()
    st.stop() 

# ==========================================
# 🛠️ 設定管理
# ==========================================

@st.cache_data(ttl=60)
def get_app_settings():
    if not supabase: return [], [], {}, []
    
    response = supabase.table('app_settings').select("*").execute()
    data = response.data
    
    expense_cats = []
    income_cats = []
    monthly_budgets = {}
    subscriptions = [] 
    
    default_expense = "飲食,交通,娛樂,購物,居住,醫療,投資,寵物,進修,其他"
    default_income = "薪資,獎金,投資收益,退款,兼職,其他"

    for row in data:
        section = row['section']
        key = row['key_name']
        value = row['value']

        if section == 'categories':
            if key == 'expense': expense_cats = value.split(',')
            elif key == 'income': income_cats = value.split(',')
        elif section == 'budget':
            monthly_budgets[key] = float(value)
        elif section == 'subscription':
            try:
                sub_data = json.loads(value)
                sub_data['name'] = key
                subscriptions.append(sub_data)
            except: pass
    
    if not expense_cats: expense_cats = default_expense.split(',')
    if not income_cats: income_cats = default_income.split(',')
            
    return expense_cats, income_cats, monthly_budgets, subscriptions

def update_monthly_budget(month_str, amount):
    existing = supabase.table('app_settings').select("id").eq("section", "budget").eq("key_name", month_str).execute()
    if existing.data:
        supabase.table('app_settings').update({"value": str(amount)}).eq("id", existing.data[0]['id']).execute()
    else:
        supabase.table('app_settings').insert({"section": "budget", "key_name": month_str, "value": str(amount)}).execute()
    get_app_settings.clear()

def add_new_category(cat_type, new_cat):
    key = "expense" if cat_type == "expense" else "income"
    existing = supabase.table('app_settings').select("*").eq("section", "categories").eq("key_name", key).execute()
    
    if existing.data:
        current_id = existing.data[0]['id']
        current_val = existing.data[0]['value']
        if new_cat not in current_val:
            new_val = current_val + "," + new_cat
            supabase.table('app_settings').update({"value": new_val}).eq("id", current_id).execute()
            get_app_settings.clear()
            return True, "新增成功"
        else:
            return False, "類別已存在"
    else:
        data = {"section": "categories", "key_name": key, "value": new_cat}
        supabase.table('app_settings').insert(data).execute()
        get_app_settings.clear()
        return True, "新增成功"

def add_subscription_template(name, amount, category, payment_method, note):
    value_data = {"amount": amount, "category": category, "payment_method": payment_method, "note": note}
    json_str = json.dumps(value_data, ensure_ascii=False)
    existing = supabase.table('app_settings').select("id").eq("section", "subscription").eq("key_name", name).execute()
    
    if existing.data:
        supabase.table('app_settings').update({"value": json_str}).eq("id", existing.data[0]['id']).execute()
    else:
        supabase.table('app_settings').insert({"section": "subscription", "key_name": name, "value": json_str}).execute()
    get_app_settings.clear()

def delete_subscription_template(name):
    supabase.table('app_settings').delete().eq("section", "subscription").eq("key_name", name).execute()
    get_app_settings.clear()

def generate_subscriptions_for_month(date_obj, subs_list):
    start_date = date_obj.replace(day=1).strftime("%Y-%m-%d")
    next_month = (date_obj.replace(day=28) + timedelta(days=4)).replace(day=1).strftime("%Y-%m-%d")
    
    response = supabase.table('transactions').select("note").gte("date", start_date).lt("date", next_month).is_("deleted_at", "null").execute()
    existing_notes = set([row['note'] for row in response.data if row.get('note')])
    
    rows_to_add = []
    added_count = 0
    skipped_count = 0
    
    for sub in subs_list:
        target_note = f"{sub['name']} ({sub['note']})"
        if target_note in existing_notes:
            skipped_count += 1
            continue
            
        cf_date, _ = calculate_cash_flow_info(date_obj, sub['payment_method'])
        rows_to_add.append({
            "date": date_obj.strftime("%Y-%m-%d"),
            "cash_flow_date": cf_date.strftime("%Y-%m-%d"),
            "type": "支出",
            "category": sub['category'],
            "amount": sub['amount'],
            "payment_method": sub['payment_method'],
            "tags": "#固定支出", 
            "note": target_note
        })
        added_count += 1
        
    if rows_to_add:
        supabase.table('transactions').insert(rows_to_add).execute()
        get_data.clear()
        
    return added_count, skipped_count

# ==========================================
# 🧮