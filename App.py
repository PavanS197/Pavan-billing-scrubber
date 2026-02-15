import streamlit as st
import pandas as pd
import io

# --- CONFIGURATION ---
st.set_page_config(page_title="Billing Scrubber & Analytics", layout="wide")

# --- 1. DATA CLEANING HELPER ---
def clean_code(val):
    if pd.isna(val) or str(val).strip() == "": 
        return ""
    s = str(val).split('.')[0].strip().upper()
    if s.isdigit() and len(s) < 5:
        s = s.zfill(5)
    return s

# --- 2. ROBUST MASTER DATA LOADER ---
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
    cpt_cols = [c for c in df.columns if 'CPT' in str(c).upper()]
    rejection_reasons = [] # For Analytics
    
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
                rejection_reasons.append(cpt)
            else:
                msg = []
                if cpt in data['mue'] and units > data['mue'][cpt]:
                    msg.append("⚠️ MUE Violation")
                    errors += 1
                    rejection_reasons.append(cpt)
                for other in row_cpts:
                    if (other, cpt) in data['ncci'] and not any(m in ['59', '25', '91'] for m in mods):
                        msg.append(f"🚫 Bundled with {other}")
                        errors += 1
                        rejection_reasons.append(cpt)
            status_msg.append(f"[{cpt}]: " + ("✅ Clean" if not status_parts else " | ".join(status_parts)))

        res_row = row.to_dict()
        res_row['Validation_Results'] = " | ".join(status_msg)
        res_row['Status'] = "REJECTED" if errors > 0 else "ACCEPTED"
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
        
        # --- METRICS ---
        st.subheader("📊 Performance Summary")
        m1, m2, m3 = st.columns(3)
        m1.metric("Total Claims", len(final_df))
        m2.metric("Accepted ✅", len(final_df[final_df['Status'] == 'ACCEPTED']))
        m3.metric("Rejected ❌", len(final_df[final_df['Status'] == 'REJECTED']), delta_color="inverse")

        # --- NEW: TREND ANALYTICS ---
        if error_codes:
            st.subheader("📈 Top Codes Causing Rejections")
            error_counts = pd.Series(error_codes).value_counts().reset_index()
            error_counts.columns = ['CPT Code', 'Error Count']
            st.bar_chart(data=error_counts, x='CPT Code', y='Error Count', color="#FF4B4B")
        
        # --- TABS ---
        tab1, tab2 = st.tabs(["❌ Rejected Claims", "✅ Accepted Claims"])
        with tab1:
            st.dataframe(final_df[final_df['Status'] == 'REJECTED'], use_container_width=True)
        with tab2:
            st.dataframe(final_df[final_df['Status'] == 'ACCEPTED'], use_container_width=True)
        
        # --- DOWNLOAD ---
        buffer = io.BytesIO()
        final_df.to_excel(buffer, index=False)
        st.download_button("📥 Export Results", buffer.getvalue(), "Scrubbed_Audit.xlsx")
