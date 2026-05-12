"""
fetch_liq_levels.py - używa Binance Spot + Futures API
"""
import requests
import json
from datetime import datetime, timezone
from collections import defaultdict

SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
HEADERS = {"User-Agent": "Mozilla/5.0"}

def fetch_price(symbol):
    try:
        r = requests.get(
            f"https://api.binance.com/api/v3/ticker/price?symbol={symbol}",
            headers=HEADERS, timeout=10
        )
        data = r.json()
        print(f"  Price response: {data}")
        return float(data["price"])
    except Exception as e:
        print(f"  Price error: {e}")
        return None

def fetch_oi_history(symbol, period="5m", limit=288):
    try:
        r = requests.get(
            "https://fapi.binance.com/futures/data/openInterestHist",
            params={"symbol": symbol, "period": period, "limit": limit},
            headers=HEADERS, timeout=10
        )
        data = r.json()
        print(f"  OI response type: {type(data)}, len: {len(data) if isinstance(data, list) else 'N/A'}")
        if isinstance(data, list):
            return data
        print(f"  OI error response: {data}")
        return []
    except Exception as e:
        print(f"  OI error: {e}")
        return []

def fetch_klines(symbol, interval="5m", limit=288):
    try:
        r = requests.get(
            "https://fapi.binance.com/fapi/v1/klines",
            params={"symbol": symbol, "interval": interval, "limit": limit},
            headers=HEADERS, timeout=10
        )
        data = r.json()
        if isinstance(data, list):
            return data
        return []
    except Exception as e:
        print(f"  Klines error: {e}")
        return []

def calculate_liq_levels(symbol):
    print(f"\nProcessing {symbol}...")
    price = fetch_price(symbol)
    if price is None:
        return {"symbol": symbol, "error": "price fetch failed", "levels": []}

    oi_history = fetch_oi_history(symbol)
    klines = fetch_klines(symbol)

    if not oi_history:
        return {
            "symbol": symbol,
            "current_price": price,
            "error": "OI fetch failed - fapi may be restricted",
            "nearby_long_liq": [],
            "nearby_short_liq": [],
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }

    kline_map = {k[0]: float(k[4]) for k in klines}

    clusters = defaultdict(lambda: {"oi_usd": 0, "count": 0})
    prev_oi = None

    for snap in oi_history:
        oi_usd = float(snap.get("sumOpenInterestValue", 0))
        ts = snap.get("timestamp", 0)
        if prev_oi is not None and oi_usd > prev_oi:
            delta = oi_usd - prev_oi
            delta_pct = (delta / prev_oi) * 100
            if delta_pct > 0.1:
                entry_price = kline_map.get(ts)
                if entry_price is None:
                    closest = min(kline_map.keys(), key=lambda x: abs(x - ts), default=None)
                    if closest:
                        entry_price = kline_map[closest]
                if entry_price:
                    bucket = round(round(entry_price / (price * 0.005)) * (price * 0.005), 2)
                    clusters[bucket]["oi_usd"] += delta
                    clusters[bucket]["count"] += 1
        prev_oi = oi_usd

    LEVS = [
        {"lev": 100, "color": "red",    "move": 0.01},
        {"lev": 50,  "color": "orange", "move": 0.02},
        {"lev": 25,  "color": "blue",   "move": 0.04},
    ]

    levels = []
    for entry_price, data in clusters.items():
        if data["oi_usd"] < 1_000_000:
            continue
        for l in LEVS:
            levels.append({
                "entry_price": entry_price,
                "liq_long":  round(entry_price * (1 - l["move"]), 2),
                "liq_short": round(entry_price * (1 + l["move"]), 2),
                "leverage": l["lev"],
                "color": l["color"],
                "oi_usd": round(data["oi_usd"]),
                "significance": "large" if data["oi_usd"] > 10e6 else "medium" if data["oi_usd"] > 5e6 else "small",
            })

    nearby_long  = sorted([l for l in levels if l["liq_long"]  < price], key=lambda x: abs(x["liq_long"]  - price))[:10]
    nearby_short = sorted([l for l in levels if l["liq_short"] > price], key=lambda x: abs(x["liq_short"] - price))[:10]

    return {
        "symbol": symbol,
        "current_price": price,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "nearby_long_liq": nearby_long,
        "nearby_short_liq": nearby_short,
        "all_levels_count": len(levels),
    }

def main():
    result = {}
    for symbol in SYMBOLS:
        result[symbol] = calculate_liq_levels(symbol)
    result["updated_at"] = datetime.now(timezone.utc).isoformat()
    with open("liq_levels.json", "w") as f:
        json.dump(result, f, indent=2)
    print("\nSaved liq_levels.json")

if __name__ == "__main__":
    main()
