#!/usr/bin/env python3
"""
Vestník — analytický nástroj (pattern detection)
Spúšťa dotazy na SQLite databázu data/vestnik.db a vypisuje výsledky.

Usage:
  python3 tools/vestnik-analyze.py
  python3 tools/vestnik-analyze.py --db /cesta/k/vestnik.db

Ak databáza neexistuje, ponúkne import z JSON súborov v data/results/.
"""

import sqlite3
import json
import os
import sys
import re
import argparse
from pathlib import Path
from datetime import datetime, timedelta
from collections import defaultdict

# ─── paths ────────────────────────────────────────────────────────────────────

ROOT = Path(__file__).parent.parent
DEFAULT_DB = ROOT / "data" / "vestnik.db"
RESULTS_DIR = ROOT / "data" / "results"
REPORT_OUT = ROOT / "data" / "results" / "analysis_report.json"

# ─── terminal helpers ──────────────────────────────────────────────────────────

SEP = "─" * 80


def header(title: str):
    print()
    print("═" * 80)
    print(f"  {title}")
    print("═" * 80)


def subheader(title: str):
    print()
    print(f"  {title}")
    print(SEP)


def fmt_eur(val) -> str:
    if val is None:
        return "N/A"
    try:
        return f"{float(val):,.2f} €".replace(",", " ")
    except (ValueError, TypeError):
        return str(val)


def fmt_pct(val) -> str:
    if val is None:
        return "N/A"
    return f"{float(val):.1f}%"


def print_table(headers: list, rows: list, col_widths: list = None):
    """Print a simple ASCII table without external deps."""
    if not rows:
        print("  (žiadne záznamy)")
        return

    if col_widths is None:
        col_widths = [max(len(str(h)), max((len(str(r[i])) for r in rows), default=0))
                      for i, h in enumerate(headers)]

    row_fmt = "  " + "  ".join(f"{{:<{w}}}" for w in col_widths)
    sep = "  " + "  ".join("-" * w for w in col_widths)

    print(row_fmt.format(*[str(h) for h in headers]))
    print(sep)
    for row in rows:
        cells = [str(c) if c is not None else "—" for c in row]
        # Truncate long cells to fit width
        cells = [c[:w] if len(c) > w else c for c, w in zip(cells, col_widths)]
        print(row_fmt.format(*cells))


# ─── DB creation from JSON ────────────────────────────────────────────────────

CREATE_SCHEMA = """
CREATE TABLE IF NOT EXISTS dokumenty (
    id      TEXT PRIMARY KEY,
    vestnik TEXT,
    rok     INTEGER,
    action  TEXT,
    url     TEXT
);
CREATE TABLE IF NOT EXISTS obstaravatelia (
    doc_id  TEXT,
    nazov   TEXT,
    ico     TEXT,
    mesto   TEXT,
    FOREIGN KEY(doc_id) REFERENCES dokumenty(id)
);
CREATE TABLE IF NOT EXISTS zakazky (
    doc_id   TEXT,
    predmet  TEXT,
    cpv_kod  TEXT,
    druh     TEXT,
    FOREIGN KEY(doc_id) REFERENCES dokumenty(id)
);
CREATE TABLE IF NOT EXISTS vysledky (
    doc_id          TEXT,
    celkova_hodnota REAL,
    pocet_ponuk     INTEGER,
    FOREIGN KEY(doc_id) REFERENCES dokumenty(id)
);
CREATE TABLE IF NOT EXISTS ucastnici (
    doc_id   TEXT,
    nazov    TEXT,
    ico      TEXT,
    cena     REAL,
    je_vitaz INTEGER,
    FOREIGN KEY(doc_id) REFERENCES dokumenty(id)
);
CREATE TABLE IF NOT EXISTS prilezitosti (
    doc_id        TEXT,
    hodnota       REAL,
    mena          TEXT,
    lehota_datum  TEXT,
    FOREIGN KEY(doc_id) REFERENCES dokumenty(id)
);
CREATE TABLE IF NOT EXISTS zmeny_zmluv (
    doc_id             TEXT,
    dodavatel_nazov    TEXT,
    dodavatel_ico      TEXT,
    hodnota_po_zmene   REAL,
    dovod_zmeny        TEXT,
    FOREIGN KEY(doc_id) REFERENCES dokumenty(id)
);
CREATE TABLE IF NOT EXISTS zmluvy (
    doc_id      TEXT,
    zmluva_id   TEXT,
    datum       TEXT,
    url         TEXT,
    FOREIGN KEY(doc_id) REFERENCES dokumenty(id)
);
"""


