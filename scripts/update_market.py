import json
import re
from pathlib import Path
from datetime import datetime, timezone
import requests

MARKET_PATH = Path("data/market.json")

# CNN Fear & Greed (kan blokere bots)
CNN_FNG_JSON_TODAY = "https://production.dataviz.cnn.io/index/fearandgreed/graphdata/{date}"
CNN_FNG_PAGE = "https://edition.cnn.com/markets/fear-and-greed"

# VIX fra FRED (CSV)
FRED_VIX_CSV = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=VIXCLS"

# Browser-agtige headers (hjælper nogle gange mod 418)
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "da,en-US;q=0.8,en;q=0.6",
    "Connection": "keep-alive",
    "Referer": "https://edition.cnn.com/markets/fear-and-greed",
}

TIMEOUT = 25


def utc_now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def safe_float(x):
    try:
        return float(x)
    except Exception:
        return None


def load_existing_market():
    if MARKET_PATH.exists():
        try:
            return json.loads(MARKET_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    # default skeleton
    return {
        "updatedAt": None,
        "fearGreed": {"value": None, "label": None, "asof": None, "source": None},
        "vix": {"value": None, "asof": None, "source": None},
        "notes": [],
    }


def save_market(obj):
    MARKET_PATH.parent.mkdir(parents=True, exist_ok=True)
    MARKET_PATH.write_text(
        json.dumps(obj, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def http_get_text(url):
    r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    return r.text


def http_get_json(url):
    r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()


def label_fng(v):
    if v is None:
        return None
    # simple CNN-like buckets
    if v <= 24:
        return "Extreme fear"
    if v <= 44:
        return "Fear"
    if v <= 54:
        return "Neutral"
    if v <= 74:
        return "Greed"
    return "Extreme greed"


def find_current_fng_value(payload):
    """
    CNN graphdata JSON kan ændre format.
    Vi prøver at finde en 'now' værdi robust ved at traversere.
    Typisk ligger det i payload['fear_and_greed']['now']['value'] eller lign.
    """
    stack = [payload]
    while stack:
        cur = stack.pop()
        if isinstance(cur, dict):
            # mønster: {"now": {"value": 53, ...}}
            if "now" in cur and isinstance(cur["now"], dict) and "value" in cur["now"]:
                v = safe_float(cur["now"]["value"])
                if v is not None:
                    return v
            # eller {"now": 53}
            if "now" in cur and not isinstance(cur["now"], (dict, list)):
                v = safe_float(cur["now"])
                if v is not None:
                    return v
            stack.extend(cur.values())
        elif isinstance(cur, list):
            stack.extend(cur)
    return None


def fetch_fng_from_cnn_json():
    """
    Prøver CNN dataviz JSON endpoint for i dag (UTC dato).
    """
    today = datetime.now(timezone.utc).date().isoformat()
    url = CNN_FNG_JSON_TODAY.format(date=today)
    payload = http_get_json(url)

    v = find_current_fng_value(payload)
    if v is None:
        raise RuntimeError("Could not locate Fear & Greed value in CNN JSON payload")

    return {
        "value": v,
        "label": label_fng(v),
        "asof": today,
        "source": "CNN (dataviz json)",
    }


def fetch_fng_from_cnn_page_fallback():
    """
    Fallback: henter HTML og prøver at finde et tal 0-100.
    Ikke garanteret (CNN ændrer siden), men nogle gange virker det.
    """
    html = http_get_text(CNN_FNG_PAGE)

    # Prøv nogle typiske mønstre (meget defensivt)
    # 1) JSON-ish: "fearGreedIndex": 62
    m = re.search(r'"fearGreedIndex"\s*:\s*(\d{1,3})', html)
    if m:
        v = safe_float(m.group(1))
        if v is not None and 0 <= v <= 100:
            today = datetime.now(timezone.utc).date().isoformat()
            return {
                "value": v,
                "label": label_fng(v),
                "asof": today,
                "source": "CNN (page fallback)",
            }

    # 2) et "now" value: "now":{"value":62
    m = re.search(r'"now"\s*:\s*\{\s*"value"\s*:\s*(\d{1,3})', html)
    if m:
        v = safe_float(m.group(1))
        if v is not None and 0 <= v <= 100:
            today = datetime.now(timezone.utc).date().isoformat()
            return {
                "value": v,
                "label": label_fng(v),
                "asof": today,
                "source": "CNN (page fallback now)",
            }

    raise RuntimeError("Fallback could not extract Fear & Greed from CNN page")


def fetch_vix_from_fred():
    """
    FRED CSV: DATE,VIXCLS
    Finder seneste linje med tal.
    """
    csv_text = http_get_text(FRED_VIX_CSV)
    lines = [ln.strip() for ln in csv_text.splitlines() if ln.strip()]
    if len(lines) < 2:
        raise RuntimeError("FRED CSV too short")

    last_date = None
    last_val = None

    # gå bagfra og find første gyldige tal
    for ln in reversed(lines[1:]):  # skip header
        parts = ln.split(",")
        if len(parts) < 2:
            continue
        d, v = parts[0].strip(), parts[1].strip()
        fv = safe_float(v)
        if fv is None:
            continue
        last_date, last_val = d, fv
        break

    if last_val is None:
        raise RuntimeError("Could not parse any VIX value from FRED CSV")

    return {
        "value": last_val,
        "asof": last_date,
        "source": "FRED (VIXCLS)",
    }


def main():
    out = load_existing_market()

    # ensure structure
    out.setdefault("fearGreed", {"value": None, "label": None, "asof": None, "source": None})
    out.setdefault("vix", {"value": None, "asof": None, "source": None})
    out.setdefault("notes", [])

    out["updatedAt"] = utc_now_iso()

    # Fear & Greed (må ikke stoppe build)
    try:
        out["fearGreed"] = fetch_fng_from_cnn_json()
    except Exception as e1:
        out["notes"].append(f"Fear&Greed JSON failed: {type(e1).__name__}: {e1}")
        # fallback
        try:
            out["fearGreed"] = fetch_fng_from_cnn_page_fallback()
        except Exception as e2:
            out["notes"].append(f"Fear&Greed fallback failed: {type(e2).__name__}: {e2}")
            # behold eksisterende out["fearGreed"] (fra gamle market.json)

    # VIX (stabil)
    try:
        out["vix"] = fetch_vix_from_fred()
    except Exception as e:
        out["notes"].append(f"VIX failed: {type(e).__name__}: {e}")
        # behold eksisterende out["vix"] hvis den fandtes

    save_market(out)
    print(f"Wrote {MARKET_PATH}")


if __name__ == "__main__":
    main()
