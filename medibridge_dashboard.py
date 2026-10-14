import os
import sqlite3
import time
from contextlib import closing

import pandas as pd
import streamlit as st


# ---------------------------------------------------------------------------
# Page configuration
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="MediBridge | Pharmacy Inventory",
    page_icon="💊",
    layout="wide",
    initial_sidebar_state="collapsed",
)


# ---------------------------------------------------------------------------
# Theme and styling
# ---------------------------------------------------------------------------
st.markdown(
    """
    <style>
    @import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700&display=swap');

    :root {
        --mb-teal: #0f766e;
        --mb-teal-dark: #073b3a;
        --mb-teal-light: #dff7f2;
        --mb-ink: #172b2b;
        --mb-muted: #718181;
        --mb-border: #e4eceb;
        --mb-bg: #f3f7f6;
    }

    html, body, [class*="css"] {
        font-family: 'DM Sans', sans-serif;
    }

    .stApp {
        background: var(--mb-bg);
    }

    [data-testid="stHeader"] {
        background: transparent;
    }

    [data-testid="stToolbar"] {
        right: 1rem;
    }

    .block-container {
        max-width: 1600px;
        padding-top: 1.3rem;
        padding-bottom: 2rem;
    }

    #MainMenu, footer {
        visibility: hidden;
    }

    /* Sidebar */
    section[data-testid="stSidebar"] {
        background: #102d2d;
        border-right: 1px solid rgba(255,255,255,.08);
    }
    section[data-testid="stSidebar"] * {
        color: #edf8f6;
    }
    section[data-testid="stSidebar"] [data-testid="stMarkdownContainer"] p {
        color: #c2d8d5;
    }
    section[data-testid="stSidebar"] .stButton button {
        background: #e5f8f4;
        color: #0c5550;
        border: 0;
        border-radius: 10px;
        font-weight: 700;
    }
    section[data-testid="stSidebar"] [data-testid="stSlider"] {
        padding-top: .2rem;
    }

    /* Hero banner */
    .mb-hero {
        position: relative;
        overflow: hidden;
        padding: 27px 32px 25px;
        margin: 0 0 20px 0;
        border-radius: 18px;
        color: #ffffff;
        background:
            radial-gradient(circle at 88% 12%, rgba(103,232,208,.23), transparent 25%),
            linear-gradient(118deg, #138b83 0%, #08756f 48%, #073b3a 100%);
        box-shadow: 0 12px 30px rgba(13, 94, 88, .14);
    }
    .mb-hero:after {
        content: "＋";
        position: absolute;
        right: 42px;
        top: -42px;
        font-size: 190px;
        line-height: 1;
        color: rgba(255,255,255,.075);
        font-weight: 300;
        transform: rotate(-15deg);
        pointer-events: none;
    }
    .mb-hero-top {
        display: flex;
        justify-content: space-between;
        align-items: center;
        gap: 16px;
        position: relative;
        z-index: 1;
    }
    .mb-brand {
        font-size: 25px;
        line-height: 1.2;
        letter-spacing: -.7px;
        font-weight: 700;
    }
    .mb-module {
        display: inline-block;
        margin-left: 10px;
        padding: 5px 9px;
        vertical-align: middle;
        border: 1px solid rgba(255,255,255,.25);
        border-radius: 7px;
        background: rgba(255,255,255,.1);
        font-size: 10px;
        letter-spacing: .7px;
        text-transform: uppercase;
        font-weight: 700;
    }
    .mb-live {
        display: inline-flex;
        align-items: center;
        gap: 7px;
        padding: 7px 11px;
        border: 1px solid rgba(255,255,255,.22);
        border-radius: 999px;
        background: rgba(255,255,255,.10);
        font-size: 11px;
        white-space: nowrap;
    }
    .mb-live-dot {
        height: 7px;
        width: 7px;
        border-radius: 50%;
        background: #8ef0c5;
        box-shadow: 0 0 0 4px rgba(142,240,197,.13);
    }
    .mb-hero h1 {
        color: white;
        font-size: clamp(23px, 2.2vw, 31px);
        line-height: 1.25;
        margin: 21px 0 7px;
        font-weight: 700;
        letter-spacing: -.8px;
        position: relative;
        z-index: 1;
    }
    .mb-hero p {
        color: rgba(255,255,255,.80);
        font-size: 13px;
        line-height: 1.65;
        max-width: 690px;
        margin: 0;
        position: relative;
        z-index: 1;
    }
    .mb-hero-foot {
        display: flex;
        flex-wrap: wrap;
        gap: 9px;
        margin-top: 17px;
        position: relative;
        z-index: 1;
    }
    .mb-hero-chip {
        border: 1px solid rgba(255,255,255,.17);
        background: rgba(255,255,255,.09);
        color: #f4fffd;
        padding: 7px 10px;
        border-radius: 8px;
        font-size: 11px;
    }

    /* Section headings */
    .mb-section-title {
        color: var(--mb-ink);
        font-size: 17px;
        font-weight: 700;
        letter-spacing: -.3px;
        margin: 2px 0 3px;
    }
    .mb-section-sub {
        color: var(--mb-muted);
        font-size: 11px;
        margin-bottom: 12px;
    }
    .mb-card {
        background: #ffffff;
        border: 1px solid var(--mb-border);
        border-radius: 15px;
        padding: 17px 18px;
        box-shadow: 0 4px 16px rgba(20, 60, 56, .035);
        min-height: 110px;
    }
    .mb-card-label {
        color: #728381;
        text-transform: uppercase;
        font-size: 10px;
        font-weight: 700;
        letter-spacing: .8px;
        margin-bottom: 11px;
    }
    .mb-card-value {
        color: var(--mb-ink);
        font-size: 29px;
        line-height: 1.1;
        font-weight: 700;
        letter-spacing: -.8px;
    }
    .mb-card-note {
        color: #82918f;
        font-size: 11px;
        margin-top: 8px;
    }
    .mb-card-icon {
        float: right;
        color: #0c8a7d;
        background: #e4f8f3;
        border-radius: 9px;
        padding: 7px 9px;
        font-size: 15px;
    }
    .mb-alert-card {
        background: white;
        border: 1px solid var(--mb-border);
        border-radius: 15px;
        padding: 18px;
        min-height: 180px;
        box-shadow: 0 4px 16px rgba(20, 60, 56, .035);
    }
    .mb-alert-title {
        font-size: 15px;
        font-weight: 700;
        color: var(--mb-ink);
        margin-bottom: 4px;
    }
    .mb-alert-sub {
        font-size: 11px;
        color: var(--mb-muted);
        margin-bottom: 13px;
    }
    .mb-alert-row {
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 12px;
        padding: 8px 0;
        border-bottom: 1px solid #eef2f1;
        font-size: 12px;
        color: #344b49;
    }
    .mb-alert-row:last-child { border-bottom: 0; }
    .mb-count {
        font-size: 10px;
        font-weight: 700;
        padding: 4px 8px;
        border-radius: 999px;
        white-space: nowrap;
    }
    .mb-count-red { color: #b42318; background: #fff0ee; }
    .mb-count-amber { color: #995800; background: #fff5d9; }
    .mb-count-green { color: #08765d; background: #e3f8ee; }
    .mb-note {
        border: 1px dashed #d6e4e1;
        background: #f8fbfa;
        border-radius: 10px;
        color: #6d7e7b;
        font-size: 12px;
        line-height: 1.6;
        padding: 12px;
    }

    /* Streamlit widgets */
div[data-testid="stTextInput"] input {
border-radius: 9px !important;
border: 1px solid #dce7e5 !important;
background-color: #ffffff !important;
color: #000000 !important;
-webkit-text-fill-color: #000000 !important;
caret-color: #000000 !important;
}

/* Search placeholder text */
div[data-testid="stTextInput"] input::placeholder {
color: #6b7280 !important;
opacity: 1 !important;
}

/* Dropdown styling */
div[data-testid="stSelectbox"] div[data-baseweb="select"] > div {
border-radius: 9px !important;
border-color: #dce7e5 !important;
}
    div[data-testid="stButton"] button {
        border-radius: 9px;
        font-weight: 600;
        min-height: 39px;
        border: 1px solid #dce7e5;
    }
    div[data-testid="stButton"] button[kind="primary"] {
        background: #087f75;
        border-color: #087f75;
        color: white;
    }
    div[data-testid="stDataFrame"] {
        border: 1px solid #e2ebe9;
        border-radius: 12px;
        overflow: hidden;
        background: white;
    }
    div[data-testid="stDataFrame"] [data-testid="glideDataEditor"] {
        border-radius: 12px;
    }
    hr {
        border-color: #e1eae8;
    }
    @media (max-width: 700px) {
        .block-container { padding: 1rem .8rem 2rem; }
        .mb-hero { padding: 22px 19px; }
        .mb-hero:after { right: 0; font-size: 120px; }
        .mb-hero-top { align-items: flex-start; flex-direction: column; }
        .mb-brand { font-size: 22px; }
    }
    </style>
    """,
    unsafe_allow_html=True,
)


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "medibridge.db")


