import streamlit as st
import pandas as pd
import io
import os

# --- CONFIGURATION ---
st.set_page_config(page_title="Pavan's Claim Scrubber", layout="wide")

# --- CACHED DATA LOADING ---
@st.cache_data
def load_master_data(master_path):
    """Loads and caches the heavy master file from the GitHub repository."""
    with pd.ExcelFile(master_path) as xls:
        # Load sheets with dynamic header detection
        cpt_df = pd.read_excel(xls, 'CPTHCPCS CODE', skiprows=3)
        mue_df = pd.read_excel(xls, 'MUE_Edits', skiprows=3)
        ncci_df = pd.read_excel(xls, 'NCCI_Edits')
        icd_df = pd.read_excel(xls, 'ICD-10')
        mod_df = pd.read_excel(xls, 'Modifier')
        
    # Pre-process for high-speed lookups
    mue_dict = dict(zip(mue_df.iloc[:, 0].astype(str).str.strip().upper(), mue_df.iloc[:, 1]))
    ncci_bundles = {(str(r[0]).strip().upper(), str(r[1]).strip().upper()): str(r[5]) for _, r in ncci_df.iterrows()}
    valid_cpts = set(cpt_df.iloc[:, 0].astype(str).str.strip().upper())
    
    return {
        "mue": mue_dict,
        "ncci": ncci_bundles,
        "valid_cpts": valid_cpts,
        "mods": set(mod_df['Code'].astype(str).str.strip().upper())
    }

# --- CORE SCRUBBING FUNCTION ---
def run_validation(df, data):
    results = []
    cpt_cols = [c for c in df.columns if 'CPT' in str(c).upper()]
    
    for _, row in df.iterrows():
        units = row.get('Units', 1)
        mods = [m.strip().upper() for m in str(row.get('Modifier', '')).replace(',', ' ').split() if m.strip()]
        row_cpts = [str(row[c]).strip().upper() for c in cpt_cols if pd.notna(row[c])]
        row_status, error_count = [], 0

        for cpt in row_cpts:
            # Automatic Anesthesia Padding (e.g., 100 -> 00100)
            if cpt.isdigit() and len(cpt) < 5: cpt = cpt.zfill(5)
            
            status_parts = []
            
            # 1. CPT Validity Check
            if cpt not in data['valid_cpts']:
                status_parts.append("❌ Invalid CPT")
                error_count += 1
            else:
                # 2. MUE Validation (Unit Limits)
                if cpt in data['mue'] and units > data['mue'][cpt]:
                    status_parts.append(f"⚠️ MUE Violation (Max: {data['mue'][cpt]})")
                    error_count += 1
                
                # 3. NCCI Bundling (Procedure Unbundling)
                for other in row_cpts:
                    if (other, cpt) in data['ncci'] and not any(m in ['59', '25', '91'] for m in mods):
                        status_parts.append(f"🚫 Bundled with {other}")
                        error_count += 1

            row_status.append(f"[{cpt}]: " + ("✅ Clean" if not status_parts else " | ".join(status_parts)))

        res = row.to_dict()
        res['Validation_Results'] = " | ".join(row_status)
        res['Status'] = "REJECTED" if error_count >= 1 else "ACCEPTED"
        results.append(res)
        
    return pd.DataFrame(results)

# --- APP INTERFACE ---
st.title("🏥 Namma Throttle Billing Scrubber")
st.markdown("Automated Medical Claim Scrubbing Tool for Windows & Mobile.")

MASTER_FILE = 'Billing_Master_Data.xlsx'

if os.path.exists(MASTER_FILE):
    data = load_master_data(MASTER_FILE)
    st.sidebar.success("✅ Master Data Connected")
    
    claim_file = st.file_uploader("Upload Claim_Entry.xlsx", type=['xlsx'])
    
    if claim_file:
        if st.button("🚀 Run Scrubber"):
            with st.spinner("Analyzing claims against NCCI/MUE edits..."):
                input_df = pd.read_excel(claim_file)
                processed_df = run_validation(input_df, data)
                
                st.success("Scrubbing Complete!")
                st.subheader("Results Preview")
                st.dataframe(processed_df[['Claim_ID', 'Status', 'Validation_Results']].head(15))
                
                # Create Excel for Download
                buffer = io.BytesIO()
                processed_df.to_excel(buffer, index=False)
                st.download_button(
                    label="📥 Download Full Scrubbed Results",
                    data=buffer.getvalue(),
                    file_name="Scrubbed_Results.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                )
else:
    st.error(f"'{MASTER_FILE}' not found. Please push it to GitHub using GitHub Desktop.")
