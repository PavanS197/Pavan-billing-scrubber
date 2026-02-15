import streamlit as st
import pandas as pd
import io
import google.generativeai as genai

# --- CONFIGURATION ---
st.set_page_config(page_title="Billing AI Scrubber", layout="wide")

# --- AI RISK ASSESSMENT LOGIC ---
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
        
        Provide the output in exactly this format:
        RISK: [High/Medium/Low] | REASON: [Short 1-sentence explanation]
        """
        response = model.generate_content(prompt).text
        risk = "Low"
        if "High" in response: risk = "High"
        elif "Medium" in response: risk = "Medium"
        return risk, response
    except:
        return "Error", "AI Connection Failed"

# --- (Standard Load & Scrub Functions - Included in Final Paste) ---

st.title("🏥 Billing AI Scrubber")

with st.sidebar:
    st.header("🔑 AI Settings")
    api_key = st.text_input("AIzaSyBTf_OaWUfTZ0ZW0-HvMgiTEiuR-2NS9mY", type="password")
    use_ai = st.checkbox("Enable Denial Predictor")
    st.header("📂 Data Upload")
    master_file = st.file_uploader("Upload Master Data", type=['xlsx'])
    claim_file = st.file_uploader("Upload Claim List", type=['xlsx'])

if master_file and claim_file:
    data = load_master_data(master_file) # Uses cached function from previous turns
    
    if st.button("🚀 Run AI Scrubber"):
        input_df = pd.read_excel(claim_file)
        processed_df = run_validation_with_progress(input_df, data)
        
        if use_ai and api_key:
            risk_levels, ai_reasons = [], []
            for _, row in processed_df.iterrows():
                # Extracting codes for AI
                cpts = [str(row[c]) for c in input_df.columns if 'CPT' in str(c).upper() and pd.notna(row[c])]
                dxs = [str(row[c]) for c in input_df.columns if 'DX' in str(c).upper() and pd.notna(row[c])]
                risk, reason = get_ai_prediction(cpts, dxs, api_key)
                risk_levels.append(risk)
                ai_reasons.append(reason)
            
            processed_df['Risk_Level'] = risk_levels
            processed_df['AI_Insight'] = ai_reasons

        # --- RISK DASHBOARD ---
        st.subheader("📊 Denial Risk Dashboard")
        if 'Risk_Level' in processed_df.columns:
            r1, r2, r3 = st.columns(3)
            high_count = len(processed_df[processed_df['Risk_Level'] == 'High'])
            med_count = len(processed_df[processed_df['Risk_Level'] == 'Medium'])
            low_count = len(processed_df[processed_df['Risk_Level'] == 'Low'])
            
            r1.metric("High Risk 🔥", high_count, delta_color="inverse")
            r2.metric("Medium Risk ⚠️", med_count)
            r3.metric("Low Risk ✅", low_count)

        st.subheader("📋 Detailed Analysis")
        st.dataframe(processed_df.head(20))
