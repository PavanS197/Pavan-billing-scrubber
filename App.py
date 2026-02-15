import streamlit as st
import pandas as pd
import io
import google.generativeai as genai

# --- CONFIGURATION ---
st.set_page_config(page_title="Billing AI Scrubber", layout="wide")

# --- DATA CLEANING HELPER ---
def clean_code(val):
    """Strips .0 and whitespace from codes like 11042.0"""
    if pd.isna(val): return ""
    s = str(val).strip().upper()
    if s.endswith('.0'): s = s[:-2]
    return s

# --- CACHED DATA LOADING ---
@st.cache_data
def load_master_data(uploaded_master):
    try:
        with pd.ExcelFile(uploaded_master) as xls:
            cpt_df = pd.read_excel(xls, 'CPTHCPCS CODE', skiprows=3)
            mue_df = pd.read_excel(xls, 'MUE_Edits', skiprows=3)
            ncci_df = pd.read_excel(xls, 'NCCI_Edits')
            
        mue_dict = dict(zip(cpt_df.iloc[:, 0].apply(clean_code), mue_df.iloc[:, 1]))
        ncci_bundles = {(clean_code(r[0]), clean_code(r[1])): str(r[5]) for _, r in ncci_df.iterrows()}
        valid_cpts = set(cpt_df.iloc[:, 0].apply(clean_code))
        
        return {"mue": mue_dict, "ncci": ncci_bundles, "valid_cpts": valid_cpts}
    except Exception as e:
        st.error(f"Error reading master file: {e}")
        return None

# --- AI DENIAL PREDICTOR ---
def get_ai_prediction(cpt_list, dx_list, api_key):
    if not api_key or not cpt_list: return "N/A", "N/A"
    try:
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel('gemini-pro')
        prompt = f"Analyze CPTs {cpt_list} with DX {dx_list}. Predict denial risk for medical necessity. Format: RISK: [High/Medium/Low] | REASON: [Short explanation]"
        response = model.generate_content(prompt).text
        risk = "High" if "High" in response else ("Medium" if "Medium" in response else "Low")
        return risk, response
    except Exception as e:
        return "AI Error", str(e)

# --- SCRUBBING LOGIC ---
def run_validation(df, data):
    results = []
    cpt_cols = [c for c in df.columns if 'CPT' in str(c).upper()]
    for _, row in df.iterrows():
        units = row.get('Units', 1)
        mods = [m.strip().upper() for m in str(row.get('Modifier', '')).replace(',', ' ').split() if m.strip()]
        row_cpts = [clean_code(row[c]) for c in cpt_cols if pd.notna(row[c])]
        
        row_status, error_count = [], 0
        for cpt in row_cpts:
            if cpt.isdigit() and len(cpt) < 5: cpt = cpt.zfill(5) # Anesthesia padding
            status_parts = []
            if cpt not in data['valid_cpts']:
                status_parts.append("❌ Invalid CPT")
                error_count += 1
            else:
                if cpt in data['mue'] and units > data['mue'][cpt]:
                    status_parts.append(f"⚠️ MUE Violation")
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
    return pd.DataFrame(results)

# --- UI INTERFACE ---
st.title("🏥 Billing AI Scrubber")

with st.sidebar:
    st.header("🔑 AI Settings")
    api_key = st.text_input("Gemini API Key", type="password")
    use_ai = st.checkbox("Enable AI Denial Predictor")
    st.header("📂 Data Upload")
    master_file = st.file_uploader("Upload Master Data", type=['xlsx'])
    claim_file = st.file_uploader("Upload Claim List", type=['xlsx'])

if master_file and claim_file:
    data = load_master_data(master_file)
    if data and st.button("🚀 Run Scrubber"):
        input_df = pd.read_excel(claim_file)
        processed_df = run_validation(input_df, data)
        
        if use_ai and api_key:
            with st.spinner("🤖 AI analyzing medical necessity..."):
                dx_cols = [c for c in input_df.columns if 'DX' in str(c).upper()]
                risks, insights = [], []
                for _, row in processed_df.iterrows():
                    cpts = [clean_code(row[c]) for c in input_df.columns if 'CPT' in str(c).upper() and pd.notna(row[c])]
                    dxs = [clean_code(row[c]) for c in dx_cols if pd.notna(row[c])]
                    r, ins = get_ai_prediction(cpts, dxs, api_key)
                    risks.append(r)
                    insights.append(ins)
                processed_df['Risk_Level'] = risks
                processed_df['AI_Insight'] = insights

        st.subheader("📊 Scrubbing Summary")
        m1, m2, m3 = st.columns(3)
        m1.metric("Total Claims", len(processed_df))
        m2.metric("Accepted ✅", len(processed_df[processed_df['Status'] == 'ACCEPTED']))
        m3.metric("Rejected ❌", len(processed_df[processed_df['Status'] == 'REJECTED']))

        st.subheader("📋 Results")
        st.dataframe(processed_df)
        
        buffer = io.BytesIO()
        processed_df.to_excel(buffer, index=False)
        st.download_button("📥 Download Results", buffer.getvalue(), "Scrubbed_AI_Results.xlsx")
