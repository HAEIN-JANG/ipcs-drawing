import streamlit as st
import pandas as pd
import io
import os
import cloudinary
import cloudinary.uploader
from datetime import datetime
from supabase import create_client, Client, ClientOptions
from concurrent.futures import ThreadPoolExecutor

# ==========================================
# 1. Configuration & Secrets
# ==========================================
st.set_page_config(
    page_title="IPCS Drawing Management",
    page_icon="🏗️",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Professional Dashboard Layout Refinement
st.markdown("""
    <style>
        .block-container { 
            padding-top: 3rem !important; 
            padding-bottom: 0rem !important; 
            max-width: 98% !important;
        }
        
        h1 { 
            font-size: 1.7rem !important; 
            font-weight: 800 !important; 
            margin-bottom: 1rem !important; 
            color: #0f172a;
            letter-spacing: -0.05rem;
        }
        
        .main { background: #fdfdfd; }
        
        /* KPI Cards - Refined Spacing & Typography */
        [data-testid="stMetric"] {
            background: white;
            padding: 12px 18px !important;
            border-radius: 8px;
            box-shadow: 0 1px 3px rgba(0,0,0,0.1);
            border: 1px solid #e2e8f0;
        }
        [data-testid="stMetricLabel"] { 
            font-size: 0.75rem !important; 
            color: #475569 !important; 
            margin-bottom: 4px !important; /* Fixed: Added spacing */
            font-weight: 500 !important;
        }
        [data-testid="stMetricValue"] { 
            font-size: 1.6rem !important; 
            font-weight: 800 !important; 
            color: #1e293b !important; 
            line-height: 1.2 !important;
        }
        
        /* Tab Styling - Right Aligned & Blue Theme */
        [data-testid="stTabPanel"] [role="tablist"] {
            justify-content: flex-end !important;
        }
        button[data-testid="stBasewebTab"] {
            height: 35px !important;
            padding: 0px 15px !important;
        }
        button[data-testid="stBasewebTab"][aria-selected="true"] {
            color: #0b5394 !important;
            border-bottom: 2px solid #0b5394 !important;
        }
        button[data-testid="stBasewebTab"][aria-selected="true"] p {
            color: #0b5394 !important;
        }

        /* Data Table Height & Spacing - Minimized Gap */
        .stDataFrame {
            margin-top: -25px !important; /* Minimized even further */
        }

        /* Force Center Alignment for Dataframe Headers */
        [data-testid="stDataFrame"] div[role="columnheader"] > div {
            justify-content: center !important;
            text-align: center !important;
        }

        /* Remove default padding from tab panels */
        [data-testid="stTabPanel"] {
            padding-top: 0px !important;
        }
        
        /* Reduce global block gap */
        .stVerticalBlock { gap: 0.2rem !important; }
    </style>
""", unsafe_allow_html=True)

def get_secret(key, default=None):
    try:
        return st.secrets[key]
    except:
        return os.environ.get(key, default)

@st.cache_resource
def get_supabase() -> Client:
    url = get_secret("SUPABASE_URL")
    key = get_secret("SUPABASE_KEY")
    if not url or not key:
        st.error("Missing Supabase configuration")
        st.stop()
    options = ClientOptions(schema="public")
    return create_client(url, key, options=options)

# Configure Cloudinary
c_name = get_secret("CLOUDINARY_NAME")
c_key = get_secret("CLOUDINARY_API_KEY")
c_secret = get_secret("CLOUDINARY_API_SECRET")
if all([c_name, c_key, c_secret]):
    cloudinary.config(cloud_name=c_name, api_key=c_key, api_secret=c_secret, secure=True)

TABLE_ALL = "dwg_iso"
TABLE_LATEST = "dwg_latest"

@st.cache_data(ttl=600, show_spinner=False)
def get_cached_stats():
    supabase = get_supabase()
    total_res = supabase.table(TABLE_ALL).select("id", count="exact").limit(1).execute()
    c01_res   = supabase.table(TABLE_ALL).select("id", count="exact").eq("revision", "C01").execute()
    c01a_res  = supabase.table(TABLE_ALL).select("id", count="exact").eq("revision", "C01A").execute()
    c01b_res  = supabase.table(TABLE_ALL).select("id", count="exact").eq("revision", "C01B").execute()
    return {
        "Total": total_res.count if hasattr(total_res, 'count') else 0,
        "C01": c01_res.count if hasattr(c01_res, 'count') else 0,
        "C01A": c01a_res.count if hasattr(c01a_res, 'count') else 0,
        "C01B": c01b_res.count if hasattr(c01b_res, 'count') else 0
    }

@st.cache_data(ttl=300, show_spinner="Fetching...")
def fetch_data(search_query="", area="All", system="All", status="All", limit=150, offset=0):
    supabase = get_supabase()
    target_table = TABLE_LATEST if status == "All" else TABLE_ALL
    query = supabase.table(target_table).select("*", count="exact")
    if search_query:
        query = query.or_(f"drawing_no.ilike.%{search_query}%,line_no.ilike.%{search_query}%,title.ilike.%{search_query}%")
    if area != "All": query = query.eq("area", area)
    if system != "All": query = query.eq("system", system)
    if status != "All": query = query.eq("revision", status)
    res = query.order("drawing_no").range(offset, offset + limit - 1).execute()
    return res.data, res.count

def get_cloudinary_url(file_key):
    if not file_key: return None
    if file_key.startswith("http"): return file_key
    return cloudinary.utils.cloudinary_url(file_key, resource_type="image", secure=True)[0]

# ==========================================
# 3. UI Components
# ==========================================
def main():
    st.title("🏗️ IPCS Drawing Management System")

    with st.sidebar:
        st.header("Search & Filters")
        search_query = st.text_input("🔍 Keyword Search", placeholder="Drawing No, Title...")
        area_filter = st.selectbox("Area", ["All", "MB", "YARD", "YD BLDG"])
        system_filter = st.selectbox("System", ["All", "AS", "ATM", "CCW", "CD", "DW", "FG", "FGH", "FO", "FW", "GT MISC", "HP", "HW", "IA", "LO", "LP", "N2", "PW", "RW", "SA", "SS", "ST MISC", "SW", "WWT"])
        status_filter = st.selectbox("Revision", ["All", "C01", "C01A", "C01B"])
        st.markdown("---")
        st.info("IPCS Cloud synced.")

    stats = get_cached_stats()
    cols = st.columns(4)
    cols[0].metric("Total Drawings", f"{stats['Total']:,}")
    cols[1].metric("Revision C01", f"{stats['C01']:,}")
    cols[2].metric("Revision C01A", f"{stats['C01A']:,}")
    cols[3].metric("Revision C01B", f"{stats['C01B']:,}")

    tab_list, tab_upload, tab_export = st.tabs(["📋 Drawing List", "📤 Upload Data", "📥 Export & Reports"])

    with tab_list:
        # Adjusted to exactly 17 rows per view
        per_page = 17
        if 'page' not in st.session_state: st.session_state.page = 1
        data, total_count = fetch_data(search_query, area_filter, system_filter, status_filter, limit=per_page, offset=(st.session_state.page - 1) * per_page)
        
        if data:
            df = pd.DataFrame(data)
            
            # Drawing Link Transformer
            def create_link_with_id(row):
                fk = str(row.get('file_link', '')).strip()
                dwg = str(row.get('drawing_no', '')).strip()
                if fk:
                    url = get_cloudinary_url(fk)
                    return f"{url}#{dwg}"
                return dwg

            df['drawing_link'] = df.apply(create_link_with_id, axis=1)
            
            # CSS hack to minimize bottom gap
            st.markdown("<style>div[data-testid='stDataFrame'] { font-size: 10px !important; margin-bottom: -30px !important; }</style>", unsafe_allow_html=True)

            st.dataframe(
                df,
                column_order=("drawing_link", "revision", "area", "system", "title", "issued_date"),
                column_config={
                    "drawing_link": st.column_config.LinkColumn(
                        "Drawing No.",
                        help="Click to open drawing",
                        display_text=r"#(.+)$",
                        alignment="center"
                    ),
                    "revision": st.column_config.TextColumn("Rev.", alignment="center"),
                    "area": st.column_config.TextColumn("Area", alignment="center"),
                    "system": st.column_config.TextColumn("System", alignment="center"),
                    "title": st.column_config.TextColumn("Drawing Title", alignment="center"),
                    "issued_date": st.column_config.TextColumn("Issued Date", alignment="center")
                },
                use_container_width=True,
                hide_index=True,
                height=600 # Precision height for 17 rows to avoid empty space and crash
            )

            # Footer Pagination
            total_pages = (total_count + per_page - 1) // per_page
            p_cols = st.columns([1, 2, 1])
            if st.session_state.page > 1 and p_cols[0].button("Previous"):
                st.session_state.page -= 1
                st.rerun()
            if st.session_state.page < total_pages and p_cols[2].button("Next"):
                st.session_state.page += 1
                st.rerun()
            p_cols[1].markdown(f"<center><small>Page {st.session_state.page} of {total_pages} ({total_count} records)</small></center>", unsafe_allow_html=True)
        else:
            st.warning("No data found.")

    with tab_upload:
        st.subheader("Import Excel Data")
        uploaded_file = st.file_uploader("Choose an Excel file", type=["xlsx", "xls"])
        if uploaded_file is not None and st.button("Process & Upload"):
            with st.spinner("Processing..."):
                try:
                    df_up = pd.read_excel(uploaded_file)
                    df_up.columns = [str(c).lower().strip() for c in df_up.columns]
                    records = []
                    for _, r in df_up.iterrows():
                        dr_no = str(r.get("drawing_no", r.get("drawing_n", ""))).strip()
                        if dr_no: records.append({"drawing_no": dr_no, "line_no": str(r.get("line_no", "")).strip(), "system": str(r.get("system", "")).strip(), "area": str(r.get("area", "")).strip(), "bore": str(r.get("bore", "")).strip(), "title": str(r.get("title", "")).strip(), "revision": str(r.get("revision", "")).strip(), "file_link": str(r.get("file_link", "")).strip()})
                    if records:
                        supabase = get_supabase()
                        for i in range(0, len(records), 1000):
                            supabase.table(TABLE_ALL).upsert(records[i:i+1000], on_conflict="drawing_no,revision").execute()
                        st.success(f"Success: {len(records)} records.")
                except Exception as e: st.error(f"Error: {e}")

    with tab_export:
        st.subheader("Data Export")
        if st.button("Generate Excel Master List"):
            with st.spinner("Preparing..."):
                try:
                    supabase = get_supabase()
                    res = supabase.table(TABLE_ALL).select("*")
                    all_data = res.execute().data
                    if all_data:
                        export_df = pd.DataFrame(all_data)
                        output = io.BytesIO()
                        with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
                            export_df.to_excel(writer, index=False, sheet_name='DrawingMaster')
                        st.download_button(label="Download Excel File", data=output.getvalue(), file_name=f"ISO_Master_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
                except Exception as e: st.error(f"Failed: {e}")

if __name__ == "__main__":
    main()
