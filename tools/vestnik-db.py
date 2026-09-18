#!/usr/bin/env python3
"""
vestnik-db.py — Načíta všetky JSON výsledky parsera do SQLite databázy.

Použitie: python3 tools/vestnik-db.py

Načíta všetky vestnik_*_parser_results.json a vestnik_*_legacy_results.json
z data/results/ a uloží ich do data/vestnik.db.
Idempotentné — bezpečné na opakované spustenie (INSERT OR REPLACE).
"""

import json
import sqlite3
import glob
import re
import os
import sys
from pathlib import Path

# Cesty
SCRIPT_DIR = Path(__file__).parent
PROJECT_DIR = SCRIPT_DIR.parent
RESULTS_DIR = PROJECT_DIR / "data" / "results"
DB_PATH = PROJECT_DIR / "data" / "vestnik.db"


# ─── DDL ─────────────────────────────────────────────────────────────────────

DDL = """
CREATE TABLE IF NOT EXISTS dokumenty (
    id      TEXT PRIMARY KEY,
    vestnik TEXT,
    rok     INTEGER,
    action  TEXT,
    kod     TEXT,
    typ     TEXT,
    url     TEXT
);

CREATE TABLE IF NOT EXISTS obstaravatelia (
    doc_id          TEXT,
    nazov           TEXT,
    ico             TEXT,
    email           TEXT,
    adresa          TEXT,
    psc             TEXT,
    mesto           TEXT,
    telefon         TEXT,
    typ_kupujuceho  TEXT,
    cinnost         TEXT,
    profil_url      TEXT
);

CREATE TABLE IF NOT EXISTS zakazky (
    doc_id          TEXT,
    predmet         TEXT,
    cpv_kod         TEXT,
    druh            TEXT,
    opis            TEXT,
    nuts            TEXT,
    miesto_plnenia  TEXT,
    druh_postupu    TEXT,
    pravny_zaklad   TEXT
);

CREATE TABLE IF NOT EXISTS vysledky (
    doc_id              TEXT,
    celkova_hodnota     REAL,
    mena                TEXT,
    pocet_ponuk         INTEGER,
    elektronicke_ponuky INTEGER
);

CREATE TABLE IF NOT EXISTS ucastnici (
    doc_id          TEXT,
    nazov           TEXT,
    ico             TEXT,
    cena            REAL,
    poradie         INTEGER,
    je_vitaz        BOOLEAN,
    velkost_podniku TEXT
);

CREATE TABLE IF NOT EXISTS prilezitosti (
    doc_id              TEXT,
    hodnota             REAL,
    mena                TEXT,
    lehota_datum        TEXT,
    lehota_cas          TEXT,
    elektronicka_aukcia BOOLEAN,
    trvanie             TEXT,
    eu_fond             TEXT,
    ramcova_dohoda      BOOLEAN,
    kriterium           TEXT
);

CREATE TABLE IF NOT EXISTS zmeny_zmluv (
    doc_id              TEXT,
    dodavatel_nazov     TEXT,
    dodavatel_ico       TEXT,
    hodnota_po_zmene    REAL,
    mena                TEXT,
    zmluva_id           TEXT,
    zmluva_datum        TEXT,
    dovod_zmeny         TEXT,
    zhrnutie            TEXT
);

CREATE TABLE IF NOT EXISTS zmluvy (
    doc_id      TEXT,
    zmluva_id   TEXT,
    datum       TEXT,
    nazov       TEXT,
    url         TEXT
);
"""

INDEXES = """
CREATE INDEX IF NOT EXISTS idx_dokumenty_rok     ON dokumenty (rok);
CREATE INDEX IF NOT EXISTS idx_dokumenty_action  ON dokumenty (action);
CREATE INDEX IF NOT EXISTS idx_obstaravatelia_doc ON obstaravatelia (doc_id);
CREATE INDEX IF NOT EXISTS idx_obstaravatelia_ico ON obstaravatelia (ico);
CREATE INDEX IF NOT EXISTS idx_zakazky_doc       ON zakazky (doc_id);
CREATE INDEX IF NOT EXISTS idx_vysledky_doc      ON vysledky (doc_id);
CREATE INDEX IF NOT EXISTS idx_ucastnici_doc     ON ucastnici (doc_id);
CREATE INDEX IF NOT EXISTS idx_ucastnici_ico     ON ucastnici (ico);
CREATE INDEX IF NOT EXISTS idx_prilezitosti_doc  ON prilezitosti (doc_id);
CREATE INDEX IF NOT EXISTS idx_zmeny_zmluv_doc   ON zmeny_zmluv (doc_id);
CREATE INDEX IF NOT EXISTS idx_zmeny_zmluv_ico   ON zmeny_zmluv (dodavatel_ico);
CREATE INDEX IF NOT EXISTS idx_zmluvy_doc        ON zmluvy (doc_id);
"""


