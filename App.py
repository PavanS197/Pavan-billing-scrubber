import streamlit as st
import pandas as pd
import io
import os
import google.generativeai as genai

# --- CONFIGURATION ---
st.set_page_config(page_title="Billing AI Scrubber", layout="wide")

# --- 1. THE ULTIMATE CLEANER ---
def clean_code(val):
    if pd.isna(val) or str(val).strip() == "": 
        return ""
    # Remove .0, strip spaces, convert to upper string
    s = str(val).split('.')[0].strip().upper()
    # Handle leading zeros for Anesthesia (100 -> 00100)
    if s.isdigit() and len(s) < 5:
        s = s.zfill(5)
    return s

# --- 2. AI AGENT SETUP (STABLE) ---
def check_ai_status(api_key):
    if not api_key: return False
    try:
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel('models/gemini-1.5-flash-latest')
        model.generate_content("Ping", generation_config={"max_output_tokens": 1})
        return True
    except:
        return False

def get_ai_prediction(cpts, dxs, api_key):
    if not api_key or not cpts: return "Low", "N/A"
    try:
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel('models/gemini-1.5-flash-latest')
        prompt = f"Analyze CPTs {cpts} with DX {dxs}. Predict denial risk for medical necessity. Format: RISK: [High/Medium/Low] | REASON: [Short explanation]"
        response = model.generate_content(prompt)
        res = response.text
        risk = "High" if "High" in res else ("Medium" if "Medium" in res else "Low")
        return risk, res
    except Exception as e:
        return "Error", str(e)

# --- 3. ROBUST MASTER LOADER (THE CACHE FIX) ---
@st.cache_data(show_spinner=False)
def load_master_data(uploaded_file):
    try:
        with pd.ExcelFile(uploaded_file) as xls:
            # Load CPT Master - Scan ALL columns in sheet
            cpt_df = pd.read_excel(xls, 'CPTHCPCS CODE')
            all_valid_codes = set()
            for col in cpt_df.columns:
                all_valid_codes.update(cpt_df[col].dropna().apply(clean_code))
            
            # Load MUE
            mue_df = pd.read_excel(xls, 'MUE_Edits')
            mue_map = dict(zip(mue_df.iloc[:, 0].apply(clean_code), mue_df.iloc[:, 1]))
            
            # Load NCCI
            ncci_df = pd.read_excel(xls, 'NCCI_Edits')
            ncci_map = {(clean_code(r[0]), clean_code(r[1])): str(r[5]) for _, r in ncci_df.iterrows()}
            
        return {"valid_cpts": all_valid_codes, "mue": mue_map, "ncci": ncci_map}
    except Exception as e:
        st.error(f"Master Data Error: {e}")
        return None

# --- 4. UI AND PROCESSING ---
st.title("🏥 Billing AI Scrubber")

with st.sidebar:
    st.header("⚙️ AI & Data Settings")
    key = st.text_input("Gemini API Key", type="password")
    if key:
        if check_ai_status(key): st.success("🟢 AI Online")
        else: st.error("🔴 AI Offline")
    
    use_ai = st.checkbox("Predict Denial Risk (AI)")
    
    # ADDED: Button to clear cache if "Invalid CPT" persists
    if st.button("♻️ Clear App Cache"):
        st.cache_data.clear()
        st.rerun()

    master_file = st.file_uploader("Upload Billing_Master_Data.xlsx", type=['xlsx'])
    claim_file = st.file_uploader("Upload Claim_Entry.xlsx", type=['xlsx'])

if master_file and claim_file:
    data = load_master_data(master_file)
    if data and st.button("🚀 Start Billing Audit"):
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
                if cpt not in data['valid_cpts']:
                    status_msg.append(f"[{cpt}]: ❌ Invalid CPT")
                    errors += 1
                else:
                    msg = []
                    # MUE Check
                    if cpt in data['mue'] and units > data['mue'][cpt]:
                        msg.append("⚠️ MUE Violation")
                        errors += 1
                    # NCCI Check
                    for other in row_cpts:
                        if (other, cpt) in data['ncci'] and not any(m in ['59', '25', '91'] for m in mods):
                            msg.append(f"🚫 Bundled with {other}")
                            errors += 1
                    
                    status_msg.append(f"[{cpt}]: " + ("✅ Clean" if not msg else " | ".join(msg)))
            
            res_row = row.to_dict()
            res_row['Validation_Results'] = " | ".join(status_msg)
            res_row['Status'] = "REJECTED" if errors > 0 else "ACCEPTED"
            
            # AI Logic
            if use_ai and key:
                risk, insight = get_ai_prediction(row_cpts, row_dxs, key)
                res_row['Risk_Level'] = risk
                res_row['AI_Insight'] = insight
            
            results.append(res_row)
        
        # --- DASHBOARD & DOWNLOAD ---
        final_df = pd.DataFrame(results)
        st.subheader("📊 Audit Summary")
        m1, m2, m3 = st.columns(3)
        m1.metric("Processed", len(final_df))
        m2.metric("Accepted ✅", len(final_df[final_df['Status'] == 'ACCEPTED']))
        m3.metric("Rejected ❌", len(final_df[final_df['Status'] == 'REJECTED']))
        
        st.dataframe(final_df)
        
        buffer = io.BytesIO()
        final_df.to_excel(buffer, index=False)
        st.download_button("📥 Download Audit Report", buffer.getvalue(), "Scrubbed_AI_Results.xlsx")
