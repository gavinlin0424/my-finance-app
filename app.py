import streamlit as st
import pandas as pd
import sqlite3
import plotly.express as px
from datetime import datetime

# --- 設定頁面配置 ---
st.set_page_config(page_title="智能理財管家", page_icon="💰", layout="wide")

# --- 資料庫功能 (不變) ---
def init_db():
    conn = sqlite3.connect('expenses.db')
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS expenses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT,
            category TEXT,
            amount REAL,
            note TEXT
        )
    ''')
    conn.commit()
    conn.close()

def add_expense(date, category, amount, note):
    conn = sqlite3.connect('expenses.db')
    c = conn.cursor()
    c.execute('INSERT INTO expenses (date, category, amount, note) VALUES (?, ?, ?, ?)',
              (date, category, amount, note))
    conn.commit()
    conn.close()

def get_expenses():
    conn = sqlite3.connect('expenses.db')
    df = pd.read_sql_query("SELECT * FROM expenses", conn)
    conn.close()
    return df

init_db()

# --- 側邊欄：設定與輸入 ---
st.sidebar.header("📝 新增支出")
with st.sidebar.form("expense_form", clear_on_submit=True):
    date = st.date_input("日期", datetime.now())
    # 【修改 B】增加了 "寵物" 和 "進修"
    category = st.selectbox("類別", ["飲食", "交通", "娛樂", "購物", "居住", "醫療", "投資", "寵物", "進修", "其他"])
    amount = st.number_input("金額", min_value=0.0, step=10.0, format="%.2f")
    note = st.text_input("備註 (選填)")
    submitted = st.form_submit_button("提交")

    if submitted:
        if amount > 0:
            add_expense(date, category, amount, note)
            st.sidebar.success("已新增一筆支出！")
        else:
            st.sidebar.error("金額必須大於 0")

st.sidebar.markdown("---")
st.sidebar.header("⚙️ 預算設定")
budget = st.sidebar.number_input("本月預算上限", min_value=1000, value=10000, step=500)

# --- 主頁面 ---
st.title("💰 智能理財管家")

df = get_expenses()

if not df.empty:
    df['date'] = pd.to_datetime(df['date'])
    current_month = datetime.now().strftime("%Y-%m")
    df['month'] = df['date'].dt.strftime("%Y-%m")
    current_month_df = df[df['month'] == current_month]
    
    total_spent = current_month_df['amount'].sum()
    remaining_budget = budget - total_spent
    usage_percentage = (total_spent / budget) * 100

    col1, col2, col3 = st.columns(3)
    # 【修改 A】將符號改為 NT$
    col1.metric("本月總支出", f"NT${total_spent:,.0f}")
    col2.metric("剩餘預算", f"NT${remaining_budget:,.0f}", delta_color="normal" if remaining_budget > 0 else "inverse")
    
    bar_color = "green"
    if usage_percentage >= 100:
        bar_color = "red"
        st.error(f"⚠️ 警告：你已經超支了！ ({usage_percentage:.1f}%)")
    elif usage_percentage >= 80:
        bar_color = "orange"
        st.warning(f"⚠️ 注意：預算即將用盡 ({usage_percentage:.1f}%)")
    else:
        st.success(f"目前預算控制良好 ({usage_percentage:.1f}%)")
        
    progress_value = min(usage_percentage / 100, 1.0)
    st.progress(progress_value)

    st.markdown("---")

    c1, c2 = st.columns(2)
    
    with c1:
        st.subheader("📊 支出類別占比")
        if not current_month_df.empty:
            fig_pie = px.pie(current_month_df, values='amount', names='category', title=f'{current_month} 各類別花費')
            st.plotly_chart(fig_pie, use_container_width=True)
        else:
            st.info("本月尚無資料")

    with c2:
        st.subheader("📈 支出趨勢")
        daily_expense = df.groupby('date')['amount'].sum().reset_index()
        fig_line = px.line(daily_expense, x='date', y='amount', title='每日支出變化')
        st.plotly_chart(fig_line, use_container_width=True)

    st.subheader("📋 詳細記錄")
    st.dataframe(df.sort_values(by='date', ascending=False), use_container_width=True)

else:
    st.info("目前還沒有任何記帳資料，請從左側側邊欄新增！")