@st.cache_data(ttl=5, show_spinner=False)
def fetch_inventory_data(db_path: str, refresh_token: int = 0) -> pd.DataFrame:
    """Read inventory from the SQLite database (supporting both normalized and flat schemas)."""
    del refresh_token  # Used only to invalidate the cache after manual refresh.

    if not os.path.exists(db_path):
        try:
            import mock_db
            mock_db.init_db()
        except Exception:
            return pd.DataFrame()

    try:
        with closing(sqlite3.connect(db_path)) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT count(*) FROM sqlite_master WHERE type='table' AND name='medicines'")
            has_medicines = cursor.fetchone()[0] > 0

            if has_medicines:
                query = """
                    SELECT
                        p.name AS "Pharmacy",
                        p.locality AS "Locality",
                        m.brand_name AS "Medicine Name",
                        m.generic_name AS "Salt Formulation",
                        CASE
                            WHEN i.stock_status = 'IN_STOCK' THEN 'In Stock'
                            WHEN i.stock_status = 'LOW_STOCK' THEN 'Low Stock'
                            ELSE 'Out of Stock'
                        END AS "Status",
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
                        i.medicine_name AS "Medicine Name",
                        i.salt AS "Salt Formulation",
                        CASE
                            WHEN i.is_available = 1 THEN 'In Stock'
                            ELSE 'Out of Stock'
                        END AS "Status",
                        i.last_updated AS "Last Verified"
                    FROM inventory i
                    JOIN pharmacies p ON i.pharmacy_id = p.id
                    ORDER BY i.medicine_name ASC, p.name ASC;
                """
            return pd.read_sql_query(query, conn)
    except (sqlite3.Error, pd.errors.DatabaseError) as exc:
        st.error(f"Database read error: {exc}")
        return pd.DataFrame()


