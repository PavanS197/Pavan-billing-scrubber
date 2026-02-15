import streamlit as st
import pandas as pd
import io

# --- CONFIGURATION ---
st.set_page_config(page_title="Billing Scrubber", layout="wide")

# --- 1. DATA CLEANING HELPER ---
def clean_code(val):
    if pd.isna(val) or str(val).strip() == "": 
        return ""
    # Strip .0 and whitespace
    s = str(val).split('.')[0].strip().upper()
    # Padding for Anesthesia: restore leading zeros (e.g., 100 -> 00100)
    if s.isdigit() and len(s) < 5:
        s = s.zfill(5)
    return s

# --- 2. ROBUST MASTER DATA LOADER ---
@st.cache_data(show_spinner=True)
def load_master_data(uploaded_file):
    try:
        with pd.ExcelFile(uploaded_file) as xls:
            # Deep scan all sheets for valid CPT codes
            all_valid_codes = set()
            for sheet in xls.sheet_names:
                df_sheet = pd.read_excel(xls, sheet)
                for col in df_sheet.columns:
                    all_valid_codes.update(df_sheet[col].dropna().apply(clean_code))
            
            # Specific Logic for MUE and NCCI
            mue_df = pd.read_excel(xls, 'MUE_Edits') if 'MUE_Edits' in xls.sheet_names else pd.DataFrame()
            ncci_df = pd.read_excel(xls, 'NCCI_Edits') if 'NCCI_Edits' in xls.sheet_names else pd.DataFrame()
            
            mue_map = dict(zip(mue_df.iloc[:, 0].apply(clean_code), mue_df.iloc[:, 1])) if not mue_df.empty else {}
            ncci_map = {(clean_code(r[0]), clean_code(r[1])): str(r[5]) for _, r in ncci_df.iterrows()} if not ncci_df.empty else {}
            
        return {"valid_cpts": all_valid_codes, "mue": mue_map, "ncci": ncci_map}
    except Exception as e:
        st.error(f"Master Data Error: {e}")
        return None

# --- 3. SCRUBBING ENGINE ---
def run_validation(df, data):
    results = []
    cpt_cols = [c for c in df.columns if 'CPT' in str(c).upper()]
    
    for _, row in df.iterrows():
        units = row.get('Units', 1)
        mods = [m.strip().upper() for m in str(row.get('Modifier', '')).replace(',', ' ').split() if m.strip()]
        row_cpts = [clean_code(row[c]) for c in cpt_cols if pd.notna(row[c]) and str(row[c]).strip() != ""]
        
        status_msg, errors = [], 0
        for cpt in row_cpts:
            status_parts = []
            if cpt not in data['valid_cpts']:
                status_parts.append("❌ Invalid CPT")
                errors += 1
            else:
                if cpt in data['mue'] and units > data['mue'][cpt]:
                    status_parts.append("⚠️ MUE Violation")
                    errors += 1
                for other in row_cpts:
                    if (other, cpt) in data['ncci'] and not any(m in ['59', '25', '91'] for m in mods):
                        status_parts.append(f"🚫 Bundled with {other}")
                        errors += 1
            status_msg.append(f"[{cpt}]: " + ("✅ Clean" if not status_parts else " | ".join(status_parts)))

        res_row = row.to_dict()
        res_row['Validation_Results'] = " | ".join(status_msg)
        res_row['Status'] = "REJECTED" if errors > 0 else "ACCEPTED"
        results.append(res_row)
    return pd.DataFrame(results)

# --- 4. UI INTERFACE ---
st.title("🏥 Billing Data Scrubber")

with st.sidebar:
    st.header("📂 Upload Center")
    master_file = st.file_uploader("1. Upload Billing_Master_Data.xlsx", type=['xlsx'])
    claim_file = st.file_uploader("2. Upload Claim_Entry.xlsx", type=['xlsx'])
    
    if st.button("♻️ Clear App Cache"):
        st.cache_data.clear()
        st.success("Cache Cleared!")

if master_file and claim_file:
    data = load_master_data(master_file)
    if data and st.button("🚀 Run Comprehensive Audit"):
        input_df = pd.read_excel(claim_file)
        final_df = run_validation(input_df, data)
        
        # --- SUMMARY STATISTICS ---
        st.subheader("📊 Audit Overview")
        m1, m2, m3 = st.columns(3)
        m1.metric("Total Rows", len(final_df))
        m2.metric("Accepted ✅", len(final_df[final_df['Status'] == 'ACCEPTED']))
        m3.metric("Rejected ❌", len(final_df[final_df['Status'] == 'REJECTED']), delta_color="inverse")

        # --- DATA DISPLAY TABS ---
        tab1, tab2, tab3 = st.tabs(["❌ Rejected Claims", "✅ Accepted Claims", "📂 All Data"])
        
        with tab1:
            rejected_df = final_df[final_df['Status'] == 'REJECTED']
            if not rejected_df.empty:
                st.warning(f"Found {len(rejected_df)} claims with errors.")
                st.dataframe(rejected_df, use_container_width=True)
            else:
                st.success("No rejected claims found!")

        with tab2:
            accepted_df = final_df[final_df['Status'] == 'ACCEPTED']
            if not accepted_df.empty:
                st.success(f"{len(accepted_df)} claims are clean and ready for submission.")
                st.dataframe(accepted_df, use_container_width=True)
            else:
                st.info("No claims passed validation.")

        with tab3:
            st.dataframe(final_df, use_container_width=True)
        
        # --- DOWNLOAD CENTER ---
        st.divider()
        st.subheader("📥 Download Results")
        buffer = io.BytesIO()
        final_df.to_excel(buffer, index=False)
        st.download_button("Download Final Audit Report (Excel)", buffer.getvalue(), "Scrubbed_Audit_Report.xlsx")
else:
    st.info("Please upload your Master Data and Claim Entry files to start.")