def parse_vestnik_meta(vestnik_str: str):
    """Extract vestnik number and rok from 'VVO 191/2026'."""
    m = re.search(r'(\d+)/(\d{4})', vestnik_str or "")
    if m:
        return int(m.group(2))
    return None


def import_json_to_db(conn: sqlite3.Connection, json_path: Path) -> int:
    """Import one parser-results JSON file into the DB. Returns record count."""
    with open(json_path, encoding="utf-8") as f:
        records = json.load(f)

    if not isinstance(records, list):
        return 0

    cur = conn.cursor()
    count = 0

    for rec in records:
        doc_id = rec.get("id", "")
        if not doc_id:
            continue

        vestnik_str = rec.get("vestnik", "")
        rok = parse_vestnik_meta(vestnik_str)
        action = rec.get("action", "unknown")
        url = rec.get("url", "")

        # dokumenty
        cur.execute(
            "INSERT OR IGNORE INTO dokumenty(id, vestnik, rok, action, url) VALUES (?,?,?,?,?)",
            (doc_id, vestnik_str, rok, action, url)
        )

        ext = rec.get("extraction", {})
        if not ext:
            count += 1
            continue

        # obstaravatelia
        o = ext.get("obstaravatel", {})
        if o and o.get("nazov"):
            cur.execute(
                "INSERT INTO obstaravatelia(doc_id, nazov, ico, mesto) VALUES (?,?,?,?)",
                (doc_id, o.get("nazov"), o.get("ico"), o.get("mesto"))
            )

        # zakazky
        z = ext.get("zakazka", {})
        if z and z.get("predmet"):
            cur.execute(
                "INSERT INTO zakazky(doc_id, predmet, cpv_kod, druh) VALUES (?,?,?,?)",
                (doc_id, z.get("predmet"), z.get("cpv_kod"), z.get("druh"))
            )

        # vysledky + ucastnici
        v = ext.get("vysledok", {})
        if v:
            cur.execute(
                "INSERT INTO vysledky(doc_id, celkova_hodnota, pocet_ponuk) VALUES (?,?,?)",
                (doc_id, v.get("celkova_hodnota"), v.get("pocet_ponuk"))
            )
            for u in v.get("ucastnici", []):
                cur.execute(
                    "INSERT INTO ucastnici(doc_id, nazov, ico, cena, je_vitaz) VALUES (?,?,?,?,?)",
                    (doc_id, u.get("nazov"), u.get("ico"), u.get("cena"), 1 if u.get("je_vitaz") else 0)
                )
            for zm in v.get("zmluvy", []):
                cur.execute(
                    "INSERT INTO zmluvy(doc_id, zmluva_id, datum, url) VALUES (?,?,?,?)",
                    (doc_id, zm.get("id"), zm.get("datum"), zm.get("url"))
                )

        # prilezitosti (from vyhlasenie)
        p = ext.get("prilezitost", {})
        if p and (p.get("hodnota") or p.get("lehota_datum")):
            cur.execute(
                "INSERT INTO prilezitosti(doc_id, hodnota, mena, lehota_datum) VALUES (?,?,?,?)",
                (doc_id, p.get("hodnota"), p.get("mena", "EUR"), p.get("lehota_datum"))
            )

        # zmeny_zmluv
        zz = ext.get("zmena_zmluvy", {})
        if zz:
            dodavatel = zz.get("dodavatel", {}) or {}
            cur.execute(
                "INSERT INTO zmeny_zmluv(doc_id, dodavatel_nazov, dodavatel_ico, hodnota_po_zmene, dovod_zmeny) VALUES (?,?,?,?,?)",
                (doc_id,
                 dodavatel.get("nazov") if isinstance(dodavatel, dict) else None,
                 dodavatel.get("ico") if isinstance(dodavatel, dict) else None,
                 zz.get("hodnota_po_zmene"),
                 zz.get("dovod_zmeny"))
            )

        count += 1

    conn.commit()
    return count


