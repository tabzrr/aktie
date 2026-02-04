import json
import re
from datetime import datetime, timezone
from pathlib import Path

import requests

MARKET_PATH = Path("data/market.json")

# CNN endpoints (kan være ustabile / ændrer format)
CNN_GRAPH_URL = "https://production.dataviz.cnn.io/index/fearandgreed/graphdata"
CNN_PAGE_URL = "https://edition.cnn.com/markets/fear-and-greed"

# VIX (stabil) via FRED CSV
FRED_VIX_CSV = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=VIXCLS"

HEADERS = {
    # Ligner en normal browser. (Helbred: CNN kan stadig blokere, men vi prøver.)
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Accept": "text/html,application/json,text/plain,*/*",
    "Accept-Language": "da-DK,da;q=0.9,en-US;q=0.8,en;q=0.7",
    "Referer": "https://edition.cnn.com/",
    "Connection": "keep-alive",
}


def utc_now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def safe_float(x):
    try:
        if x is None:
            return None
        if isinstance(x, (int, float)):
            return float(x)
        s = str(x).strip()
        if s == "" or s.lower() == "null":
            return None
        return float(s)
    except Exception:
        return None


def safe_int_0_100(x):
    v = safe_float(x)
    if v is None:
        return None
    v = int(round(v))
    if 0 <= v <= 100:
        return v
    return None


def fetch_json(url, timeout=25):
    r = requests.get(url, timeout=timeout, headers=HEADERS)
    r.raise_for_status()
    return r.json()


def fetch_text(url, timeout=25):
    r = requests.get(url, timeout=timeout, headers=HEADERS)
    r.raise_for_status()
    return r.text


def load_existing_market():
    if MARKET_PATH.exists():
        try:
            return json.loads(MARKET_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass

    # Default structure
    return {
        "updatedAt": None,
        "fearGreed": {"value": None, "label": None, "asof": None, "source": None},
        "vix": {"value": None, "asof": None, "source": None},
        "notes": [],
    }


def label_fng(v):
    if v is None:
        return None
    # simple bins
    if v <= 24:
        return "Ekstrem frygt"
    if v <= 44:
        return "Frygt"
    if v <= 54:
        return "Neutral"
    if v <= 74:
        return "Grådighed"
    return "Ekstrem grådighed"


def _walk_find_numbers(obj):
    """Find alle tal-lignende værdier dybt i JSON (som floats)."""
    nums = []
    stack = [obj]
    while stack:
        cur = stack.pop()
        if isinstance(cur, dict):
            for k, v in cur.items():
                # gem både key og value, så vi kan spotte mønstre
                stack.append(v)
        elif isinstance(cur, list):
            stack.extend(cur)
        else:
            fv = safe_float(cur)
            if fv is not None:
                nums.append(fv)
    return nums


def _find_fng_from_graph_json(payload):
    """
    CNN graphdata har skiftet format flere gange.
    Vi prøver flere patterns:
    - noget med now.value / now.score
    - noget der hedder fear_and_greed / fearAndGreed og indeholder en "now"
    - fallback: find et heltal 0-100 som "ligner" et index (sidste udvej)
    """
    # 1) Generic: find dict med now -> { value/score }
    stack = [payload]
    while stack:
        cur = stack.pop()
        if isinstance(cur, dict):
            # pattern: cur["now"] er dict og har value/score
            now = cur.get("now")
            if isinstance(now, dict):
                for key in ("value", "score", "index"):
                    if key in now:
                        v = safe_int_0_100(now.get(key))
                        if v is not None:
                            return v
            # gå dybere
            for v in cur.values():
                stack.append(v)
        elif isinstance(cur, list):
            stack.extend(cur)

    # 2) Specifikke keys
    if isinstance(payload, dict):
        for key in ("fear_and_greed", "fearAndGreed", "fear_greed", "fearGreed"):
            if key in payload and isinstance(payload[key], dict):
                d = payload[key]
                # prøv både "now" og "data" felter
                now = d.get("now")
                if isinstance(now, dict):
                    for k in ("value", "score", "index"):
                        v = safe_int_0_100(now.get(k))
                        if v is not None:
                            return v
                # nogle gange ligger det bare som "score"
                v = safe_int_0_100(d.get("score") or d.get("value") or d.get("index"))
                if v is not None:
                    return v

    # 3) Last resort: scan alle tal og find et heltal 0-100 (men undgå fx years/dates)
    nums = _walk_find_numbers(payload)
    candidates = [int(round(x)) for x in nums if 0 <= x <= 100]
    # typisk er index et heltal — vi tager det mest "heltals-agtige"
    for c in candidates:
        if 0 <= c <= 100:
            return c

    return None


def fetch_fng_best_effort(notes):
    # A) Graph JSON
    try:
        payload = fetch_json(CNN_GRAPH_URL)
        v = _find_fng_from_graph_json(payload)
        if v is not None:
            return {
                "value": v,
                "label": label_fng(v),
                "asof": datetime.now(timezone.utc).date().isoformat(),
                "source": "CNN (graphdata)",
            }
        notes.append("Fear&Greed: kunne ikke finde 0-100 i CNN graphdata.")
    except Exception as e:
        notes.append(f"Fear&Greed graphdata failed: {type(e).__name__}: {e}")

    # B) HTML side (regex)
    try:
        html = fetch_text(CNN_PAGE_URL)
        # Prøv et par typiske mønstre: "fearAndGreed":{... "now":{..."value": 62 ...}}
        patterns = [
            r'"fearAndGreed"\s*:\s*\{.*?"now"\s*:\s*\{.*?"value"\s*:\s*(\d{1,3})',
            r'"fear_and_greed"\s*:\s*\{.*?"now"\s*:\s*\{.*?"value"\s*:\s*(\d{1,3})',
            r'"now"\s*:\s*\{[^}]*?"value"\s*:\s*(\d{1,3})',
            # nogle gange bare "score": 62
            r'"score"\s*:\s*(\d{1,3})',
        ]
        for p in patterns:
            m = re.search(p, html, flags=re.IGNORECASE | re.DOTALL)
            if m:
                v = safe_int_0_100(m.group(1))
                if v is not None:
                    return {
                        "value": v,
                        "label": label_fng(v),
                        "asof": datetime.now(timezone.utc).date().isoformat(),
                        "source": "CNN (page)",
                    }
        notes.append("Fear&Greed: kunne ikke extracte fra CNN page (regex).")
    except Exception as e:
        notes.append(f"Fear&Greed page failed: {type(e).__name__}: {e}")

    return None


def fetch_vix_from_fred(notes):
    try:
        csv_text = fetch_text(FRED_VIX_CSV)
        lines = [ln.strip() for ln in csv_text.splitlines() if ln.strip()]
        # header: DATE,VIXCLS
        # find sidste gyldige datapunkt bagfra
        last_date, last_val = None, None
        for ln in reversed(lines[1:]):
            parts = ln.split(",")
            if len(parts) >= 2:
                d, v = parts[0], parts[1]
                fv = safe_float(v)
                if fv is not None:
                    last_date, last_val = d, fv
                    break
        if last_val is None:
            notes.append("VIX: ingen gyldig værdi i FRED CSV.")
            return None
        return {"value": round(last_val, 2), "asof": last_date, "source": "FRED (VIXCLS)"}
    except Exception as e:
        notes.append(f"VIX failed: {type(e).__name__}: {e}")
        return None


def main():
    existing = load_existing_market()

    # vi skriver vores egne noter for denne run (max 6 linjer for ikke at spamme)
    run_notes = []

    out = existing if isinstance(existing, dict) else {}
    out["updatedAt"] = utc_now_iso()

    # Sørg for structure
    out.setdefault("fearGreed", {"value": None, "label": None, "asof": None, "source": None})
    out.setdefault("vix", {"value": None, "asof": None, "source": None})
    out.setdefault("notes", [])

    # --- Fear & Greed (BEST EFFORT) ---
    fng = fetch_fng_best_effort(run_notes)
    if fng is not None and fng.get("value") is not None:
        out["fearGreed"] = fng
    else:
        # VIGTIGT: behold sidste kendte værdi i stedet for at nulstille
        prev = out.get("fearGreed") or {}
        if prev.get("value") is None:
            run_notes.append("Fear&Greed: stadig ingen værdi (beholder None).")
        else:
            run_notes.append("Fear&Greed: fetch fejlede, beholdt sidste kendte værdi.")

    # --- VIX (stabil) ---
    vix = fetch_vix_from_fred(run_notes)
    if vix is not None and vix.get("value") is not None:
        out["vix"] = vix
    else:
        prev = out.get("vix") or {}
        if prev.get("value") is None:
            run_notes.append("VIX: stadig ingen værdi (beholder None).")
        else:
            run_notes.append("VIX: fetch fejlede, beholdt sidste kendte værdi.")

    # Append noter (men begræns)
    merged_notes = (out.get("notes") or [])
    # Fjern gamle hvis de er blevet for mange
    merged_notes = merged_notes[-20:]
    run_notes = run_notes[:6]
    out["notes"] = merged_notes + run_notes

    # Write
    MARKET_PATH.parent.mkdir(parents=True, exist_ok=True)
    MARKET_PATH.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote {MARKET_PATH}")


if __name__ == "__main__":
    main()
