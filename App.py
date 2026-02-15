import streamlit as st
import pandas as pd
import io

# --- CONFIGURATION ---
st.set_page_config(page_title="Namma Throttle Scrubber", layout="wide")

# --- CACHED DATA LOADING ---
@st.cache_data
def load_master_data(uploaded_master):
    try:
        with pd.ExcelFile(uploaded_master) as xls:
            cpt_df = pd.read_excel(xls, 'CPTHCPCS CODE', skiprows=3)
            mue_df = pd.read_excel(xls, 'MUE_Edits', skiprows=3)
            ncci_df = pd.read_excel(xls, 'NCCI_Edits')
            
        mue_dict = dict(zip(mue_df.iloc[:, 0].astype(str).str.strip().str.upper(), mue_df.iloc[:, 1]))
        ncci_bundles = {(str(r[0]).strip().upper(), str(r[1]).strip().upper()): str(r[5]) for _, r in ncci_df.iterrows()}
        valid_cpts = set(cpt_df.iloc[:, 0].astype(str).str.strip().str.upper())
        
        return {"mue": mue_dict, "ncci": ncci_bundles, "valid_cpts": valid_cpts}
    except Exception as e:
        st.error(f"Error reading master file: {e}")
        return None

# --- CORE SCRUBBING FUNCTION ---
def run_validation_with_progress(df, data):
    results = []
    cpt_cols = [c for c in df.columns if 'CPT' in str(c).upper()]
    total_rows = len(df)
    
    progress_bar = st.progress(0)
    status_text = st.empty()
    
    for i, row in df.iterrows():
        progress_bar.progress((i + 1) / total_rows)
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
st.title("🏥 Namma Throttle Billing Scrubber")

with st.sidebar:
    st.header("📂 Data Upload")
    master_file = st.file_uploader("1. Upload Master Data (Excel)", type=['xlsx'])
    claim_file = st.file_uploader("2. Upload Claim Entry List", type=['xlsx'])

if master_file and claim_file:
    data = load_master_data(master_file)
    
    if data:
        if st.button("🚀 Run Scrubber"):
            input_df = pd.read_excel(claim_file)
            processed_df = run_validation_with_progress(input_df, data)
            
            # --- FIXED SUMMARY STATISTICS ---
            st.subheader("📊 Scrubbing Summary")
            total = len(processed_df)
            accepted = len(processed_df[processed_df['Status'] == 'ACCEPTED'])
            rejected = len(processed_df[processed_df['Status'] == 'REJECTED'])
            
            # Corrected Column Usage
            m1, m2, m3 = st.columns(3)
            m1.metric("Total Claims", total)
            m2.metric("Accepted ✅", accepted, f"{int((accepted/total)*100)}%")
            m3.metric("Rejected ❌", rejected, f"{int((rejected/total)*100)}%", delta_color="inverse")
            
            st.subheader("📋 Preview Results")
            st.dataframe(processed_df[['Claim_ID', 'Status', 'Validation_Results']].head(20))
            
            buffer = io.BytesIO()
            processed_df.to_excel(buffer, index=False)
            st.download_button("📥 Download Full Results", buffer.getvalue(), "Scrubbed_Results.xlsx")
else:
    st.info("Please upload both files in the sidebar to begin.")
