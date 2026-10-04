import streamlit as st
import pandas as pd
import requests
import hashlib
import json
import threading
import websocket
import time

# पेज लेआउट
st.set_page_config(page_title="Arbitrage Scanner", layout="wide")

# --- 1. Token Master (CSV) Load ---
@st.cache_data
def load_tokens():
    try:
        return pd.read_csv("NFO.csv") 
    except FileNotFoundError:
        return None

df_tokens = load_tokens()

# --- 2. WebSocket Functions ---
if 'live_prices' not in st.session_state:
    st.session_state['live_prices'] = {}
if 'ws_started' not in st.session_state:
    st.session_state['ws_started'] = False

def on_message(ws, message):
    data = json.loads(message)
    if data.get("t") in ["tf", "tk"] and "lp" in data:
        st.session_state['live_prices'][str(data.get("tk"))] = float(data.get("lp"))

def on_open(ws, wss_token, uid, token_list):
    login_payload = {"susertoken": wss_token, "t": "c", "actid": f"{uid}_API", "uid": f"{uid}_API", "source": "API"}
    ws.send(json.dumps(login_payload))
    k_string = "#".join([f"NFO|{t}" for t in token_list])
    ws.send(json.dumps({"k": k_string, "t": "t"}))

def start_websocket(wss_token, uid, token_list):
    ws_url = "wss://protrade.jainam.in/NorenWSTP/"
    ws = websocket.WebSocketApp(ws_url, on_open=lambda ws: on_open(ws, wss_token, uid, token_list), on_message=on_message)
    ws.run_forever()

# --- 3. Sidebar: Jainam API Login ---
st.sidebar.header("🔐 Jainam API Login")
user_id = st.sidebar.text_input("User ID:", key="uid", value="AX1678")
auth_code = st.sidebar.text_input("Auth Code:", key="auth")
api_secret = st.sidebar.text_input("API Secret:", type="password", key="secret")
# यहाँ NIFTY Future का टोकन फिक्स रहेगा (जैसे 54957), बाकी कोड खुद ढूँढेगा
nifty_fut_token = st.sidebar.text_input("NIFTY Future Token", value="54957")

if df_tokens is None:
    st.error("⚠️ NFO.csv फ़ाइल नहीं मिली! कृपया इसे अपने ऐप वाले फोल्डर में रखें।")
    st.stop()

# --- 4. Main UI Layout ---
st.title("Conversion & Reversal Scanner (NSE index options)")
st.caption("Open DevTools with F12 / Ctrl+Shift+I. Console shows logs: type CR.help().")

# Settings Row
c1, c2, c3, c4, c5, c6, c7, c8, c9 = st.columns([1.2, 1, 1.5, 1.2, 1.5, 1.5, 1, 1, 1.2])
with c1: spot = st.number_input("Spot (Strike)", value=24500, step=50)
with c2: lot_size = st.number_input("Lot size", value=75)
with c3: min_net = st.number_input("Min net / lot (Rs)", value=100)
with c4: slippage = st.number_input("Slippage (pts)", value=0.25)
with c5: alert_price = st.number_input("ALERT if price above (Rs)", value=15.0)
with c6: show_type = st.selectbox("Show", ["Conversion + Reversal", "Conversion", "Reversal"])
with c7: beep = st.checkbox("Beep on alert", value=False)
with c8: refresh_ms = st.number_input("Refresh (ms)", value=1500)
with c9: round_trip = st.checkbox("Round trip costs", value=True)

st.write("") 

# Buttons Row
ctrl1, ctrl2, ctrl3, ctrl4, ctrl5, ctrl6 = st.columns([2.5, 1, 1, 1, 1.5, 3])
with ctrl1: st.checkbox("Pause in debugger on best trade")
with ctrl2: st.button("Scan once")