# ─── Pomocné funkcie ─────────────────────────────────────────────────────────

def extract_rok(vestnik: str, filename: str) -> int | None:
    """Extrahuje rok z vestnik stringu (napr. 'VVO 191/2026' → 2026)
    alebo z názvu súboru ako záložné riešenie."""
    if vestnik:
        m = re.search(r"/(\d{4})", vestnik)
        if m:
            return int(m.group(1))
    # záložné riešenie: z názvu súboru (napr. vestnik_191_2026_parser_results.json)
    m = re.search(r"_(\d{4})_", filename)
    if m:
        return int(m.group(1))
    return None


def is_error_doc(doc: dict) -> bool:
    """Vráti True ak dokument obsahuje chybu extrakcie."""
    ext = doc.get("extraction", {})
    if not isinstance(ext, dict):
        return True
    if "error" in ext:
        return True
    if doc.get("result") in ("fetch_error", "error"):
        return True
    # prázdna extrakcia (žiadne kľúčové sekcie)
    known_sections = {"obstaravatel", "zakazka", "vysledok", "prilezitost",
                      "zmena_zmluvy", "metadata"}
    if ext and not any(k in ext for k in known_sections):
        return True
    return False


def safe_bool(val) -> int | None:
    """Konvertuje rôzne hodnoty na SQLite boolean (0/1)."""
    if val is None:
        return None
    if isinstance(val, bool):
        return int(val)
    if isinstance(val, int):
        return val
    if isinstance(val, str):
        return 1 if val.lower() in ("true", "1", "ano", "áno", "yes") else 0
    return None


def safe_real(val) -> float | None:
    """Konvertuje hodnotu na float alebo None."""
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def safe_int(val) -> int | None:
    """Konvertuje hodnotu na int alebo None."""
    if val is None:
        return None
    try:
        return int(val)
    except (TypeError, ValueError):
        return None


def trvanie_str(p: dict) -> str | None:
    """Zostaví trvanie reťazec z trvanie_mesiace/trvanie_dni alebo trvanie."""
    # nový formát: trvanie_mesiace / trvanie_dni
    mesiace = p.get("trvanie_mesiace")
    dni = p.get("trvanie_dni")
    if mesiace is not None:
        return f"{mesiace} mesiacov"
    if dni is not None:
        return f"{dni} dní"
    # starý formát: trvanie ako reťazec
    t = p.get("trvanie")
    if t:
        return str(t)
    return None


# ─── Vkladanie dát ───────────────────────────────────────────────────────────

