#!/usr/bin/env python3
"""Simple HTTP server for UVO Vestnik dashboard."""

import json
import glob
import os
import sqlite3
import subprocess
import sys
import time
from http.server import HTTPServer, SimpleHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

PORT = 8080
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data", "results")
DB_PATH = os.path.join(BASE_DIR, "data", "vestnik.db")
DASHBOARD_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_DIR = os.path.join(BASE_DIR, "config")
WATCHLIST_PATH = os.path.join(CONFIG_DIR, "watchlist.json")
WATCHDOG_MATCHES_PATH = os.path.join(DATA_DIR, "watchdog_matches.json")


def query_profile(query: str, profile_type: str) -> dict:
    """Generate profile data from SQLite DB."""
    if not os.path.exists(DB_PATH):
        return {"error": "Databáza neexistuje. Spustite: python3 tools/vestnik-db.py"}

    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    query = query.strip()

    # Determine if query is IČO or name
    is_ico = query.isdigit() and len(query) >= 6

    if profile_type == "dodavatel":
        return _profile_dodavatel(db, query, is_ico)
    else:
        return _profile_obstaravatel(db, query, is_ico)


def _find_entity(db, query, is_ico, table, name_col="nazov", ico_col="ico"):
    """Find entity by IČO or fuzzy name."""
    if is_ico:
        row = db.execute(f"SELECT {name_col}, {ico_col} FROM {table} WHERE {ico_col} = ? LIMIT 1", (query,)).fetchone()
        if row:
            return row[name_col], row[ico_col]
    # Fuzzy name search
    rows = db.execute(
        f"SELECT {name_col}, {ico_col}, COUNT(*) as cnt FROM {table} WHERE {name_col} LIKE ? AND {ico_col} != '' GROUP BY {ico_col} ORDER BY cnt DESC LIMIT 1",
        (f"%{query}%",)
    ).fetchone()
    if rows:
        return rows[name_col], rows[ico_col]
    return None, None


def _fmt_eur(val):
    if val is None:
        return "0 EUR"
    return f"{val:,.2f} EUR".replace(",", " ").replace(".", ",")


def _enrich_if_needed(db, ico):
    """Lazy enrichment — fetch from ORSF+RUZ if not cached within 30 days."""
    row = db.execute("SELECT enriched_at FROM firmy WHERE ico = ?", (ico,)).fetchone()
    if row:
        from datetime import datetime, timedelta
        try:
            enriched = datetime.fromisoformat(row[0])
            if datetime.now() - enriched < timedelta(days=30):
                return  # still fresh
        except:
            pass
    # Run enrichment
    import subprocess
    subprocess.run(
        [sys.executable, os.path.join(BASE_DIR, "tools", "company-enrichment.py"), ico],
        capture_output=True, timeout=30
    )


def _fetch_rpvs_data(ico):
    """Lazy-load RPVS data for IČO. Returns dict or None."""
    try:
        rpvs_script = os.path.join(BASE_DIR, "tools", "rpvs-lookup.py")
        if not os.path.exists(rpvs_script):
            return None
        # Try importing directly first (faster)
        sys.path.insert(0, os.path.join(BASE_DIR, "tools"))
        try:
            from importlib import import_module
            rpvs = import_module("rpvs-lookup".replace("-", "_"))
            # Module name with hyphens can't be imported directly
        except (ImportError, ModuleNotFoundError):
            pass

        # Fallback to subprocess
        result = subprocess.run(
            [sys.executable, rpvs_script, ico],
            capture_output=True, timeout=15, text=True
        )
        # Read from DB after subprocess ran
        db = sqlite3.connect(DB_PATH)
        db.row_factory = sqlite3.Row
        rows = db.execute("SELECT * FROM rpvs WHERE ico = ?", (ico,)).fetchall()
        db.close()
        if not rows:
            return None
        records = [dict(r) for r in rows]
        # Check if it's just a negative cache entry
        if len(records) == 1 and not records[0].get("ubo_meno"):
            return {"is_registered": False, "ubos": [], "opravnene_osoby": []}

        ubos = []
        ubo_map = {}  # key → best record (prefer one with DOB)
        opravnene_osoby = set()
        for rec in records:
            if rec.get("ubo_meno"):
                key = (rec["ubo_meno"], rec["ubo_priezvisko"])
                dob = rec.get("ubo_datum_narodenia") or ""
                # Convert ISO date to DD.MM.YYYY
                if dob and "T" in dob:
                    import re
                    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", dob)
                    if m:
                        dob = f"{m.group(3)}.{m.group(2)}.{m.group(1)}"
                # Keep record with DOB over one without
                if key not in ubo_map or (dob and not ubo_map[key].get("datum_narodenia")):
                    ubo_map[key] = {
                        "meno": rec["ubo_meno"],
                        "priezvisko": rec["ubo_priezvisko"],
                        "datum_narodenia": dob,
                        "je_verejny_cinitel": bool(rec.get("ubo_je_verejny_cinitel")),
                        "adresa": rec.get("ubo_adresa", ""),
                        "statna_prislusnost": rec.get("ubo_statna_prislusnost", ""),
                    }
            if rec.get("opravnena_osoba"):
                opravnene_osoby.add(rec["opravnena_osoba"])
        ubos = list(ubo_map.values())

        return {
            "is_registered": True,
            "obchodne_meno": records[0].get("obchodne_meno", ""),
            "ubos": ubos,
            "opravnene_osoby": list(opravnene_osoby),
            "platnost_od": records[0].get("platnost_od", ""),
            "platnost_do": records[0].get("platnost_do", ""),
        }
    except Exception as e:
        print(f"RPVS lookup error for {ico}: {e}")
        return None


def _fetch_dlznici_data(ico):
    """Lazy-load FS + SP debtor data for IČO. Returns dict or None."""
    result = {"fs": None, "sp": None}
    try:
        # Check if data exists in DB (cached)
        db = sqlite3.connect(DB_PATH)
        db.row_factory = sqlite3.Row

        # FS dlžníci
        fs_row = db.execute("SELECT * FROM fs_dlznici WHERE ico = ?", (ico,)).fetchone()
        if fs_row:
            from datetime import datetime, timedelta
            try:
                checked = datetime.fromisoformat(fs_row["checked_at"])
                if datetime.now() - checked < timedelta(days=30):
                    result["fs"] = {
                        "je_dlznik": bool(fs_row["je_dlznik"]),
                        "dlh_suma": fs_row["dlh_suma"],
                        "typ_dlhu": fs_row["typ_dlhu"] or "",
                    }
            except:
                pass

        # SP dlžníci
        sp_row = db.execute("SELECT * FROM sp_dlznici WHERE ico = ?", (ico,)).fetchone()
        if sp_row:
            from datetime import datetime, timedelta
            try:
                checked = datetime.fromisoformat(sp_row["checked_at"])
                if datetime.now() - checked < timedelta(days=30):
                    result["sp"] = {
                        "je_dlznik": bool(sp_row["je_dlznik"]),
                        "dlh_suma": sp_row["dlh_suma"],
                        "obdobie": sp_row["obdobie"] or "",
                    }
            except:
                pass

        db.close()

        # If not cached, run lookups via subprocess
        if result["fs"] is None:
            fs_script = os.path.join(BASE_DIR, "tools", "fs-dlznici-lookup.py")
            if os.path.exists(fs_script):
                subprocess.run(
                    [sys.executable, fs_script, ico],
                    capture_output=True, timeout=15, text=True
                )
                db = sqlite3.connect(DB_PATH)
                db.row_factory = sqlite3.Row
                fs_row = db.execute("SELECT * FROM fs_dlznici WHERE ico = ?", (ico,)).fetchone()
                if fs_row:
                    result["fs"] = {
                        "je_dlznik": bool(fs_row["je_dlznik"]),
                        "dlh_suma": fs_row["dlh_suma"],
                        "typ_dlhu": fs_row["typ_dlhu"] or "",
                    }
                db.close()

        if result["sp"] is None:
            sp_script = os.path.join(BASE_DIR, "tools", "sp-dlznici-lookup.py")
            if os.path.exists(sp_script):
                subprocess.run(
                    [sys.executable, sp_script, ico],
                    capture_output=True, timeout=15, text=True
                )
                db = sqlite3.connect(DB_PATH)
                db.row_factory = sqlite3.Row
                sp_row = db.execute("SELECT * FROM sp_dlznici WHERE ico = ?", (ico,)).fetchone()
                if sp_row:
                    result["sp"] = {
                        "je_dlznik": bool(sp_row["je_dlznik"]),
                        "dlh_suma": sp_row["dlh_suma"],
                        "obdobie": sp_row["obdobie"] or "",
                    }
                db.close()

    except Exception as e:
        print(f"Dlznici lookup error for {ico}: {e}")

    return result


