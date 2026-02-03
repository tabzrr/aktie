import json
import datetime as dt
import urllib.request
from pathlib import Path

OUT_PATH = Path("data/market.json")

CNN_FNG_URL = "https://production.dataviz.cnn.io/index/fearandgreed/graphdata"
CBOE_VIX_CSV = "https://cdn.cboe.com/api/global/us_indices/daily_prices/VIX_History.csv"

def http_get_text(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", errors="replace")

def parse_vix_from_csv(csv_text: str):
    # CSV header: DATE, OPEN, HIGH, LOW, CLOSE
    lines = [ln.strip() for ln in csv_text.splitlines() if ln.strip()]
    # Find last data row (skip header)
    # Some files may have extra blank lines; we already removed blanks.
    header = lines[0].lower()
    if "date" not in header or "close" not in header:
        raise ValueError("Unexpected VIX CSV header")

    last = lines[-1]
    parts = [p.strip() for p in last.split(",")]
    if len(parts) < 5:
        raise ValueError("Unexpected VIX CSV row format")

    date_str = parts[0]
    close_str = parts[4]
    return date_str, float(close_str)

def parse_fng_from_cnn(json_text: str):
    data = json.loads(json_text)

    # CNN structure (observed): data["fear_and_greed_historical"]["data"] = [{"x": <ms>, "y": <value>}, ...]
    hist = data.get("fear_and_greed_historical", {}).get("data", [])
    if not hist:
        raise ValueError("CNN F&G: missing historical data")

    last = hist[-1]
    value = float(last["y"])
    ts_ms = int(last["x"])
    asof = dt.datetime.utcfromtimestamp(ts_ms / 1000).date().isoformat()

    def label(v: float) -> str:
        if v < 25: return "Extreme fear"
        if v < 45: return "Fear"
        if v <= 55: return "Neutral"
        if v <= 75: return "Greed"
        return "Extreme greed"

    return asof, value, label(value)

def main():
    # Fetch sources
    fng_raw = http_get_text(CNN_FNG_URL)
    vix_raw = http_get_text(CBOE_VIX_CSV)

    fng_date, fng_value, fng_label = parse_fng_from_cnn(fng_raw)
    vix_date, vix_close = parse_vix_from_csv(vix_raw)

    # Pick latest "as of" among the two
    asof = max(fng_date, vix_date)

    payload = {
        "asOf": asof,
        "fearGreed": round(fng_value, 1),
        "fearGreedLabel": fng_label,
        "vixClose": round(vix_close, 2),
        "vixDate": vix_date,
        "sources": {
            "cnnFearGreed": CNN_FNG_URL,
            "cboeVixCsv": CBOE_VIX_CSV
        }
    }

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote {OUT_PATH}")

if __name__ == "__main__":
    main()
