#!/usr/bin/env python3
"""
company-enrichment.py — Obohacuje firemné dáta z verejných slovenských registrov.

Použitie:
  python3 tools/company-enrichment.py 36038351              # jedno IČO
  python3 tools/company-enrichment.py 36038351 17317282     # viacero IČO
  python3 tools/company-enrichment.py --all                  # všetky firmy z DB
  python3 tools/company-enrichment.py --top 50               # top 50 podľa počtu zákaziek

Zdroje:
  - ORSF API (api.orsf.sk) — základné údaje o firme
  - RUZ API (registeruz.sk) — účtovné závierky a finančné údaje
"""

import argparse
import json
import os
import sqlite3
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime, timedelta
from pathlib import Path

# ─── Cesty ──────────────────────────────────────────────────────────────────

SCRIPT_DIR = Path(__file__).parent
PROJECT_DIR = SCRIPT_DIR.parent
DB_PATH = PROJECT_DIR / "data" / "vestnik.db"
RESULTS_DIR = PROJECT_DIR / "data" / "results"
JSON_OUT = RESULTS_DIR / "company_enrichment.json"

# ─── Rate limiting ──────────────────────────────────────────────────────────

ORSF_DELAY = 1.0   # max 60/min
RUZ_DELAY = 0.5
CACHE_DAYS = 30

# ─── Mapovanie kódov ────────────────────────────────────────────────────────

VELKOST_MAP = {
    "00": "bez zamestnancov",
    "11": "0 zamestnancov",
    "12": "1-9 zamestnancov",
    "13": "1-5 zamestnancov",
    "14": "6-9 zamestnancov",
    "21": "10-19 zamestnancov",
    "22": "20-49 zamestnancov",
    "23": "20-24 zamestnancov",
    "24": "25-49 zamestnancov",
    "25": "50-249 zamestnancov",
    "31": "50-99 zamestnancov",
    "32": "100-249 zamestnancov",
    "33": "250-999 zamestnancov",
    "34": "1000+ zamestnancov",
    "35": "500-999 zamestnancov",
    "36": "1000-4999 zamestnancov",
    "37": "5000+ zamestnancov",
    "38": "10000+ zamestnancov",
    "39": "25000+ zamestnancov",
}

LEGAL_FORM_MAP = {
    "101": "podnikateľ (FO)",
    "109": "podnikateľ (FO) - nezapísaný v OR",
    "111": "verejná obchodná spoločnosť",
    "112": "s.r.o.",
    "113": "komanditná spoločnosť",
    "117": "nadácia",
    "118": "neinvestičný fond",
    "119": "nezisková organizácia",
    "121": "a.s.",
    "205": "družstvo",
    "271": "spoločenstvo vlastníkov bytov",
    "301": "štátny podnik",
    "311": "príspevková organizácia (ústredná)",
    "321": "rozpočtová organizácia (ústredná)",
    "331": "príspevková organizácia",
    "332": "príspevková organizácia (obec)",
    "333": "príspevková organizácia (VÚC)",
    "381": "fondy",
    "382": "zdravotná poisťovňa",
    "421": "zahraničná osoba",
    "701": "združenie (zväz, spolok)",
    "711": "politická strana",
    "721": "cirkevná organizácia",
    "741": "stavovská organizácia (komora)",
    "745": "komora (s výnimkou profesných)",
    "751": "záujmové združenie PO",
    "801": "obec",
    "802": "krajský úrad",
    "804": "VÚC",
    "921": "medzinárodná organizácia",
}


# ─── HTTP helper ─────────────────────────────────────────────────────────────

