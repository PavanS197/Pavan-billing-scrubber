import streamlit as st
import pandas as pd
import io

# --- CONFIGURATION ---
st.set_page_config(page_title="Billing Scrubber & Analytics", layout="wide")

# --- 1. DATA CLEANING ---
def clean_code(val):
    if pd.isna(val) or str(val).strip() == "": return ""
    s = str(val).split('.')[0].strip().upper()
    if s.isdigit() and len(s) < 5: s = s.zfill(5)
    return s

# --- 2. DEEP SCAN LOADER ---
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

# --- 3. SCRUBBING ENGINE (UPDATED FOR INDIVIDUAL UNITS) ---
def run_validation(df, data):
    results = []
    col_names = list(df.columns)
    rejection_reasons = [] 
    
    for i, row in df.iterrows():
        # Clean modifiers
        mods = [m.strip().upper() for m in str(row.get('Modifier', '')).replace(',', ' ').split() if m.strip()]
        
        # 1. Pair CPTs with their specific Units
        cpt_unit_pairs = []
        for idx, col in enumerate(col_names):
            if 'CPT' in col.upper():
                cpt_val = clean_code(row[col])
                if cpt_val:
                    # Look at the very next column for units
                    # (Handles 'Units', 'Units.1', 'Units.2', etc.)
                    units = 1
                    if idx + 1 < len(col_names) and 'UNIT' in col_names[idx+1].upper():
                        units = row[col_names[idx+1]]
                        if pd.isna(units): units = 1
                    
                    cpt_unit_pairs.append({'code': cpt_val, 'units': int(units)})

        row_summary = []
        is_rejected = False
        row_codes_only = [p['code'] for p in cpt_unit_pairs]
        
        for pair in cpt_unit_pairs:
            cpt = pair['code']
            u = pair['units']
            cpt_errors = []
            
            if cpt not in data['valid_cpts']:
                cpt_errors.append("❌ Invalid CPT")
            else:
                # MUE Check using specific units for THIS CPT
                if cpt in data['mue'] and u > data['mue'][cpt]:
                    cpt_errors.append(f"⚠️ MUE Limit ({data['mue'][cpt]}) - Billed: {u}")
                
                # NCCI Check
                for other in row_codes_only:
                    if (other, cpt) in data['ncci'] and not any(m in ['59', '25', '91'] for m in mods):
                        cpt_errors.append(f"🚫 Bundled with {other}")
            
            if cpt_errors:
                is_rejected = True
                row_summary.append(f"[{cpt}]: " + " | ".join(cpt_errors))
                rejection_reasons.append(cpt)
            else:
                row_summary.append(f"[{cpt}]: ✅ Clean ({u} units)")

        res_row = row.to_dict()
        res_row['Validation_Results'] = " | ".join(row_summary)
        res_row['Status'] = "REJECTED" if is_rejected else "ACCEPTED"
        results.append(res_row)
        
    return pd.DataFrame(results), rejection_reasons

# --- 4. UI INTERFACE ---
st.title("🏥 Billing Scrubber & Trend Analytics")

with st.sidebar:
    st.header("📂 Data Center")
    master_file = st.file_uploader("1. Master Data", type=['xlsx'])
    claim_file = st.file_uploader("2. Claim Entry", type=['xlsx'])
    if st.button("♻️ Reset Cache"):
        st.cache_data.clear()
        st.rerun()

if master_file and claim_file:
    data = load_master_data(master_file)
    if data and st.button("🚀 Run Audit"):
        input_df = pd.read_excel(claim_file)
        final_df, error_codes = run_validation(input_df, data)
        
        st.subheader("📊 Performance Summary")
        m1, m2, m3 = st.columns(3)
        m1.metric("Total Claims", len(final_df))
        m2.metric("Accepted ✅", len(final_df[final_df['Status'] == 'ACCEPTED']))
        m3.metric("Rejected ❌", len(final_df[final_df['Status'] == 'REJECTED']), delta_color="inverse")

        if error_codes:
            st.subheader("📈 Top Codes Causing Rejections")
            counts = pd.Series(error_codes).value_counts().reset_index()
            counts.columns = ['CPT Code', 'Error Count']
            st.bar_chart(data=counts, x='CPT Code', y='Error Count', color="#FF4B4B")
        
        tab1, tab2 = st.tabs(["❌ Rejected Claims", "✅ Accepted Claims"])
        with tab1: st.dataframe(final_df[final_df['Status'] == 'REJECTED'], use_container_width=True)
        with tab2: st.dataframe(final_df[final_df['Status'] == 'ACCEPTED'], use_container_width=True)
        
        buffer = io.BytesIO()
        final_df.to_excel(buffer, index=False)
        st.download_button("📥 Export Final Results", buffer.getvalue(), "Scrubbed_Audit.xlsx")
