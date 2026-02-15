import streamlit as st
import pandas as pd
import io

# --- CONFIGURATION ---
st.set_page_config(page_title="Billing Scrubber Pro", layout="wide")

# --- 1. DATA CLEANING HELPER ---
def clean_code(val):
    if pd.isna(val) or str(val).strip() == "": 
        return ""
    s = str(val).split('.')[0].strip().upper()
    if s.isdigit() and len(s) < 5:
        s = s.zfill(5)
    return s

# --- 2. DEEP SCAN MASTER LOADER ---
@st.cache_data(show_spinner=True)
def load_master_data(uploaded_file):
    try:
        with pd.ExcelFile(uploaded_file) as xls:
            all_valid_codes = set()
            for sheet in xls.sheet_names:
                df_sheet = pd.read_excel(xls, sheet)
                for col in df_sheet.columns:
                    all_valid_codes.update(df_sheet[col].dropna().apply(clean_code))
            
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
    col_names = list(df.columns)
    rejection_reasons = [] 
    
    for i, row in df.iterrows():
        mods = [m.strip().upper() for m in str(row.get('Modifier', '')).replace(',', ' ').split() if m.strip()]
        
        # Dynamic CPT-Unit Pairing
        cpt_unit_pairs = []
        for idx, col in enumerate(col_names):
            if 'CPT' in col.upper():
                cpt_val = clean_code(row[col])
                if cpt_val:
                    units_val = 1
                    if idx + 1 < len(col_names) and 'UNIT' in col_names[idx+1].upper():
                        val = row[col_names[idx+1]]
                        units_val = val if pd.notna(val) and str(val).strip() != "" else 0
                    cpt_unit_pairs.append({'code': cpt_val, 'units': units_val})

        row_summary = []
        is_rejected = False
        row_codes_only = [p['code'] for p in cpt_unit_pairs]
        
        for pair in cpt_unit_pairs:
            cpt, u = pair['code'], pair['units']
            cpt_errors = []
            
            if u == 0: cpt_errors.append("❗ Missing Units")
            
            if cpt not in data['valid_cpts']:
                cpt_errors.append("❌ Invalid CPT")
            else:
                if cpt in data['mue'] and u > data['mue'][cpt]:
                    cpt_errors.append(f"⚠️ MUE Limit ({data['mue'][cpt]}) - Billed: {u}")
                for other in row_codes_only:
                    if (other, cpt) in data['ncci'] and not any(m in ['59', '25', '91'] for m in mods):
                        cpt_errors.append(f"🚫 Bundled with {other}")
            
            if cpt_errors:
                is_rejected = True
                row_summary.append(f"[{cpt}]: " + " | ".join(cpt_errors))
                rejection_reasons.append(cpt)
            else:
                row_summary.append(f"[{cpt}]: ✅ Clean ({int(u)} units)")

        res_row = row.to_dict()
        res_row['Validation_Results'] = " | ".join(row_summary)
        res_row['Status'] = "REJECTED" if is_rejected else "ACCEPTED"
        results.append(res_row)
        
    return pd.DataFrame(results), rejection_reasons

# --- 4. UI INTERFACE ---
st.title("🏥 Billing Scrubber Pro")

with st.sidebar:
    st.header("📂 Data Center")
    master_file = st.file_uploader("1. Master Data", type=['xlsx'])
    claim_file = st.file_uploader("2. Claim Entry", type=['xlsx'])
    if st.button("♻️ Reset Cache"):
        st.cache_data.clear()
        st.rerun()

if master_file and claim_file:
    data = load_master_data(master_file)
    if data and st.button("🚀 Run Comprehensive Audit"):
        input_df = pd.read_excel(claim_file)
        final_df, error_codes = run_validation(input_df, data)
        
        # --- TOP LEVEL METRICS ---
        st.subheader("📊 Performance Summary")
        m1, m2, m3 = st.columns(3)
        m1.metric("Total Rows", len(final_df))
        m2.metric("Accepted ✅", len(final_df[final_df['Status'] == 'ACCEPTED']))
        m3.metric("Rejected ❌", len(final_df[final_df['Status'] == 'REJECTED']), delta_color="inverse")

        # --- TREND ANALYTICS ---
        if error_codes:
            st.subheader("📈 Rejection Hotspots (Top CPTs)")
            counts = pd.Series(error_codes).value_counts().reset_index()
            counts.columns = ['CPT Code', 'Count']
            st.bar_chart(data=counts, x='CPT Code', y='Count', color="#FF4B4B")
        
        # --- SEARCH BAR ---
        st.divider()
        search_query = st.text_input("🔍 Search Claims (Type Claim ID, CPT, or Error Type...)", "").strip().upper()

        # Apply filtering based on search
        if search_query:
            # We search across Claim_ID and Validation_Results
            mask = final_df.apply(lambda row: search_query in str(row['Claim_ID']).upper() or 
                                              search_query in str(row['Validation_Results']).upper(), axis=1)
            display_df = final_df[mask]
        else:
            display_df = final_df

        # --- TABS ---
        tab1, tab2, tab3 = st.tabs(["❌ Rejected Claims", "✅ Accepted Claims", "📂 Filtered View"])
        
        with tab1:
            st.dataframe(final_df[final_df['Status'] == 'REJECTED'], use_container_width=True)
        with tab2:
            st.dataframe(final_df[final_df['Status'] == 'ACCEPTED'], use_container_width=True)
        with tab3:
            st.write(f"Showing {len(display_df)} results for search: '{search_query}'")
            st.dataframe(display_df, use_container_width=True)
        
        # --- DOWNLOAD ---
        st.divider()
        buffer = io.BytesIO()
        final_df.to_excel(buffer, index=False)
        st.download_button("📥 Export Audit Report", buffer.getvalue(), "Scrubbed_Audit_Report.xlsx")
else:
    st.info("Upload your Excel files to start the automated audit.")