# ---------------------------------------------------------------------------
# Session state and sidebar controls
# ---------------------------------------------------------------------------
if "refresh_token" not in st.session_state:
    st.session_state.refresh_token = 0
if "filter_search" not in st.session_state:
    st.session_state.filter_search = ""
if "filter_locality" not in st.session_state:
    st.session_state.filter_locality = "All Localities"
if "filter_status" not in st.session_state:
    st.session_state.filter_status = "All Statuses"


def reset_filters():
    st.session_state.filter_search = ""
    st.session_state.filter_locality = "All Localities"
    st.session_state.filter_status = "All Statuses"


with st.sidebar:
    st.markdown("## 💊 MediBridge")
    st.caption("PHARMACY INVENTORY ERP")
    st.markdown("---")

    st.markdown("### System controls")
    if st.button("↻  Refresh inventory", use_container_width=True):
        st.session_state.refresh_token += 1
        st.cache_data.clear()
        st.rerun()

    auto_refresh = st.toggle("Auto-refresh feed", value=False)
    refresh_interval = st.slider(
        "Refresh interval (seconds)",
        min_value=5,
        max_value=30,
        value=10,
        disabled=not auto_refresh,
    )

    st.markdown("---")
    st.markdown("### Database status")
    db_exists = os.path.exists(DB_PATH)
    if db_exists:
        db_size_kb = os.path.getsize(DB_PATH) / 1024
        st.success("SQLite database detected")
        st.caption(f"File: `medibridge.db`")
        st.caption(f"Size: {db_size_kb:.1f} KB")
    else:
        st.warning("Database not found")
        st.caption("Run `python mock_db.py` if that is how your project initializes sample data.")

    st.markdown("---")
    st.caption("MediBridge • Pharmacy inventory visibility")


