import streamlit as st
import pandas as pd
import io
import os
import google.generativeai as genai

# --- CONFIGURATION ---
st.set_page_config(page_title="Billing AI Scrubber", layout="wide")

# --- 1. CACHED DATA LOADING (The missing function) ---
@st.cache_data
def load_master_data(uploaded_master):
    """Processes the master file once and caches it."""
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

# --- 2. AI RISK ASSESSMENT LOGIC ---
def get_ai_prediction(cpt_list, dx_list, api_key):
    if not api_key:
        return "N/A", "N/A"
    try:
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel('gemini-pro')
        prompt = f"""
        Analyze these medical codes for insurance denial risk:
        CPTs: {', '.join(cpt_list)}
        ICD-10: {', '.join(dx_list)}
        Output format: RISK: [High/Medium/Low] | REASON: [Short 1-sentence explanation]
        """
        response = model.generate_content(prompt).text
        risk = "Low"
        if "High" in response: risk = "High"
        elif "Medium" in response: risk = "Medium"
        return risk, response
    except:
        return "Error", "AI Connection Failed"

# --- 3. CORE SCRUBBING FUNCTION ---
def run_validation_with_progress(df, data):
    results = []
    cpt_cols = [c for c in df.columns if 'CPT' in str(c).upper()]
    total_rows = len(df)
    progress_bar = st.progress(0)
    
    for i, row in df.iterrows():
        progress_bar.progress((i + 1) / total_rows)
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
    progress_bar.empty()
    return pd.DataFrame(results)

# --- 4. MAIN INTERFACE ---
st.title("🏥 Billing AI Scrubber")

with st.sidebar:
    st.header("🔑 AI Settings")
    api_key = st.text_input("AIzaSyBTf_OaWUfTZ0ZW0-HvMgiTEiuR-2NS9mY", type="password")
    use_ai = st.checkbox("Enable Denial Predictor")
    st.header("📂 Data Upload")
    master_file = st.file_uploader("Upload Master Data", type=['xlsx'])
    claim_file = st.file_uploader("Upload Claim List", type=['xlsx'])

if master_file and claim_file:
    data = load_master_data(master_file)
    
    if data and st.button("🚀 Run AI Scrubber"):
        input_df = pd.read_excel(claim_file)
        processed_df = run_validation_with_progress(input_df, data)
        
        if use_ai and api_key:
            with st.spinner("🤖 AI is analyzing medical necessity..."):
                risks, insights = [], []
                for _, row in processed_df.iterrows():
                    cpts = [str(row[c]) for c in input_df.columns if 'CPT' in str(c).upper() and pd.notna(row[c])]
                    dxs = [str(row[c]) for c in input_df.columns if 'DX' in str(c).upper() and pd.notna(row[c])]
                    r, ins = get_ai_prediction(cpts, dxs, api_key)
                    risks.append(r)
                    insights.append(ins)
                processed_df['Risk_Level'] = risks
                processed_df['AI_Insight'] = insights

        # --- DASHBOARD ---
        st.subheader("📊 Denial Risk Dashboard")
        m1, m2, m3 = st.columns(3)
        if 'Risk_Level' in processed_df.columns:
            m1.metric("High Risk 🔥", len(processed_df[processed_df['Risk_Level'] == 'High']))
            m2.metric("Medium Risk ⚠️", len(processed_df[processed_df['Risk_Level'] == 'Medium']))
            m3.metric("Low Risk ✅", len(processed_df[processed_df['Risk_Level'] == 'Low']))
        else:
            total = len(processed_df)
            m1.metric("Total Claims", total)
            m2.metric("Accepted ✅", len(processed_df[processed_df['Status'] == 'ACCEPTED']))
            m3.metric("Rejected ❌", len(processed_df[processed_df['Status'] == 'REJECTED']))

        st.subheader("📋 Results")
        st.dataframe(processed_df)
        
        buffer = io.BytesIO()
        processed_df.to_excel(buffer, index=False)
        st.download_button("📥 Download Results", buffer.getvalue(), "Scrubbed_AI_Results.xlsx")
