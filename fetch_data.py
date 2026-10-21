import os
import sys
import json
import time
import math
import urllib.request
import csv
import io
from datetime import datetime, timezone

try:
    import yfinance as yf
except ImportError:
    print("FATAL: yfinance not installed. Run: pip install yfinance")
    sys.exit(1)

CONFIG_FILE = "config.json"
DATA_DIR = "data"
OUTPUT_DATA = os.path.join(DATA_DIR, "data.json")

# Map common Yahoo industry names to Damodaran's industry classification
DAMODARAN_MAP = {
    "Semiconductors": "Semiconductor",
    "Semiconductor Equipment & Materials": "Semiconductor Equip",
    "Oil & Gas E&P": "Oil/Gas (Production and Exploration)",
    "Oil & Gas Integrated": "Oil/Gas (Integrated)",
    "Oil & Gas Refining & Marketing": "Oil/Gas (Refining and Marketing)",
    "Auto Manufacturers": "Auto & Truck",
    "Auto Parts": "Auto Parts",
    "Software - Infrastructure": "Software (System & Application)",
    "Software - Application": "Software (System & Application)",
    "Internet Content & Information": "Information Services",
    "Banks - Diversified": "Bank (Money Center)",
    "Banks - Regional": "Banks (Regional)",
    "Biotechnology": "Biotechnology",
    "Drug Manufacturers - General": "Drugs (Pharmaceutical)",
    "Consumer Electronics": "Computers/Peripherals",
    "Telecom Services": "Telecom (Wireless)",
    "Utilities - Regulated Electric": "Utility (General)"
}

def clean_val(val):
    if val is None:
        return None
    if isinstance(val, (int, str, bool)):
        return val
    if isinstance(val, float):
        return None if math.isnan(val) or math.isinf(val) else val
    return None

def fetch_damodaran_benchmarks():
    """Download Damodaran NYU Stern datasets for Valuation and Margins"""
    benchmarks = {}
    print("Fetching Damodaran NYU Stern industry benchmarks...")

    # Valuation dataset (P/E, Price/Book, EV/EBITDA, EV/Sales)
    url_pedata = "https://pages.stern.nyu.edu/~adamodar/pc/datasets/pedata.csv"
    # Margins dataset (Gross, Operating, Net margins)
    url_margin = "https://pages.stern.nyu.edu/~adamodar/pc/datasets/margin.csv"

    headers = {'User-Agent': 'Mozilla/5.0'}

    def parse_csv_stream(url):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=10) as resp:
                lines = [line.decode('latin-1') for line in resp.readlines()]
                return list(csv.reader(lines))
        except Exception as e:
            print(f"Warning: Could not fetch {url}: {e}")
            return []

    # Parse P/E & Valuation
    rows_pe = parse_csv_stream(url_pedata)
    for r in rows_pe:
        if len(r) > 8 and r[0].strip() and r[0].strip() != "Industry Name":
            ind_name = r[0].strip()
            def safe_float(idx):
                try: return float(r[idx].replace(',', '').replace('%', '').strip())
                except: return None

            benchmarks[ind_name] = {
                "trailingPE": safe_float(4),
                "forwardPE": safe_float(5),
                "priceToBook": safe_float(6),
                "enterpriseToEbitda": safe_float(8)
            }

    # Parse Margins
    rows_m = parse_csv_stream(url_margin)
    for r in rows_m:
        if len(r) > 6 and r[0].strip():
            ind_name = r[0].strip()
            def safe_float_pct(idx):
                try: return float(r[idx].replace(',', '').replace('%', '').strip())
                except: return None

            if ind_name in benchmarks:
                benchmarks[ind_name]["grossMargins"] = safe_float_pct(2)
                benchmarks[ind_name]["operatingMargins"] = safe_float_pct(4)
                benchmarks[ind_name]["profitMargins"] = safe_float_pct(5)

    print(f"Loaded benchmarks for {len(benchmarks)} Damodaran industries.")
    return benchmarks

def fetch_ticker_data(ticker_symbol):
    max_retries = 3
    backoff = [3, 8]
    data = None
    last_err = None

    for attempt in range(max_retries):
        try:
            t = yf.Ticker(ticker_symbol)
            info = t.info
            if not info or len(info) < 5:
                raise ValueError("Incomplete info payload from Yahoo")
            data = {"info": info}
            break
        except Exception as e:
            last_err = str(e)
            if attempt < max_retries - 1:
                time.sleep(backoff[attempt])
            else:
                return None, last_err
        time.sleep(0.5)

    info = data["info"]
    scalars = {}

    for k, v in info.items():
        if isinstance(v, (int, float, str, bool)):
            clean = clean_val(v)
            if clean is not None:
                scalars[k] = clean

    if "currentPrice" not in scalars and "regularMarketPrice" in scalars:
        scalars["currentPrice"] = scalars["regularMarketPrice"]

    return scalars, None

def main():
    if not os.path.exists(CONFIG_FILE):
        print(f"Error: {CONFIG_FILE} not found.")
        sys.exit(1)

    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        config = json.load(f)

    tickers = config.get("tickers", [])
    if not tickers:
        sys.exit(0)

    os.makedirs(DATA_DIR, exist_ok=True)
    damodaran = fetch_damodaran_benchmarks()

    all_data = {}
    errors = {}
    success_count = 0

    print(f"Fetching data for {len(tickers)} tickers...")
    for sym in tickers:
        sym_clean = sym.strip().upper()
        scalars, err = fetch_ticker_data(sym_clean)
        if scalars:
            # Map industry benchmarks
            raw_ind = scalars.get("industry", "")
            mapped_ind = DAMODARAN_MAP.get(raw_ind, raw_ind)
            scalars["_industryBenchmark"] = damodaran.get(mapped_ind, {})
            scalars["_industryName"] = mapped_ind

            all_data[sym_clean] = scalars
            success_count += 1
            print(f"-> SUCCESS: {sym_clean} ({raw_ind} -> {mapped_ind})")
        else:
            errors[sym_clean] = err
            print(f"-> FAIL: {sym_clean} - {err}")
        time.sleep(1.0)

    output = {
        "updated": datetime.now(timezone.utc).isoformat(),
        "count": success_count,
        "tickers": all_data,
        "errors": errors
    }

    with open(OUTPUT_DATA, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2)

    print(f"Complete. Wrote {success_count} tickers to {OUTPUT_DATA}")

if __name__ == "__main__":
    main()