def fetch_json(url: str, timeout: int = 15) -> dict | None:
    """Stiahne JSON z URL, vráti None ak zlyhá."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "UVOTool/1.0"})
        resp = urllib.request.urlopen(req, timeout=timeout)
        return json.loads(resp.read())
    except (urllib.error.URLError, urllib.error.HTTPError, OSError, json.JSONDecodeError) as e:
        print(f"  CHYBA: {url}: {e}", file=sys.stderr)
        return None


# ─── ORSF API ────────────────────────────────────────────────────────────────

def fetch_orsf(ico: str) -> dict | None:
    """Získa základné údaje o firme z ORSF API."""
    url = f"https://api.orsf.sk/v1/companies/{ico}"
    data = fetch_json(url)
    if not data:
        return None
    if "error" in data or "message" in data:
        print(f"  ORSF chyba pre {ico}: {data.get('message', data)}", file=sys.stderr)
        return None
    return data


# ─── RUZ API ─────────────────────────────────────────────────────────────────

def fetch_ruz_financials(ruz_id: int) -> dict:
    """Získa najnovšie finančné údaje z RUZ (Register účtovných závierok).

    Returns dict with keys: trzby_posledne, trzby_predosle, zisk_posledne,
    zisk_predosle, rok_zavierky (or empty dict on failure).
    """
    if not ruz_id:
        return {}

    # Step 1: Get list of filings
    url1 = f"https://www.registeruz.sk/cruz-public/api/uctovna-jednotka?id={ruz_id}"
    uj = fetch_json(url1)
    if not uj:
        return {}

    filing_ids = uj.get("idUctovnychZavierok", [])
    if not filing_ids:
        return {}

    time.sleep(RUZ_DELAY)

    # Step 2+3: Try filings from newest to oldest, looking for structured data
    filing_ids_sorted = sorted(filing_ids, reverse=True)

    for fid in filing_ids_sorted[:8]:  # Try up to 8 filings
        url2 = f"https://www.registeruz.sk/cruz-public/api/uctovna-zavierka?id={fid}"
        filing = fetch_json(url2)
        if not filing:
            time.sleep(RUZ_DELAY)
            continue

        obdobie_do = filing.get("obdobieDo", "")
        try:
            year = int(obdobie_do.split("-")[0]) if obdobie_do else 0
        except (ValueError, IndexError):
            year = 0

        vykaz_ids = filing.get("idUctovnychVykazov", [])
        time.sleep(RUZ_DELAY)

        if not vykaz_ids:
            continue

        # Try each vykaz in this filing for structured income data
        for vid in vykaz_ids:
            url3 = f"https://www.registeruz.sk/cruz-public/api/uctovny-vykaz?id={vid}"
            vykaz = fetch_json(url3)
            if not vykaz:
                time.sleep(RUZ_DELAY)
                continue

            tabulky = vykaz.get("obsah", {}).get("tabulky", [])
            if not tabulky:
                time.sleep(RUZ_DELAY)
                continue

            # Find income statement (table index 2, or by name)
            income_table = None
            for t in tabulky:
                nazov = t.get("nazov", {})
                if isinstance(nazov, dict) and "Income" in nazov.get("en", ""):
                    income_table = t
                    break
                elif isinstance(nazov, str) and "zisk" in nazov.lower():
                    income_table = t
                    break
            # Fallback: if 3 tables, the 3rd is income statement
            if not income_table and len(tabulky) >= 3:
                income_table = tabulky[2]

            if not income_table:
                time.sleep(RUZ_DELAY)
                continue

            rows = income_table.get("data", [])
            if len(rows) < 2:
                time.sleep(RUZ_DELAY)
                continue

            # Row 0,1 = Revenue (current, previous)
            # Last 2 rows = Net profit/loss (current, previous)
            return {
                "rok_zavierky": year,
                "trzby_posledne": _parse_financial(rows[0]),
                "trzby_predosle": _parse_financial(rows[1]),
                "zisk_posledne": _parse_financial(rows[-2]),
                "zisk_predosle": _parse_financial(rows[-1]),
            }

    return {}


def _parse_financial(val) -> float | None:
    """Parse a financial value from RUZ (string or int)."""
    if val is None or val == "":
        return None
    try:
        return float(str(val).replace(" ", "").replace(",", "."))
    except (ValueError, TypeError):
        return None


# ─── Formatting ──────────────────────────────────────────────────────────────

def format_eur(val: float | None) -> str:
    """Formátuje hodnotu v EUR na čitateľný tvar."""
    if val is None:
        return "N/A"
    abs_val = abs(val)
    sign = "-" if val < 0 else ""
    if abs_val >= 1_000_000_000:
        return f"{sign}{abs_val / 1_000_000_000:.1f}B EUR"
    elif abs_val >= 1_000_000:
        return f"{sign}{abs_val / 1_000_000:.1f}M EUR"
    elif abs_val >= 1_000:
        return f"{sign}{abs_val / 1_000:.0f}K EUR"
    else:
        return f"{sign}{abs_val:.0f} EUR"


def format_date(iso_date: str | None) -> str:
    """Konvertuje ISO dátum na DD.MM.YYYY."""
    if not iso_date:
        return ""
    try:
        dt = datetime.fromisoformat(iso_date.replace("Z", "+00:00"))
        return dt.strftime("%d.%m.%Y")
    except (ValueError, AttributeError):
        # Try plain date format
        try:
            dt = datetime.strptime(iso_date[:10], "%Y-%m-%d")
            return dt.strftime("%d.%m.%Y")
        except (ValueError, AttributeError):
            return iso_date


def print_company(row: dict):
    """Vypíše prehľad firmy na konzolu."""
    nazov = row.get("nazov", "?")
    ico = row.get("ico", "?")
    forma = row.get("pravna_forma", "")
    status = row.get("status", "")
    datum = row.get("datum_vzniku", "")

    year_str = ""
    if datum:
        parts = datum.split(".")
        if len(parts) == 3:
            year_str = f" od {parts[2]}"

    print(f"\n{nazov} ({ico}) | {forma} | {status}{year_str}")

    adresa = row.get("adresa", "")
    psc = row.get("psc", "")
    mesto = row.get("mesto", "")
    if adresa or mesto:
        print(f"  Sidlo: {adresa}, {psc} {mesto}")

    nace = row.get("nace", "")
    velkost = row.get("velkost", "")
    if nace or velkost:
        print(f"  NACE: {nace} | Velkost: {velkost}")

    rok = row.get("rok_zavierky")
    rok_prev = rok - 1 if rok else None

    trzby = row.get("trzby_posledne")
    trzby_prev = row.get("trzby_predosle")
    if trzby is not None:
        line = f"  Trzby {rok or '?'}: {format_eur(trzby)}"
        if trzby_prev is not None:
            line += f" ({rok_prev or '?'}: {format_eur(trzby_prev)})"
        print(line)

    zisk = row.get("zisk_posledne")
    zisk_prev = row.get("zisk_predosle")
    if zisk is not None:
        line = f"  Zisk {rok or '?'}: {format_eur(zisk)}"
        if zisk_prev is not None:
            line += f" ({rok_prev or '?'}: {format_eur(zisk_prev)})"
        print(line)


# ─── SQLite DDL ──────────────────────────────────────────────────────────────

FIRMY_DDL = """
CREATE TABLE IF NOT EXISTS firmy (
    ico TEXT PRIMARY KEY,
    nazov TEXT,
    status TEXT,
    pravna_forma TEXT,
    nace TEXT,
    datum_vzniku TEXT,
    datum_zaniku TEXT,
    adresa TEXT,
    mesto TEXT,
    psc TEXT,
    velkost TEXT,
    dic TEXT,
    icdph TEXT,
    trzby_posledne REAL,
    trzby_predosle REAL,
    zisk_posledne REAL,
    zisk_predosle REAL,
    rok_zavierky INTEGER,
    pocet_aktivit INTEGER,
    enriched_at TEXT
);
"""


# ─── Enrichment logic ───────────────────────────────────────────────────────

def enrich_company(ico: str, con: sqlite3.Connection) -> dict | None:
    """Obohatí jednu firmu podľa IČO. Vráti dict s údajmi alebo None."""
    cur = con.cursor()

    # Check cache
    cur.execute(
        "SELECT enriched_at FROM firmy WHERE ico = ?", (ico,)
    )
    existing = cur.fetchone()
    if existing and existing[0]:
        try:
            enriched_dt = datetime.fromisoformat(existing[0])
            if datetime.now() - enriched_dt < timedelta(days=CACHE_DAYS):
                # Already enriched recently, load from DB
                cur.execute("SELECT * FROM firmy WHERE ico = ?", (ico,))
                row = cur.fetchone()
                cols = [d[0] for d in cur.description]
                result = dict(zip(cols, row))
                result["_cached"] = True
                print(f"  {result.get('nazov', ico)}: cache ({existing[0][:10]})")
                return result
        except (ValueError, TypeError):
            pass

    # Fetch from ORSF
    print(f"  Stahujem ORSF: {ico}...", end=" ", flush=True)
    orsf = fetch_orsf(ico)
    if not orsf:
        print("ZLYHALO")
        return None
    print("OK")
    time.sleep(ORSF_DELAY)

    # Map fields
    velkost_code = orsf.get("velkost", "")
    velkost_label = VELKOST_MAP.get(velkost_code, velkost_code)

    legal_form_code = orsf.get("legalForm", "")
    legal_form_label = LEGAL_FORM_MAP.get(legal_form_code, legal_form_code)

    activities = orsf.get("activities", [])
    pocet_aktivit = len(activities) if activities else 0

    row = {
        "ico": ico,
        "nazov": orsf.get("name", ""),
        "status": orsf.get("status", ""),
        "pravna_forma": legal_form_label,
        "nace": orsf.get("nace", ""),
        "datum_vzniku": format_date(orsf.get("establishedOn")),
        "datum_zaniku": format_date(orsf.get("dissolvedOn")),
        "adresa": orsf.get("street", ""),
        "mesto": orsf.get("city", ""),
        "psc": orsf.get("psc", ""),
        "velkost": velkost_label,
        "dic": orsf.get("dic", ""),
        "icdph": orsf.get("icdph", ""),
        "pocet_aktivit": pocet_aktivit,
    }

    # Fetch from RUZ
    ruz_id = orsf.get("ruzId")
    if ruz_id:
        print(f"  Stahujem RUZ (id={ruz_id})...", end=" ", flush=True)
        financials = fetch_ruz_financials(ruz_id)
        if financials:
            row.update(financials)
            print("OK")
        else:
            print("ziadne data")
    else:
        print(f"  RUZ: nie je dostupne (ziadne ruzId)")

    row["enriched_at"] = datetime.now().isoformat()

    # Save to DB
    cur.execute(
        """INSERT OR REPLACE INTO firmy
        (ico, nazov, status, pravna_forma, nace, datum_vzniku, datum_zaniku,
         adresa, mesto, psc, velkost, dic, icdph,
         trzby_posledne, trzby_predosle, zisk_posledne, zisk_predosle,
         rok_zavierky, pocet_aktivit, enriched_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            row.get("ico"),
            row.get("nazov"),
            row.get("status"),
            row.get("pravna_forma"),
            row.get("nace"),
            row.get("datum_vzniku"),
            row.get("datum_zaniku"),
            row.get("adresa"),
            row.get("mesto"),
            row.get("psc"),
            row.get("velkost"),
            row.get("dic"),
            row.get("icdph"),
            row.get("trzby_posledne"),
            row.get("trzby_predosle"),
            row.get("zisk_posledne"),
            row.get("zisk_predosle"),
            row.get("rok_zavierky"),
            row.get("pocet_aktivit"),
            row.get("enriched_at"),
        ),
    )
    con.commit()

    return row


