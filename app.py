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
# 🧮 核心邏輯
# ==========================================

def calculate_cash_flow_info(date_obj, payment_method):
    config = CREDIT_CARDS_CONFIG.get(payment_method, CREDIT_CARDS_CONFIG.get("其他", {"cutoff": 0, "gap": 0}))
    cutoff = config.get('cutoff', 0)
    gap = config.get('gap', 0)
    
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

@st.cache_data(ttl=60, show_spinner="正在從 Supabase 讀取資料...")
def get_data():
    if not supabase: return pd.DataFrame()

    try:
        response = supabase.table('transactions').select("*").is_("deleted_at", "null").execute()
        data = response.data
    except Exception as e:
        st.error(f"讀取資料失敗: {e}")
        return pd.DataFrame()

    if not data:
        return pd.DataFrame(columns=["date", "cash_flow_date", "type", "category", "amount", "payment_method", "tags", "note", "id"])

    df = pd.DataFrame(data)
    df['amount'] = pd.to_numeric(df['amount'], errors='coerce').fillna(0)
    df['date'] = pd.to_datetime(df['date']).dt.date
    df['cash_flow_date'] = pd.to_datetime(df['cash_flow_date']).dt.date
    
    return df

def add_transaction(date_obj, record_type, category, amount, payment_method, note, tags, installment_months=1):
    if not supabase: return

    monthly_amount = round(amount / installment_months)
    rows_to_add = []
    current_date = date_obj

    for i in range(installment_months):
        cf_date, _ = calculate_cash_flow_info(current_date, payment_method)
        final_note = note
        final_tags = tags
        if installment_months > 1:
            final_note = f"{note} ({i+1}/{installment_months})"
            final_tags = f"{tags},#分期"
        
        row_data = {
            "date": current_date.strftime("%Y-%m-%d"),
            "cash_flow_date": cf_date.strftime("%Y-%m-%d"),
            "type": record_type,
            "category": category,
            "amount": monthly_amount,
            "payment_method": payment_method,
            "tags": final_tags,
            "note": final_note
        }
        rows_to_add.append(row_data)
        current_date = current_date + relativedelta(months=1)

    supabase.table('transactions').insert(rows_to_add).execute()
    get_data.clear()

def safe_update_transaction(edited_row, original_row):
    uid = edited_row['id']
    cf_date, _ = calculate_cash_flow_info(edited_row['date'], edited_row['payment_method'])
    
    update_data = {
        "date": edited_row['date'].strftime("%Y-%m-%d"),
        "cash_flow_date": cf_date.strftime("%Y-%m-%d"),
        "type": edited_row['type'],
        "category": edited_row['category'],
        "amount": float(edited_row['amount']),
        "payment_method": edited_row['payment_method'],
        "tags": edited_row['tags'],
        "note": edited_row['note']
    }
    
    try:
        supabase.table('transactions').update(update_data).eq("id", uid).execute()
        return True
    except Exception as e:
        st.error(f"更新失敗 ID {uid}: {e}")
        return False

def delete_transaction(target_id):
    if not supabase: return
    try:
        now_str = datetime.now().isoformat()
        supabase.table('transactions').update({"deleted_at": now_str}).eq("id", target_id).execute()
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
    date_val = st.date_input("交易日期", datetime.now())
    
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
            with st.spinner("正在寫入資料庫..."):
                add_transaction(date_val, record_type, category, amount, payment_method, note, tags, installment_months)
            st.sidebar.success("已新增！")
            time.sleep(0.5) 
            st.rerun()
        else:
            st.sidebar.error("金額必須大於 0")

# 🔥 側邊欄：新增類別
with st.sidebar.expander("⚙️ 類別管理 (新增)"):
    new_cat_type = st.selectbox("類別類型", ["支出", "收入"], index=0)
    new_cat_name = st.text_input("輸入新類別名稱")
    if st.button("➕ 新增類別"):
        if new_cat_name:
            target_key = "expense" if new_cat_type == "支出" else "income"
            success, msg = add_new_category(target_key, new_cat_name)
            if success:
                st.success(f"已新增：{new_cat_name}")
                time.sleep(1)
                st.rerun()
            else:
                st.warning(msg)
        else:
            st.warning("請輸入名稱")