def _get_company_info(db, ico):
    """Get enriched company data from firmy table."""
    try:
        row = db.execute("SELECT * FROM firmy WHERE ico = ?", (ico,)).fetchone()
        if not row:
            return None
        return dict(row)
    except:
        return None


def _profile_dodavatel(db, query, is_ico):
    name, ico = _find_entity(db, query, is_ico, "ucastnici")
    if not ico:
        return {"error": f"Dodávateľ '{query}' nebol nájdený."}

    # Lazy enrichment from ORSF + RUZ
    try:
        _enrich_if_needed(db, ico)
        db.close()
        db = sqlite3.connect(DB_PATH)
        db.row_factory = sqlite3.Row
    except:
        pass

    company = _get_company_info(db, ico)
    result = {"typ": "dodavatel", "nazov": name, "ico": ico, "sekcie": []}

    # 0. Company info from registers (if enriched)
    if company:
        size_labels = {"11": "0 zam.", "12": "1-9 zam.", "21": "10-19 zam.", "22": "20-49 zam.",
                       "31": "50-99 zam.", "32": "100-249 zam.", "33": "250-999 zam.", "34": "1000+ zam."}
        velkost = size_labels.get(company.get("velkost", ""), company.get("velkost", ""))

        result["sekcie"].append({
            "nazov": "Registre SR",
            "text": "",
            "data": {
                "status": company.get("status", ""),
                "pravna_forma": company.get("pravna_forma", ""),
                "nace": company.get("nace", ""),
                "velkost": velkost,
                "datum_vzniku": company.get("datum_vzniku", ""),
                "mesto": company.get("mesto", ""),
                "dic": company.get("dic", ""),
                "trzby": company.get("trzby_posledne"),
                "trzby_predosle": company.get("trzby_predosle"),
                "zisk": company.get("zisk_posledne"),
                "zisk_predosle": company.get("zisk_predosle"),
                "rok": company.get("rok_zavierky"),
            }
        })

    # 1. Overall stats — include ALL participations (not just wins)
    stats = db.execute("""
        SELECT COUNT(*) as wins, SUM(cena) as total, MIN(cena) as min_val, MAX(cena) as max_val, AVG(cena) as avg_val
        FROM ucastnici WHERE ico = ? AND je_vitaz = 1 AND cena IS NOT NULL AND cena > 0
    """, (ico,)).fetchone()

    wins_all = db.execute("SELECT COUNT(*) FROM ucastnici WHERE ico = ? AND je_vitaz = 1", (ico,)).fetchone()[0]
    total_bids = db.execute("SELECT COUNT(*) FROM ucastnici WHERE ico = ?", (ico,)).fetchone()[0]

    # Build short one-sentence summary
    if wins_all > 0 and stats['total']:
        celkovy_text = f"{total_bids} {'tender' if total_bids == 1 else 'tendrov'}, {wins_all} {'víťazstvo' if wins_all == 1 else 'víťazstiev'} ({_fmt_eur(stats['total'])})."
        if company and company.get("trzby_posledne") and stats["max_val"] and stats["max_val"] > company["trzby_posledne"]:
            celkovy_text += " Najväčšia zákazka presahuje ročné tržby firmy."
    elif total_bids > 0:
        celkovy_text = f"{total_bids} {'tender' if total_bids == 1 else 'tendrov'}, zatiaľ bez výhry."
    else:
        celkovy_text = "Žiadne záznamy o účasti v tendroch."

    result["sekcie"].append({
        "nazov": "Celkový profil",
        "text": celkovy_text,
        "data": {
            "zakazky_vyhrane": wins_all, "ucasti_celkom": total_bids,
            "celkova_hodnota": stats['total'] or 0,
            "priemer": stats['avg_val'] or 0, "min": stats['min_val'] or 0, "max": stats['max_val'] or 0,
            "win_rate": round(wins_all / max(total_bids, 1) * 100, 1),
        }
    })

    # 2. Customer mix — ALL participations (not just wins)
    customers = db.execute("""
        SELECT o.nazov, o.ico, COUNT(*) as cnt, SUM(u.cena) as total,
               SUM(CASE WHEN u.je_vitaz = 1 THEN 1 ELSE 0 END) as wins
        FROM ucastnici u
        JOIN obstaravatelia o ON u.doc_id = o.doc_id
        WHERE u.ico = ?
        GROUP BY o.ico ORDER BY cnt DESC LIMIT 10
    """, (ico,)).fetchall()

    cust_list = [{"nazov": c["nazov"], "ico": c["ico"], "zakazky": c["cnt"],
                  "hodnota": c["total"] or 0, "vyhry": c["wins"] or 0,
                  "podiel": round(c["cnt"] / max(total_bids, 1) * 100, 1)} for c in customers]

    top_cust = cust_list[0] if cust_list else None
    if cust_list:
        result["sekcie"].append({
            "nazov": "Zákaznícky mix",
            "text": "",
            "data": cust_list
        })

    # 3. Sector focus (CPV)
    cpv_rows = db.execute("""
        SELECT z.cpv_kod, COUNT(*) as cnt
        FROM ucastnici u JOIN zakazky z ON u.doc_id = z.doc_id
        WHERE u.ico = ? AND u.je_vitaz = 1 AND z.cpv_kod != ''
        GROUP BY z.cpv_kod ORDER BY cnt DESC LIMIT 5
    """, (ico,)).fetchall()

    cpv_list = [{"cpv": r["cpv_kod"], "pocet": r["cnt"]} for r in cpv_rows]
    if cpv_list:
        result["sekcie"].append({
            "nazov": "Sektorové zameranie",
            "text": "",
            "data": cpv_list
        })

    # 4. Competitive behavior
    single_bidder = db.execute("""
        SELECT COUNT(*) FROM ucastnici u
        JOIN vysledky v ON u.doc_id = v.doc_id
        WHERE u.ico = ? AND u.je_vitaz = 1 AND v.pocet_ponuk = 1
    """, (ico,)).fetchone()[0]

    avg_bids = db.execute("""
        SELECT AVG(v.pocet_ponuk)
        FROM ucastnici u JOIN vysledky v ON u.doc_id = v.doc_id
        WHERE u.ico = ? AND v.pocet_ponuk IS NOT NULL AND v.pocet_ponuk > 0
    """, (ico,)).fetchone()[0]

    sb_rate = round(single_bidder / max(wins_all, 1) * 100, 1)
    result["sekcie"].append({
        "nazov": "Súťažné správanie",
        "text": "",
        "data": {
            "win_rate": round(wins_all / max(total_bids, 1) * 100, 1),
            "single_bidder": single_bidder,
            "single_bidder_rate": sb_rate,
            "avg_bids": round(avg_bids, 1) if avg_bids else None,
        }
    })

    # 5. Yearly trend
    yearly = db.execute("""
        SELECT d.rok, COUNT(*) as cnt, SUM(u.cena) as total
        FROM ucastnici u JOIN dokumenty d ON u.doc_id = d.id
        WHERE u.ico = ? AND u.je_vitaz = 1 AND d.rok IS NOT NULL
        GROUP BY d.rok ORDER BY d.rok
    """, (ico,)).fetchall()

    yearly_list = [{"rok": r["rok"], "zakazky": r["cnt"], "hodnota": r["total"] or 0} for r in yearly]
    if yearly_list:
        result["sekcie"].append({
            "nazov": "Časový vývoj",
            "text": "",
            "data": yearly_list
        })

    # 6. Network ties (co-bidders)
    cobidders = db.execute("""
        SELECT u2.nazov, u2.ico, COUNT(DISTINCT u2.doc_id) as cnt
        FROM ucastnici u1
        JOIN ucastnici u2 ON u1.doc_id = u2.doc_id AND u1.ico != u2.ico
        WHERE u1.ico = ? AND u2.ico != '' AND u2.ico IS NOT NULL
        GROUP BY u2.ico ORDER BY cnt DESC LIMIT 8
    """, (ico,)).fetchall()

    cobid_list = [{"nazov": c["nazov"], "ico": c["ico"], "spolocne_tendre": c["cnt"]} for c in cobidders]
    if cobid_list:
        result["sekcie"].append({
            "nazov": "Sieťové väzby",
            "text": "",
            "data": cobid_list
        })

    # 7. Contracts — ALL participations, mark wins
    contracts = db.execute("""
        SELECT d.rok, u.cena, u.je_vitaz, u.poradie, z.predmet, d.url, o.nazov as obstaravatel
        FROM ucastnici u
        JOIN dokumenty d ON u.doc_id = d.id
        JOIN zakazky z ON u.doc_id = z.doc_id
        LEFT JOIN obstaravatelia o ON u.doc_id = o.doc_id
        WHERE u.ico = ?
        ORDER BY d.rok DESC, u.cena DESC LIMIT 30
    """, (ico,)).fetchall()

    contract_list = [{"rok": c["rok"], "hodnota": c["cena"] or 0, "predmet": c["predmet"] or "",
                      "url": c["url"] or "", "je_vitaz": bool(c["je_vitaz"]), "poradie": c["poradie"],
                      "obstaravatel": c["obstaravatel"] or ""} for c in contracts]
    wins_in_list = sum(1 for c in contract_list if c.get("je_vitaz"))
    if contract_list:
        result["sekcie"].append({
            "nazov": "Zmluvy",
            "text": f"{len(contract_list)} {'účasť' if len(contract_list) == 1 else 'účasti'}, {wins_in_list} {'víťazstvo' if wins_in_list == 1 else 'víťazstiev'}.",
            "data": contract_list
        })

    # 7b. Contract value vs revenue
    if company and company.get("trzby_posledne") and stats["total"]:
        trzby = company["trzby_posledne"]
        # Per-year contract values
        yearly_data = db.execute("""
            SELECT d.rok, SUM(u.cena) as total FROM ucastnici u
            JOIN dokumenty d ON u.doc_id = d.id
            WHERE u.ico = ? AND u.je_vitaz = 1 AND u.cena > 0 AND d.rok IS NOT NULL
            GROUP BY d.rok ORDER BY d.rok
        """, (ico,)).fetchall()

        comparisons = []
        for y in yearly_data:
            yr, total_won = y["rok"], y["total"]
            if total_won and trzby > 0:
                pomer = total_won / trzby * 100
                comparisons.append({"rok": yr, "zakazky_hodnota": total_won, "trzby": trzby, "pomer": round(pomer, 1)})

        if comparisons:
            # Use the most recent year's ratio as the headline
            latest = comparisons[-1]
            result["sekcie"].append({
                "nazov": "Zákazky vs obrat",
                "text": f"Zákazky = {latest['pomer']}% tržieb firmy.",
                "data": comparisons
            })

    # 8. RPVS — Vlastnícka štruktúra
    try:
        rpvs_data = _fetch_rpvs_data(ico)
        if rpvs_data:
            if rpvs_data.get("is_registered"):
                ubos = rpvs_data.get("ubos", [])
                ubo_list = []
                for u in ubos:
                    ubo_list.append({
                        "meno": u.get("meno", ""),
                        "priezvisko": u.get("priezvisko", ""),
                        "datum_narodenia": u.get("datum_narodenia", ""),
                        "je_verejny_cinitel": u.get("je_verejny_cinitel", False),
                        "adresa": u.get("adresa", ""),
                        "statna_prislusnost": u.get("statna_prislusnost", ""),
                    })
                result["sekcie"].append({
                    "nazov": "Vlastnícka štruktúra (RPVS)",
                    "text": "",
                    "data": {
                        "is_registered": True,
                        "ubos": ubo_list,
                        "opravnene_osoby": rpvs_data.get("opravnene_osoby", []),
                        "platnost_od": rpvs_data.get("platnost_od", ""),
                        "platnost_do": rpvs_data.get("platnost_do", ""),
                    }
                })
            else:
                result["sekcie"].append({
                    "nazov": "Vlastnícka štruktúra (RPVS)",
                    "text": "Firma NIE JE registrovaná v Registri partnerov verejného sektora.",
                    "data": {"is_registered": False}
                })
    except Exception as e:
        print(f"RPVS section error: {e}")

    # 9. Dlhy voči štátu (FS + SP)
    try:
        dlznici = _fetch_dlznici_data(ico)
        fs = dlznici.get("fs") if dlznici else None
        sp = dlznici.get("sp") if dlznici else None

        dlhy_parts = []
        dlhy_data = {"fs": fs, "sp": sp}

        if fs:
            if fs.get("je_dlznik"):
                suma = f" ({_fmt_eur(fs['dlh_suma'])})" if fs.get("dlh_suma") else ""
                dlhy_parts.append(f"Finančná správa: DLŽNÍK{suma}")
            else:
                dlhy_parts.append("Finančná správa: bez dlhov")
        if sp:
            if sp.get("je_dlznik"):
                suma = f" ({_fmt_eur(sp['dlh_suma'])})" if sp.get("dlh_suma") else ""
                dlhy_parts.append(f"Sociálna poisťovňa: DLŽNÍK{suma}")
            else:
                dlhy_parts.append("Sociálna poisťovňa: bez dlhov")

        if dlhy_parts:
            result["sekcie"].append({
                "nazov": "Dlhy voči štátu",
                "text": "",
                "data": dlhy_data
            })
    except Exception as e:
        print(f"Dlznici section error: {e}")

    # 10. Risk flags
    flags = []
    if sb_rate > 50:
        flags.append(f"Vysoký podiel zákaziek bez súťaže: {sb_rate}% single-bidder rate")
    if top_cust and top_cust["podiel"] > 70:
        flags.append(f"Vysoká závislosť na jednom zákazníkovi: {top_cust['nazov']} = {top_cust['podiel']}%")
    if company:
        if company.get("status") and company["status"] != "aktívna":
            flags.append(f"Firma nie je aktívna: status = {company['status']}")
        if company.get("velkost") in ("11",):
            flags.append(f"Firma nemá zamestnancov (veľkosť: 0)")
        if company.get("trzby_posledne") and stats["total"]:
            biggest_val = stats["max_val"] or 0
            if biggest_val > company["trzby_posledne"] * 0.5 and company["trzby_posledne"] > 0:
                flags.append(f"Najväčšia zákazka ({_fmt_eur(biggest_val)}) presahuje 50% tržieb ({_fmt_eur(company['trzby_posledne'])})")
    # Add debtor flags
    try:
        if dlznici:
            if dlznici.get("fs") and dlznici["fs"].get("je_dlznik"):
                flags.append("Firma je daňovým dlžníkom (Finančná správa)")
            if dlznici.get("sp") and dlznici["sp"].get("je_dlznik"):
                flags.append("Firma je dlžníkom Sociálnej poisťovne")
    except:
        pass

    result["sekcie"].append({
        "nazov": "Rizikové indikátory",
        "text": "Žiadne rizikové indikátory neboli identifikované." if not flags else None,
        "data": flags
    })

    return result


