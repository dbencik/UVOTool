#!/usr/bin/env python3
"""
rpvs-lookup.py — Vyhľadávanie v Registri partnerov verejného sektora (RPVS).

Použitie:
  python3 tools/rpvs-lookup.py 36038351              # jedno IČO
  python3 tools/rpvs-lookup.py 36038351 17317282     # viacero IČO
  python3 tools/rpvs-lookup.py --top 20              # top 20 víťazov z DB

Zdroj: OData v4 API https://rpvs.gov.sk/opendatav2/partneri
Výstup: SQLite tabuľka rpvs + data/results/rpvs_data.json
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
from collections import defaultdict

# ─── Cesty ──────────────────────────────────────────────────────────────────

SCRIPT_DIR = Path(__file__).parent
PROJECT_DIR = SCRIPT_DIR.parent
DB_PATH = PROJECT_DIR / "data" / "vestnik.db"
RESULTS_DIR = PROJECT_DIR / "data" / "results"
JSON_OUT = RESULTS_DIR / "rpvs_data.json"

# ─── Konfigurácia ───────────────────────────────────────────────────────────

API_BASE = "https://rpvs.gov.sk/opendatav2/partneri"
ODATA_EXPAND = "$expand=PartneriVerejnehoSektora($expand=Adresa),KonecniUzivateliaVyhod($expand=Adresa,StatnaPrislusnost),OpravneneOsoby($expand=Adresa)"
RATE_LIMIT = 1.0  # seconds between requests
CACHE_DAYS = 30
REQUEST_TIMEOUT = 30

# ─── DB setup ───────────────────────────────────────────────────────────────

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS rpvs (
    ico TEXT,
    cislo_vlozky INTEGER,
    obchodne_meno TEXT,
    ubo_meno TEXT,
    ubo_priezvisko TEXT,
    ubo_datum_narodenia TEXT,
    ubo_je_verejny_cinitel BOOLEAN,
    ubo_adresa TEXT,
    ubo_statna_prislusnost TEXT,
    opravnena_osoba TEXT,
    platnost_od TEXT,
    platnost_do TEXT,
    fetched_at TEXT
);
"""

CREATE_INDEX_SQL = "CREATE INDEX IF NOT EXISTS idx_rpvs_ico ON rpvs(ico);"


def init_db(con: sqlite3.Connection):
    con.execute(CREATE_TABLE_SQL)
    con.execute(CREATE_INDEX_SQL)
    con.commit()


# ─── API ────────────────────────────────────────────────────────────────────

def _http_get(url: str) -> dict | None:
    """Fetch JSON from URL, return parsed dict or None on error."""
    req = urllib.request.Request(url)
    req.add_header("Accept", "application/json")
    req.add_header("User-Agent", "UVOTool/1.0 (rpvs-lookup)")
    try:
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
            data = resp.read().decode("utf-8")
            return json.loads(data)
    except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError, TimeoutError) as e:
        print(f"  CHYBA HTTP: {e}", file=sys.stderr)
        return None