# 🔥 側邊欄：訂閱與固定支出管理
with st.sidebar.expander("🔄 訂閱/固定支出管家"):
    st.caption("設定房租、Netflix等固定開銷，每月可一鍵生成。")
    
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
    gen_date_val = st.date_input("生成日期 (通常選每月1號)", datetime.now().replace(day=1))
    if st.button("⚡ 一鍵生成本月固定支出"):
        if subscriptions:
            with st.spinner(f"正在檢查與生成..."):
                added, skipped = generate_subscriptions_for_month(gen_date_val, subscriptions)
            st.success(f"生成完成！新增 {added} 筆，略過 {skipped} 筆(已存在)。")
            time.sleep(1.5)
            st.rerun()
        else:
            st.warning("請先新增樣板")

# --- 主畫面 ---
st.title("💎 個人理財管家 Pro")

if df.empty:
    st.info("💡 目前資料庫中沒有資料，請建立第一筆帳務！")
else:
    stats_df = df.copy()
    stats_df['month_str'] = stats_df['date'].apply(lambda x: x.strftime("%Y-%m"))
    
    current_month_str = datetime.now().strftime("%Y-%m")
    available_months = sorted(stats_df['month_str'].unique(), reverse=True)
    if current_month_str not in available_months: available_months.insert(0, current_month_str)
    
    try:
        default_index = available_months.index(current_month_str)
    except ValueError:
        default_index = 0

    col_filter1, col_filter2 = st.columns([1, 2])
    with col_filter1:
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

    # 🔥 新增 Tab 5: 🧮 自訂/多選計算機
    tab1, tab2, tab3, tab4, tab5 = st.tabs(["📊 收支概況", "💳 現金流分析", "🏷️ 專案/標籤分析", "📅 每日明細", "🧮 自訂/多選計算機"])
    
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

    with tab4:
        st.subheader("📆 每日消費查詢")
        search_date = st.date_input("選擇日期", datetime.now(), key='daily_search')
        
        daily_mask = df['date'] == search_date
        daily_df = df[daily_mask]
        
        if not daily_df.empty:
            d_income = daily_df[daily_df['type']=='收入']['amount'].sum()
            d_expense = daily_df[daily_df['type']=='支出']['amount'].sum()
            
            k1, k2, k3 = st.columns(3)
            k1.metric("當日支出", f"${d_expense:,.0f}")
            k2.metric("當日收入", f"${d_income:,.0f}")
            k3.metric("筆數", f"{len(daily_df)} 筆")
            
            st.dataframe(
                daily_df[['type', 'category', 'amount', 'note', 'payment_method', 'tags']],
                use_container_width=True,
                column_config={
                    "amount": st.column_config.NumberColumn("金額", format="$ %d")
                }
            )
        else:
            st.info(f"{search_date} 沒有任何交易記錄。")

    # 🔥 新功能：Tab 5 多選計算機
    with tab5:
        st.subheader("🧮 自訂範圍/多選計算機")
        st.caption("勾選特定的交易，系統會自動幫您加總。")

        # 模式 1：日期範圍快篩
        with st.expander("📅 日期範圍篩選器", expanded=True):
            col_d1, col_d2 = st.columns(2)
            d_start = col_d1.date_input("開始日期", datetime.now().replace(day=1))
            d_end = col_d2.date_input("結束日期", datetime.now())
            
            # 篩選資料
            range_mask = (df['date'] >= d_start) & (df['date'] <= d_end)
            range_df = df[range_mask].sort_values('date', ascending=False)
        
        # 模式 2：勾選加總
        if not range_df.empty:
            # 為了讓使用者勾選，我們需要在 dataframe 裡加一個 checkbox 欄位
            # Streamlit 的 data_editor 支援這個功能
            
            # 先準備顯示的資料，只留重要欄位
            display_df = range_df[['date', 'type', 'category', 'amount', 'note', 'tags']].copy()
            # 預設增加一個 'Select' 欄位，全選 False
            display_df.insert(0, "Select", False)
            
            edited_selection = st.data_editor(
                display_df,
                column_config={
                    "Select": st.column_config.CheckboxColumn("選取", help="勾選以加入計算", default=False),
                    "amount": st.column_config.NumberColumn("金額", format="$ %d"),
                    "date": st.column_config.DateColumn("日期", format="YYYY-MM-DD"),
                },
                use_container_width=True,
                hide_index=True,
                num_rows="fixed" # 禁止新增刪除，只許修改 checkbox
            )
            
            # 計算勾選的項目
            selected_rows = edited_selection[edited_selection["Select"] == True]
            
            st.markdown("---")
            c_calc1, c_calc2, c_calc3 = st.columns(3)
            
            if not selected_rows.empty:
                sel_income = selected_rows[selected_rows['type'] == '收入']['amount'].sum()
                sel_expense = selected_rows[selected_rows['type'] == '支出']['amount'].sum()
                sel_net = sel_income - sel_expense
                sel_count = len(selected_rows)
                
                c_calc1.metric("已選筆數", f"{sel_count} 筆")
                c_calc2.metric("已選總支出", f"${sel_expense:,.0f}")
                c_calc3.metric("已選淨額", f"${sel_net:,.0f}", delta=f"收入 ${sel_income:,.0f}")
                
                # 顯示選取明細
                with st.expander("查看選取項目明細"):
                    st.dataframe(selected_rows.drop(columns=['Select']), use_container_width=True)
            else:
                # 如果都沒勾，預設顯示範圍內的總計
                total_in_range_exp = range_df[range_df['type']=='支出']['amount'].sum()
                c_calc1.metric("範圍內總筆數", f"{len(range_df)} 筆")
                c_calc2.metric("範圍內總支出", f"${total_in_range_exp:,.0f}")
                c_calc3.info("💡 請勾選上方表格來計算特定項目")
                
        else:
            st.info("該日期範圍內沒有交易資料。")

    st.markdown("---")
    
    # ==========================================
    # 🔥 詳細記錄 (編輯/刪除) - Supabase 版
    # ==========================================
    st.subheader("📋 詳細記錄 (可編輯與刪除)")
    
    all_cats = expense_cats + income_cats + ["其他"]
    all_pm = list(CREDIT_CARDS_CONFIG.keys())

    edited_df = st.data_editor(
        current_month_df.sort_values('date', ascending=False),
        column_config={
            "id": None, 
            "created_at": None,
            "deleted_at": None,
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
        num_rows="dynamic",
        hide_index=True,
        key="data_editor_main"
    )

    if st.button("💾 儲存變更"):
        with st.spinner("正在同步資料庫..."):
            original_map = current_month_df.set_index('id').to_dict('index')
            current_ids = set(row['id'] for i, row in edited_df.iterrows() if row['id'])
            original_ids = set(original_map.keys())
            
            changes_count = 0
            delete_count = 0

            # 1. 刪除
            deleted_ids = original_ids - current_ids
            for uid in deleted_ids:
                delete_transaction(uid)
                delete_count += 1

            # 2. 修改
            progress_bar = st.progress(0)
            total_rows = len(edited_df)
            
            for i, (idx, row) in enumerate(edited_df.iterrows()):
                uid = row['id']
                if not uid or uid not in original_map: continue 
                
                orig = original_map[uid]
                
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
                    if safe_update_transaction(row, orig):
                        changes_count += 1
                
                if total_rows > 0:
                    progress_bar.progress((i + 1) / total_rows)
            
            if changes_count > 0 or delete_count > 0:
                st.success(f"✅ 同步完成！更新 {changes_count} 筆，刪除 {delete_count} 筆。")
                get_data.clear()
                time.sleep(1)
                st.rerun()
            else:
                st.info("沒有偵測到任何變更。")