import os
import requests
import base58
from solders.keypair import Keypair
from dotenv import load_dotenv
from supabase import create_client
from datetime import datetime

# Load env
load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
PRIVATE_KEY = os.getenv("SOLANA_PRIVATE_KEY")
RPC_URL = os.getenv("RPC_URL") or "https://api.mainnet-beta.solana.com"

if not SUPABASE_URL or not SUPABASE_KEY or not PRIVATE_KEY:
    print("❌ Error: Missing SUPABASE credentials or PRIVATE_KEY in .env")
    exit(1)

# Connect Supabase
try:
    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
except Exception as e:
    print(f"❌ Supabase Connection failed: {e}")
    exit(1)

# Derive Wallet Address
try:
    # Handle base58 encoded private key
    keypair = Keypair.from_base58_string(PRIVATE_KEY)
    WALLET_ADDRESS = str(keypair.pubkey())
    print(f"🔑 Wallet: {WALLET_ADDRESS}")
except Exception as e:
    print(f"❌ Failed to derive wallet address: {e}")
    exit(1)

def format_ts(ts):
    if not ts: return "N/A"
    return datetime.fromtimestamp(ts).strftime('%H:%M:%S')

def short_sig(sig):
    if not sig: return "---"
    return f"...{sig[-8:]}"

# 1. FETCH ON-CHAIN SIGNATURES
print("\n📡 Fetching recent blockchain transactions...")
headers = {"Content-Type": "application/json"}
payload = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "getSignaturesForAddress",
    "params": [
        WALLET_ADDRESS,
        {"limit": 10}
    ]
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
                'err': item['err']
            })
    else:
        print(f"❌ RPC API Error: {data}")
except Exception as e:
    print(f"❌ Failed to fetch blockchain data: {e}")

# 2. FETCH SUPABASE TRADES
print("💾 Fetching Supabase trade logs...")
db_txs = []
try:
    # Fetch last 20 to ensure we catch overlaps
    resp = supabase.table('trades').select("*").order('block_time', desc=True).limit(20).execute()
    db_txs = resp.data
except Exception as e:
    print(f"❌ Failed to fetch Supabase data: {e}")

# 3. COMPARE AND PRINT
print(f"\n{'='*20} COMPARISON (Last 10 on-chain) {'='*20}")
print(f"{'TIME':<10} | {'SIGNATURE':<12} | {'STATUS':<8} | {'IN DB?':<10} | {'TYPE/MINT'}")
print("-" * 75)

# Create lookup for DB trades
db_sigs = {t.get('signature'): t for t in db_txs if t.get('signature')}

for tx in real_txs:
    sig = tx['signature']
    ts = format_ts(tx['blockTime'])
    status = "❌ FAIL" if tx['err'] else "✅ OK"
    
    # Check if in DB
    in_db = "YES" if sig in db_sigs else "NO"
    
    # Extra info if in DB
    extra = ""
    if sig in db_sigs:
        trade = db_sigs[sig]
        # Match DB symbol
        extra = f"{trade.get('type', '?').upper()} {trade.get('token_symbol')}"
    
    # Highlight missing ones
    in_db_str = in_db
    if in_db == "NO":
        in_db_str = "🔴 MISSING"
    else:
        in_db_str = "🟢 LOGGED"

    print(f"{ts:<10} | {short_sig(sig):<12} | {status:<8} | {in_db_str:<10} | {extra}")

print("\n")
print(f"{'='*20} UNMATCHED DB LOGS (Recent) {'='*20}")
# Logs in DB that are NOT in the recent real_txs list?
# (Note: real_txs is only last 10, so older DB logs shouldn't be flagged as 'phantom' necessarily)
# But if there is a DB log OLDER than the newest real tx, but NEWER than the oldest real tx, and missing... that's weird.
# For simplicity, just show most recent DB log
if db_txs:
    latest_db = db_txs[0]
    ts_db = format_ts(latest_db.get('block_time'))
    print(f"Latest DB Log: {ts_db} - {latest_db.get('type')} {latest_db.get('token_symbol')} ({short_sig(latest_db.get('signature'))})")
else:
    print("No logs in database.")

print("\nDONE.")
