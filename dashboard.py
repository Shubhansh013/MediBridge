import streamlit as st
import pandas as pd
import sqlite3
import os
import time

st.set_page_config(
    page_title="MediBridge Live Monitor",
    page_icon="🏥",
    layout="wide"
)

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "medibridge.db")

# Custom Title & Badges
st.markdown("""
    <div style="background-color:#1E3A8A;padding:15px;border-radius:10px;margin-bottom:20px;">
        <h1 style="color:white;margin:0;font-size:28px;">🏥 MediBridge — Real-Time Stock Engine</h1>
        <p style="color:#93C5FD;margin:5px 0 0 0;font-size:14px;">
            IEEE Hackathon Demo Monitor • Pilot City: Delhi NCR (Rohini Sector 14 & 15)
        </p>
    </div>
""", unsafe_allow_html=True)

# Sidebar controls
st.sidebar.header("⚙️ Demo Controls")
auto_refresh = st.sidebar.checkbox("Auto-Refresh Live Stream", value=True)
refresh_interval = st.sidebar.slider("Refresh Rate (seconds)", min_value=1, max_value=5, value=2)

if st.sidebar.button("🔄 Reset Demo Database"):
    import mock_db
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)
    mock_db.init_db()
    st.sidebar.success("Database reset to initial demo state!")
    st.rerun()

st.sidebar.markdown("---")
st.sidebar.info(
    "💡 **Judge Demo Tip:**\n"
    "1. Keep this page open on your laptop.\n"
    "2. On your phone, send `Dolo 650 khatam` to the bot.\n"
    "3. Tap **Confirm**.\n"
    "4. Watch the status below flip to **OUT OF STOCK** automatically!"
)

# Fetch Data
def get_live_data():
    if not os.path.exists(DB_PATH):
        try:
            import mock_db
            mock_db.init_db()
        except Exception:
            return pd.DataFrame()

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT count(*) FROM sqlite_master WHERE type='table' AND name='medicines'")
    has_medicines = cursor.fetchone()[0] > 0

    if has_medicines:
        query = """
            SELECT
                p.name AS "Pharmacy",
                p.locality AS "Locality",
                m.brand_name AS "Medicine",
                m.generic_name AS "Salt Formulation",
                CASE
                    WHEN i.stock_status = 'IN_STOCK' THEN '🟢 IN STOCK'
                    WHEN i.stock_status = 'LOW_STOCK' THEN '🟡 LOW STOCK'
                    ELSE '🔴 OUT OF STOCK'
                END AS "Live Status",
                coalesce(i.last_confirmed_at, i.updated_at, i.created_at) AS "Last Verified"
            FROM inventory i
            JOIN pharmacies p ON i.pharmacy_id = p.id
            JOIN medicines m ON i.medicine_id = m.id
            ORDER BY m.brand_name ASC, p.name ASC;
        """
    else:
        query = """
            SELECT
                p.name AS "Pharmacy",
                p.locality AS "Locality",
                i.medicine_name AS "Medicine",
                i.salt AS "Salt Formulation",
                CASE WHEN i.is_available = 1 THEN '🟢 IN STOCK' ELSE '🔴 OUT OF STOCK' END AS "Live Status",
                i.last_updated AS "Last Verified"
            FROM inventory i
            JOIN pharmacies p ON i.pharmacy_id = p.id
            ORDER BY i.medicine_name ASC, p.name ASC;
        """
    df = pd.read_sql_query(query, conn)
    conn.close()
    return df

df = get_live_data()

if df.empty:
    st.warning("No data found in database. Please run bot.py first to initialize data.")
else:
    # Key Metrics Bar
    total_pharmacies = len(df["Pharmacy"].unique())
    total_items = len(df)
    in_stock_count = len(df[df["Live Status"] == "🟢 IN STOCK"])
    out_stock_count = len(df[df["Live Status"] == "🔴 OUT OF STOCK"])

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("🏥 Registered Pharmacies", f"{total_pharmacies} Shops")
    col2.metric("💊 Tracked SKU Items", f"{total_items} Items")
    col3.metric("🟢 In Stock Items", in_stock_count)
    col4.metric("🔴 Out of Stock Alerts", out_stock_count)

    st.markdown("### 📊 Live Inventory Matrix")

    # Styling helper for dataframe
    def highlight_status(row):
        color = '#d1e7dd' if 'IN STOCK' in row['Live Status'] else '#f8d7da'
        text_color = '#0f5132' if 'IN STOCK' in row['Live Status'] else '#842029'
        return [f'background-color: {color}; color: {text_color}; font-weight: bold;' if col == 'Live Status' else '' for col in row.index]

    styled_df = df.style.apply(highlight_status, axis=1)
    st.dataframe(styled_df, use_container_width=True, height=350)

# Real-time polling
if auto_refresh:
    time.sleep(refresh_interval)
    st.rerun()
