import streamlit as st
import pandas as pd
import io
import os
import google.generativeai as genai

# --- CONFIGURATION ---
st.set_page_config(page_title="Billing AI Scrubber", layout="wide")

# --- 1. DATA CLEANING (Ensures 11045.0 == 11045) ---
def clean_code(val):
    if pd.isna(val) or str(val).strip() == "": 
        return ""
    # Strip decimals, spaces, and convert to upper string
    s = str(val).split('.')[0].strip().upper()
    # Handle leading zeros for Anesthesia (100 -> 00100)
    if s.isdigit() and len(s) < 5:
        s = s.zfill(5)
    return s

# --- 2. DEEP-SCAN MASTER LOADER ---
@st.cache_data(show_spinner=True)
def load_master_data(uploaded_file):
    try:
        with pd.ExcelFile(uploaded_file) as xls:
            # --- VALID CPT DEEP SCAN ---
            # Instead of looking at one sheet, we scan the whole file for procedure codes
            all_valid_codes = set()
            for sheet in xls.sheet_names:
                df_sheet = pd.read_excel(xls, sheet)
                # We flatten the entire sheet into one list of codes
                for col in df_sheet.columns:
                    all_valid_codes.update(df_sheet[col].dropna().apply(clean_code))
            
            # --- MUE & NCCI SPECIFIC LOADING ---
            # We look for the sheets by name specifically for logic
            mue_df = pd.read_excel(xls, 'MUE_Edits') if 'MUE_Edits' in xls.sheet_names else pd.DataFrame()
            ncci_df = pd.read_excel(xls, 'NCCI_Edits') if 'NCCI_Edits' in xls.sheet_names else pd.DataFrame()
            
            mue_map = {}
            if not mue_df.empty:
                mue_map = dict(zip(mue_df.iloc[:, 0].apply(clean_code), mue_df.iloc[:, 1]))
                
            ncci_map = {}
            if not ncci_df.empty:
                ncci_map = {(clean_code(r[0]), clean_code(r[1])): str(r[5]) for _, r in ncci_df.iterrows()}
            
        return {"valid_cpts": all_valid_codes, "mue": mue_map, "ncci": ncci_map}
    except Exception as e:
        st.error(f"Deep Scan Error: {e}")
        return None

# --- 3. AI AGENT STABLE LOGIC ---
def get_ai_prediction(cpts, dxs, api_key):
    if not api_key or not cpts: return "Low", "N/A"
    try:
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel('models/gemini-1.5-flash-latest')
        prompt = f"Analyze CPTs {cpts} with DX {dxs}. Predict denial risk. Format: RISK: [High/Medium/Low] | REASON: [Short explanation]"
        response = model.generate_content(prompt)
        res = response.text
        risk = "High" if "High" in res else ("Medium" if "Medium" in res else "Low")
        return risk, res
    except Exception as e:
        return "Error", f"AI Connection Failed: {str(e)}"

# --- 4. UI INTERFACE ---
st.title("🏥 Billing AI Scrubber")

with st.sidebar:
    st.header("⚙️ Settings")
    api_key = st.text_input("Gemini API Key", type="password")
    use_ai = st.checkbox("Enable AI Denial Predictor")
    
    if st.button("♻️ Force Reset App Cache"):
        st.cache_data.clear()
        st.success("Cache Cleared!")

    st.header("📂 Upload Center")
    master_file = st.file_uploader("Upload Billing_Master_Data.xlsx", type=['xlsx'])
    claim_file = st.file_uploader("Upload Claim_Entry.xlsx", type=['xlsx'])

if master_file and claim_file:
    data = load_master_data(master_file)
    if data and st.button("🚀 Run Comprehensive Audit"):
        claims_df = pd.read_excel(claim_file)
        
        # --- SCRUBBING ENGINE ---
        results = []
        cpt_cols = [c for c in claims_df.columns if 'CPT' in str(c).upper()]
        dx_cols = [c for c in claims_df.columns if 'DX' in str(c).upper()]
        
        for i, row in claims_df.iterrows():
            units = row.get('Units', 1)
            mods = [m.strip().upper() for m in str(row.get('Modifier', '')).replace(',', ' ').split() if m.strip()]
            row_cpts = [clean_code(row[c]) for c in cpt_cols if pd.notna(row[c]) and str(row[c]).strip() != ""]
            row_dxs = [clean_code(row[c]) for c in dx_cols if pd.notna(row[c]) and str(row[c]).strip() != ""]
            
            status_msg, errors = [], 0
            for cpt in row_cpts:
                # 1. Deep Scan Validity Check
                if cpt not in data['valid_cpts']:
                    status_msg.append(f"[{cpt}]: ❌ Invalid CPT")
                    errors += 1
                else:
                    msg = []
                    # 2. MUE Check
                    if cpt in data['mue'] and units > data['mue'][cpt]:
                        msg.append("⚠️ MUE Violation")
                        errors += 1
                    # 3. NCCI Check
                    for other in row_cpts:
                        if (other, cpt) in data['ncci'] and not any(m in ['59', '25', '91'] for m in mods):
                            msg.append(f"🚫 Bundled with {other}")
                            errors += 1
                    status_msg.append(f"[{cpt}]: " + ("✅ Clean" if not msg else " | ".join(msg)))
            
            res_row = row.to_dict()
            res_row['Validation_Results'] = " | ".join(status_msg)
            res_row['Status'] = "REJECTED" if errors > 0 else "ACCEPTED"
            
            if use_ai and api_key:
                risk, insight = get_ai_prediction(row_cpts, row_dxs, api_key)
                res_row['Risk_Level'] = risk
                res_row['AI_Insight'] = insight
            
            results.append(res_row)
        
        st.subheader("📊 Audit Dashboard")
        st.dataframe(pd.DataFrame(results))
        
        buffer = io.BytesIO()
        pd.DataFrame(results).to_excel(buffer, index=False)
        st.download_button("📥 Download Final Report", buffer.getvalue(), "Scrubbed_AI_Audit.xlsx")