# ---------------------------------------------------------------------------
# Load data
# ---------------------------------------------------------------------------
df = fetch_inventory_data(DB_PATH, st.session_state.refresh_token)
total_pharmacies = int(df["Pharmacy"].nunique()) if not df.empty else 0
total_items = int(len(df))
in_stock_count = int((df["Status"] == "In Stock").sum()) if not df.empty else 0
out_stock_count = int((df["Status"] == "Out of Stock").sum()) if not df.empty else 0


# ---------------------------------------------------------------------------
# Hero header
# ---------------------------------------------------------------------------
db_status = "Database connected" if db_exists else "Database not initialized"
st.markdown(
    f"""
    <div class="mb-hero">
      <div class="mb-hero-top">
        <div>
          <div class="mb-brand">MediBridge <span class="mb-module">Pharmacy ERP</span></div>
        </div>
        <div class="mb-live"><span class="mb-live-dot"></span>{db_status}</div>
      </div>
      <h1>Good evening, Pharmacy Team 👋</h1>
      <p>
        Your pharmacy inventory at a glance. Search local medicine availability,
        review stock status, and identify records that may need attention.
      </p>
      <div class="mb-hero-foot">
        <span class="mb-hero-chip">⌂ Delhi NCR inventory</span>
        <span class="mb-hero-chip">▣ SQLite data source</span>
        <span class="mb-hero-chip">↻ Live refresh available</span>
      </div>
    </div>
    """,
    unsafe_allow_html=True,
)


# ---------------------------------------------------------------------------
# KPI cards
# ---------------------------------------------------------------------------
st.markdown('<div class="mb-section-title">Inventory overview</div>', unsafe_allow_html=True)
st.markdown(
    '<div class="mb-section-sub">A quick summary of the records currently stored in your database.</div>',
    unsafe_allow_html=True,
)

