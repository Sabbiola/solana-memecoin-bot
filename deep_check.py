import os
import requests
import re
import base58
from solders.keypair import Keypair
from dotenv import load_dotenv
from supabase import create_client
from datetime import datetime, timezone, timedelta

# Load env
load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
PRIVATE_KEY = os.getenv("SOLANA_PRIVATE_KEY")
RPC_URL = os.getenv("RPC_URL") or "https://api.mainnet-beta.solana.com"

if not SUPABASE_URL or not SUPABASE_KEY or not PRIVATE_KEY:
    print("❌ Error: Missing credentials.")
    exit(1)

# Connect Supabase
try:
    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
except Exception as e:
    print(f"❌ Supabase Connection failed: {e}")
    exit(1)

# Derive Wallet
try:
    keypair = Keypair.from_base58_string(PRIVATE_KEY)
    WALLET_ADDRESS = str(keypair.pubkey())
    print(f"🔑 Wallet: {WALLET_ADDRESS}")
except Exception as e:
    print(f"❌ Failed to derive keys: {e}")
    exit(1)

def format_ts_unix(ts):
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%H:%M:%S')

def parse_iso(iso_str):
    try:
        return datetime.fromisoformat(iso_str.replace('Z', '+00:00'))
    except:
        return None

# 1. FETCH ON-CHAIN TXS (Last 50)
print("\n📡 Fetching ON-CHAIN history (last 50)...")
headers = {"Content-Type": "application/json"}
payload = {
    "jsonrpc": "2.0", "id": 1, "method": "getSignaturesForAddress",
    "params": [WALLET_ADDRESS, {"limit": 50}]
}
real_txs = []
try:
    resp = requests.post(RPC_URL, json=payload, headers=headers)
    data = resp.json()
    if 'result' in data:
        for item in data['result']:
            real_txs.append({
                'signature': item['signature'],
                'blockTime': item['blockTime'],
                'err': item['err'],
                'dt': datetime.fromtimestamp(item['blockTime'], tz=timezone.utc)
            })
    else:
        print(f"❌ RPC Error: {data}")
except Exception as e:
    print(f"❌ RPC Exception: {e}")

# 2. FETCH & PARSE SUPABASE LOGS (Last 200)
# We look for "BUY", "SELL", "ENTRY", "EXIT" in the message
print("💾 Fetching SUPABASE LOGS (last 200)...")
log_events = []
try:
    resp = supabase.table('logs').select("created_at,message,level").order('created_at', desc=True).limit(200).execute()
    logs = resp.data
    
    # Regex to catch trade actions
    # Patterns like: "ENTRY_SCOUT B2s5... (Mint): 0.01 SOL..."
    # or "EXIT FAILED..."
    # or "PRICES [SOL: ...]" (ignore)
    
    for log in logs:
        msg = log['message']
        ts_str = log['created_at']
        dt = parse_iso(ts_str)
        if not dt: continue
        
        # Look for trade keywords
        if any(x in msg for x in ["ENTRY", "EXIT", "BUY", "SELL", "force sell", "stop loss"]):
            # exclude price updates
            if "PRICES [" in msg: continue
            
            # Attempt to extract Token Mint (often 6-8 chars like B2s5...)
            # or full mint if present
            
            log_events.append({
                'dt': dt,
                'msg': msg,
                'level': log['level']
            })
            
except Exception as e:
    print(f"❌ Log fetch error: {e}")

# 3. ADVANCED CORRELATION
print(f"\n{'='*30} DEEP ANALYSIS: LOGS vs CHAIN {'='*30}")
print(f"{'TIME (UTC)':<12} | {'SOURCE':<10} | {'DETAILS'}")
print("-" * 80)

# Merge both lists by time
combined = []
for tx in real_txs:
    combined.append({'type': 'CHAIN', 'obj': tx, 'time': tx['dt']})
for log in log_events:
    combined.append({'type': 'LOG', 'obj': log, 'time': log['dt']})

# Sort descending (newest first)
combined.sort(key=lambda x: x['time'], reverse=True)

# Find matches (simple time window matching)
matched_indices = set()

for i, item in enumerate(combined):
    if item['type'] == 'CHAIN':
        tx = item['obj']
        ts_str = tx['dt'].strftime('%H:%M:%S')
        status = "✅" if not tx['err'] else "❌"
        sig_short = f"...{tx['signature'][-6:]}"
        
        # Look for a LOG nearby (+- 5 seconds)
        found_match = False
        nearby_logs = []
        
        # Scan neighbours
        window = 10 # seconds
        for j in range(max(0, i-20), min(len(combined), i+20)):
            other = combined[j]
            if other['type'] == 'LOG':
                delta = abs((tx['dt'] - other['time']).total_seconds())
                if delta <= window:
                    nearby_logs.append(other['obj']['msg'])
                    matched_indices.add(j)
                    found_match = True
        
        log_summary = " | ".join(nearby_logs[:1]) if nearby_logs else "⚠️  NO MATCHING LOG FOUND"
        if len(log_summary) > 60: log_summary = log_summary[:57] + "..."
        
        print(f"{ts_str:<12} | CHAIN {status}  | {sig_short} -> {log_summary}")

    elif item['type'] == 'LOG' and i not in matched_indices:
        # This is a log that didn't match a chain tx?
        # Only print if it looks like an EXECUTION log, not just an "attempt"
        log = item['obj']
        msg = log['msg']
        ts_str = log['dt'].strftime('%H:%M:%S')
        
        # Filter noise
        if "FAILED" in msg:
            print(f"{ts_str:<12} | LOG (ERR)  | {msg[:60]}...")
        elif "EXIT" in msg or "ENTRY" in msg:
             print(f"{ts_str:<12} | 👻 GHOST?  | Logged but NO TX found: {msg[:50]}...")

print("\nDONE.")