def insert_doc(cur: sqlite3.Cursor, doc: dict, filename: str):
    """Vloží jeden dokument do všetkých relevantných tabuliek."""
    doc_id  = str(doc.get("id", "")).strip()
    vestnik = doc.get("vestnik", "") or ""
    action  = doc.get("action", "") or ""
    kod     = doc.get("code", "") or doc.get("num", "") or ""
    typ     = doc.get("type", "") or ""
    url     = doc.get("url", "") or ""
    rok     = extract_rok(vestnik, filename)

    # ── dokumenty ────────────────────────────────────────────────────────────
    cur.execute(
        "INSERT OR REPLACE INTO dokumenty (id, vestnik, rok, action, kod, typ, url) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (doc_id, vestnik, rok, action, kod, typ, url),
    )

    ext = doc.get("extraction", {}) or {}

    # ── obstaravatelia ───────────────────────────────────────────────────────
    # Najprv zmaž staré záznamy pre tento doc_id (pre idempotentnosť)
    cur.execute("DELETE FROM obstaravatelia WHERE doc_id = ?", (doc_id,))
    o = ext.get("obstaravatel", {}) or {}
    if o:
        cur.execute(
            "INSERT INTO obstaravatelia "
            "(doc_id, nazov, ico, email, adresa, psc, mesto, telefon, "
            " typ_kupujuceho, cinnost, profil_url) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                doc_id,
                o.get("nazov"),
                o.get("ico"),
                o.get("email"),
                o.get("adresa"),
                o.get("psc"),
                o.get("mesto"),
                o.get("telefon"),
                o.get("typ_kupujuceho"),
                o.get("cinnost"),
                o.get("profil_url"),
            ),
        )

    # ── zakazky ──────────────────────────────────────────────────────────────
    cur.execute("DELETE FROM zakazky WHERE doc_id = ?", (doc_id,))
    z = ext.get("zakazka", {}) or {}
    if z:
        cur.execute(
            "INSERT INTO zakazky "
            "(doc_id, predmet, cpv_kod, druh, opis, nuts, miesto_plnenia, "
            " druh_postupu, pravny_zaklad) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                doc_id,
                z.get("predmet"),
                z.get("cpv_kod"),
                z.get("druh"),
                z.get("opis"),
                z.get("nuts"),
                z.get("miesto_plnenia"),
                z.get("druh_postupu"),
                z.get("pravny_zaklad"),
            ),
        )

    # ── vysledky + ucastnici + zmluvy ────────────────────────────────────────
    cur.execute("DELETE FROM vysledky WHERE doc_id = ?", (doc_id,))
    cur.execute("DELETE FROM ucastnici WHERE doc_id = ?", (doc_id,))
    cur.execute("DELETE FROM zmluvy WHERE doc_id = ?", (doc_id,))
    v = ext.get("vysledok", {}) or {}
    if v:
        cur.execute(
            "INSERT INTO vysledky "
            "(doc_id, celkova_hodnota, mena, pocet_ponuk, elektronicke_ponuky) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                doc_id,
                safe_real(v.get("celkova_hodnota")),
                v.get("mena"),
                safe_int(v.get("pocet_ponuk")),
                safe_int(v.get("elektronicke_ponuky")),
            ),
        )

        for u in (v.get("ucastnici") or []):
            if not u:
                continue
            cur.execute(
                "INSERT INTO ucastnici "
                "(doc_id, nazov, ico, cena, poradie, je_vitaz, velkost_podniku) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    doc_id,
                    u.get("nazov"),
                    u.get("ico"),
                    safe_real(u.get("cena")),
                    safe_int(u.get("poradie")),
                    safe_bool(u.get("je_vitaz")),
                    u.get("velkost_podniku"),
                ),
            )

        for zm in (v.get("zmluvy") or []):
            if not zm:
                continue
            cur.execute(
                "INSERT INTO zmluvy (doc_id, zmluva_id, datum, nazov, url) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    doc_id,
                    zm.get("id"),
                    zm.get("datum"),
                    zm.get("nazov"),
                    zm.get("url"),
                ),
            )

    # ── prilezitosti ─────────────────────────────────────────────────────────
    cur.execute("DELETE FROM prilezitosti WHERE doc_id = ?", (doc_id,))
    p = ext.get("prilezitost", {}) or {}
    if p:
        cur.execute(
            "INSERT INTO prilezitosti "
            "(doc_id, hodnota, mena, lehota_datum, lehota_cas, "
            " elektronicka_aukcia, trvanie, eu_fond, ramcova_dohoda, kriterium) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                doc_id,
                safe_real(p.get("hodnota")),
                p.get("mena"),
                p.get("lehota_datum"),
                p.get("lehota_cas"),
                safe_bool(p.get("elektronicka_aukcia")),
                trvanie_str(p),
                p.get("eu_fond"),
                safe_bool(p.get("ramcova_dohoda")),
                p.get("kriterium"),
            ),
        )

    # ── zmeny_zmluv ──────────────────────────────────────────────────────────
    cur.execute("DELETE FROM zmeny_zmluv WHERE doc_id = ?", (doc_id,))
    zz = ext.get("zmena_zmluvy", {}) or {}
    if zz:
        dodavatel = zz.get("dodavatel", {}) or {}
        cur.execute(
            "INSERT INTO zmeny_zmluv "
            "(doc_id, dodavatel_nazov, dodavatel_ico, hodnota_po_zmene, mena, "
            " zmluva_id, zmluva_datum, dovod_zmeny, zhrnutie) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                doc_id,
                dodavatel.get("nazov"),
                dodavatel.get("ico"),
                safe_real(zz.get("hodnota_po_zmene")),
                zz.get("mena"),
                zz.get("zmluva_id"),
                zz.get("zmluva_datum"),
                zz.get("dovod_zmeny"),
                zz.get("zhrnutie") or zz.get("odovodnenie"),
            ),
        )