k1, k2, k3, k4 = st.columns(4, gap="medium")
metrics = [
    (k1, "PHARMACIES REGISTERED", total_pharmacies, "🏥", "Unique pharmacies in the database", ""),
    (k2, "TRACKED INVENTORY RECORDS", total_items, "▤", "Medicine–pharmacy records", ""),
    (k3, "IN STOCK", in_stock_count, "✓", "Records marked available", "green"),
    (k4, "OUT OF STOCK", out_stock_count, "!", "Records marked unavailable", "red"),
]
for col, label, value, icon, note, tone in metrics:
    value_class = "mb-card-value"
    if tone == "green":
        value_class += ' style="color:#0c9676"'
    elif tone == "red":
        value_class += ' style="color:#dc5656"'
    with col:
        st.markdown(
            f"""
            <div class="mb-card">
              <span class="mb-card-icon">{icon}</span>
              <div class="mb-card-label">{label}</div>
              <div class="{value_class}">{value:,}</div>
              <div class="mb-card-note">{note}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )

st.write("")


# ---------------------------------------------------------------------------
# Inventory search and table
# ---------------------------------------------------------------------------
left, right = st.columns([1.75, 1], gap="large")

with left:
    st.markdown('<div class="mb-section-title">Medicine inventory</div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="mb-section-sub">Search and filter the current inventory records.</div>',
        unsafe_allow_html=True,
    )

    f1, f2, f3 = st.columns([1.55, 1, 1], gap="small")
    with f1:
        search_query = st.text_input(
            "Search medicine",
            placeholder="Search brand or salt formulation…",
            key="filter_search",
            label_visibility="collapsed",
        ).strip().lower()

    locality_options = ["All Localities"]
    status_options = ["All Statuses", "In Stock", "Out of Stock"]
    if not df.empty:
        locality_options += sorted(df["Locality"].dropna().astype(str).unique().tolist())

    # If the database changes while the page is open, avoid invalid selectbox values.
    if st.session_state.filter_locality not in locality_options:
        st.session_state.filter_locality = "All Localities"
    if st.session_state.filter_status not in status_options:
        st.session_state.filter_status = "All Statuses"

    with f2:
        selected_locality = st.selectbox(
            "Locality",
            locality_options,
            key="filter_locality",
            label_visibility="collapsed",
        )
    with f3:
        selected_status = st.selectbox(
            "Stock status",
            status_options,
            key="filter_status",
            label_visibility="collapsed",
        )

    filtered_df = df.copy()
    if not filtered_df.empty and search_query:
        name_match = filtered_df["Medicine Name"].fillna("").str.lower().str.contains(
            search_query, regex=False
        )
        salt_match = filtered_df["Salt Formulation"].fillna("").str.lower().str.contains(
            search_query, regex=False
        )
        filtered_df = filtered_df[name_match | salt_match]

    if selected_locality != "All Localities" and not filtered_df.empty:
        filtered_df = filtered_df[filtered_df["Locality"] == selected_locality]
    if selected_status != "All Statuses" and not filtered_df.empty:
        filtered_df = filtered_df[filtered_df["Status"] == selected_status]

    b1, b2 = st.columns([1, 5])
    with b1:
        st.button("Clear filters", on_click=reset_filters, use_container_width=True)
    with b2:
        st.caption(f"Showing **{len(filtered_df)}** of **{len(df)}** records")

    if df.empty:
        if not db_exists:
            st.warning(
                "The database file was not found. Check that `medibridge.db` is in the same "
                "directory as this Streamlit script and initialize it using your project's setup."
            )
        else:
            st.info("The database is present, but no inventory records were returned.")
    elif filtered_df.empty:
        st.warning("No records match these filters. Try a different search or clear the filters.")
    else:
        display_df = filtered_df.copy()
        display_df["Status"] = display_df["Status"].map(
            {"In Stock": "🟢 In Stock", "Out of Stock": "🔴 Out of Stock"}
        ).fillna(display_df["Status"])

        st.dataframe(
            display_df,
            use_container_width=True,
            hide_index=True,
            height=390,
            column_config={
                "Pharmacy": st.column_config.TextColumn("Pharmacy", width="medium"),
                "Locality": st.column_config.TextColumn("Locality / Zone", width="medium"),
                "Medicine Name": st.column_config.TextColumn("Medicine (Brand)", width="medium"),
                "Salt Formulation": st.column_config.TextColumn("Salt / Formulation", width="large"),
                "Status": st.column_config.TextColumn("Stock status", width="medium"),
                "Last Verified": st.column_config.TextColumn("Last verified", width="medium"),
            },
        )

    csv_data = filtered_df.to_csv(index=False).encode("utf-8") if not filtered_df.empty else b""
    st.download_button(
        "↓  Export filtered inventory (CSV)",
        data=csv_data,
        file_name="medibridge_inventory.csv",
        mime="text/csv",
        disabled=filtered_df.empty,
        use_container_width=True,
    )


# ---------------------------------------------------------------------------
# Operational insights panel (only derived from fields that currently exist)
# ---------------------------------------------------------------------------
with right:
    st.markdown('<div class="mb-section-title">Smart inventory insights</div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="mb-section-sub">Rule-based observations from the current database fields.</div>',
        unsafe_allow_html=True,
    )

    if total_items:
        availability_pct = round((in_stock_count / total_items) * 100, 1)
        stockout_pct = round((out_stock_count / total_items) * 100, 1)
        st.markdown(
            f"""
            <div class="mb-alert-card">
              <div class="mb-alert-title">📊 Availability snapshot</div>
              <div class="mb-alert-sub">Based on records currently loaded</div>
              <div class="mb-alert-row"><span>Availability rate</span><span class="mb-count mb-count-green">{availability_pct}%</span></div>
              <div class="mb-alert-row"><span>Out-of-stock share</span><span class="mb-count {'mb-count-red' if out_stock_count else 'mb-count-green'}">{stockout_pct}%</span></div>
              <div class="mb-alert-row"><span>Unique pharmacies</span><strong>{total_pharmacies}</strong></div>
              <div class="mb-alert-row"><span>Inventory records</span><strong>{total_items}</strong></div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            '<div class="mb-alert-card"><div class="mb-alert-title">📊 Availability snapshot</div>'
            '<div class="mb-note">Insights will appear when inventory records are available.</div></div>',
            unsafe_allow_html=True,
        )

    st.write("")
    st.markdown(
        f"""
        <div class="mb-alert-card">
          <div class="mb-alert-title">🚨 Stock availability alerts</div>
          <div class="mb-alert-sub">Records marked unavailable in the source database</div>
          <div class="mb-alert-row"><span>Out-of-stock records</span>
            <span class="mb-count {'mb-count-red' if out_stock_count else 'mb-count-green'}">{out_stock_count} records</span>
          </div>
          <div class="mb-alert-row"><span>Available records</span>
            <span class="mb-count mb-count-green">{in_stock_count} records</span>
          </div>
          <div class="mb-note" style="margin-top:12px;">
            This database currently stores availability, not numeric quantities or reorder thresholds.
            Quantity-based low-stock recommendations are therefore not calculated.
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


# ---------------------------------------------------------------------------
# Bottom operational cards
# ---------------------------------------------------------------------------
st.write("")
st.markdown('<div class="mb-section-title">Operations & follow-up</div>', unsafe_allow_html=True)
st.markdown(
    '<div class="mb-section-sub">Operational panels are shown without fabricating fields absent from the current schema.</div>',
    unsafe_allow_html=True,
)
c1, c2, c3 = st.columns(3, gap="medium")

with c1:
    st.markdown(
        """
        <div class="mb-alert-card">
          <div class="mb-alert-title">⚠️ Stock follow-up</div>
          <div class="mb-alert-sub">Availability records that may need checking</div>
        """,
        unsafe_allow_html=True,
    )
    st.markdown(
        f'<div class="mb-alert-row"><span>Marked out of stock</span><span class="mb-count {"mb-count-red" if out_stock_count else "mb-count-green"}">{out_stock_count}</span></div>',
        unsafe_allow_html=True,
    )
    if out_stock_count and not df.empty:
        unavailable = df[df["Status"] == "Out of Stock"]["Medicine Name"].dropna().drop_duplicates().head(4)
        for medicine in unavailable:
            st.markdown(
                f'<div class="mb-alert-row"><span>{medicine}</span><span class="mb-count mb-count-amber">Verify</span></div>',
                unsafe_allow_html=True,
            )
    else:
        st.markdown('<div class="mb-note">No records are currently marked out of stock.</div>', unsafe_allow_html=True)
    st.markdown("</div>", unsafe_allow_html=True)

with c2:
    st.markdown(
        """
        <div class="mb-alert-card">
          <div class="mb-alert-title">📅 Expiry monitoring</div>
          <div class="mb-alert-sub">Expiry dates are not present in the current query</div>
          <div class="mb-note">
            Add or expose an expiry-date field in the inventory schema before showing
            expiry alerts or days remaining. No expiry status is being guessed here.
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

with c3:
    st.markdown(
        """
        <div class="mb-alert-card">
          <div class="mb-alert-title">🚚 Supplier updates</div>
          <div class="mb-alert-sub">Supplier and purchase-order data is not connected here</div>
          <div class="mb-note">
            This view currently reads pharmacy availability records only. Supplier status,
            purchase orders, and reorder actions can be connected when those tables or
            backend endpoints are available.
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

st.markdown(
    """
    <div style="text-align:center;color:#8a9a97;font-size:11px;padding:20px 0 2px;">
      MediBridge Pharmacy ERP · Inventory visibility for Delhi NCR
    </div>
    """,
    unsafe_allow_html=True,
)


# ---------------------------------------------------------------------------
# Optional auto-refresh
# ---------------------------------------------------------------------------
# This uses Streamlit's rerun mechanism and does not change the database.
# It pauses between refreshes, matching the existing app's polling behaviour.
if auto_refresh:
    time.sleep(refresh_interval)
    st.session_state.refresh_token += 1
    st.rerun()
