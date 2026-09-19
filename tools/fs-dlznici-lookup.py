#!/usr/bin/env python3
"""
fs-dlznici-lookup.py — Kontrola v zozname daňových dlžníkov Finančnej správy SR.

Použitie:
  python3 tools/fs-dlznici-lookup.py 36038351              # jedno IČO
  python3 tools/fs-dlznici-lookup.py 36038351 17317282     # viacero IČO
  python3 tools/fs-dlznici-lookup.py --top 20              # top 20 víťazov z DB

Zdroj: https://www.financnasprava.sk zoznamy dlžníkov
Výstup: SQLite tabuľka fs_dlznici + data/results/fs_dlznici_data.json
"""

import argparse
import json
import os
import re
import sqlite3
import sys
import time
import urllib.request
import urllib.error
import urllib.parse
from datetime import datetime, timedelta
from pathlib import Path
from html.parser import HTMLParser

# ─── Cesty ──────────────────────────────────────────────────────────────────

SCRIPT_DIR = Path(__file__).parent
PROJECT_DIR = SCRIPT_DIR.parent
DB_PATH = PROJECT_DIR / "data" / "vestnik.db"
RESULTS_DIR = PROJECT_DIR / "data" / "results"
JSON_OUT = RESULTS_DIR / "fs_dlznici_data.json"

# ─── Konfigurácia ───────────────────────────────────────────────────────────

RATE_LIMIT = 1.0
CACHE_DAYS = 30
REQUEST_TIMEOUT = 30

# FS search URLs to try
FS_SEARCH_URLS = [
    # Direct search endpoint
    "https://www.financnasprava.sk/sk/elektronicke-sluzby/verejne-sluzby/zoznamy/detail/_f4211cf3-eb6d-4b43-928e-a62800e27a3a",
    # Alternative search
    "https://www.financnasprava.sk/sk/elektronicke-sluzby/verejne-sluzby/zoznamy/zoznam-danovych-dlznikov",
]