# Login & Start Button
with ctrl3: 
    if st.button("Start (Login)"):
        with st.spinner("Connecting to Jainam..."):
            raw_string = f"{user_id}{auth_code}{api_secret}"
            checksum = hashlib.sha256(raw_string.encode('utf-8')).hexdigest()
            login_url = "https://protrade.jainam.in/omt/auth/sso/vendor/getUserDetails"
            res = requests.post(login_url, json={"checkSum": checksum}, timeout=10)
            
            if res.status_code == 200 and len(res.json().get("result", [])) > 0:
                acc_token = res.json()["result"][0].get("accessToken")
                wss_token = hashlib.sha256(hashlib.sha256(acc_token.encode()).hexdigest().encode()).hexdigest()
                
                # NIFTY CE & PE Tokens CSV से अपने-आप निकालना
                try:
                    ce_token = str(df_tokens[(df_tokens['StrikePrice'] == spot) & (df_tokens['OptionType'] == 'CE')]['Token'].values[0])
                    pe_token = str(df_tokens[(df_tokens['StrikePrice'] == spot) & (df_tokens['OptionType'] == 'PE')]['Token'].values[0])
                except:
                    ce_token, pe_token = "1111", "2222" # अगर स्ट्राइक प्राइस नहीं मिला
                
                tokens_to_subscribe = [nifty_fut_token, ce_token, pe_token]
                ws_thread = threading.Thread(target=start_websocket, args=(wss_token, user_id, tokens_to_subscribe), daemon=True)
                ws_thread.start()
                st.session_state['ws_started'] = True
                st.session_state['ce_token'] = ce_token
                st.session_state['pe_token'] = pe_token
                st.success("✅ Connected & Subscribed to Live Data!")
            else:
                st.error("❌ Login Failed. Check Auth Code.")

with ctrl4: st.button("Stop")
with ctrl5: st.button("Reset paper P&L")
st.caption("Paper trading only. Verify all charge rates in CONFIG.charges against current NSE / broker schedules.")
st.divider()

# --- 5. Arbitrage Calculations & Data Display ---
st.markdown("#### NET PROFIT IN RUPEES per lot (after ALL costs)")

fut_ltp = st.session_state['live_prices'].get(nifty_fut_token, 0.0)
ce_ltp = st.session_state['live_prices'].get(st.session_state.get('ce_token', '0'), 0.0)
pe_ltp = st.session_state['live_prices'].get(st.session_state.get('pe_token', '0'), 0.0)

conv_gross = 0
rev_gross = 0
conv_net = 0
rev_net = 0

if fut_ltp > 0 and ce_ltp > 0 and pe_ltp > 0:
    conv_gross = (spot - fut_ltp) + ce_ltp - pe_ltp
    conv_net = (conv_gross * lot_size) - min_net 
    
    rev_gross = (fut_ltp - spot) + pe_ltp - ce_ltp
    rev_net = (rev_gross * lot_size) - min_net

bc1, bc2 = st.columns(2)
bc1.info(f"**BEST CONVERSION (net Rs / lot)**\n\n# ₹ {conv_net:.2f}" if conv_net > 0 else "**BEST CONVERSION (net Rs / lot)**\n\n# -")
bc2.info(f"**BEST REVERSAL (net Rs / lot)**\n\n# ₹ {rev_net:.2f}" if rev_net > 0 else "**BEST REVERSAL (net Rs / lot)**\n\n# -")

df1 = pd.DataFrame([{
    "Strike": spot,
    "Conv. gross/unit": round(conv_gross, 2),
    "Conv. costs Rs": min_net,
    "CONV. NET Rs/lot": round(conv_net, 2),
    "Rev. gross/unit": round(rev_gross, 2),
    "Rev. costs Rs": min_net,
    "REV. NET Rs/lot": round(rev_net, 2)
}])
st.dataframe(df1, use_container_width=True, hide_index=True)
st.caption("NET = (gross per unit x lot size) - brokerage - STT - exchange/SEBI/stamp - GST - slippage. Green = profit, red = loss.")
st.write("")

alert_data = []
if conv_gross > alert_price:
    alert_data.append({"Type": "Conversion", "Legs": "Buy Fut, Sell CE, Buy PE", "Strike": spot, "PRICE (Rs/unit)": round(conv_gross, 2), "Gross / lot": round(conv_gross * lot_size, 2), "Net / lot after costs": round(conv_net, 2)})
if rev_gross > alert_price:
    alert_data.append({"Type": "Reversal", "Legs": "Sell Fut, Buy CE, Sell PE", "Strike": spot, "PRICE (Rs/unit)": round(rev_gross, 2), "Gross / lot": round(rev_gross * lot_size, 2), "Net / lot after costs": round(rev_net, 2)})

if alert_data:
    st.markdown(f"#### 🚨 Prices above Rs {alert_price} detected!")
    st.dataframe(pd.DataFrame(alert_data), use_container_width=True, hide_index=True)
else:
    st.markdown(f"#### No price above Rs {alert_price} yet")
    df2 = pd.DataFrame(columns=["Type", "Legs", "Strike", "PRICE (Rs/unit)", "Gross / lot", "Net / lot after costs"])
    st.dataframe(df2, use_container_width=True, hide_index=True)

if st.session_state['ws_started']:
    time.sleep(refresh_ms / 1000)
    st.rerun()