def fetch_rpvs_odata(ico: str) -> list[dict]:
    """Fetch RPVS data for given IČO via OData API with any() filter."""
    filter_str = f"PartneriVerejnehoSektora/any(p: p/Ico eq '{ico}')"
    url = f"{API_BASE}?$filter={urllib.parse.quote(filter_str)}&{ODATA_EXPAND}"

    result = _http_get(url)
    if result is None:
        return []

    # OData returns {"value": [...]}
    entries = result.get("value", [])
    if not entries:
        return []

    records = []
    for entry in entries:
        cislo_vlozky = entry.get("Id")
        platnost_od = entry.get("DatumZapisu", "")
        platnost_do = entry.get("DatumVymazu", "")

        # Find the matching partner company
        partners = entry.get("PartneriVerejnehoSektora", [])
        obchodne_meno = ""
        for p in partners:
            if p.get("Ico") == ico:
                obchodne_meno = p.get("ObchodneMeno", "")
                break
        if not obchodne_meno and partners:
            obchodne_meno = partners[0].get("ObchodneMeno", "")

        # Oprávnené osoby
        opravnene = entry.get("OpravneneOsoby", [])
        op_list = []
        for op in opravnene:
            parts = []
            if op.get("Meno"):
                parts.append(op["Meno"])
            if op.get("Priezvisko"):
                parts.append(op["Priezvisko"])
            if op.get("ObchodneMeno"):
                parts.append(f"({op['ObchodneMeno']})")
            if op.get("Ico"):
                parts.append(f"IČO: {op['Ico']}")
            op_list.append(" ".join(parts))
        opravnena_osoba_str = "; ".join(op_list) if op_list else ""

        # UBO — Koneční užívatelia výhod
        ubos = entry.get("KonecniUzivateliaVyhod", [])
        if ubos:
            for ubo in ubos:
                adresa_parts = []
                adr = ubo.get("Adresa", {})
                if adr:
                    if adr.get("Ulica"):
                        adresa_parts.append(adr["Ulica"])
                        if adr.get("CisloDomu"):
                            adresa_parts[-1] += f" {adr['CisloDomu']}"
                    if adr.get("Psc"):
                        adresa_parts.append(adr["Psc"])
                    if adr.get("Mesto"):
                        adresa_parts.append(adr["Mesto"])
                    if adr.get("Stat"):
                        adresa_parts.append(adr["Stat"])

                statna = ubo.get("StatnaPrislusnost", {})
                statna_str = ""
                if isinstance(statna, dict):
                    statna_str = statna.get("Nazov", "")
                elif isinstance(statna, str):
                    statna_str = statna

                records.append({
                    "ico": ico,
                    "cislo_vlozky": cislo_vlozky,
                    "obchodne_meno": obchodne_meno,
                    "ubo_meno": ubo.get("Meno", ""),
                    "ubo_priezvisko": ubo.get("Priezvisko", ""),
                    "ubo_datum_narodenia": ubo.get("DatumNarodenia", ""),
                    "ubo_je_verejny_cinitel": bool(ubo.get("JeVerejnyCinitel", False)),
                    "ubo_adresa": ", ".join(adresa_parts),
                    "ubo_statna_prislusnost": statna_str,
                    "opravnena_osoba": opravnena_osoba_str,
                    "platnost_od": platnost_od,
                    "platnost_do": platnost_do,
                    "fetched_at": datetime.now().isoformat(),
                })
        else:
            # No UBOs — still record the entry
            records.append({
                "ico": ico,
                "cislo_vlozky": cislo_vlozky,
                "obchodne_meno": obchodne_meno,
                "ubo_meno": "",
                "ubo_priezvisko": "",
                "ubo_datum_narodenia": "",
                "ubo_je_verejny_cinitel": False,
                "ubo_adresa": "",
                "ubo_statna_prislusnost": "",
                "opravnena_osoba": opravnena_osoba_str,
                "platnost_od": platnost_od,
                "platnost_do": platnost_do,
                "fetched_at": datetime.now().isoformat(),
            })

    return records


def fetch_rpvs_html(ico: str) -> list[dict]:
    """Fallback: scrape RPVS web search for given IČO."""
    url = f"https://rpvs.gov.sk/rpvs/Partner/Partner/VyhladajPartnera?Ico={ico}"
    req = urllib.request.Request(url)
    req.add_header("User-Agent", "UVOTool/1.0 (rpvs-lookup)")
    try:
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
            html = resp.read().decode("utf-8")
    except Exception as e:
        print(f"  CHYBA HTML scrape: {e}", file=sys.stderr)
        return []

    # Very basic parsing — look for partner name and registration number
    # This is a fallback; the OData API is preferred
    records = []
    if "Žiadne výsledky" in html or "nenašli" in html.lower():
        return []

    # If we got results, create a minimal record indicating registration exists
    records.append({
        "ico": ico,
        "cislo_vlozky": None,
        "obchodne_meno": f"Registrovaný partner (IČO: {ico})",
        "ubo_meno": "",
        "ubo_priezvisko": "",
        "ubo_datum_narodenia": "",
        "ubo_je_verejny_cinitel": False,
        "ubo_adresa": "",
        "ubo_statna_prislusnost": "",
        "opravnena_osoba": "",
        "platnost_od": "",
        "platnost_do": "",
        "fetched_at": datetime.now().isoformat(),
    })
    return records


# ─── Lookup logic ───────────────────────────────────────────────────────────

