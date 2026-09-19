#!/usr/bin/env python3
"""
FR SR XML → SQLite importer.

Imports Financial Administration (Financna sprava SR) open data XML datasets
into SQLite tables in data/vestnik.db.

Usage:
    python3 tools/frsr-import.py              # import all datasets
    python3 tools/frsr-import.py --force      # drop & reimport all
    python3 tools/frsr-import.py ds_dph_iban  # import single dataset
    python3 tools/frsr-import.py ds_dsdd --force  # reimport single dataset
"""

import os
import re
import sqlite3
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent
DB_PATH = BASE_DIR / "data" / "vestnik.db"
FRSR_DIR = Path("/Users/dodo/peppol-faktura/data/frsr")

# ---------------------------------------------------------------------------
# Dataset definitions: (dir_name, table_name, xml_data_tag, fields)
# xml_data_tag is the second-level container element (child of root holding ITEMs)
# ---------------------------------------------------------------------------

DATASETS = {
    "ds_dph_iban": {
        "table": "frsr_dph_iban",
        "data_tag": "DS_DPH_IBAN",
        "fields": ["IC_DPH", "ICO", "IBAN", "NAZOV_SUBJEKTU", "OBEC", "ULICA_CISLO", "PSC", "STAT"],
    },
    "ds_dphs": {
        "table": "frsr_dphs",
        "data_tag": "DS_DPHS",
        "fields": ["IC_DPH", "ICO", "NAZOV_DS", "OBEC", "PSC", "ULICA_CISLO", "STAT",
                    "DRUH_REG_DPH", "DATUM_REG", "PLAT_DPH_OD"],
    },
    "ds_dphz": {
        "table": "frsr_dphz",
        "data_tag": "DS_DPHZ",
        "fields": ["IC_DPH", "ICO", "NAZOV", "OBEC", "PSC", "ADRESA",
                    "ROK_PORUSENIA", "DAT_ZVEREJNENIA"],
    },
    "ds_dphv": {
        "table": "frsr_dphv",
        "data_tag": "DS_DPHV",
        "fields": ["IC_DPH", "ICO", "NAZOV", "OBEC", "PSC", "ADRESA",
                    "ROK_PORUSENIA", "DAT_ZVEREJNENIA", "DAT_VYMAZU"],
    },
    "ds_dpho": {
        "table": "frsr_dpho",
        "data_tag": "DS_DPHO",
        "fields": ["IC_DPH", "ICO", "NAZOV", "OBEC", "PSC", "ADRESA",
                    "DAT_ZACATIA", "DAT_SKONCENIA", "ZDAN_OBDOBIE"],
    },
    "ds_dphno": {
        "table": "frsr_dphno",
        "data_tag": "DS_DPHNO",
        "fields": ["DIC", "IC_DPH", "ICO", "NAZOV_DS", "OBEC", "PSC", "ULICA_CISLO", "STAT",
                    "ZDAN_OBDOBIE", "NADMERNY_ODPOCET", "VLAST_DAN_POV"],
    },
    "ds_dsdd": {
        "table": "frsr_dsdd",
        "data_tag": "DS_DSDD",
        "fields": ["NAZOV_SUBJEKTU", "CIASTKA", "ULICA_CISLO", "PSC", "OBEC"],
    },
    "ds_dsrdp": {
        "table": "frsr_dsrdp",
        "data_tag": "DS_DSRDP",
        "fields": ["DIC", "ICO", "NAZOV_DS", "OBEC", "PSC", "ULICA_CISLO", "NAZOV_STATU"],
    },
    "ds_iz_ran": {
        "table": "frsr_iz_ran",
        "data_tag": "DS_IZ_RAN",
        "fields": ["DIC", "ICO", "IDS", "NAZOV_SUBJEKTU", "OBEC", "PSC", "ULICA_CISLO", "STAT"],
    },
}

# Columns that should be REAL (numeric) in SQLite
NUMERIC_FIELDS = {"CIASTKA", "NADMERNY_ODPOCET", "VLAST_DAN_POV"}


def normalize_name(name: str) -> str:
    """Normalize company name for matching: lowercase, strip, remove quotes and legal suffixes."""
    if not name:
        return ""
    n = name.lower().strip()
    # Remove quotes
    n = n.replace('"', '').replace("'", '').replace('\u201e', '').replace('\u201c', '')
    # Remove common legal form suffixes
    for suffix in [", s.r.o.", " s.r.o.", ", spol. s r.o.", " spol. s r.o.",
                   ", a.s.", " a.s.", ", s. r. o.", " s. r. o.",
                   " spoločnosť s ručením obmedzeným",
                   " v likvidácii", " v konkurze", " \"v likvidácii\"",
                   " - v likvidácii", ", v likvidácii"]:
        if n.endswith(suffix):
            n = n[: -len(suffix)]
    # Collapse whitespace
    n = re.sub(r"\s+", " ", n).strip()
    return n