def _profile_obstaravatel(db, query, is_ico):
    name, ico = _find_entity(db, query, is_ico, "obstaravatelia")
    if not ico:
        return {"error": f"Obstarávateľ '{query}' nebol nájdený."}

    # Get profil URL
    profil_row = db.execute(
        "SELECT profil_url FROM obstaravatelia WHERE ico = ? AND profil_url != '' LIMIT 1", (ico,)
    ).fetchone()
    profil_url = profil_row[0] if profil_row else ""

    result = {"typ": "obstaravatel", "nazov": name, "ico": ico, "profil_url": profil_url, "sekcie": []}

    # Overall stats
    total_docs = db.execute("SELECT COUNT(*) FROM obstaravatelia WHERE ico = ?", (ico,)).fetchone()[0]
    total_results = db.execute("""
        SELECT COUNT(*) FROM obstaravatelia o JOIN vysledky v ON o.doc_id = v.doc_id WHERE o.ico = ?
    """, (ico,)).fetchone()[0]
    total_value = db.execute("""
        SELECT SUM(v.celkova_hodnota) FROM obstaravatelia o JOIN vysledky v ON o.doc_id = v.doc_id WHERE o.ico = ?
    """, (ico,)).fetchone()[0]

    years = db.execute("""
        SELECT MIN(d.rok), MAX(d.rok) FROM obstaravatelia o JOIN dokumenty d ON o.doc_id = d.id WHERE o.ico = ?
    """, (ico,)).fetchone()

    info = db.execute("""
        SELECT typ_kupujuceho, cinnost, mesto FROM obstaravatelia WHERE ico = ? AND typ_kupujuceho != '' LIMIT 1
    """, (ico,)).fetchone()

    result["sekcie"].append({
        "nazov": "Celkový profil",
        "text": f"{name} (IČO: {ico}) eviduje v databáze {total_docs} dokumentov, z toho {total_results} výsledkov. "
                f"Aktivita od roku {years[0] or '?'} do {years[1] or '?'}. "
                f"Celková hodnota zadaných zákaziek: {_fmt_eur(total_value)}."
                + (f" Typ: {info['typ_kupujuceho']}. Činnosť: {info['cinnost']}. Sídlo: {info['mesto']}." if info else ""),
        "data": {"dokumenty": total_docs, "vysledky": total_results, "celkova_hodnota": total_value or 0}
    })

    # Top suppliers (dvorní dodávatelia)
    suppliers = db.execute("""
        SELECT u.nazov, u.ico, COUNT(*) as cnt, SUM(u.cena) as total
        FROM obstaravatelia o
        JOIN ucastnici u ON o.doc_id = u.doc_id
        WHERE o.ico = ? AND u.je_vitaz = 1 AND u.nazov IS NOT NULL AND u.nazov != ''
        GROUP BY u.ico ORDER BY cnt DESC LIMIT 10
    """, (ico,)).fetchall()

    sup_list = [{"nazov": s["nazov"], "ico": s["ico"], "zakazky": s["cnt"],
                 "hodnota": s["total"] or 0,
                 "podiel": round(s["cnt"] / max(total_results, 1) * 100, 1)} for s in suppliers]

    # HHI
    all_suppliers = db.execute("""
        SELECT u.ico, COUNT(*) as cnt FROM obstaravatelia o
        JOIN ucastnici u ON o.doc_id = u.doc_id
        WHERE o.ico = ? AND u.je_vitaz = 1 AND u.ico != '' GROUP BY u.ico
    """, (ico,)).fetchall()
    total_wins = sum(s["cnt"] for s in all_suppliers) if all_suppliers else 0
    hhi = sum((s["cnt"] / max(total_wins, 1) * 100) ** 2 for s in all_suppliers) if all_suppliers else 0
    hhi = round(hhi)

    hhi_label = "nízka" if hhi < 1500 else ("stredná" if hhi < 2500 else "vysoká")
    top_sup = sup_list[0] if sup_list else None

    result["sekcie"].append({
        "nazov": "Dvorní dodávatelia",
        "text": (f"Najčastejším dodávateľom je {top_sup['nazov']} s {top_sup['zakazky']} zákazkami "
                 f"({top_sup['podiel']}%). HHI index koncentrácie: {hhi} ({hhi_label} koncentrácia)."
                 if top_sup else "Žiadni dodávatelia."),
        "data": sup_list,
        "hhi": hhi, "hhi_label": hhi_label
    })

    # Competitiveness
    avg_bids = db.execute("""
        SELECT AVG(v.pocet_ponuk) FROM obstaravatelia o
        JOIN vysledky v ON o.doc_id = v.doc_id
        WHERE o.ico = ? AND v.pocet_ponuk IS NOT NULL AND v.pocet_ponuk > 0
    """, (ico,)).fetchone()[0]

    single = db.execute("""
        SELECT COUNT(*) FROM obstaravatelia o
        JOIN vysledky v ON o.doc_id = v.doc_id
        WHERE o.ico = ? AND v.pocet_ponuk = 1
    """, (ico,)).fetchone()[0]

    sb_rate = round(single / max(total_results, 1) * 100, 1)
    result["sekcie"].append({
        "nazov": "Konkurenčnosť",
        "text": f"Priemerný počet ponúk na tender: {f'{avg_bids:.1f}' if avg_bids else '?'}. "
                f"V {single} prípadoch ({sb_rate}%) bol podaný iba jeden ponúk.",
        "data": {"avg_bids": round(avg_bids, 1) if avg_bids else None,
                 "single_bidder": single, "single_bidder_rate": sb_rate}
    })

    # Sector
    cpv_rows = db.execute("""
        SELECT z.cpv_kod, COUNT(*) as cnt FROM obstaravatelia o
        JOIN zakazky z ON o.doc_id = z.doc_id
        WHERE o.ico = ? AND z.cpv_kod != '' GROUP BY z.cpv_kod ORDER BY cnt DESC LIMIT 5
    """, (ico,)).fetchall()
    cpv_list = [{"cpv": r["cpv_kod"], "pocet": r["cnt"]} for r in cpv_rows]

    result["sekcie"].append({
        "nazov": "Štruktúra nákupov",
        "text": (f"Primárne nakupuje v kategórii {cpv_list[0]['cpv']} ({cpv_list[0]['pocet']} zákaziek)."
                 if cpv_list else "Sektorové zameranie nie je možné určiť."),
        "data": cpv_list
    })

    # Yearly
    yearly = db.execute("""
        SELECT d.rok, COUNT(*) as cnt, SUM(v.celkova_hodnota) as total
        FROM obstaravatelia o JOIN dokumenty d ON o.doc_id = d.id
        LEFT JOIN vysledky v ON o.doc_id = v.doc_id
        WHERE o.ico = ? AND d.rok IS NOT NULL
        GROUP BY d.rok ORDER BY d.rok
    """, (ico,)).fetchall()
    yearly_list = [{"rok": r["rok"], "dokumenty": r["cnt"], "hodnota": r["total"] or 0} for r in yearly]

    result["sekcie"].append({
        "nazov": "Časový vývoj",
        "text": " ".join(f"V roku {y['rok']}: {y['dokumenty']} dokumentov, {_fmt_eur(y['hodnota'])}." for y in yearly_list),
        "data": yearly_list
    })

    # Contract modifications
    mods = db.execute("""
        SELECT COUNT(*) as cnt, SUM(hodnota_po_zmene) as total
        FROM obstaravatelia o JOIN zmeny_zmluv z ON o.doc_id = z.doc_id WHERE o.ico = ?
    """, (ico,)).fetchone()

    mod_list = db.execute("""
        SELECT z.dodavatel_nazov, z.hodnota_po_zmene, z.dovod_zmeny
        FROM obstaravatelia o JOIN zmeny_zmluv z ON o.doc_id = z.doc_id
        WHERE o.ico = ? ORDER BY z.hodnota_po_zmene DESC LIMIT 10
    """, (ico,)).fetchall()

    result["sekcie"].append({
        "nazov": "Dodatky k zmluvám",
        "text": f"Celkovo {mods['cnt']} dodatkov v hodnote {_fmt_eur(mods['total'])}." if mods["cnt"] else "Žiadne dodatky.",
        "data": [{"dodavatel": m["dodavatel_nazov"], "hodnota": m["hodnota_po_zmene"] or 0,
                  "dovod": m["dovod_zmeny"] or ""} for m in mod_list]
    })

    # Risk flags
    flags = []
    if sb_rate > 50:
        flags.append(f"Vysoký single-bidder rate: {sb_rate}%")
    if hhi > 2500:
        flags.append(f"Vysoká koncentrácia dodávateľov: HHI = {hhi}")
    if mods["cnt"] and mods["cnt"] > 5:
        flags.append(f"Veľký počet dodatkov: {mods['cnt']}")

    result["sekcie"].append({
        "nazov": "Rizikové indikátory",
        "text": "Žiadne rizikové indikátory neboli identifikované." if not flags else None,
        "data": flags
    })

    return result


class DashboardHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=DASHBOARD_DIR, **kwargs)

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/data":
            self.send_api_data()
        elif parsed.path == "/api/analysis":
            self.send_json_file("analysis_report.json")
        elif parsed.path == "/api/graph":
            self.send_json_file("graph_analysis.json")
        elif parsed.path == "/api/latest":
            self.send_latest()
        elif parsed.path == "/api/profile":
            self.send_profile(parsed)
        elif parsed.path == "/api/watchlist":
            self.send_watchlist()
        elif parsed.path == "/api/watchdog-matches":
            self.send_watchdog_matches()
        elif parsed.path == "/api/modules":
            self.send_modules()
        elif parsed.path == "/api/v1/enrich-tdd":
            self.send_enrich_tdd_docs()
        elif parsed.path.startswith("/api/v1/company/"):
            self.send_company_api(parsed)
        elif parsed.path.startswith("/api/pipelines/") and "/run" in parsed.path:
            pipeline_id = parsed.path.split("/")[3]
            self.run_pipeline_api_post(pipeline_id)
        elif parsed.path.startswith("/api/pipelines/"):
            pipeline_id = parsed.path.split("/")[3] if len(parsed.path.split("/")) > 3 else None
            self.send_pipeline(pipeline_id)
        elif parsed.path == "/api/pipelines":
            self.send_pipelines_list()
        else:
            super().do_GET()

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/watchlist":
            self.save_watchlist()
        elif parsed.path == "/api/pipelines" or parsed.path.startswith("/api/pipelines/"):
            if "/run" in parsed.path:
                pipeline_id = parsed.path.split("/")[3]
                self.run_pipeline_api_post(pipeline_id)
            else:
                self.save_pipeline_api()
        elif parsed.path == "/api/v1/batch":
            self.batch_company_api()
        elif parsed.path == "/api/v1/enrich-tdd":
            self.enrich_tdd_api()
        elif parsed.path == "/api/v1/enrich-tdd/batch":
            self.enrich_tdd_batch_api()
        else:
            self.send_response(404)
            self.end_headers()

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def send_latest(self):
        """Return latest UVO vestník + latest TED data, grouped by source."""
        import os as _os
        result = {"uvo": [], "ted": [], "uvo_vestnik": "", "ted_label": ""}

        # Latest UVO file by vestník number (highest number = newest)
        def _vestnik_sort_key(filepath):
            import re as _re
            fname = os.path.basename(filepath)
            m = _re.search(r'vestnik_(\d+)_(\d{4})', fname)
            if m:
                return int(m.group(2)) * 1000 + int(m.group(1))
            return 0
        uvo_files = sorted(
            glob.glob(os.path.join(DATA_DIR, "vestnik_*_2026_parser_results.json")) +
            glob.glob(os.path.join(DATA_DIR, "vestnik_*_2025_parser_results.json")),
            key=_vestnik_sort_key, reverse=True
        )
        if uvo_files:
            with open(uvo_files[0], "r", encoding="utf-8") as f:
                docs = json.load(f)
            result["uvo_vestnik"] = docs[0].get("vestnik", "") if docs else ""
            for d in docs:
                ext = d.get("extraction", {})
                if "error" in ext:
                    continue
                obst = ext.get("obstaravatel", {})
                zak = ext.get("zakazka", {})
                vys = ext.get("vysledok", {})
                pril = ext.get("prilezitost", {})
                zm = ext.get("zmena_zmluvy", {})
                hodnota = vys.get("celkova_hodnota") or pril.get("hodnota") or zm.get("hodnota_po_zmene")
                vitaz = ""
                lehota = pril.get("lehota_datum", "")
                if d.get("action") == "vysledok":
                    winners = [u for u in vys.get("ucastnici", []) if u.get("je_vitaz")]
                    vitaz = ", ".join(w.get("nazov", "") for w in winners)
                result["uvo"].append({
                    "id": d.get("id", ""), "action": d.get("action", ""),
                    "url": d.get("url", ""), "obstaravatel": obst.get("nazov", ""),
                    "ico": obst.get("ico", ""), "predmet": zak.get("predmet", ""),
                    "hodnota": hodnota, "vitaz": vitaz, "lehota": lehota,
                    "cpv": zak.get("cpv_kod", ""),
                })

        # Latest TED
        ted_files = sorted(glob.glob(os.path.join(DATA_DIR, "ted_*_results.json")),
                           key=_os.path.getmtime, reverse=True)
        if ted_files:
            with open(ted_files[0], "r", encoding="utf-8") as f:
                docs = json.load(f)
            result["ted_label"] = f"TED ({len(docs)} dokumentov)"
            # Take last 100 by ID (newest)
            docs_sorted = sorted(docs, key=lambda x: x.get("id", ""), reverse=True)[:100]
            for d in docs_sorted:
                ext = d.get("extraction", {})
                if "error" in ext:
                    continue
                obst = ext.get("obstaravatel", {})
                zak = ext.get("zakazka", {})
                vys = ext.get("vysledok", {})
                pril = ext.get("prilezitost", {})
                zm = ext.get("zmena_zmluvy", {})
                hodnota = vys.get("celkova_hodnota") or pril.get("hodnota") or zm.get("hodnota_po_zmene")
                vitaz = ""
                lehota = pril.get("lehota_datum", "")
                if d.get("action") == "vysledok":
                    winners = [u for u in vys.get("ucastnici", []) if u.get("je_vitaz")]
                    vitaz = ", ".join(w.get("nazov", "") for w in winners)
                result["ted"].append({
                    "id": d.get("id", ""), "action": d.get("action", ""),
                    "url": d.get("url", ""), "obstaravatel": obst.get("nazov", ""),
                    "ico": obst.get("ico", ""), "predmet": zak.get("predmet", ""),
                    "hodnota": hodnota, "vitaz": vitaz, "lehota": lehota,
                    "cpv": zak.get("cpv_kod", ""),
                })

        self.send_json(result)

    def send_watchlist(self):
        if not os.path.exists(WATCHLIST_PATH):
            self.send_json({"pravidla": [], "notifikacia": {}})
            return
        with open(WATCHLIST_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.send_json(data)

    def save_watchlist(self):
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            data = json.loads(body.decode("utf-8"))
            os.makedirs(CONFIG_DIR, exist_ok=True)
            with open(WATCHLIST_PATH, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            # Clear old matches and state when rules change
            if os.path.exists(WATCHDOG_MATCHES_PATH):
                with open(WATCHDOG_MATCHES_PATH, "w") as f:
                    json.dump([], f)
            state_path = os.path.join(BASE_DIR, "data", "watchdog_state.json")
            if os.path.exists(state_path):
                with open(state_path, "w") as f:
                    json.dump({"scanned_files": [], "last_run": None}, f)
            self.send_json({"ok": True})
        except Exception as e:
            self.send_response(400)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps({"error": str(e)}, ensure_ascii=False).encode("utf-8"))

    def send_watchdog_matches(self):
        if not os.path.exists(WATCHDOG_MATCHES_PATH):
            self.send_json([])
            return
        with open(WATCHDOG_MATCHES_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        # Also attach last_run from watchdog_state.json
        state_path = os.path.join(BASE_DIR, "data", "watchdog_state.json")
        last_run = None
        if os.path.exists(state_path):
            try:
                with open(state_path, "r", encoding="utf-8") as f:
                    state = json.load(f)
                    last_run = state.get("last_run")
            except Exception:
                pass
        self.send_json({"matches": data, "last_run": last_run})

    def send_profile(self, parsed):
        params = parse_qs(parsed.query)
        query = params.get("q", [""])[0]
        ptype = params.get("type", ["dodavatel"])[0]
        if not query:
            self.send_json({"error": "Parameter 'q' je povinný."})
            return
        result = query_profile(query, ptype)
        self.send_json(result)

    def _get_pipeline_engine(self):
        """Import and return PipelineEngine, adding pipeline dir to sys.path."""
        if BASE_DIR not in sys.path:
            sys.path.insert(0, BASE_DIR)
        from pipeline import PipelineEngine
        return PipelineEngine()

    def send_company_api(self, parsed):
        """Public REST API for company data.

        Endpoints:
          GET /api/v1/company/{ico}                    → all modules
          GET /api/v1/company/{ico}?modules=orsf,rpvs  → selected modules

        Available modules: orsf, ruz, rpvs, fs_dlznici, sp_dlznici, uvo, ted
        """
        parts = parsed.path.rstrip("/").split("/")

        # /api/v1/company → docs
        if len(parts) < 5 or not parts[4]:
            self.send_json({
                "api": "UVOTool Public API v1",
                "usage": "GET /api/v1/company/{ico}?modules=orsf,ruz,rpvs,fs_dlznici,sp_dlznici,uvo,ted",
                "available_modules": {
                    "orsf": "Obchodný register — názov, status, právna forma, NACE, adresa, veľkosť, DIČ",
                    "ruz": "Register účtovných závierok — tržby, zisk (aktuálny + predchádzajúci rok)",
                    "rpvs": "Register partnerov VS — koneční užívatelia výhod (UBO), oprávnené osoby",
                    "fs_dlznici": "Finančná správa — kontrola daňových dlhov",
                    "sp_dlznici": "Sociálna poisťovňa — kontrola dlhov na sociálnom poistení",
                    "uvo": "UVO Vestník — účasť vo verejných zákazkách (víťazstvá, poradie)",
                    "ted": "TED eForms — účasť v nadlimitných EU zákazkách",
                },
                "examples": [
                    "/api/v1/company/46884769",
                    "/api/v1/company/46884769?modules=orsf,rpvs",
                    "/api/v1/company/36038351?modules=orsf,ruz,uvo",
                ],
                "batch": "POST /api/v1/batch with body: {\"icos\": [\"12345678\", ...], \"modules\": \"orsf,rpvs\"}",
            })
            return

        ico = parts[4]
        if not ico.isdigit() or len(ico) < 6 or len(ico) > 8:
            self.send_json({"error": "Neplatné IČO. Musí byť 6-8 číslic.", "ico": ico})
            return

        params = parse_qs(parsed.query)
        requested = params.get("modules", [""])[0]
        available = ["orsf", "ruz", "rpvs", "fs_dlznici", "sp_dlznici", "uvo", "ted",
                     "frsr_dph", "frsr_dane", "frsr_dph_odpocty", "frsr_spolahliv"]

        if requested:
            modules = [m.strip() for m in requested.split(",") if m.strip() in available]
        else:
            modules = available

        result = self._run_company_lookup(ico, modules)
        self.send_json(result)

    def _run_company_lookup(self, ico, modules):
        """Run selected modules for a single IČO and return structured result."""
        from datetime import datetime
        try:
            engine = self._get_pipeline_engine()
        except Exception as e:
            return {"error": f"Engine not available: {e}"}

        INTERNAL_KEYS = {"enriched_at", "checked_at", "fetched_at", "generated_at", "db_path", "_index", "_status"}

        result = {
            "ico": ico,
            "timestamp": datetime.now().isoformat(),
            "modules": {},
            "errors": [],
        }

        for mod_id in modules:
            try:
                mod_result = engine._run_module(mod_id, {"ico": ico})
                if mod_result.get("status") == "success":
                    data = mod_result.get("data", {})
                    clean = {k: v for k, v in data.items() if k not in INTERNAL_KEYS and not k.startswith("_")}
                    result["modules"][mod_id] = {
                        "status": "ok",
                        "data": clean,
                        "cached": mod_result.get("cached", False),
                    }
                else:
                    result["modules"][mod_id] = {"status": "error", "message": mod_result.get("message", "")}
                    result["errors"].append(mod_id)
            except Exception as e:
                result["modules"][mod_id] = {"status": "error", "message": str(e)}
                result["errors"].append(mod_id)

        # Flat summary — scalars + key arrays summarized
        flat = {"ico": ico}
        for mod_id, mod_data in result["modules"].items():
            if mod_data.get("status") != "ok":
                continue
            data = mod_data.get("data", {})
            for k, v in data.items():
                if k in INTERNAL_KEYS:
                    continue
                prefix = f"{mod_id}.{k}"
                if isinstance(v, list):
                    flat[prefix + "_count"] = len(v)
                elif isinstance(v, dict):
                    continue
                else:
                    flat[prefix] = v
        result["flat"] = flat

        return result

    def batch_company_api(self):
        """POST /api/v1/batch — batch lookup for multiple IČOs.
        Body: {"icos": ["12345678", "87654321", ...], "modules": "orsf,rpvs"}
        """
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            data = json.loads(body.decode("utf-8")) if body else {}
        except Exception as e:
            self.send_json({"error": f"Invalid JSON: {e}"})
            return

        icos = data.get("icos", [])
        if not icos:
            self.send_json({"error": "Chýba pole 'icos' v requeste"})
            return
        if len(icos) > 500:
            self.send_json({"error": "Maximum 500 IČO na jeden request"})
            return

        requested = data.get("modules", "")
        available = ["orsf", "ruz", "rpvs", "fs_dlznici", "sp_dlznici", "uvo", "ted",
                     "frsr_dph", "frsr_dane", "frsr_dph_odpocty", "frsr_spolahliv"]
        if requested:
            modules = [m.strip() for m in requested.split(",") if m.strip() in available]
        else:
            modules = available

        from datetime import datetime
        results = []
        for ico in icos:
            ico = str(ico).strip()
            if not ico.isdigit() or len(ico) < 6:
                results.append({"ico": ico, "error": "Neplatné IČO"})
                continue
            r = self._run_company_lookup(ico, modules)
            results.append(r)

        self.send_json({
            "total": len(icos),
            "processed": len(results),
            "modules_used": modules,
            "timestamp": datetime.now().isoformat(),
            "results": results,
        })

    def send_modules(self):
        try:
            engine = self._get_pipeline_engine()
            self.send_json(engine.get_modules())
        except Exception as e:
            self.send_json({"error": f"Pipeline engine not available: {e}"})

    def send_pipelines_list(self):
        try:
            engine = self._get_pipeline_engine()
            self.send_json(engine.list_pipelines())
        except Exception as e:
            self.send_json({"error": f"Pipeline engine not available: {e}"})

    def send_pipeline(self, pipeline_id):
        if not pipeline_id:
            return self.send_pipelines_list()
        try:
            engine = self._get_pipeline_engine()
            config = engine.get_pipeline(pipeline_id)
            if config:
                self.send_json(config)
            else:
                self.send_json({"error": "Pipeline not found"})
        except Exception as e:
            self.send_json({"error": f"Pipeline engine not available: {e}"})

    def save_pipeline_api(self):
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            data = json.loads(body.decode("utf-8"))
            pid = data.get("id", "custom-" + str(int(time.time())))
            engine = self._get_pipeline_engine()
            engine.save_pipeline(pid, data)
            self.send_json({"ok": True, "id": pid})
        except Exception as e:
            self.send_json({"error": f"Failed to save pipeline: {e}"})

    def run_pipeline_api_post(self, pipeline_id):
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            params = json.loads(body.decode("utf-8")) if body else {}
            engine = self._get_pipeline_engine()

            # Batch mode: if params has "ico_list", run batch
            if "ico_list" in params:
                ico_list = params["ico_list"]
                if isinstance(ico_list, str):
                    # CSV string — split by newlines and commas
                    ico_list = [x.strip() for line in ico_list.split("\n") for x in line.split(",") if x.strip()]
                    # Filter: keep only things that look like IČO (6-8 digits)
                    import re
                    ico_list = [x for x in ico_list if re.match(r'^\d{6,8}$', x)]
                result = engine.run_batch(pipeline_id, ico_list)
            else:
                result = engine.run_pipeline(pipeline_id, params)
            self.send_json(result)
        except Exception as e:
            self.send_json({"error": f"Failed to run pipeline: {e}"})

    # ─── TDD Enrichment API ────────────────────────────────────────────────

    def _get_tdd_enricher(self):
        """Return singleton TDDEnricher instance."""
        if not hasattr(DashboardHandler, '_tdd_enricher'):
            tools_dir = os.path.join(BASE_DIR, "tools")
            import importlib.util
            spec = importlib.util.spec_from_file_location(
                "tdd_enrichment", os.path.join(tools_dir, "tdd-enrichment.py")
            )
            tdd_mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(tdd_mod)
            DashboardHandler._tdd_enricher = tdd_mod.TDDEnricher.get_instance(DB_PATH)
        return DashboardHandler._tdd_enricher

    def send_enrich_tdd_docs(self):
        """GET /api/v1/enrich-tdd → documentation."""
        self.send_json({
            "api": "TDD Enrichment API v1",
            "description": "Obohacuje Peppol TDD (Tax Data Document) XML o firemne data zo slovenskych registrov.",
            "endpoints": {
                "POST /api/v1/enrich-tdd": {
                    "description": "Enrich single TDD",
                    "body_formats": [
                        "Raw TDD XML string (Content-Type: application/xml)",
                        'JSON: {"xml": "<TaxData>...</TaxData>"}',
                        '{"ic_dph_supplier": "SK2012345678", "ic_dph_customer": "SK2098765432"}',
                    ],
                    "query_params": {
                        "level": "fast (default) | full — fast uses in-memory lookups (<0.1ms), full adds ORSF+RUZ (~5ms)"
                    },
                    "response": {
                        "invoice": {"uuid": "...", "issue_date": "...", "payable_amount": 1805.0, "currency": "EUR"},
                        "supplier": {"ic_dph": "SK...", "ico": "12345678", "nazov": "...", "spolahliv": "...", "je_dlznik": False},
                        "customer": {"ic_dph": "SK...", "ico": "87654321", "nazov": "...", "spolahliv": "...", "je_dlznik": False},
                    },
                },
                "POST /api/v1/enrich-tdd/batch": {
                    "description": "Enrich multiple TDDs at once",
                    "body_formats": [
                        '{"tdds": ["<xml1>...", "<xml2>..."]}',
                        '{"pairs": [{"supplier": "SK...", "customer": "SK..."}, ...]}',
                    ],
                    "query_params": {
                        "level": "fast (default) | full"
                    },
                },
            },
            "levels": {
                "fast": "IC DPH -> ICO + nazov + spolahliv + dlznik (in-memory, <0.1ms/party)",
                "full": "fast + trzby, zisk, status, pravna_forma z ORSF/RUZ (~5ms/party)",
            },
        })

    def enrich_tdd_api(self):
        """POST /api/v1/enrich-tdd — enrich single TDD."""
        try:
            enricher = self._get_tdd_enricher()
        except Exception as e:
            self.send_json({"error": f"TDD Enricher not available: {e}"})
            return

        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)
        level = params.get("level", ["fast"])[0]
        if level not in ("fast", "full"):
            level = "fast"

        try:
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            content_type = self.headers.get("Content-Type", "")
        except Exception as e:
            self.send_json({"error": f"Failed to read request body: {e}"})
            return

        if not body:
            self.send_json({"error": "Empty request body"})
            return

        body_str = body.decode("utf-8")

        # Detect format: raw XML or JSON
        if content_type.startswith("application/xml") or content_type.startswith("text/xml") or body_str.strip().startswith("<"):
            # Raw XML
            result = enricher.enrich_tdd_xml(body_str, level)
            self.send_json(result)
            return

        # JSON body
        try:
            data = json.loads(body_str)
        except json.JSONDecodeError as e:
            self.send_json({"error": f"Invalid JSON: {e}"})
            return

        if "xml" in data:
            result = enricher.enrich_tdd_xml(data["xml"], level)
        elif "ic_dph_supplier" in data or "ic_dph_customer" in data:
            supplier = data.get("ic_dph_supplier", "")
            customer = data.get("ic_dph_customer", "")
            result = enricher.enrich_pair(supplier, customer, level)
        else:
            result = {"error": "Request must contain 'xml', or 'ic_dph_supplier'/'ic_dph_customer'"}

        self.send_json(result)

    def enrich_tdd_batch_api(self):
        """POST /api/v1/enrich-tdd/batch — enrich multiple TDDs."""
        try:
            enricher = self._get_tdd_enricher()
        except Exception as e:
            self.send_json({"error": f"TDD Enricher not available: {e}"})
            return

        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)
        level = params.get("level", ["fast"])[0]
        if level not in ("fast", "full"):
            level = "fast"

        try:
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            data = json.loads(body.decode("utf-8")) if body else {}
        except Exception as e:
            self.send_json({"error": f"Invalid request: {e}"})
            return

        from datetime import datetime as dt

        if "tdds" in data:
            tdds = data["tdds"]
            if not isinstance(tdds, list):
                self.send_json({"error": "'tdds' must be an array of XML strings"})
                return
            if len(tdds) > 1000:
                self.send_json({"error": "Maximum 1000 TDDs per batch request"})
                return
            results = enricher.enrich_batch(tdds, level)
            self.send_json({
                "total": len(tdds),
                "processed": len(results),
                "level": level,
                "timestamp": dt.now().isoformat(),
                "results": results,
            })
        elif "pairs" in data:
            pairs = data["pairs"]
            if not isinstance(pairs, list):
                self.send_json({"error": "'pairs' must be an array of {supplier, customer} objects"})
                return
            if len(pairs) > 1000:
                self.send_json({"error": "Maximum 1000 pairs per batch request"})
                return
            results = enricher.enrich_batch_pairs(pairs, level)
            self.send_json({
                "total": len(pairs),
                "processed": len(results),
                "level": level,
                "timestamp": dt.now().isoformat(),
                "results": results,
            })
        else:
            self.send_json({"error": "Request must contain 'tdds' (array of XML strings) or 'pairs' (array of {supplier, customer})"})

    def send_json(self, data):
        import gzip as gz
        raw = json.dumps(data, ensure_ascii=False).encode("utf-8")
        # Use gzip if client accepts it and response is large
        accept_enc = self.headers.get("Accept-Encoding", "")
        if "gzip" in accept_enc and len(raw) > 10000:
            response = gz.compress(raw)
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Encoding", "gzip")
            self.send_header("Content-Length", str(len(response)))
        else:
            response = raw
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(response)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(response)

    def send_json_file(self, filename):
        filepath = os.path.join(DATA_DIR, filename)
        if not os.path.exists(filepath):
            self.send_json({"error": "not found"})
            return
        with open(filepath, "r", encoding="utf-8") as f:
            data = f.read()
        response = data.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(response)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(response)

    def send_api_data(self):
        all_docs = []
        patterns = [
            os.path.join(DATA_DIR, "vestnik_*_parser_results.json"),
            os.path.join(DATA_DIR, "vestnik_*_legacy_results.json"),
            os.path.join(DATA_DIR, "ted_*_results.json"),
        ]
        for pattern in patterns:
            for filepath in sorted(glob.glob(pattern)):
                try:
                    with open(filepath, "r", encoding="utf-8") as f:
                        docs = json.load(f)
                        all_docs.extend(docs)
                except (json.JSONDecodeError, IOError) as e:
                    print(f"Error reading {filepath}: {e}")

        # Slim down for dashboard — keep only fields needed for table + detail
        slim = []
        for d in all_docs:
            ext = d.get("extraction", {})
            if "error" in ext:
                continue
            obst = ext.get("obstaravatel", {})
            zak = ext.get("zakazka", {})
            meta = ext.get("metadata", {})
            vys = ext.get("vysledok", {})
            pril = ext.get("prilezitost", {})
            zm = ext.get("zmena_zmluvy", {})
            casti = ext.get("casti", [])

            slim.append({
                "id": d.get("id", ""),
                "num": d.get("num", ""),
                "code": d.get("code", ""),
                "type": d.get("type", ""),
                "action": d.get("action", ""),
                "url": d.get("url", ""),
                "vestnik": d.get("vestnik", ""),
                "extraction": {
                    "metadata": {"id_zakazky": meta.get("id_zakazky", ""),
                                 "typ_oznamenia": meta.get("typ_oznamenia", ""),
                                 "typ_formulara": meta.get("typ_formulara", ""),
                                 "verzia": meta.get("verzia", "")},
                    "obstaravatel": {"nazov": obst.get("nazov", ""), "ico": obst.get("ico", ""),
                                     "email": obst.get("email", ""), "adresa": obst.get("adresa", ""),
                                     "mesto": obst.get("mesto", ""), "typ_kupujuceho": obst.get("typ_kupujuceho", ""),
                                     "cinnost": obst.get("cinnost", ""), "profil_url": obst.get("profil_url", "")},
                    "zakazka": {"predmet": zak.get("predmet", ""), "cpv_kod": zak.get("cpv_kod", ""),
                                "druh": zak.get("druh", ""), "opis": (zak.get("opis", "") or "")[:300],
                                "nuts": zak.get("nuts", ""), "druh_postupu": zak.get("druh_postupu", ""),
                                "pocet_casti": zak.get("pocet_casti", 1),
                                "max_casti_ponuka": zak.get("max_casti_ponuka"),
                                "max_casti_zadanie": zak.get("max_casti_zadanie")},
                    **({"vysledok": {"celkova_hodnota": vys.get("celkova_hodnota"),
                                     "pocet_ponuk": vys.get("pocet_ponuk"),
                                     "ucastnici": [{"nazov": u.get("nazov",""), "ico": u.get("ico",""),
                                                     "cena": u.get("cena"), "poradie": u.get("poradie"),
                                                     "je_vitaz": u.get("je_vitaz")}
                                                    for u in vys.get("ucastnici", [])],
                                     "zmluvy": [{"id": z.get("id",""), "datum": z.get("datum",""),
                                                  "url": z.get("url","")}
                                                 for z in (vys.get("zmluvy") or [])[:5]]}} if vys else {}),
                    **({"prilezitost": {"hodnota": pril.get("hodnota"), "mena": pril.get("mena", "EUR"),
                                        "lehota_datum": pril.get("lehota_datum", ""),
                                        "lehota_cas": pril.get("lehota_cas", ""),
                                        "eu_fond": pril.get("eu_fond", ""),
                                        "ramcova_dohoda": pril.get("ramcova_dohoda"),
                                        "kriterium": pril.get("kriterium", "")}} if pril else {}),
                    **({"zmena_zmluvy": {"dodavatel": zm.get("dodavatel", {}),
                                         "hodnota_po_zmene": zm.get("hodnota_po_zmene"),
                                         "zmluva_id": zm.get("zmluva_id", ""),
                                         "dovod_zmeny": zm.get("dovod_zmeny", ""),
                                         "zhrnutie": (zm.get("zhrnutie", "") or "")[:200]}} if zm else {}),
                    **({"casti": [{"cislo": c.get("cislo"), "lot_id": c.get("lot_id",""),
                                   "nazov": c.get("nazov",""), "cpv_kod": c.get("cpv_kod",""),
                                   "hodnota": c.get("hodnota"), "opis": (c.get("opis","") or "")[:150]}
                                  for c in casti]} if casti else {}),
                },
            })

        self.send_json(slim)


if __name__ == "__main__":
    HTTPServer.allow_reuse_address = True
    server = HTTPServer(("0.0.0.0", PORT), DashboardHandler)
    print(f"UVO Vestnik Dashboard: http://localhost:{PORT}")
    print(f"API: /api/data, /api/analysis, /api/graph, /api/profile?type=dodavatel&q=ICO")
    print(f"     /api/watchlist (GET/POST), /api/watchdog-matches")
    print(f"     /api/modules, /api/pipelines, /api/pipelines/<id>/run")
    print(f"     /api/v1/enrich-tdd (GET=docs, POST=enrich)")
    print(f"     /api/v1/enrich-tdd/batch (POST=batch enrich)")
    print(f"Data: {DATA_DIR}")
    print(f"DB: {DB_PATH}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nServer stopped.")
        server.server_close()