def lookup_ico(ico: str, con: sqlite3.Connection, force: bool = False) -> list[dict]:
    """Lookup single IČO — check cache first, then fetch."""
    # Check cache
    if not force:
        cached = con.execute(
            "SELECT * FROM rpvs WHERE ico = ? ORDER BY fetched_at DESC LIMIT 1",
            (ico,)
        ).fetchone()
        if cached:
            try:
                fetched = datetime.fromisoformat(cached["fetched_at"])
                if datetime.now() - fetched < timedelta(days=CACHE_DAYS):
                    # Return all cached records for this IČO
                    rows = con.execute("SELECT * FROM rpvs WHERE ico = ?", (ico,)).fetchall()
                    return [dict(r) for r in rows]
            except (ValueError, TypeError):
                pass

    # Fetch from API
    print(f"  Fetching RPVS data for IČO {ico}...")
    records = fetch_rpvs_odata(ico)

    # Fallback to HTML scraping if OData returns nothing
    if not records:
        print(f"  OData returned nothing, trying HTML scrape...")
        records = fetch_rpvs_html(ico)

    # Store results
    if records:
        # Clear old records for this IČO
        con.execute("DELETE FROM rpvs WHERE ico = ?", (ico,))
        for rec in records:
            con.execute("""
                INSERT INTO rpvs (ico, cislo_vlozky, obchodne_meno, ubo_meno, ubo_priezvisko,
                    ubo_datum_narodenia, ubo_je_verejny_cinitel, ubo_adresa, ubo_statna_prislusnost,
                    opravnena_osoba, platnost_od, platnost_do, fetched_at)
                VALUES (:ico, :cislo_vlozky, :obchodne_meno, :ubo_meno, :ubo_priezvisko,
                    :ubo_datum_narodenia, :ubo_je_verejny_cinitel, :ubo_adresa, :ubo_statna_prislusnost,
                    :opravnena_osoba, :platnost_od, :platnost_do, :fetched_at)
            """, rec)
        con.commit()
    else:
        # Record that we checked and found nothing (negative cache)
        con.execute("DELETE FROM rpvs WHERE ico = ?", (ico,))
        con.execute("""
            INSERT INTO rpvs (ico, cislo_vlozky, obchodne_meno, ubo_meno, ubo_priezvisko,
                ubo_datum_narodenia, ubo_je_verejny_cinitel, ubo_adresa, ubo_statna_prislusnost,
                opravnena_osoba, platnost_od, platnost_do, fetched_at)
            VALUES (?, NULL, '', '', '', '', 0, '', '', '', '', '', ?)
        """, (ico, datetime.now().isoformat()))
        con.commit()

    return records


def get_top_winner_icos(con: sqlite3.Connection, limit: int) -> list[str]:
    """Get top N winner IČOs by number of wins."""
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


def find_shared_persons(all_records: list[dict]) -> list[dict]:
    """Find UBOs that appear in multiple companies."""
    # Group by person key (meno + priezvisko + datum_narodenia)
    person_map = defaultdict(list)
    for rec in all_records:
        if not rec.get("ubo_meno") or not rec.get("ubo_priezvisko"):
            continue
        key = (
            rec["ubo_meno"].strip().lower(),
            rec["ubo_priezvisko"].strip().lower(),
            (rec.get("ubo_datum_narodenia") or "")[:10],
        )
        company_info = {
            "ico": rec["ico"],
            "obchodne_meno": rec.get("obchodne_meno", ""),
        }
        # Avoid duplicates
        if company_info not in person_map[key]:
            person_map[key].append(company_info)

    # Filter to persons appearing in 2+ companies
    shared = []
    for (meno, priezvisko, dn), companies in person_map.items():
        unique_icos = set(c["ico"] for c in companies)
        if len(unique_icos) >= 2:
            shared.append({
                "meno": meno.title(),
                "priezvisko": priezvisko.title(),
                "datum_narodenia": dn,
                "firmy": companies,
            })

    return sorted(shared, key=lambda x: len(x["firmy"]), reverse=True)


def mask_date(date_str: str) -> str:
    """Partially mask date of birth: **.**.1975"""
    if not date_str:
        return ""
    # Try to extract year
    m = re.search(r"(\d{4})", date_str)
    if m:
        return f"**.**.{m.group(1)}"
    return date_str


def print_report(all_records: list[dict], icos: list[str]):
    """Print summary report."""
    # Group by IČO
    by_ico = defaultdict(list)
    for rec in all_records:
        by_ico[rec["ico"]].append(rec)

    print(f"\nRPVS Lookup — {len(icos)} firiem")
    print("=" * 60)

    for ico in icos:
        records = by_ico.get(ico, [])
        if not records or (len(records) == 1 and not records[0].get("ubo_meno")):
            obm = records[0].get("obchodne_meno", "") if records else ""
            if obm:
                print(f"  {obm} ({ico}): NIE JE registrovaný partner")
            else:
                print(f"  IČO {ico}: NIE JE registrovaný partner")
            continue

        obm = records[0].get("obchodne_meno", ico)
        ubos = [r for r in records if r.get("ubo_meno")]
        # Deduplicate UBOs by name
        seen_ubos = set()
        unique_ubo_names = []
        for u in ubos:
            key = (u["ubo_meno"], u["ubo_priezvisko"])
            if key not in seen_ubos:
                seen_ubos.add(key)
                unique_ubo_names.append(f"{u['ubo_meno']} {u['ubo_priezvisko']}")
        print(f"  {obm} ({ico}): {len(unique_ubo_names)} UBO ({', '.join(unique_ubo_names)})")

    # Cross-ownership detection
    shared = find_shared_persons(all_records)
    if shared:
        print(f"\nPREPOJENIA OSÔB:")
        print("-" * 60)
        for person in shared:
            dn_display = mask_date(person["datum_narodenia"])
            dn_str = f" (*{dn_display})" if dn_display else ""
            print(f"  {person['meno']} {person['priezvisko']}{dn_str} je UBO v:")
            for firma in person["firmy"]:
                print(f"    - {firma['obchodne_meno']} (IČO: {firma['ico']})")
    elif len(icos) > 1:
        print(f"\nPREPOJENIA OSÔB: žiadne prepojenia neboli nájdené")


