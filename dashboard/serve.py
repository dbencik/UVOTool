#!/usr/bin/env python3
"""Simple HTTP server for UVO Vestnik dashboard."""

import json
import glob
import os
import sqlite3
import subprocess
import sys
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


def _profile_dodavatel(db, query, is_ico):
    name, ico = _find_entity(db, query, is_ico, "ucastnici")
    if not ico:
        return {"error": f"Dodávateľ '{query}' nebol nájdený."}

    result = {"typ": "dodavatel", "nazov": name, "ico": ico, "sekcie": []}

    # 1. Overall stats
    stats = db.execute("""
        SELECT COUNT(*) as wins, SUM(cena) as total, MIN(cena) as min_val, MAX(cena) as max_val, AVG(cena) as avg_val
        FROM ucastnici WHERE ico = ? AND je_vitaz = 1 AND cena IS NOT NULL AND cena > 0
    """, (ico,)).fetchone()

    wins_all = db.execute("SELECT COUNT(*) FROM ucastnici WHERE ico = ? AND je_vitaz = 1", (ico,)).fetchone()[0]
    total_bids = db.execute("SELECT COUNT(*) FROM ucastnici WHERE ico = ?", (ico,)).fetchone()[0]

    years = db.execute("""
        SELECT MIN(d.rok) as min_y, MAX(d.rok) as max_y
        FROM ucastnici u JOIN dokumenty d ON u.doc_id = d.id WHERE u.ico = ? AND u.je_vitaz = 1
    """, (ico,)).fetchone()

    # Biggest contract
    biggest = db.execute("""
        SELECT u.cena, z.predmet FROM ucastnici u
        JOIN zakazky z ON u.doc_id = z.doc_id
        WHERE u.ico = ? AND u.je_vitaz = 1 AND u.cena IS NOT NULL
        ORDER BY u.cena DESC LIMIT 1
    """, (ico,)).fetchone()

    result["sekcie"].append({
        "nazov": "Celkový profil",
        "text": f"Firma {name} (IČO: {ico}) získala od roku {years['min_y'] or '?'} do roku {years['max_y'] or '?'} "
                f"celkovo {wins_all} zákaziek v hodnote {_fmt_eur(stats['total'])}. "
                f"Priemerná zákazka mala hodnotu {_fmt_eur(stats['avg_val'])}, "
                f"najmenšia {_fmt_eur(stats['min_val'])}, najväčšia {_fmt_eur(stats['max_val'])}"
                + (f" za \"{biggest['predmet'][:80]}\"." if biggest and biggest['predmet'] else "."),
        "data": {
            "zakazky": wins_all, "celkova_hodnota": stats['total'] or 0,
            "priemer": stats['avg_val'] or 0, "min": stats['min_val'] or 0, "max": stats['max_val'] or 0,
            "win_rate": round(wins_all / max(total_bids, 1) * 100, 1),
            "total_bids": total_bids,
        }
    })

    # 2. Customer mix
    customers = db.execute("""
        SELECT o.nazov, o.ico, COUNT(*) as cnt, SUM(u.cena) as total
        FROM ucastnici u
        JOIN obstaravatelia o ON u.doc_id = o.doc_id
        WHERE u.ico = ? AND u.je_vitaz = 1
        GROUP BY o.ico ORDER BY cnt DESC LIMIT 10
    """, (ico,)).fetchall()

    cust_list = [{"nazov": c["nazov"], "ico": c["ico"], "zakazky": c["cnt"],
                  "hodnota": c["total"] or 0,
                  "podiel": round(c["cnt"] / max(wins_all, 1) * 100, 1)} for c in customers]

    top_cust = cust_list[0] if cust_list else None
    result["sekcie"].append({
        "nazov": "Zákaznícky mix",
        "text": (f"Najvýznamnejším zákazníkom firmy {name} je {top_cust['nazov']} "
                 f"s {top_cust['zakazky']} zákazkami v hodnote {_fmt_eur(top_cust['hodnota'])}, "
                 f"čo predstavuje {top_cust['podiel']}% zákaziek." if top_cust else "Žiadni zákazníci."),
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
    result["sekcie"].append({
        "nazov": "Sektorové zameranie",
        "text": (f"Firma sa primárne zameriava na oblasť {cpv_list[0]['cpv']} ({cpv_list[0]['pocet']} zákaziek)."
                 if cpv_list else "Sektorové zameranie nie je možné určiť."),
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
        "text": f"Z {total_bids} tendrov, v ktorých firma súťažila, zvíťazila v {wins_all} prípadoch "
                f"(win rate {round(wins_all / max(total_bids, 1) * 100, 1)}%). "
                f"V {single_bidder} prípadoch bola jediným uchádzačom ({sb_rate}%). "
                + (f"Priemerný počet ponúk v tendroch bol {avg_bids:.1f}." if avg_bids else ""),
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
    result["sekcie"].append({
        "nazov": "Časový vývoj",
        "text": " ".join(f"V roku {y['rok']}: {y['zakazky']} zákaziek za {_fmt_eur(y['hodnota'])}." for y in yearly_list),
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
    result["sekcie"].append({
        "nazov": "Sieťové väzby",
        "text": (f"Firma sa najčastejšie stretáva v tendroch s firmou {cobid_list[0]['nazov']} "
                 f"({cobid_list[0]['spolocne_tendre']}-krát)." if cobid_list else "Žiadne sieťové väzby."),
        "data": cobid_list
    })

    # 7. Contracts
    contracts = db.execute("""
        SELECT d.rok, u.cena, z.predmet, d.url
        FROM ucastnici u
        JOIN dokumenty d ON u.doc_id = d.id
        JOIN zakazky z ON u.doc_id = z.doc_id
        WHERE u.ico = ? AND u.je_vitaz = 1
        ORDER BY u.cena DESC LIMIT 20
    """, (ico,)).fetchall()

    contract_list = [{"rok": c["rok"], "hodnota": c["cena"] or 0, "predmet": c["predmet"] or "", "url": c["url"] or ""} for c in contracts]
    result["sekcie"].append({
        "nazov": "Zmluvy",
        "data": contract_list
    })

    # 8. Risk flags
    flags = []
    if sb_rate > 50:
        flags.append(f"Vysoký podiel zákaziek bez súťaže: {sb_rate}% single-bidder rate")
    if top_cust and top_cust["podiel"] > 70:
        flags.append(f"Vysoká závislosť na jednom zákazníkovi: {top_cust['nazov']} = {top_cust['podiel']}%")

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
        elif parsed.path == "/api/profile":
            self.send_profile(parsed)
        elif parsed.path == "/api/watchlist":
            self.send_watchlist()
        elif parsed.path == "/api/watchdog-matches":
            self.send_watchdog_matches()
        else:
            super().do_GET()

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/watchlist":
            self.save_watchlist()
        else:
            self.send_response(404)
            self.end_headers()

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

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
    print(f"Data: {DATA_DIR}")
    print(f"DB: {DB_PATH}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nServer stopped.")
        server.server_close()
