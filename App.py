import streamlit as st
import pandas as pd
import io

# --- CONFIGURATION ---
st.set_page_config(page_title="Billing Scrubber Pro", layout="wide", page_icon="🏥")

# --- 1. DATA CLEANING HELPER ---
def clean_code(val):
    if pd.isna(val) or str(val).strip() == "": 
        return ""
    # Strip decimals (.0), whitespace, and uppercase
    s = str(val).split('.')[0].strip().upper()
    # Padding for Anesthesia: restore leading zeros (e.g., 100 -> 00100)
    if s.isdigit() and len(s) < 5:
        s = s.zfill(5)
    return s

# --- 2. DEEP SCAN MASTER LOADER ---
@st.cache_data(show_spinner=True)
def load_master_data(uploaded_file):
    try:
        with pd.ExcelFile(uploaded_file) as xls:
            # Building a master set of all valid codes across all sheets
            all_valid_codes = set()
            for sheet in xls.sheet_names:
                df_sheet = pd.read_excel(xls, sheet)
                for col in df_sheet.columns:
                    all_valid_codes.update(df_sheet[col].dropna().apply(clean_code))
            
            # Load MUE (Units) and NCCI (Bundling) logic
            mue_df = pd.read_excel(xls, 'MUE_Edits') if 'MUE_Edits' in xls.sheet_names else pd.DataFrame()
            ncci_df = pd.read_excel(xls, 'NCCI_Edits') if 'NCCI_Edits' in xls.sheet_names else pd.DataFrame()
            
            mue_map = dict(zip(mue_df.iloc[:, 0].apply(clean_code), mue_df.iloc[:, 1])) if not mue_df.empty else {}
            ncci_map = {(clean_code(r[0]), clean_code(r[1])): str(r[5]) for _, r in ncci_df.iterrows()} if not ncci_df.empty else {}
            
        return {"valid_cpts": all_valid_codes, "mue": mue_map, "ncci": ncci_map}
    except Exception as e:
        st.error(f"Master Data Error: {e}")
        return None

# --- 3. SCRUBBING ENGINE (GROUPED LOGIC) ---
def run_validation(df, data):
    results = []
    col_names = list(df.columns)
    rejection_reasons = [] 
    
    for i, row in df.iterrows():
        # Identify CPT groupings (CPT followed by its own Units, DX, and Modifiers)
        cpt_groups = []
        for idx, col in enumerate(col_names):
            if 'CPT' in col.upper():
                cpt_val = clean_code(row[col])
                
                # Gather associated data following this CPT column until the next CPT starts
                units, dxs, mods = 1, [], []
                j = idx + 1
                while j < len(col_names) and 'CPT' not in col_names[j].upper():
                    header = col_names[j].upper()
                    val = row[col_names[j]]
                    if 'UNIT' in header:
                        units = val if pd.notna(val) and str(val).strip() != "" else 0
                    elif 'DX' in header and pd.notna(val):
                        dxs.append(str(val).strip())
                    elif 'MODIFIER' in header and pd.notna(val):
                        mods.extend([m.strip().upper() for m in str(val).replace(',', ' ').split() if m.strip()])
                    j += 1
                
                cpt_groups.append({
                    'code': cpt_val, 
                    'units': units, 
                    'dxs': dxs, 
                    'mods': mods,
                    'is_orphan': cpt_val == "" and (len(dxs) > 0 or len(mods) > 0)
                })

        row_summary = []
        is_rejected = False
        all_codes_in_row = [g['code'] for g in cpt_groups if g['code']]
        
        for group in cpt_groups:
            cpt, u, mods = group['code'], group['units'], group['mods']
            cpt_errors = []
            
            # Check 1: Orphan Data (Modifier/DX present but CPT missing)
            if group['is_orphan']:
                cpt_errors.append("🚫 Missing CPT Code (Modifier/DX found)")
            
            if cpt:
                # Check 2: Zero Units
                if u == 0: 
                    cpt_errors.append("❗ Missing Units")
                
                # Check 3: Validity
                if cpt not in data['valid_cpts']:
                    cpt_errors.append("❌ Invalid CPT")
                else:
                    # Check 4: MUE (Units Limit)
                    if cpt in data['mue'] and u > data['mue'][cpt]:
                        cpt_errors.append(f"⚠️ MUE Limit ({data['mue'][cpt]}) - Billed: {u}")
                    
                    # Check 5: NCCI Bundling (Requires Modifier check)
                    for other in all_codes_in_row:
                        if (other, cpt) in data['ncci'] and not any(m in ['59', '25', '91'] for m in mods):
                            cpt_errors.append(f"🚫 Bundled with {other}")
            
            if cpt_errors:
                is_rejected = True
                display_name = cpt if cpt else "Empty CPT"
                row_summary.append(f"[{display_name}]: " + " | ".join(cpt_errors))
                if cpt: rejection_reasons.append(cpt)
            elif cpt:
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
    master_file = st.file_uploader("1. Master Data (Excel)", type=['xlsx'])
    claim_file = st.file_uploader("2. Claim Entry (Excel)", type=['xlsx'])
    
    if st.button("♻️ Reset App Cache"):
        st.cache_data.clear()
        st.success("Cache cleared successfully!")

if master_file and claim_file:
    data = load_master_data(master_file)
    if data and st.button("🚀 Run Comprehensive Audit"):
        input_df = pd.read_excel(claim_file)
        final_df, error_codes = run_validation(input_df, data)
        
        # --- METRICS ---
        st.subheader("📊 Performance Summary")
        m1, m2, m3 = st.columns(3)
        m1.metric("Total Rows", len(final_df))
        m2.metric("Accepted ✅", len(final_df[final_df['Status'] == 'ACCEPTED']))
        m3.metric("Rejected ❌", len(final_df[final_df['Status'] == 'REJECTED']), delta_color="inverse")

        # --- TRENDS ---
        if error_codes:
            st.subheader("📈 Top Denial Hotspots (by CPT)")
            counts = pd.Series(error_codes).value_counts().reset_index()
            counts.columns = ['CPT Code', 'Count']
            st.bar_chart(data=counts, x='CPT Code', y='Count', color="#FF4B4B")
        
        # --- TABBED RESULTS ---
        st.divider()
        tab1, tab2, tab3 = st.tabs(["❌ Rejected Claims", "✅ Accepted Claims", "📂 Full Audit Log"])
        
        with tab1:
            st.dataframe(final_df[final_df['Status'] == 'REJECTED'], use_container_width=True)
        with tab2:
            st.dataframe(final_df[final_df['Status'] == 'ACCEPTED'], use_container_width=True)
        with tab3:
            st.dataframe(final_df, use_container_width=True)
        
        # --- DOWNLOAD ---
        st.divider()
        buffer = io.BytesIO()
        final_df.to_excel(buffer, index=False)
        st.download_button("📥 Download Final Audit Report", buffer.getvalue(), "Scrubbed_Billing_Report.xlsx")
else:
    st.info("Please upload your Master Data and Claim Entry files to begin the audit.")