def create_table(db: sqlite3.Connection, table: str, fields: list[str], force: bool):
    """Create table if not exists (or drop+create if force)."""
    if force:
        db.execute(f"DROP TABLE IF EXISTS {table}")

    cols = []
    for f in fields:
        if f in NUMERIC_FIELDS:
            cols.append(f"{f} REAL")
        else:
            cols.append(f"{f} TEXT")
    # Extra column for dsdd ICO matching
    if table == "frsr_dsdd":
        cols.append("ico_matched TEXT")
        cols.append("nazov_norm TEXT")

    col_str = ", ".join(cols)
    db.execute(f"CREATE TABLE IF NOT EXISTS {table} ({col_str})")


def create_indexes(db: sqlite3.Connection, table: str, fields: list[str]):
    """Create indexes on key lookup columns."""
    index_cols = {"ICO", "DIC", "IC_DPH", "NAZOV_SUBJEKTU"}
    for f in fields:
        if f in index_cols:
            idx_name = f"idx_{table}_{f.lower()}"
            db.execute(f"CREATE INDEX IF NOT EXISTS {idx_name} ON {table}({f})")

    # Special indexes
    if table == "frsr_dsdd":
        db.execute(f"CREATE INDEX IF NOT EXISTS idx_frsr_dsdd_ico_matched ON {table}(ico_matched)")
        db.execute(f"CREATE INDEX IF NOT EXISTS idx_frsr_dsdd_nazov_norm ON {table}(nazov_norm)")
    if table == "frsr_dsrdp":
        db.execute(f"CREATE INDEX IF NOT EXISTS idx_frsr_dsrdp_nazov_ds ON {table}(NAZOV_DS)")


def table_has_data(db: sqlite3.Connection, table: str) -> bool:
    """Check if table exists and has rows."""
    try:
        row = db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
        return row[0] > 0
    except sqlite3.OperationalError:
        return False


def import_dataset(db: sqlite3.Connection, ds_name: str, ds_config: dict, force: bool) -> int:
    """Import a single XML dataset into SQLite. Returns record count."""
    table = ds_config["table"]
    data_tag = ds_config["data_tag"]
    fields = ds_config["fields"]

    # Check idempotency
    if not force and table_has_data(db, table):
        count = db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        print(f"  [{table}] already has {count:,} records — skipping (use --force to reimport)")
        return count

    xml_path = FRSR_DIR / ds_name / f"{ds_name}.xml"
    if not xml_path.exists():
        print(f"  [{table}] XML not found: {xml_path}")
        return 0

    create_table(db, table, fields, force)

    # Build INSERT statement
    if table == "frsr_dsdd":
        placeholders = ", ".join(["?"] * (len(fields) + 2))  # + ico_matched + nazov_norm
        insert_sql = f"INSERT INTO {table} VALUES ({placeholders})"
    else:
        placeholders = ", ".join(["?"] * len(fields))
        insert_sql = f"INSERT INTO {table} VALUES ({placeholders})"

    count = 0
    batch = []
    batch_size = 5000
    t0 = time.time()

    # Stream parse using iterparse
    context = ET.iterparse(str(xml_path), events=("end",))
    for event, elem in context:
        if elem.tag != "ITEM":
            continue

        # Check if this ITEM is under the correct data section
        # (We just match all ITEMs — the structure guarantees they're in the right section)
        values = []
        for f in fields:
            child = elem.find(f)
            text = child.text.strip() if child is not None and child.text else None
            # Convert numeric fields
            if f in NUMERIC_FIELDS and text:
                try:
                    values.append(float(text))
                except ValueError:
                    values.append(None)
            else:
                values.append(text)

        if table == "frsr_dsdd":
            # Add ico_matched (NULL initially) and nazov_norm
            nazov = values[0] if values else ""
            values.append(None)  # ico_matched
            values.append(normalize_name(nazov) if nazov else None)

        batch.append(tuple(values))
        count += 1

        if count % batch_size == 0:
            db.executemany(insert_sql, batch)
            batch = []

        if count % 10000 == 0:
            elapsed = time.time() - t0
            rate = count / elapsed if elapsed > 0 else 0
            print(f"    {count:>10,} records ({rate:,.0f}/s)", flush=True)

        # Free memory
        elem.clear()

    # Insert remaining
    if batch:
        db.executemany(insert_sql, batch)

    db.commit()

    # Create indexes after bulk insert (faster)
    create_indexes(db, table, fields)
    db.commit()

    elapsed = time.time() - t0
    print(f"  [{table}] {count:,} records in {elapsed:.1f}s")
    return count