def get_all_icos(con: sqlite3.Connection) -> list[str]:
    """Vráti všetky unikátne IČO z tabuliek ucastnici, obstaravatelia, zmeny_zmluv."""
    cur = con.cursor()
    icos = set()
    for table, col in [
        ("ucastnici", "ico"),
        ("obstaravatelia", "ico"),
        ("zmeny_zmluv", "dodavatel_ico"),
    ]:
        try:
            cur.execute(f"SELECT DISTINCT {col} FROM {table} WHERE {col} IS NOT NULL AND {col} != ''")
            for row in cur.fetchall():
                ico = row[0].strip()
                if ico and len(ico) >= 6:
                    icos.add(ico)
        except sqlite3.OperationalError:
            pass
    return sorted(icos)


def get_top_icos(con: sqlite3.Connection, limit: int) -> list[str]:
    """Vráti top N IČO podľa počtu výskytov v zákazkách."""
    cur = con.cursor()
    cur.execute("""
        SELECT ico, COUNT(*) as cnt FROM (
            SELECT ico FROM ucastnici WHERE ico IS NOT NULL AND ico != ''
            UNION ALL
            SELECT ico FROM obstaravatelia WHERE ico IS NOT NULL AND ico != ''
            UNION ALL
            SELECT dodavatel_ico as ico FROM zmeny_zmluv WHERE dodavatel_ico IS NOT NULL AND dodavatel_ico != ''
        )
        GROUP BY ico
        ORDER BY cnt DESC
        LIMIT ?
    """, (limit,))
    return [row[0] for row in cur.fetchall() if row[0] and len(row[0]) >= 6]