def save_json(all_records: list[dict]):
    """Save results to JSON file."""
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    with open(JSON_OUT, "w", encoding="utf-8") as f:
        json.dump(all_records, f, ensure_ascii=False, indent=2)
    print(f"\nJSON uložený: {JSON_OUT}")


# ─── Callable from serve.py ─────────────────────────────────────────────────

def lookup_rpvs_for_profile(ico: str) -> dict:
    """
    Callable from dashboard serve.py — returns structured RPVS data for a profile.
    Returns dict with keys: is_registered, ubos, opravnene_osoby, platnost_od/do, shared_persons
    """
    if not DB_PATH.exists():
        return {"is_registered": False, "error": "DB neexistuje"}

    con = sqlite3.connect(str(DB_PATH))
    con.row_factory = sqlite3.Row
    init_db(con)

    records = lookup_ico(ico, con)
    con.close()

    if not records or (len(records) == 1 and not records[0].get("ubo_meno")):
        return {"is_registered": False, "ubos": [], "opravnene_osoby": []}

    ubos = []
    seen_ubos = set()
    opravnene_osoby = set()
    platnost_od = records[0].get("platnost_od", "")
    platnost_do = records[0].get("platnost_do", "")
    obchodne_meno = records[0].get("obchodne_meno", "")

    for rec in records:
        if rec.get("ubo_meno"):
            key = (rec["ubo_meno"], rec["ubo_priezvisko"], rec.get("ubo_datum_narodenia", ""))
            if key not in seen_ubos:
                seen_ubos.add(key)
                ubos.append({
                    "meno": rec["ubo_meno"],
                    "priezvisko": rec["ubo_priezvisko"],
                    "datum_narodenia": rec.get("ubo_datum_narodenia", ""),
                    "je_verejny_cinitel": bool(rec.get("ubo_je_verejny_cinitel")),
                    "adresa": rec.get("ubo_adresa", ""),
                    "statna_prislusnost": rec.get("ubo_statna_prislusnost", ""),
                })
        if rec.get("opravnena_osoba"):
            opravnene_osoby.add(rec["opravnena_osoba"])

    return {
        "is_registered": True,
        "obchodne_meno": obchodne_meno,
        "ubos": ubos,
        "opravnene_osoby": list(opravnene_osoby),
        "platnost_od": platnost_od,
        "platnost_do": platnost_do,
    }


# ─── Main ───────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Vyhľadávanie v Registri partnerov verejného sektora (RPVS)"
    )
    parser.add_argument("icos", nargs="*", help="IČO firiem na vyhľadanie")
    parser.add_argument("--top", type=int, metavar="N", help="Vyhľadať top N víťazov z DB")
    parser.add_argument("--force", action="store_true", help="Ignorovať cache, stiahnuť znova")
    args = parser.parse_args()

    if not args.icos and args.top is None:
        parser.print_help()
        sys.exit(1)

    if not DB_PATH.exists():
        print(f"CHYBA: Databáza {DB_PATH} neexistuje. Spustite najprv vestnik-db.py.", file=sys.stderr)
        sys.exit(1)

    con = sqlite3.connect(str(DB_PATH))
    con.row_factory = sqlite3.Row
    init_db(con)

    # Determine IČOs to process
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

    # Process
    all_records = []
    for i, ico in enumerate(icos):
        print(f"[{i+1}/{len(icos)}] IČO: {ico}")
        records = lookup_ico(ico, con, force=args.force)
        all_records.extend(records)
        if i < len(icos) - 1:
            time.sleep(RATE_LIMIT)

    # Report
    print_report(all_records, icos)

    # Save JSON
    save_json(all_records)

    con.close()


if __name__ == "__main__":
    main()