# ─── DB setup ───────────────────────────────────────────────────────────────

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS fs_dlznici (
    ico TEXT PRIMARY KEY,
    je_dlznik BOOLEAN,
    dlh_suma REAL,
    typ_dlhu TEXT,
    checked_at TEXT
);
"""


def init_db(con: sqlite3.Connection):
    con.execute(CREATE_TABLE_SQL)
    con.commit()


# ─── HTML Parser ────────────────────────────────────────────────────────────

class FSResultParser(HTMLParser):
    """Parse FS debtor search results HTML."""

    def __init__(self):
        super().__init__()
        self.in_result = False
        self.in_table = False
        self.in_td = False
        self.found_results = False
        self.current_text = ""
        self.td_values = []
        self.rows = []
        self.dlh_suma = None
        self.typ_dlhu = ""

    def handle_starttag(self, tag, attrs):
        attrs_dict = dict(attrs)
        if tag == "table":
            self.in_table = True
        if tag == "td" and self.in_table:
            self.in_td = True
            self.current_text = ""

    def handle_endtag(self, tag):
        if tag == "td" and self.in_td:
            self.in_td = False
            self.td_values.append(self.current_text.strip())
        if tag == "tr" and self.td_values:
            self.rows.append(self.td_values)
            self.td_values = []
        if tag == "table":
            self.in_table = False

    def handle_data(self, data):
        if self.in_td:
            self.current_text += data
        # Check for "no results" indicators
        text = data.strip().lower()
        if any(kw in text for kw in ["žiadne záznamy", "nenašli", "nebol nájdený", "0 záznamov"]):
            self.found_results = False
        if any(kw in text for kw in ["nedoplatk", "dlžník", "dlžníci"]):
            self.found_results = True


# ─── Fetch logic ────────────────────────────────────────────────────────────

def _http_request(url: str, method: str = "GET", data: bytes = None,
                  headers: dict = None) -> str | None:
    """Generic HTTP request, returns response body as string or None."""
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("User-Agent", "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) UVOTool/1.0")
    if headers:
        for k, v in headers.items():
            req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as e:
        print(f"  CHYBA HTTP: {e}", file=sys.stderr)
        return None


def fetch_fs_dlznik(ico: str) -> dict:
    """
    Check if IČO appears in FS debtor list.
    Returns dict: {ico, je_dlznik, dlh_suma, typ_dlhu, checked_at}
    """
    result = {
        "ico": ico,
        "je_dlznik": False,
        "dlh_suma": None,
        "typ_dlhu": "",
        "checked_at": datetime.now().isoformat(),
    }

    # Approach 1: Try POST form search
    for search_url in FS_SEARCH_URLS:
        form_data = urllib.parse.urlencode({
            "Ico": ico,
            "DatovaStruktura": "2",  # Daňoví dlžníci
        }).encode("utf-8")

        html = _http_request(
            search_url,
            method="POST",
            data=form_data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )

        if html:
            # Check for debtor indicators
            html_lower = html.lower()

            # Negative indicators (not a debtor)
            if any(kw in html_lower for kw in [
                "žiadne záznamy", "žiadne výsledky", "0 záznamov",
                "nebol nájdený", "neboli nájdené",
            ]):
                result["je_dlznik"] = False
                return result

            # Positive indicators (is a debtor)
            if ico in html and any(kw in html_lower for kw in [
                "nedoplatok", "dlžník", "suma", "dlh",
            ]):
                result["je_dlznik"] = True

                # Try to extract sum
                sum_patterns = [
                    r'(?:suma|nedoplatok|dlh)[^<]*?(\d[\d\s]*[.,]\d{2})\s*(?:EUR|€)',
                    r'(\d[\d\s]*[.,]\d{2})\s*(?:EUR|€)',
                ]
                for pattern in sum_patterns:
                    m = re.search(pattern, html, re.IGNORECASE)
                    if m:
                        suma_str = m.group(1).replace(" ", "").replace(",", ".")
                        try:
                            result["dlh_suma"] = float(suma_str)
                        except ValueError:
                            pass
                        break

                # Try to extract debt type
                typ_patterns = [
                    r'(?:typ[^<]*?|druh[^<]*?)([^<]{3,50})',
                ]
                for pattern in typ_patterns:
                    m = re.search(pattern, html, re.IGNORECASE)
                    if m:
                        result["typ_dlhu"] = m.group(1).strip()[:100]
                        break

                return result

            # If page loaded but no clear indicators, try parsing table
            parser = FSResultParser()
            try:
                parser.feed(html)
            except Exception:
                pass

            if parser.rows:
                # If we have table rows with the IČO, likely a debtor
                for row in parser.rows:
                    row_text = " ".join(row).lower()
                    if ico in row_text:
                        result["je_dlznik"] = True
                        # Try to find numeric value
                        for cell in row:
                            cell_clean = cell.strip().replace(" ", "").replace(",", ".")
                            try:
                                val = float(cell_clean)
                                if val > 0:
                                    result["dlh_suma"] = val
                                    break
                            except ValueError:
                                continue
                        return result

    # Approach 2: Try GET with IČO in URL
    get_url = f"https://www.financnasprava.sk/sk/elektronicke-sluzby/verejne-sluzby/zoznamy/zoznam-danovych-dlznikov?ico={ico}"
    html = _http_request(get_url)
    if html and ico in html:
        html_lower = html.lower()
        if any(kw in html_lower for kw in ["nedoplatok", "dlžník", "suma"]):
            result["je_dlznik"] = True
            return result

    # Could not determine — mark as not found (not necessarily = not a debtor)
    result["je_dlznik"] = False
    result["typ_dlhu"] = "nedostupné — FS API neodpovedalo"
    return result


# ─── Lookup logic ───────────────────────────────────────────────────────────

def lookup_ico(ico: str, con: sqlite3.Connection, force: bool = False) -> dict:
    """Lookup single IČO — check cache first, then fetch."""
    if not force:
        row = con.execute(
            "SELECT * FROM fs_dlznici WHERE ico = ?", (ico,)
        ).fetchone()
        if row:
            try:
                checked = datetime.fromisoformat(row["checked_at"])
                if datetime.now() - checked < timedelta(days=CACHE_DAYS):
                    return dict(row)
            except (ValueError, TypeError):
                pass

    # Fetch
    print(f"  Fetching FS dlžníci for IČO {ico}...")
    result = fetch_fs_dlznik(ico)

    # Store
    con.execute("""
        INSERT OR REPLACE INTO fs_dlznici (ico, je_dlznik, dlh_suma, typ_dlhu, checked_at)
        VALUES (:ico, :je_dlznik, :dlh_suma, :typ_dlhu, :checked_at)
    """, result)
    con.commit()

    return result


def get_top_winner_icos(con: sqlite3.Connection, limit: int) -> list[str]:
    """Get top N winner IČOs."""
    cur = con.cursor()
    cur.execute("""
        SELECT ico, COUNT(*) as cnt
        FROM ucastnici
        WHERE ico IS NOT NULL AND ico != '' AND je_vitaz = 1
        GROUP BY ico
        ORDER BY cnt DESC
        LIMIT ?
    """, (limit,))
    return [row[0] for row in cur.fetchall() if row[0] and len(row[0]) >= 6]


# ─── Callable from serve.py ─────────────────────────────────────────────────

def lookup_fs_for_profile(ico: str) -> dict:
    """
    Callable from dashboard serve.py — returns FS debtor status.
    Returns dict with keys: je_dlznik, dlh_suma, typ_dlhu
    """
    if not DB_PATH.exists():
        return {"je_dlznik": None, "error": "DB neexistuje"}

    con = sqlite3.connect(str(DB_PATH))
    con.row_factory = sqlite3.Row
    init_db(con)

    result = lookup_ico(ico, con)
    con.close()

    return {
        "je_dlznik": bool(result.get("je_dlznik", False)),
        "dlh_suma": result.get("dlh_suma"),
        "typ_dlhu": result.get("typ_dlhu", ""),
    }


# ─── Report ─────────────────────────────────────────────────────────────────

def print_report(results: list[dict]):
    """Print summary report."""
    print(f"\nFS Dlžníci — {len(results)} firiem")
    print("=" * 60)

    dlznici = [r for r in results if r.get("je_dlznik")]
    ok = [r for r in results if not r.get("je_dlznik")]

    for r in results:
        status = "DLŽNÍK" if r.get("je_dlznik") else "OK"
        suma = f" — {r['dlh_suma']:,.2f} EUR".replace(",", " ") if r.get("dlh_suma") else ""
        typ = f" ({r['typ_dlhu']})" if r.get("typ_dlhu") and "nedostupné" not in r.get("typ_dlhu", "") else ""
        print(f"  IČO {r['ico']}: {status}{suma}{typ}")

    print(f"\nSúhrn: {len(dlznici)} dlžníkov, {len(ok)} bez dlhov")


def save_json(results: list[dict]):
    """Save results to JSON."""
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    with open(JSON_OUT, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"JSON uložený: {JSON_OUT}")


# ─── Main ───────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Kontrola v zozname daňových dlžníkov Finančnej správy SR"
    )
    parser.add_argument("icos", nargs="*", help="IČO firiem na kontrolu")
    parser.add_argument("--top", type=int, metavar="N", help="Skontrolovať top N víťazov z DB")
    parser.add_argument("--force", action="store_true", help="Ignorovať cache")
    args = parser.parse_args()

    if not args.icos and args.top is None:
        parser.print_help()
        sys.exit(1)

    if not DB_PATH.exists():
        print(f"CHYBA: Databáza {DB_PATH} neexistuje.", file=sys.stderr)
        sys.exit(1)

    con = sqlite3.connect(str(DB_PATH))
    con.row_factory = sqlite3.Row
    init_db(con)

    icos = args.icos or []
    if args.top:
        top_icos = get_top_winner_icos(con, args.top)
        icos.extend(top_icos)
        print(f"Top {args.top} víťazov: {len(top_icos)} IČO")

    # Deduplicate
    seen = set()
    unique_icos = []
    for ico in icos:
        if ico not in seen:
            seen.add(ico)
            unique_icos.append(ico)
    icos = unique_icos

    results = []
    for i, ico in enumerate(icos):
        print(f"[{i+1}/{len(icos)}] IČO: {ico}")
        result = lookup_ico(ico, con, force=args.force)
        results.append(result)
        if i < len(icos) - 1:
            time.sleep(RATE_LIMIT)

    print_report(results)
    save_json(results)
    con.close()


if __name__ == "__main__":
    main()
