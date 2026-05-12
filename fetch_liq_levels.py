"""
fetch_liq_levels.py
Pobiera OI history z Binance i oblicza poziomy likwidacji.
Wynik zapisuje do liq_levels.json
"""

import requests
import json
from datetime import datetime, timezone
from collections import defaultdict

SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]

# Progi lewaru — odpowiadaja kolorom na mapie Leviathana
# x100 = likwidacja przy 1% ruchu (czerwone)
# x50  = likwidacja przy 2% ruchu (pomaranczowe)
# x25  = likwidacja przy 4% ruchu (niebieskie)
LEVERAGE_LEVELS = [
    {"leverage": 100, "color": "red",    "move_pct": 1.0},
    {"leverage": 50,  "color": "orange", "move_pct": 2.0},
    {"leverage": 25,  "color": "blue",   "move_pct": 4.0},
]

def fetch_current_price(symbol: str) -> float | None:
    try:
        r = requests.get(
            f"https://fapi.binance.com/fapi/v1/ticker/price?symbol={symbol}",
            timeout=10
        )
        return float(r.json()["price"])
    except:
        return None

def fetch_oi_history(symbol: str, period="5m", limit=288) -> list:
    """Pobiera historie OI z ostatnich ~24h (288 * 5min = 24h)"""
    try:
        r = requests.get(
            f"https://fapi.binance.com/futures/data/openInterestHist",
            params={"symbol": symbol, "period": period, "limit": limit},
            timeout=10
        )
        return r.json()
    except:
        return []

def fetch_klines(symbol: str, interval="5m", limit=288) -> list:
    """Pobiera swieczki do korelacji z OI"""
    try:
        r = requests.get(
            f"https://fapi.binance.com/fapi/v1/klines",
            params={"symbol": symbol, "interval": interval, "limit": limit},
            timeout=10
        )
        return r.json()
    except:
        return []

def calculate_liq_levels(symbol: str) -> dict:
    """
    Glowna logika:
    1. Dla kazdego snapshotu OI sprawdzamy jaka byla cena
    2. Jesli OI wzrosl (nowe pozycje otwarte) -> zapisujemy ten poziom cenowy
    3. Obliczamy gdzie beda liq levels dla roznych lewatow
    4. Grupujemy po clusterach (co 0.5% ceny)
    """
    price = fetch_current_price(symbol)
    if price is None:
        return {"symbol": symbol, "error": "price fetch failed", "levels": []}

    oi_history = fetch_oi_history(symbol)
    klines = fetch_klines(symbol)

    if not oi_history or not klines:
        return {"symbol": symbol, "error": "data fetch failed", "levels": []}

    # Mapuj timestamp -> cena zamkniecia swieczki
    kline_map = {}
    for k in klines:
        ts = k[0]
        close = float(k[4])
        kline_map[ts] = close

    # Znajdz gdzie OI wzrosl znaczaco
    clusters = defaultdict(lambda: {"oi_usd": 0, "count": 0})

    prev_oi = None
    for snap in oi_history:
        oi_usd = float(snap.get("sumOpenInterestValue", 0))
        ts = snap.get("timestamp", 0)

        if prev_oi is not None and oi_usd > prev_oi:
            oi_delta = oi_usd - prev_oi
            delta_pct = (oi_delta / prev_oi) * 100

            # Tylko jesli delta > 0.1% (znaczacy wzrost pozycji)
            if delta_pct > 0.1:
                # Znajdz cene z tego czasu
                entry_price = kline_map.get(ts)
                if entry_price is None:
                    # Szukaj najblizszego timestampu
                    closest = min(kline_map.keys(), key=lambda x: abs(x - ts), default=None)
                    if closest:
                        entry_price = kline_map[closest]

                if entry_price:
                    # Zaokraglij do 0.5% clustra
                    bucket = round(entry_price / (price * 0.005)) * (price * 0.005)
                    clusters[round(bucket, 2)]["oi_usd"] += oi_delta
                    clusters[round(bucket, 2)]["count"] += 1

        prev_oi = oi_usd

    # Buduj poziomy likwidacji
    levels = []
    for entry_price, data in clusters.items():
        if data["oi_usd"] < 1_000_000:  # pomijaj male clustry < $1M
            continue

        for lev in LEVERAGE_LEVELS:
            # Likwidacja longów
            liq_long = entry_price * (1 - 1 / lev["leverage"])
            # Likwidacja shortów
            liq_short = entry_price * (1 + 1 / lev["leverage"])

            levels.append({
                "entry_price": round(entry_price, 2),
                "liq_long": round(liq_long, 2),
                "liq_short": round(liq_short, 2),
                "leverage": lev["leverage"],
                "color": lev["color"],
                "oi_usd": round(data["oi_usd"]),
                "significance": "large" if data["oi_usd"] > 10_000_000 else "medium" if data["oi_usd"] > 5_000_000 else "small",
            })

    # Sortuj po wartosci OI (wazniejsze pierwsze)
    levels.sort(key=lambda x: x["oi_usd"], reverse=True)

    # Znajdz najblizsze poziomy do aktualnej ceny
    nearby_long_liq = sorted(
        [l for l in levels if l["liq_long"] < price],
        key=lambda x: abs(x["liq_long"] - price)
    )[:10]

    nearby_short_liq = sorted(
        [l for l in levels if l["liq_short"] > price],
        key=lambda x: abs(x["liq_short"] - price)
    )[:10]

    return {
        "symbol": symbol,
        "current_price": price,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "nearby_long_liq": nearby_long_liq,    # poziomy gdzie licytowani beda longi (ponizej ceny)
        "nearby_short_liq": nearby_short_liq,   # poziomy gdzie likwidowani beda shorty (powyzej ceny)
        "all_levels_count": len(levels),
    }

def main():
    result = {}
    for symbol in SYMBOLS:
        print(f"Processing {symbol}...")
        result[symbol] = calculate_liq_levels(symbol)
        print(f"  Current price: {result[symbol].get('current_price')}")
        print(f"  Levels found: {result[symbol].get('all_levels_count', 0)}")
        print(f"  Nearby long liq: {len(result[symbol].get('nearby_long_liq', []))}")
        print(f"  Nearby short liq: {len(result[symbol].get('nearby_short_liq', []))}")

    result["updated_at"] = datetime.now(timezone.utc).isoformat()

    with open("liq_levels.json", "w") as f:
        json.dump(result, f, indent=2)

    print(f"\nSaved to liq_levels.json")
    print(f"BTC nearby long liq levels:")
    for l in result.get("BTCUSDT", {}).get("nearby_long_liq", [])[:5]:
        print(f"  x{l['leverage']} {l['color']:8} liq @ ${l['liq_long']:,.0f} (entry was ${l['entry_price']:,.0f}, OI delta ${l['oi_usd']/1e6:.1f}M)")

if __name__ == "__main__":
    main()