def ensure_db(db_path: Path) -> sqlite3.Connection:
    """Open DB, create schema if needed, optionally import from JSON."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    is_new = not db_path.exists()

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    if is_new:
        conn.executescript(CREATE_SCHEMA)
        conn.commit()
        print(f"[info] Vytvorená nová databáza: {db_path}")

    # Check if empty
    cur = conn.execute("SELECT COUNT(*) FROM dokumenty")
    count = cur.fetchone()[0]

    if count == 0:
        json_files = sorted(RESULTS_DIR.glob("vestnik_*_parser_results.json"))
        if not json_files:
            print("[warn] Žiadne JSON súbory v data/results/ — databáza zostáva prázdna.")
            print("       Najprv spusti: python3 tools/vestnik-parser.py 191/2026")
            return conn

        print(f"[info] Importujem {len(json_files)} JSON súborov do databázy ...")
        total = 0
        for jf in json_files:
            n = import_json_to_db(conn, jf)
            print(f"       {jf.name}: {n} záznamov")
            total += n
        print(f"[info] Import hotový — celkom {total} dokumentov")

    return conn


# ─── Analysis 1: Single-bidder rate ──────────────────────────────────────────

def analysis_single_bidder(conn: sqlite3.Connection) -> dict:
    header("1. ZÁKAZKY S JEDINÝM UCHÁDZAČOM (single-bidder rate)")

    # Overall rate
    row = conn.execute("""
        SELECT
            COUNT(*) AS total,
            SUM(CASE WHEN pocet_ponuk = 1 THEN 1 ELSE 0 END) AS single
        FROM vysledky
        WHERE pocet_ponuk IS NOT NULL AND pocet_ponuk > 0
    """).fetchone()

    total = row["total"] or 0
    single = row["single"] or 0
    rate = (single / total * 100) if total else 0

    subheader("Celkový prehľad")
    print(f"  Celkom zákaziek (s počtom ponúk): {total}")
    print(f"  Z toho s jediným uchádzačom:      {single} ({rate:.1f}%)")

    # Top 10 by single-bidder rate (min 3 tenders)
    rows = conn.execute("""
        SELECT
            o.nazov,
            o.ico,
            COUNT(*) AS total_tenders,
            SUM(CASE WHEN v.pocet_ponuk = 1 THEN 1 ELSE 0 END) AS single_bidder,
            ROUND(100.0 * SUM(CASE WHEN v.pocet_ponuk = 1 THEN 1 ELSE 0 END) / COUNT(*), 1) AS rate
        FROM vysledky v
        JOIN obstaravatelia o ON o.doc_id = v.doc_id
        WHERE v.pocet_ponuk IS NOT NULL AND v.pocet_ponuk > 0
        GROUP BY o.ico, o.nazov
        HAVING total_tenders >= 3
        ORDER BY rate DESC, total_tenders DESC
        LIMIT 10
    """).fetchall()

    subheader("Top 10 obstarávateľov — najvyšší podiel zákaziek s 1 uchádzačom (min. 3 zákazky)")
    table_rows = [
        (
            r["nazov"][:45] if r["nazov"] else "—",
            r["ico"] or "—",
            r["total_tenders"],
            r["single_bidder"],
            fmt_pct(r["rate"])
        )
        for r in rows
    ]
    print_table(
        ["Obstarávateľ", "IČO", "Zákazky", "Jediný", "Rate"],
        table_rows,
        [45, 10, 7, 7, 7]
    )

    result = {
        "celkovo": {"total": total, "single_bidder": single, "rate_pct": round(rate, 1)},
        "top_obstaravatelia": [
            {
                "nazov": r["nazov"],
                "ico": r["ico"],
                "total_tenders": r["total_tenders"],
                "single_bidder": r["single_bidder"],
                "rate_pct": r["rate"]
            }
            for r in rows
        ]
    }
    return result


# ─── Analysis 2: Repeated winners ────────────────────────────────────────────

def analysis_repeated_winners(conn: sqlite3.Connection) -> dict:
    header("2. OPAKOVANÍ VÍŤAZI (repeated winners)")

    rows = conn.execute("""
        SELECT
            o.nazov  AS obstaravatel,
            o.ico    AS obstaravatel_ico,
            u.nazov  AS vitaz,
            u.ico    AS vitaz_ico,
            COUNT(DISTINCT u.doc_id) AS win_count,
            (SELECT COUNT(DISTINCT v2.doc_id) FROM vysledky v2
             JOIN obstaravatelia o2 ON o2.doc_id = v2.doc_id
             WHERE o2.ico = o.ico AND o2.nazov = o.nazov) AS total_tenders,
            ROUND(100.0 * COUNT(DISTINCT u.doc_id) /
                MAX(1, (SELECT COUNT(DISTINCT v2.doc_id) FROM vysledky v2
                 JOIN obstaravatelia o2 ON o2.doc_id = v2.doc_id
                 WHERE o2.ico = o.ico AND o2.nazov = o.nazov)), 1) AS rate
        FROM ucastnici u
        JOIN obstaravatelia o ON o.doc_id = u.doc_id
        JOIN vysledky v ON v.doc_id = u.doc_id
        WHERE u.je_vitaz = 1
        GROUP BY o.ico, o.nazov, u.ico, u.nazov
        HAVING win_count >= 3 AND rate > 50 AND rate <= 100
        ORDER BY rate DESC, win_count DESC
        LIMIT 20
    """).fetchall()

    subheader("Páry obstarávateľ → víťaz s >50% podielom výhier (min. 3 výhry)")
    table_rows = [
        (
            r["obstaravatel"][:35] if r["obstaravatel"] else "—",
            r["vitaz"][:35] if r["vitaz"] else "—",
            r["win_count"],
            r["total_tenders"],
            fmt_pct(r["rate"])
        )
        for r in rows
    ]
    print_table(
        ["Obstarávateľ", "Víťaz", "Výhry", "Celkom", "Rate"],
        table_rows,
        [35, 35, 6, 7, 7]
    )

    if not rows:
        print("  (žiadne páry spĺňajú kritériá — možno málo dát)")

    result = {
        "flagged_pairs": [
            {
                "obstaravatel": r["obstaravatel"],
                "obstaravatel_ico": r["obstaravatel_ico"],
                "vitaz": r["vitaz"],
                "vitaz_ico": r["vitaz_ico"],
                "win_count": r["win_count"],
                "total_tenders": r["total_tenders"],
                "rate_pct": r["rate"]
            }
            for r in rows
        ]
    }
    return result


# ─── Analysis 3: Price anomalies ─────────────────────────────────────────────

def analysis_price_anomalies(conn: sqlite3.Connection) -> dict:
    header("3. CENOVÉ ANOMÁLIE (price anomalies)")

    # 3a: Tenders just below common thresholds
    thresholds = [
        (5_000, "5 000 €"),
        (70_000, "70 000 €"),
        (260_000, "260 000 €"),
    ]
    threshold_results = []

    for thresh, label in thresholds:
        low = thresh * 0.95
        high = thresh * 1.05
        # Use UNION to avoid cartesian explosion from multi-LEFT JOIN with OR
        rows = conn.execute("""
            SELECT DISTINCT d.id, d.vestnik,
                   o.nazov AS obstaravatel,
                   z.predmet,
                   p.hodnota AS hodnota,
                   NULL AS celkova_hodnota,
                   NULL AS vitazna_cena
            FROM dokumenty d
            JOIN obstaravatelia o ON o.doc_id = d.id
            JOIN zakazky z ON z.doc_id = d.id
            JOIN prilezitosti p ON p.doc_id = d.id
            WHERE p.hodnota BETWEEN ? AND ?
            UNION
            SELECT DISTINCT d.id, d.vestnik,
                   o.nazov AS obstaravatel,
                   z.predmet,
                   NULL AS hodnota,
                   v.celkova_hodnota,
                   NULL AS vitazna_cena
            FROM dokumenty d
            JOIN obstaravatelia o ON o.doc_id = d.id
            JOIN zakazky z ON z.doc_id = d.id
            JOIN vysledky v ON v.doc_id = d.id
            WHERE v.celkova_hodnota BETWEEN ? AND ?
            UNION
            SELECT DISTINCT d.id, d.vestnik,
                   o.nazov AS obstaravatel,
                   z.predmet,
                   NULL AS hodnota,
                   NULL AS celkova_hodnota,
                   u.cena AS vitazna_cena
            FROM dokumenty d
            JOIN obstaravatelia o ON o.doc_id = d.id
            JOIN zakazky z ON z.doc_id = d.id
            JOIN ucastnici u ON u.doc_id = d.id AND u.je_vitaz = 1
            WHERE u.cena BETWEEN ? AND ?
            LIMIT 30
        """, (low, high, low, high, low, high)).fetchall()

        subheader(f"Zákazky s hodnotou v okolí prahu {label} (±5%, max 30)")
        if rows:
            table_rows = [
                (
                    r["vestnik"] or "—",
                    (r["obstaravatel"] or "—")[:30],
                    (r["predmet"] or "—")[:35],
                    fmt_eur(r["hodnota"] or r["celkova_hodnota"] or r["vitazna_cena"])
                )
                for r in rows
            ]
            print_table(
                ["Vestník", "Obstarávateľ", "Predmet", "Hodnota"],
                table_rows,
                [14, 30, 35, 18]
            )
        else:
            print("  (žiadne záznamy)")

        threshold_results.append({
            "threshold": thresh,
            "label": label,
            "count": len(rows),
            "records": [
                {
                    "doc_id": r["id"],
                    "vestnik": r["vestnik"],
                    "obstaravatel": r["obstaravatel"],
                    "hodnota": r["hodnota"] or r["celkova_hodnota"] or r["vitazna_cena"]
                }
                for r in rows
            ]
        })

    # 3b: Cases where víťazná cena is very close to estimate (ratio > 0.95)
    subheader("Zákazky kde víťazná cena > 95% predpokladanej hodnoty (top 15)")
    ratio_rows = conn.execute("""
        SELECT
            d.id,
            d.vestnik,
            o.nazov      AS obstaravatel,
            z.predmet,
            p.hodnota    AS predpokladana,
            u.cena       AS vitazna,
            ROUND(u.cena / p.hodnota, 3) AS ratio
        FROM ucastnici u
        JOIN dokumenty d ON d.id = u.doc_id
        JOIN obstaravatelia o ON o.doc_id = u.doc_id
        JOIN zakazky z ON z.doc_id = u.doc_id
        JOIN prilezitosti p ON p.doc_id = u.doc_id
        WHERE u.je_vitaz = 1
          AND p.hodnota > 0
          AND u.cena > 0
          AND u.cena / p.hodnota > 0.95
          AND u.cena / p.hodnota <= 1.5
        ORDER BY ratio DESC
        LIMIT 15
    """).fetchall()

    if ratio_rows:
        table_rows = [
            (
                r["vestnik"] or "—",
                (r["obstaravatel"] or "—")[:28],
                fmt_eur(r["predpokladana"]),
                fmt_eur(r["vitazna"]),
                str(r["ratio"])
            )
            for r in ratio_rows
        ]
        print_table(
            ["Vestník", "Obstarávateľ", "Predpokl.", "Víťazná cena", "Ratio"],
            table_rows,
            [12, 28, 18, 18, 6]
        )
    else:
        print("  (žiadne záznamy — možno chýba prepojenie vyhlásenie↔výsledok v jednom vestníku)")

    return {
        "threshold_proximity": threshold_results,
        "price_close_to_estimate": [
            {
                "doc_id": r["id"],
                "vestnik": r["vestnik"],
                "obstaravatel": r["obstaravatel"],
                "predpokladana_hodnota": r["predpokladana"],
                "vitazna_cena": r["vitazna"],
                "ratio": r["ratio"]
            }
            for r in ratio_rows
        ]
    }


# ─── Analysis 4: Contract splitting ──────────────────────────────────────────

def analysis_contract_splitting(conn: sqlite3.Connection) -> dict:
    header("4. PODOZRENIE NA DELENIE ZÁKAZIEK (contract splitting)")

    # Load all relevant records in Python for date math (SQLite date parsing is tricky)
    records = conn.execute("""
        SELECT
            o.ico        AS obstaravatel_ico,
            o.nazov      AS obstaravatel,
            z.cpv_kod,
            d.id,
            d.vestnik,
            d.rok,
            p.hodnota,
            p.lehota_datum
        FROM dokumenty d
        JOIN obstaravatelia o ON o.doc_id = d.id
        JOIN zakazky z ON z.doc_id = d.id
        LEFT JOIN prilezitosti p ON p.doc_id = d.id
        WHERE d.action IN ('vyhlasenie', 'vysledok')
          AND z.cpv_kod IS NOT NULL
    """).fetchall()

    def parse_date(s):
        if not s:
            return None
        for fmt in ("%d.%m.%Y", "%Y-%m-%d"):
            try:
                return datetime.strptime(s, fmt)
            except ValueError:
                continue
        return None

    # Group by (obstaravatel_ico, cpv_kod)
    groups = defaultdict(list)
    for r in records:
        key = (r["obstaravatel_ico"] or r["obstaravatel"], r["cpv_kod"])
        groups[key].append(dict(r))

    suspicious = []
    for (ico, cpv), items in groups.items():
        if len(items) < 2:
            continue

        # Sort by date
        dated = [(parse_date(i["lehota_datum"]), i) for i in items]
        dated = [(d, i) for d, i in dated if d is not None]
        dated.sort(key=lambda x: x[0])

        # Sliding window: find clusters within 90 days
        for start_idx in range(len(dated)):
            cluster = [dated[start_idx]]
            for j in range(start_idx + 1, len(dated)):
                if (dated[j][0] - dated[start_idx][0]).days <= 90:
                    cluster.append(dated[j])
                else:
                    break
            if len(cluster) >= 3:
                total_val = sum(i["hodnota"] or 0 for _, i in cluster)
                individual_vals = [i["hodnota"] for _, i in cluster if i["hodnota"]]
                suspicious.append({
                    "obstaravatel": cluster[0][1]["obstaravatel"],
                    "obstaravatel_ico": ico,
                    "cpv_kod": cpv,
                    "count": len(cluster),
                    "period_days": (cluster[-1][0] - cluster[0][0]).days,
                    "total_hodnota": total_val,
                    "individual_values": individual_vals,
                    "doc_ids": [i["id"] for _, i in cluster],
                    "vestnik": cluster[0][1]["vestnik"]
                })
                break  # one cluster per group is enough

    suspicious.sort(key=lambda x: x["count"], reverse=True)

    subheader("Skupiny: rovnaký obstarávateľ + rovnaký CPV + min. 3 zákazky v rozmedzí 90 dní")
    if suspicious:
        table_rows = [
            (
                (s["obstaravatel"] or "—")[:35],
                (s["cpv_kod"] or "—")[:30],
                s["count"],
                f"{s['period_days']}d",
                fmt_eur(s["total_hodnota"]) if s["total_hodnota"] else "N/A"
            )
            for s in suspicious[:15]
        ]
        print_table(
            ["Obstarávateľ", "CPV kód", "Počet", "Obdobie", "Celková hodnota"],
            table_rows,
            [35, 30, 6, 8, 18]
        )
    else:
        print("  (žiadne skupiny nájdené — možno nedostatok dát s datumami)")

    return {"suspicious_groups": suspicious[:20]}


# ─── Analysis 5: Top stats ────────────────────────────────────────────────────

def analysis_top_stats(conn: sqlite3.Connection) -> dict:
    header("5. SÚHRNNÉ ŠTATISTIKY (top stats)")

    # 5a: Total value by year
    subheader("Celková hodnota zákaziek podľa roku")
    year_rows = conn.execute("""
        SELECT
            d.rok,
            COUNT(DISTINCT d.id)         AS pocet_zakaziek,
            ROUND(SUM(v.celkova_hodnota)) AS celkova_hodnota,
            ROUND(AVG(v.celkova_hodnota)) AS priemerna_hodnota
        FROM dokumenty d
        JOIN vysledky v ON v.doc_id = d.id
        WHERE d.rok IS NOT NULL AND v.celkova_hodnota IS NOT NULL
        GROUP BY d.rok
        ORDER BY d.rok
    """).fetchall()

    print_table(
        ["Rok", "Zákaziek", "Celková hodnota", "Priemerná hodnota"],
        [
            (r["rok"], r["pocet_zakaziek"], fmt_eur(r["celkova_hodnota"]), fmt_eur(r["priemerna_hodnota"]))
            for r in year_rows
        ],
        [6, 9, 20, 20]
    )

    # 5b: Top 10 winners by total won value
    subheader("Top 10 víťazov — najvyššia celková hodnota výhier")
    winner_rows = conn.execute("""
        SELECT
            u.nazov,
            u.ico,
            COUNT(*)               AS pocet_vyher,
            ROUND(SUM(u.cena))     AS celkova_cena
        FROM ucastnici u
        WHERE u.je_vitaz = 1
          AND u.cena IS NOT NULL
        GROUP BY u.ico, u.nazov
        ORDER BY celkova_cena DESC
        LIMIT 10
    """).fetchall()

    print_table(
        ["Víťaz", "IČO", "Výhry", "Celková cena"],
        [
            (
                (r["nazov"] or "—")[:45],
                r["ico"] or "—",
                r["pocet_vyher"],
                fmt_eur(r["celkova_cena"])
            )
            for r in winner_rows
        ],
        [45, 10, 6, 20]
    )

    # 5c: Top 10 buyers by total spend
    subheader("Top 10 obstarávateľov — najvyšší celkový výdavok")
    buyer_rows = conn.execute("""
        SELECT
            o.nazov,
            o.ico,
            COUNT(DISTINCT o.doc_id)   AS pocet_zakaziek,
            ROUND(SUM(v.celkova_hodnota)) AS celkovy_vydavok
        FROM obstaravatelia o
        JOIN vysledky v ON v.doc_id = o.doc_id
        WHERE v.celkova_hodnota IS NOT NULL
        GROUP BY o.ico, o.nazov
        ORDER BY celkovy_vydavok DESC
        LIMIT 10
    """).fetchall()

    print_table(
        ["Obstarávateľ", "IČO", "Zákazky", "Celkový výdavok"],
        [
            (
                (r["nazov"] or "—")[:45],
                r["ico"] or "—",
                r["pocet_zakaziek"],
                fmt_eur(r["celkovy_vydavok"])
            )
            for r in buyer_rows
        ],
        [45, 10, 8, 20]
    )

    # 5d: Most common CPV codes
    subheader("Top 15 najčastejších CPV kódov")
    cpv_rows = conn.execute("""
        SELECT
            z.cpv_kod,
            COUNT(*)               AS pocet,
            ROUND(SUM(v.celkova_hodnota)) AS celkova_hodnota
        FROM zakazky z
        LEFT JOIN vysledky v ON v.doc_id = z.doc_id
        WHERE z.cpv_kod IS NOT NULL
        GROUP BY z.cpv_kod
        ORDER BY pocet DESC
        LIMIT 15
    """).fetchall()

    print_table(
        ["CPV kód", "Počet", "Celková hodnota"],
        [
            (
                (r["cpv_kod"] or "—")[:50],
                r["pocet"],
                fmt_eur(r["celkova_hodnota"])
            )
            for r in cpv_rows
        ],
        [50, 6, 20]
    )

    result = {
        "hodnota_podla_roka": [
            {
                "rok": r["rok"],
                "pocet_zakaziek": r["pocet_zakaziek"],
                "celkova_hodnota": r["celkova_hodnota"],
                "priemerna_hodnota": r["priemerna_hodnota"]
            }
            for r in year_rows
        ],
        "top_vitazi": [
            {
                "nazov": r["nazov"],
                "ico": r["ico"],
                "pocet_vyher": r["pocet_vyher"],
                "celkova_cena": r["celkova_cena"]
            }
            for r in winner_rows
        ],
        "top_obstaravatelia": [
            {
                "nazov": r["nazov"],
                "ico": r["ico"],
                "pocet_zakaziek": r["pocet_zakaziek"],
                "celkovy_vydavok": r["celkovy_vydavok"]
            }
            for r in buyer_rows
        ],
        "top_cpv": [
            {
                "cpv_kod": r["cpv_kod"],
                "pocet": r["pocet"],
                "celkova_hodnota": r["celkova_hodnota"]
            }
            for r in cpv_rows
        ]
    }
    return result


# ─── DB quick stats ───────────────────────────────────────────────────────────

def print_db_stats(conn: sqlite3.Connection):
    header("STAV DATABÁZY")
    tables = [
        "dokumenty", "obstaravatelia", "zakazky",
        "vysledky", "ucastnici", "prilezitosti",
        "zmeny_zmluv", "zmluvy"
    ]
    for t in tables:
        row = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()
        print(f"  {t:<20} {row[0]:>6} záznamov")


# ─── main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Vestník pattern-detection analyza")
    parser.add_argument("--db", default=str(DEFAULT_DB), help="Cesta k SQLite databáze")
    parser.add_argument("--no-import", action="store_true", help="Nepokúšať sa importovať JSON")
    args = parser.parse_args()

    db_path = Path(args.db)

    print(f"Vestník — analytický nástroj")
    print(f"DB: {db_path}")
    print(f"Správa: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    conn = ensure_db(db_path)
    print_db_stats(conn)

    # Run all analyses
    report = {
        "generated_at": datetime.now().isoformat(),
        "db_path": str(db_path),
    }

    try:
        report["single_bidder"] = analysis_single_bidder(conn)
    except Exception as e:
        print(f"[chyba] single_bidder: {e}")
        report["single_bidder"] = {"error": str(e)}

    try:
        report["repeated_winners"] = analysis_repeated_winners(conn)
    except Exception as e:
        print(f"[chyba] repeated_winners: {e}")
        report["repeated_winners"] = {"error": str(e)}

    try:
        report["price_anomalies"] = analysis_price_anomalies(conn)
    except Exception as e:
        print(f"[chyba] price_anomalies: {e}")
        report["price_anomalies"] = {"error": str(e)}

    try:
        report["contract_splitting"] = analysis_contract_splitting(conn)
    except Exception as e:
        print(f"[chyba] contract_splitting: {e}")
        report["contract_splitting"] = {"error": str(e)}

    try:
        report["top_stats"] = analysis_top_stats(conn)
    except Exception as e:
        print(f"[chyba] top_stats: {e}")
        report["top_stats"] = {"error": str(e)}

    # Save JSON report
    REPORT_OUT.parent.mkdir(parents=True, exist_ok=True)
    with open(REPORT_OUT, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2, default=str)

    print()
    print(SEP)
    print(f"  JSON správa uložená: {REPORT_OUT}")
    print(SEP)

    conn.close()


if __name__ == "__main__":
    main()
