import streamlit as st
import pandas as pd
import io
import time

# --- CONFIGURATION ---
st.set_page_config(page_title="Namma Throttle Scrubber", layout="wide")

# --- CACHED DATA LOADING ---
@st.cache_data
def load_master_data(uploaded_master):
    """Processes the master file once and caches it."""
    try:
        with pd.ExcelFile(uploaded_master) as xls:
            cpt_df = pd.read_excel(xls, 'CPTHCPCS CODE', skiprows=3)
            mue_df = pd.read_excel(xls, 'MUE_Edits', skiprows=3)
            ncci_df = pd.read_excel(xls, 'NCCI_Edits')
            
        # FIX: Added .str before .upper() for Series operations
        mue_dict = dict(zip(mue_df.iloc[:, 0].astype(str).str.strip().str.upper(), mue_df.iloc[:, 1]))
        
        # NCCI logic uses iterrows (single strings), so .upper() works fine there
        ncci_bundles = {(str(r[0]).strip().upper(), str(r[1]).strip().upper()): str(r[5]) for _, r in ncci_df.iterrows()}
        
        # FIX: Added .str before .upper() here as well
        valid_cpts = set(cpt_df.iloc[:, 0].astype(str).str.strip().str.upper())
        
        return {"mue": mue_dict, "ncci": ncci_bundles, "valid_cpts": valid_cpts}
    except Exception as e:
        st.error(f"Error reading master file: {e}")
        return None

# --- CORE SCRUBBING FUNCTION WITH PROGRESS BAR ---
def run_validation_with_progress(df, data):
    results = []
    cpt_cols = [c for c in df.columns if 'CPT' in str(c).upper()]
    total_rows = len(df)
    
    progress_bar = st.progress(0)
    status_text = st.empty()
    
    for i, row in df.iterrows():
        percent_complete = (i + 1) / total_rows
        progress_bar.progress(percent_complete)
        status_text.text(f"Scrubbing Claim {i+1} of {total_rows}...")

        units = row.get('Units', 1)
        mods = [m.strip().upper() for m in str(row.get('Modifier', '')).replace(',', ' ').split() if m.strip()]
        row_cpts = [str(row[c]).strip().upper() for c in cpt_cols if pd.notna(row[c])]
        row_status, error_count = [], 0

        for cpt in row_cpts:
            if cpt.isdigit() and len(cpt) < 5: cpt = cpt.zfill(5)
            
            status_parts = []
            if cpt not in data['valid_cpts']:
                status_parts.append("❌ Invalid CPT")
                error_count += 1
            else:
                if cpt in data['mue'] and units > data['mue'][cpt]:
                    status_parts.append(f"⚠️ MUE Violation (Max: {data['mue'][cpt]})")
                    error_count += 1
                
                for other in row_cpts:
                    if (other, cpt) in data['ncci'] and not any(m in ['59', '25', '91'] for m in mods):
                        status_parts.append(f"🚫 Bundled with {other}")
                        error_count += 1

            row_status.append(f"[{cpt}]: " + ("✅ Clean" if not status_parts else " | ".join(status_parts)))

        res = row.to_dict()
        res['Validation_Results'] = " | ".join(row_status)
        res['Status'] = "REJECTED" if error_count >= 1 else "ACCEPTED"
        results.append(res)
    
    progress_bar.empty()
    status_text.empty()
    return pd.DataFrame(results)

# --- APP INTERFACE ---
st.title("🏥 Billing Scrubber")

with st.sidebar:
    st.header("📂 Data Upload")
    master_file = st.file_uploader("1. Upload Master Data (Excel)", type=['xlsx'])
    claim_file = st.file_uploader("2. Upload Claim Entry List", type=['xlsx'])

if master_file and claim_file:
    with st.spinner("Extracting Master Logic..."):
        data = load_master_data(master_file)
    
    if data:
        if st.button("🚀 Run Scrubber"):
            input_df = pd.read_excel(claim_file)
            processed_df = run_validation_with_progress(input_df, data)
            
            # --- SUMMARY STATISTICS ---
            st.subheader("📊 Scrubbing Summary")
            total = len(processed_df)
            accepted = len(processed_df[processed_df['Status'] == 'ACCEPTED'])
            rejected = len(processed_df[processed_df['Status'] == 'REJECTED'])
            
            col1, col2, col3 = st.columns(3)
            col1.metric("Total Claims", total)
            col2.metric("Accepted ✅", accepted, f"{int((accepted/total)*100)}%")
            col3