# ─── Hlavná logika ───────────────────────────────────────────────────────────

def main():
    # Nájdi všetky cieľové súbory
    pattern_parser = str(RESULTS_DIR / "vestnik_*_parser_results.json")
    pattern_legacy = str(RESULTS_DIR / "vestnik_*_legacy_results.json")
    files = sorted(glob.glob(pattern_parser) + glob.glob(pattern_legacy))

    if not files:
        print(f"Žiadne súbory nenájdené v {RESULTS_DIR}", file=sys.stderr)
        sys.exit(1)

    print(f"Nájdených {len(files)} súborov v {RESULTS_DIR}")

    # Vytvor / otvor databázu
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(DB_PATH))
    cur = con.cursor()

    # Vytvor tabuľky a indexy
    cur.executescript(DDL)
    cur.executescript(INDEXES)
    con.commit()

    total_loaded = 0
    total_skipped = 0
    per_file_stats: list[tuple[str, int, int]] = []

    for filepath in files:
        filename = os.path.basename(filepath)
        try:
            with open(filepath, encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            print(f"  PRESKOČENÝ (chyba čítania): {filename}: {e}", file=sys.stderr)
            continue

        if not isinstance(data, list):
            print(f"  PRESKOČENÝ (neočakávaný formát): {filename}", file=sys.stderr)
            continue

        loaded = 0
        skipped = 0

        for doc in data:
            if not isinstance(doc, dict):
                skipped += 1
                continue
            # Preskočiť dokumenty s chybou extrakcie
            if is_error_doc(doc):
                skipped += 1
                continue
            # Preskočiť dokumenty bez ID
            if not doc.get("id"):
                skipped += 1
                continue
            # Preskočiť dokumenty s formátom 'extractions' (starý CLI výstup)
            if "extractions" in doc and "extraction" not in doc:
                skipped += 1
                continue

            try:
                insert_doc(cur, doc, filename)
                loaded += 1
            except Exception as e:
                print(f"  CHYBA vkladania doc {doc.get('id')}: {e}", file=sys.stderr)
                skipped += 1

        con.commit()
        total_loaded += loaded
        total_skipped += skipped
        per_file_stats.append((filename, loaded, skipped))
        print(f"  {filename}: {loaded} načítaných, {skipped} preskočených")

    con.close()

    # ── Súhrn ────────────────────────────────────────────────────────────────
    print()
    print("=" * 60)
    print("SÚHRN")
    print("=" * 60)
    print(f"Databáza:           {DB_PATH}")
    print(f"Celkovo načítaných: {total_loaded} dokumentov")
    print(f"Celkovo preskočených: {total_skipped} dokumentov")
    print()

    # Počty záznamov v tabuľkách
    con2 = sqlite3.connect(str(DB_PATH))
    cur2 = con2.cursor()

    tables = [
        "dokumenty", "obstaravatelia", "zakazky", "vysledky",
        "ucastnici", "prilezitosti", "zmeny_zmluv", "zmluvy",
    ]
    print("Počty záznamov v tabuľkách:")
    for tbl in tables:
        cur2.execute(f"SELECT COUNT(*) FROM {tbl}")
        cnt = cur2.fetchone()[0]
        print(f"  {tbl:<22} {cnt:>6}")

    print()
    print("Rozdelenie podľa roku:")
    cur2.execute(
        "SELECT rok, COUNT(*) AS cnt FROM dokumenty "
        "WHERE rok IS NOT NULL GROUP BY rok ORDER BY rok"
    )
    for row in cur2.fetchall():
        print(f"  {row[0]}: {row[1]} dokumentov")

    print()
    print("Rozdelenie podľa akcie:")
    cur2.execute(
        "SELECT action, COUNT(*) AS cnt FROM dokumenty "
        "GROUP BY action ORDER BY cnt DESC"
    )
    for row in cur2.fetchall():
        print(f"  {row[0] or '(prázdna)':<20} {row[1]:>5}")

    con2.close()
    print()
    print("Hotovo.")


if __name__ == "__main__":
    main()
