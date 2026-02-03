import json
import os
from datetime import datetime, timezone
import requests

ROOT = os.path.dirname(os.path.dirname(__file__))
OUT_PATH = os.path.join(ROOT, "data", "market.json")

# CNN Fear & Greed endpoint (ofte brugt, men kan blokere bots)
CNN_BASE = "https://production.dataviz.cnn.io/index/fearandgreed/graphdata"

# VIX via FRED (stabil)
FRED_VIX_CSV = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=VIXCLS"

HEADERS = {
    # CNN blokker ofte uden en "browser"-UA
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36",
    "Accept": "application/json,text/plain,*/*",
    "Accept-Language": "en-US,en;q=0.9,da;q=0.8",
    "Referer": "https://edition.cnn.com/",
}

def utc_now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()

def safe_float(x):
    try:
        return float(x)
    except Exception:
        return None

def fetch_json(url, timeout=25):
    r = requests.get(url, timeout=timeout, headers=HEADERS)
    r.raise_for_status()
    return r.json()

def fetch_text(url, timeout=25):
    r = requests.get(url, timeout=timeout, headers=HEADERS)
    r.raise_for_status()
    return r.text

def find_current_fng_value(payload):
    # CNN format kan variere → vi søger efter et "now.value"-mønster
    stack = [payload]
    while stack:
        cur = stack.pop()
        if isinstance(cur, dict):
            if "now" in cur and isinstance(cur["now"], dict) and "value" in cur["now"]:
                v = safe_float(cur["now"]["value"])
                if v is not None:
                    return v
            stack.extend(cur.values())
        elif isinstance(cur, list):
            stack.extend(cur)
    return None

def label_fng(v):
    if v is None:
        return None
    if v <= 24: return "Extreme fear"
    if v <= 44: return "Fear"
    if v <= 54: return "Neutral"
    if v <= 74: return "Greed"
    return "Extreme greed"

def fetch_fng():
    today = datetime.now(timezone.utc).date().isoformat()
    url = f"{CNN_BASE}/{today}"
    payload = fetch_json(url)
    v = find_current_fng_value(payload)
    return {
        "value": v,
        "label": label_fng(v),
        "asOf": today,
        "source": "CNN (dataviz endpoint)",
    }

def fetch_vix_from_fred():
    csv_text = fetch_text(FRED_VIX_CSV)
    lines = [ln.strip() for ln in csv_text.splitlines() if ln.strip()]
    last_date, last_val = None, None
    for ln in reversed(lines[1:]):  # skip header
        parts = ln.split(",")
        if len(parts) >= 2:
            d, v = parts[0], parts[1]
            fv = safe_float(v)
            if fv is not None:
                last_date, last_val = d, fv
                break
    return {
        "value": last_val,
        "asOf": last_date,
        "source": "FRED (VIXCLS)",
    }

def main():
    os.makedirs(os.path.join(ROOT, "data"), exist_ok=True)

    out = {
        "updatedAt": utc_now_iso(),
        "fearGreed": None,
        "vix": None,
        "notes": [],
    }

    # Fear & Greed (må ikke crashe build)
    try:
        out["fearGreed"] = fetch_fng()
    except Exception as e:
        # Her fanger vi 418 og alt andet – og skriver det i notes.
        out["notes"].append(f"Fear&Greed failed: {type(e).__name__}: {e}")

    # VIX (stabilt)
    try:
        out["vix"] = fetch_vix_from_fred()
    except Exception as e:
        out["notes"].append(f"VIX failed: {type(e).__name__}: {e}")

    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    print(f"Wrote {OUT_PATH}")

if __name__ == "__main__":
    main()