# ─── Main ────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Obohatenie firemnych dat z verejnych registrov (ORSF + RUZ)"
    )
    parser.add_argument("icos", nargs="*", help="ICO firiem na obohatenie")
    parser.add_argument("--all", action="store_true", help="Obohatit vsetky firmy z DB")
    parser.add_argument("--top", type=int, metavar="N", help="Obohatit top N firiem podla poctu zakaziek")
    parser.add_argument("--force", action="store_true", help="Ignorovat cache, stahovat znova")
    args = parser.parse_args()

    if not args.icos and not args.all and args.top is None:
        parser.print_help()
        sys.exit(1)

    if not DB_PATH.exists():
        print(f"CHYBA: Databaza {DB_PATH} neexistuje. Spustite najprv vestnik-db.py.", file=sys.stderr)
        sys.exit(1)

    con = sqlite3.connect(str(DB_PATH))
    cur = con.cursor()

    # Create firmy table
    cur.executescript(FIRMY_DDL)
    con.commit()

    # If --force, clear cache timestamps
    if args.force:
        cur.execute("UPDATE firmy SET enriched_at = NULL")
        con.commit()
        print("Cache vymazany (--force)")

    # Determine ICOs to process
    if args.icos:
        icos = [ico.strip() for ico in args.icos if ico.strip()]
    elif args.all:
        icos = get_all_icos(con)
        print(f"Najdenych {len(icos)} unikatnych ICO v databaze")
    elif args.top is not None:
        icos = get_top_icos(con, args.top)
        print(f"Top {len(icos)} firiem podla poctu zakaziek")

    if not icos:
        print("Ziadne ICO na spracovanie.")
        sys.exit(0)

    print(f"\nSpracuvam {len(icos)} firiem...\n")
    print("=" * 70)

    results = {}
    enriched = 0
    cached = 0
    failed = 0

    # Load existing JSON if available
    if JSON_OUT.exists():
        try:
            with open(JSON_OUT, encoding="utf-8") as f:
                results = json.load(f)
        except (json.JSONDecodeError, OSError):
            results = {}

    for i, ico in enumerate(icos, 1):
        print(f"\n[{i}/{len(icos)}] ICO: {ico}")
        try:
            row = enrich_company(ico, con)
            if row:
                results[ico] = {k: v for k, v in row.items() if k != "_cached"}
                print_company(row)
                if row.get("_cached"):
                    cached += 1
                else:
                    enriched += 1
            else:
                failed += 1
        except Exception as e:
            print(f"  CHYBA: {e}", file=sys.stderr)
            failed += 1

    # Save JSON
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    with open(JSON_OUT, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    con.close()

    # Summary
    print("\n")
    print("=" * 70)
    print("SUHRN")
    print("=" * 70)
    print(f"Spracovanych:  {enriched + cached} ({enriched} novych, {cached} z cache)")
    print(f"Zlyhanych:     {failed}")
    print(f"Databaza:      {DB_PATH}")
    print(f"JSON vystup:   {JSON_OUT}")
    print(f"Celkom firiem: {len(results)}")
    print()


if __name__ == "__main__":
    main()