def enrich_dsdd(db: sqlite3.Connection):
    """Cross-reference frsr_dsdd with frsr_dsrdp to find ICO by company name."""
    print("\n--- DS_DSDD enrichment: matching company names to ICO ---")
    t0 = time.time()

    # Check both tables exist
    try:
        dsdd_count = db.execute("SELECT COUNT(*) FROM frsr_dsdd WHERE ico_matched IS NULL").fetchone()[0]
    except sqlite3.OperationalError:
        print("  frsr_dsdd table not found — skipping enrichment")
        return

    try:
        db.execute("SELECT COUNT(*) FROM frsr_dsrdp").fetchone()
    except sqlite3.OperationalError:
        print("  frsr_dsrdp table not found — skipping enrichment")
        return

    if dsdd_count == 0:
        matched = db.execute("SELECT COUNT(*) FROM frsr_dsdd WHERE ico_matched IS NOT NULL").fetchone()[0]
        print(f"  All records already matched ({matched:,} with ICO)")
        return

    print(f"  {dsdd_count:,} unmatched records in frsr_dsdd")

    # Build name → ICO lookup from dsrdp (normalized)
    print("  Building name→ICO lookup from frsr_dsrdp...")
    name_to_ico = {}
    cursor = db.execute("SELECT NAZOV_DS, ICO FROM frsr_dsrdp WHERE ICO IS NOT NULL")
    lookup_count = 0
    for row in cursor:
        nazov, ico = row
        if nazov and ico:
            norm = normalize_name(nazov)
            if norm and norm not in name_to_ico:
                name_to_ico[norm] = ico
                lookup_count += 1
    print(f"  Lookup table: {lookup_count:,} unique normalized names")

    # Match: exact normalized name
    matched = 0
    batch = []
    cursor = db.execute("SELECT rowid, nazov_norm FROM frsr_dsdd WHERE ico_matched IS NULL AND nazov_norm IS NOT NULL")
    for row in cursor:
        rowid, nazov_norm = row
        ico = name_to_ico.get(nazov_norm)
        if ico:
            batch.append((ico, rowid))
            matched += 1
            if len(batch) >= 5000:
                db.executemany("UPDATE frsr_dsdd SET ico_matched = ? WHERE rowid = ?", batch)
                batch = []

    if batch:
        db.executemany("UPDATE frsr_dsdd SET ico_matched = ? WHERE rowid = ?", batch)
    db.commit()

    elapsed = time.time() - t0
    remaining = dsdd_count - matched
    print(f"  Exact match: {matched:,} / {dsdd_count:,} ({matched*100/dsdd_count:.1f}%)")
    print(f"  Unmatched: {remaining:,}")
    print(f"  Enrichment took {elapsed:.1f}s")


def main():
    args = sys.argv[1:]
    force = "--force" in args
    if force:
        args.remove("--force")

    # Specific dataset or all
    if args:
        targets = [a for a in args if a in DATASETS]
        if not targets:
            print(f"Unknown dataset(s): {args}")
            print(f"Available: {', '.join(DATASETS.keys())}")
            sys.exit(1)
    else:
        targets = list(DATASETS.keys())

    print(f"FR SR XML → SQLite import")
    print(f"  DB: {DB_PATH}")
    print(f"  Source: {FRSR_DIR}")
    print(f"  Datasets: {len(targets)}")
    print(f"  Force: {force}")
    print()

    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(str(DB_PATH))
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA synchronous=NORMAL")

    summary = {}
    t_total = time.time()

    for ds_name in targets:
        ds_config = DATASETS[ds_name]
        print(f"--- Importing {ds_name} → {ds_config['table']} ---")
        count = import_dataset(db, ds_name, ds_config, force)
        summary[ds_config["table"]] = count

    # DS_DSDD enrichment (only if dsdd was imported)
    if "ds_dsdd" in targets and "ds_dsrdp" in targets:
        enrich_dsdd(db)
    elif "ds_dsdd" in targets:
        # dsrdp may already be in DB
        enrich_dsdd(db)

    db.close()

    elapsed = time.time() - t_total
    print(f"\n{'='*60}")
    print(f"Import complete in {elapsed:.1f}s")
    print(f"{'='*60}")
    total_records = 0
    for table, count in summary.items():
        print(f"  {table:25s} {count:>12,}")
        total_records += count
    print(f"  {'TOTAL':25s} {total_records:>12,}")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
