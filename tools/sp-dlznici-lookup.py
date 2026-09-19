#!/usr/bin/env python3
"""
sp-dlznici-lookup.py — Kontrola v zozname dlžníkov Sociálnej poisťovne SR.

Použitie:
  python3 tools/sp-dlznici-lookup.py 36038351              # jedno IČO
  python3 tools/sp-dlznici-lookup.py 36038351 17317282     # viacero IČO
  python3 tools/sp-dlznici-lookup.py --top 20              # top 20 víťazov z DB

Zdroj: https://www.socpoist.sk/nastroje-sluzby/zoznam-dlznikov
Výstup: SQLite tabuľka sp_dlznici + data/results/sp_dlznici_data.json
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

# ─── Cesty ──────────────────────────────────────────────────────────────────

SCRIPT_DIR = Path(__file__).parent
PROJECT_DIR = SCRIPT_DIR.parent
DB_PATH = PROJECT_DIR / "data" / "vestnik.db"
RESULTS_DIR = PROJECT_DIR / "data" / "results"
JSON_OUT = RESULTS_DIR / "sp_dlznici_data.json"

# ─── Konfigurácia ───────────────────────────────────────────────────────────

RATE_LIMIT = 1.0
CACHE_DAYS = 30
REQUEST_TIMEOUT = 30

SP_BASE_URL = "https://www.socpoist.sk/nastroje-sluzby/zoznam-dlznikov"

# ─── DB setup ───────────────────────────────────────────────────────────────

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS sp_dlznici (
    ico TEXT PRIMARY KEY,
    je_dlznik BOOLEAN,
    dlh_suma REAL,
    obdobie TEXT,
    checked_at TEXT
);
"""


def init_db(con: sqlite3.Connection):
    con.execute(CREATE_TABLE_SQL)
    con.commit()


# ─── Fetch logic ────────────────────────────────────────────────────────────

def _http_request(url: str, method: str = "GET", data: bytes = None,
                  headers: dict = None) -> str | None:
    """Generic HTTP request."""
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("User-Agent", "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) UVOTool/1.0")
    req.add_header("Accept", "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8")
    if headers:
        for k, v in headers.items():
            req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as e:
        print(f"  CHYBA HTTP: {e}", file=sys.stderr)
        return None


def fetch_sp_dlznik(ico: str) -> dict:
    """
    Check if IČO appears in Sociálna poisťovňa debtor list.
    Returns dict: {ico, je_dlznik, dlh_suma, obdobie, checked_at}
    """
    result = {
        "ico": ico,
        "je_dlznik": False,
        "dlh_suma": None,
        "obdobie": "",
        "checked_at": datetime.now().isoformat(),
    }

    # Approach 1: Try GET with IČO parameter
    url = f"{SP_BASE_URL}?ico={ico}"
    html = _http_request(url)

    if html:
        html_lower = html.lower()

        # Negative indicators
        if any(kw in html_lower for kw in [
            "žiadne záznamy", "žiadne výsledky", "nebol nájdený",
            "neboli nájdené", "0 výsledkov", "nenašli sa",
        ]):
            result["je_dlznik"] = False
            return result

        # Check if IČO appears in results section
        if ico in html:
            # Look for debtor indicators near the IČO
            # Find the section of HTML around the IČO
            ico_pos = html.find(ico)
            if ico_pos >= 0:
                context = html[max(0, ico_pos - 500):ico_pos + 1000].lower()

                if any(kw in context for kw in [
                    "dlžník", "nedoplatok", "dlh", "pohľadávk",
                    "predpísané", "nezaplatené",
                ]):
                    result["je_dlznik"] = True

                    # Try to extract sum
                    sum_patterns = [
                        r'(\d[\d\s]*[.,]\d{2})\s*(?:EUR|€|eur)',
                        r'(?:suma|dlh|pohľadávk)[^<]*?(\d[\d\s]*[.,]\d{2})',
                    ]
                    for pattern in sum_patterns:
                        m = re.search(pattern, html[ico_pos:ico_pos + 2000], re.IGNORECASE)
                        if m:
                            suma_str = m.group(1).replace(" ", "").replace(",", ".")
                            try:
                                result["dlh_suma"] = float(suma_str)
                            except ValueError:
                                pass
                            break

                    # Try to extract period
                    period_patterns = [
                        r'(\d{1,2}[./]\d{4})\s*[-–]\s*(\d{1,2}[./]\d{4})',
                        r'(?:obdobie|mesiac|rok)[^<]*?(\d{1,2}[./]\d{4})',
                    ]
                    for pattern in period_patterns:
                        m = re.search(pattern, html[ico_pos:ico_pos + 2000], re.IGNORECASE)
                        if m:
                            result["obdobie"] = m.group(0)[:50]
                            break

                    return result

    # Approach 2: Try POST form
    form_data = urllib.parse.urlencode({"ico": ico}).encode("utf-8")
    html = _http_request(
        SP_BASE_URL,
        method="POST",
        data=form_data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )

    if html and ico in html:
        html_lower = html.lower()
        if any(kw in html_lower for kw in ["dlžník", "nedoplatok", "dlh"]):
            result["je_dlznik"] = True
            return result

    # Could not determine
    result["je_dlznik"] = False
    result["obdobie"] = "nedostupné — SP web neodpovedalo"
    return result


# ─── Lookup logic ───────────────────────────────────────────────────────────

def lookup_ico(ico: str, con: sqlite3.Connection, force: bool = False) -> dict:
    """Lookup single IČO — check cache first."""
    if not force:
        row = con.execute(
            "SELECT * FROM sp_dlznici WHERE ico = ?", (ico,)
        ).fetchone()
        if row:
            try:
                checked = datetime.fromisoformat(row["checked_at"])
                if datetime.now() - checked < timedelta(days=CACHE_DAYS):
                    return dict(row)
            except (ValueError, TypeError):
                pass

    print(f"  Fetching SP dlžníci for IČO {ico}...")
    result = fetch_sp_dlznik(ico)

    con.execute("""
        INSERT OR REPLACE INTO sp_dlznici (ico, je_dlznik, dlh_suma, obdobie, checked_at)
        VALUES (:ico, :je_dlznik, :dlh_suma, :obdobie, :checked_at)
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

def lookup_sp_for_profile(ico: str) -> dict:
    """
    Callable from dashboard serve.py — returns SP debtor status.
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
        "obdobie": result.get("obdobie", ""),
    }


# ─── Report ─────────────────────────────────────────────────────────────────

def print_report(results: list[dict]):
    """Print summary report."""
    print(f"\nSP Dlžníci — {len(results)} firiem")
    print("=" * 60)

    dlznici = [r for r in results if r.get("je_dlznik")]
    ok = [r for r in results if not r.get("je_dlznik")]

    for r in results:
        status = "DLŽNÍK" if r.get("je_dlznik") else "OK"
        suma = f" — {r['dlh_suma']:,.2f} EUR".replace(",", " ") if r.get("dlh_suma") else ""
        obdobie = f" ({r['obdobie']})" if r.get("obdobie") and "nedostupné" not in r.get("obdobie", "") else ""
        print(f"  IČO {r['ico']}: {status}{suma}{obdobie}")

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
        description="Kontrola v zozname dlžníkov Sociálnej poisťovne SR"
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
