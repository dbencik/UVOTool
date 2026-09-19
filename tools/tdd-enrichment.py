#!/usr/bin/env python3
"""
tdd-enrichment.py — Obohacuje TDD (Tax Data Document) XML o firemne data zo slovenskych registrov.

TDD je Peppol XML obsahujuci fakturu s IC DPH dodavatela a odberatela.
Tento modul extrahuje IC DPH, preloži ich na ICO a obohati o data z FRSR, ORSF, RUZ.

Pouzitie:
  python3 tools/tdd-enrichment.py /path/to/tdd.xml                    # jeden TDD subor
  python3 tools/tdd-enrichment.py /path/to/tdds/                      # cely adresar
  python3 tools/tdd-enrichment.py --supplier SK2012345678 --customer SK2098765432  # priamo IC DPH
  python3 tools/tdd-enrichment.py /path/to/tdd.xml --level full       # plny enrichment (ORSF+RUZ)

Urovne:
  fast (default): IC DPH → ICO + nazov + spolahliv + dlznik  (in-memory, <0.1ms)
  full:           + ORSF + RUZ data (SQLite queries, ~5ms)
"""

import argparse
import json
import os
import sqlite3
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from datetime import datetime

# ─── Cesty ──────────────────────────────────────────────────────────────────

SCRIPT_DIR = Path(__file__).parent
PROJECT_DIR = SCRIPT_DIR.parent
DB_PATH = PROJECT_DIR / "data" / "vestnik.db"

# ─── XML Namespaces ─────────────────────────────────────────────────────────

NS = {
    "pxs": "urn:peppol:schema:sk-taxdata:1.0",
    "cbc": "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2",
    "cac": "urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2",
}


# ─── TDDEnricher ─────────────────────────────────────────────────────────────

class TDDEnricher:
    """Singleton enrichment engine for TDD XML documents.

    Pre-loads in-memory lookup dicts at startup for O(1) IC DPH → ICO translation.
    """

    _instance = None

    @classmethod
    def get_instance(cls, db_path=None):
        """Return singleton instance, creating it if needed."""
        if cls._instance is None:
            cls._instance = cls(db_path or str(DB_PATH))
        return cls._instance

    def __init__(self, db_path: str):
        """Load SQLite, build in-memory lookup dicts for speed."""
        self.db_path = db_path
        self.ic_dph_map = {}      # "SK2012345678" → "12345678" (ICO)
        self.ico_names = {}       # "12345678" → "Firma s.r.o."
        self.ico_spolahliv = {}   # "12345678" → "spoľahlivý"
        self.ico_dlznik_fs = {}   # "12345678" → {"je_dlznik": True, "dlh_suma": 123.0}
        self.ico_dlznik_sp = {}   # "12345678" → {"je_dlznik": True, "dlh_suma": 456.0}
        self.ico_dic = {}         # "12345678" → "2012345678"
        self._load_data()

    def _load_data(self):
        """Load all lookup tables into memory."""
        t0 = time.time()
        con = sqlite3.connect(self.db_path)
        con.row_factory = sqlite3.Row

        # IC DPH → ICO mapping from frsr_dphs
        rows = con.execute("SELECT IC_DPH, ICO FROM frsr_dphs WHERE IC_DPH IS NOT NULL AND ICO IS NOT NULL GROUP BY IC_DPH").fetchall()
        for r in rows:
            ic_dph = r["IC_DPH"].strip()
            ico = r["ICO"].strip()
            if ic_dph and ico:
                self.ic_dph_map[ic_dph] = ico

        # ICO → name from frsr_dsrdp
        rows = con.execute("SELECT ICO, NAZOV_DS FROM frsr_dsrdp WHERE ICO IS NOT NULL GROUP BY ICO").fetchall()
        for r in rows:
            ico = r["ICO"].strip()
            nazov = r["NAZOV_DS"]
            if ico and nazov:
                self.ico_names[ico] = nazov.strip()

        # ICO → DIC from frsr_dsrdp
        rows = con.execute("SELECT ICO, DIC FROM frsr_dsrdp WHERE ICO IS NOT NULL AND DIC IS NOT NULL GROUP BY ICO").fetchall()
        for r in rows:
            ico = r["ICO"].strip()
            dic = r["DIC"]
            if ico and dic:
                self.ico_dic[ico] = dic.strip()

        # ICO → spoľahlivosť from frsr_iz_ran
        rows = con.execute("SELECT ICO, IDS FROM frsr_iz_ran WHERE ICO IS NOT NULL GROUP BY ICO").fetchall()
        for r in rows:
            ico = r["ICO"].strip()
            ids = r["IDS"]
            if ico and ids:
                self.ico_spolahliv[ico] = ids.strip()

        # ICO → dlžník FS
        try:
            rows = con.execute("SELECT ico, je_dlznik, dlh_suma, typ_dlhu FROM fs_dlznici").fetchall()
            for r in rows:
                ico = r["ico"].strip() if r["ico"] else ""
                if ico:
                    self.ico_dlznik_fs[ico] = {
                        "je_dlznik": bool(r["je_dlznik"]),
                        "dlh_suma": r["dlh_suma"],
                        "typ_dlhu": r["typ_dlhu"] or "",
                    }
        except sqlite3.OperationalError:
            pass

        # ICO → dlžník SP
        try:
            rows = con.execute("SELECT ico, je_dlznik, dlh_suma, obdobie FROM sp_dlznici").fetchall()
            for r in rows:
                ico = r["ico"].strip() if r["ico"] else ""
                if ico:
                    self.ico_dlznik_sp[ico] = {
                        "je_dlznik": bool(r["je_dlznik"]),
                        "dlh_suma": r["dlh_suma"],
                        "obdobie": r["obdobie"] or "",
                    }
        except sqlite3.OperationalError:
            pass

        con.close()
        elapsed = time.time() - t0
        self._stats = {
            "ic_dph_count": len(self.ic_dph_map),
            "ico_names_count": len(self.ico_names),
            "load_time_ms": round(elapsed * 1000),
        }

    def ic_dph_to_ico(self, ic_dph: str) -> str | None:
        """Translate IC DPH to ICO using in-memory dict.

        Handles normalization:
        - With or without "SK" prefix
        - Whitespace trimming
        """
        if not ic_dph:
            return None
        ic_dph = ic_dph.strip().upper()

        # Try as-is first
        ico = self.ic_dph_map.get(ic_dph)
        if ico:
            return ico

        # Try with SK prefix
        if not ic_dph.startswith("SK"):
            ico = self.ic_dph_map.get(f"SK{ic_dph}")
            if ico:
                return ico

        # Try without SK prefix
        if ic_dph.startswith("SK"):
            ico = self.ic_dph_map.get(ic_dph[2:])
            if ico:
                return ico

        return None

    def _enrich_party_fast(self, ic_dph: str) -> dict:
        """Fast enrichment: IC DPH → ICO + name + spolahliv + dlznik. All in-memory, <0.1ms."""
        result = {"ic_dph": ic_dph}

        ico = self.ic_dph_to_ico(ic_dph)
        if not ico:
            result["ico"] = None
            result["error"] = "IC DPH not found in Slovak registers"
            return result

        result["ico"] = ico
        result["nazov"] = self.ico_names.get(ico, "")
        result["dic"] = self.ico_dic.get(ico, "")
        result["spolahliv"] = self.ico_spolahliv.get(ico, "")

        # Debtor status
        fs = self.ico_dlznik_fs.get(ico)
        sp = self.ico_dlznik_sp.get(ico)
        result["je_dlznik_fs"] = fs["je_dlznik"] if fs else False
        result["je_dlznik_sp"] = sp["je_dlznik"] if sp else False
        result["je_dlznik"] = result["je_dlznik_fs"] or result["je_dlznik_sp"]

        return result

    def _enrich_party_full(self, ic_dph: str) -> dict:
        """Full enrichment: fast + ORSF/RUZ data from firmy table."""
        result = self._enrich_party_fast(ic_dph)
        ico = result.get("ico")
        if not ico:
            return result

        # Query firmy table for ORSF+RUZ cached data
        try:
            con = sqlite3.connect(self.db_path)
            con.row_factory = sqlite3.Row
            row = con.execute("SELECT * FROM firmy WHERE ico = ?", (ico,)).fetchone()
            con.close()

            if row:
                row = dict(row)
                result["trzby"] = row.get("trzby_posledne")
                result["trzby_predosle"] = row.get("trzby_predosle")
                result["zisk"] = row.get("zisk_posledne")
                result["zisk_predosle"] = row.get("zisk_predosle")
                result["rok_zavierky"] = row.get("rok_zavierky")
                result["status"] = row.get("status", "")
                result["pravna_forma"] = row.get("pravna_forma", "")
                result["nace"] = row.get("nace", "")
                result["velkost"] = row.get("velkost", "")
                result["mesto"] = row.get("mesto", "")
                result["adresa"] = row.get("adresa", "")
            else:
                # Trigger enrichment via company-enrichment.py (lazy)
                try:
                    import subprocess
                    enrichment_script = str(SCRIPT_DIR / "company-enrichment.py")
                    if os.path.exists(enrichment_script):
                        subprocess.run(
                            [sys.executable, enrichment_script, ico],
                            capture_output=True, timeout=30
                        )
                        # Re-read
                        con = sqlite3.connect(self.db_path)
                        con.row_factory = sqlite3.Row
                        row = con.execute("SELECT * FROM firmy WHERE ico = ?", (ico,)).fetchone()
                        con.close()
                        if row:
                            row = dict(row)
                            result["trzby"] = row.get("trzby_posledne")
                            result["zisk"] = row.get("zisk_posledne")
                            result["status"] = row.get("status", "")
                            result["pravna_forma"] = row.get("pravna_forma", "")
                except Exception:
                    pass
        except Exception:
            pass

        return result

    def enrich_party(self, ic_dph: str, level: str = "fast") -> dict:
        """Enrich a single party by IC DPH."""
        if level == "full":
            return self._enrich_party_full(ic_dph)
        return self._enrich_party_fast(ic_dph)

    def parse_tdd_xml(self, xml_string: str) -> dict:
        """Parse TDD XML, extract supplier+customer IC DPH and invoice basics.

        Returns dict with keys:
        - uuid, issue_date, document_currency, payable_amount
        - supplier_ic_dph, customer_ic_dph
        - supplier_country, customer_country
        - reporting_party_dic
        - document_id, document_type_code
        """
        result = {}
        try:
            root = ET.fromstring(xml_string)
        except ET.ParseError as e:
            return {"error": f"XML parse error: {e}"}

        # Top-level TDD fields
        result["uuid"] = _find_text(root, "cbc:UUID")
        result["issue_date"] = _find_text(root, "cbc:IssueDate")

        # Reporting party DIC
        rp = _find_el(root, "pxs:ReportingParty")
        if rp is not None:
            result["reporting_party_dic"] = _find_text(rp, "cbc:EndpointID")

        # ReportedDocument (inside ReportedTransaction)
        rd = _find_el(root, ".//pxs:ReportedDocument")
        if rd is None:
            result["error"] = "No ReportedDocument found"
            return result

        result["document_id"] = _find_text(rd, "cbc:ID")
        result["document_uuid"] = _find_text(rd, "cbc:UUID")
        result["document_issue_date"] = _find_text(rd, "cbc:IssueDate")
        result["document_type_code"] = _find_text(rd, "pxs:DocumentTypeCode")
        result["document_currency"] = _find_text(rd, "cbc:DocumentCurrencyCode")

        # Supplier IC DPH
        supplier = _find_el(rd, ".//cac:AccountingSupplierParty//cac:PartyTaxScheme/cbc:CompanyID")
        result["supplier_ic_dph"] = supplier.text.strip() if supplier is not None and supplier.text else ""

        # Supplier country
        supplier_country = _find_el(rd, ".//cac:AccountingSupplierParty//cac:Country/cbc:IdentificationCode")
        result["supplier_country"] = supplier_country.text.strip() if supplier_country is not None and supplier_country.text else ""

        # Customer IC DPH
        customer = _find_el(rd, ".//cac:AccountingCustomerParty//cac:PartyTaxScheme/cbc:CompanyID")
        result["customer_ic_dph"] = customer.text.strip() if customer is not None and customer.text else ""

        # Customer country
        customer_country = _find_el(rd, ".//cac:AccountingCustomerParty//cac:Country/cbc:IdentificationCode")
        result["customer_country"] = customer_country.text.strip() if customer_country is not None and customer_country.text else ""

        # Customer registration name
        customer_name = _find_el(rd, ".//cac:AccountingCustomerParty//cac:PartyLegalEntity/cbc:RegistrationName")
        result["customer_name"] = customer_name.text.strip() if customer_name is not None and customer_name.text else ""

        # Payable amount — try multiple paths
        payable = _find_el(rd, ".//pxs:MonetaryTotal/cbc:PayableAmount")
        if payable is None:
            payable = _find_el(rd, ".//cac:LegalMonetaryTotal/cbc:PayableAmount")
        if payable is not None and payable.text:
            try:
                result["payable_amount"] = float(payable.text.strip())
            except ValueError:
                result["payable_amount"] = None
            result["payable_currency"] = payable.get("currencyID", "")
        else:
            result["payable_amount"] = None

        # Tax amount
        tax_amount = _find_el(rd, ".//cac:TaxTotal/cbc:TaxAmount")
        if tax_amount is not None and tax_amount.text:
            try:
                result["tax_amount"] = float(tax_amount.text.strip())
            except ValueError:
                result["tax_amount"] = None

        return result

    def enrich_tdd_xml(self, xml_string: str, level: str = "fast") -> dict:
        """Parse TDD XML, extract supplier+customer IC DPH, enrich both.

        Args:
            xml_string: Raw TDD XML content
            level: "fast" (in-memory only) or "full" (includes ORSF/RUZ)

        Returns:
            Enriched result dict with invoice data + supplier/customer profiles.
        """
        parsed = self.parse_tdd_xml(xml_string)
        if "error" in parsed:
            return parsed

        result = {
            "invoice": {
                "uuid": parsed.get("uuid", ""),
                "document_id": parsed.get("document_id", ""),
                "issue_date": parsed.get("document_issue_date") or parsed.get("issue_date", ""),
                "currency": parsed.get("document_currency", ""),
                "payable_amount": parsed.get("payable_amount"),
                "tax_amount": parsed.get("tax_amount"),
                "document_type_code": parsed.get("document_type_code", ""),
            },
            "reporting_party_dic": parsed.get("reporting_party_dic", ""),
        }

        # Enrich supplier
        supplier_ic_dph = parsed.get("supplier_ic_dph", "")
        if supplier_ic_dph:
            result["supplier"] = self.enrich_party(supplier_ic_dph, level)
            result["supplier"]["country"] = parsed.get("supplier_country", "")
        else:
            result["supplier"] = {"ic_dph": "", "error": "No supplier IC DPH in TDD"}

        # Enrich customer
        customer_ic_dph = parsed.get("customer_ic_dph", "")
        if customer_ic_dph:
            result["customer"] = self.enrich_party(customer_ic_dph, level)
            result["customer"]["country"] = parsed.get("customer_country", "")
            # Add registration name from XML if available
            if parsed.get("customer_name") and not result["customer"].get("nazov"):
                result["customer"]["nazov"] = parsed["customer_name"]
        else:
            result["customer"] = {"ic_dph": "", "error": "No customer IC DPH in TDD"}

        result["level"] = level
        result["timestamp"] = datetime.now().isoformat()

        return result

    def enrich_batch(self, tdd_list: list[str], level: str = "fast") -> list[dict]:
        """Enrich multiple TDD XML strings at once."""
        return [self.enrich_tdd_xml(xml, level) for xml in tdd_list]

    def enrich_pair(self, supplier_ic_dph: str, customer_ic_dph: str, level: str = "fast") -> dict:
        """Enrich a supplier+customer pair by IC DPH directly (without XML)."""
        result = {
            "invoice": None,
            "reporting_party_dic": "",
        }

        if supplier_ic_dph:
            result["supplier"] = self.enrich_party(supplier_ic_dph, level)
        else:
            result["supplier"] = {"ic_dph": "", "error": "No supplier IC DPH provided"}

        if customer_ic_dph:
            result["customer"] = self.enrich_party(customer_ic_dph, level)
        else:
            result["customer"] = {"ic_dph": "", "error": "No customer IC DPH provided"}

        result["level"] = level
        result["timestamp"] = datetime.now().isoformat()

        return result

    def enrich_batch_pairs(self, pairs: list[dict], level: str = "fast") -> list[dict]:
        """Enrich multiple supplier+customer pairs.

        Each pair: {"supplier": "SK...", "customer": "SK..."}
        """
        return [self.enrich_pair(p.get("supplier", ""), p.get("customer", ""), level) for p in pairs]

    @property
    def stats(self) -> dict:
        """Return loading statistics."""
        return self._stats


# ─── XML helpers ─────────────────────────────────────────────────────────────

def _find_el(element, path: str):
    """Find element by namespaced path, fallback to plain tag."""
    el = element.find(path, NS)
    if el is not None:
        return el
    # Fallback: strip namespaces from path
    import re as _re
    plain = _re.sub(r'\w+:', '', path)
    el = element.find(plain)
    if el is not None:
        return el
    el = element.find(".//" + plain.lstrip("./"))
    return el


def _find_text(element, path: str) -> str:
    """Find element by namespaced path and return its text, or empty string.
    Falls back to plain tag name (no namespace) if not found."""
    el = element.find(path, NS)
    if el is not None and el.text:
        return el.text.strip()
    # Fallback: try without namespace (plain XML)
    plain = path.split(":")[-1] if ":" in path else path
    el = element.find(plain)
    if el is not None and el.text:
        return el.text.strip()
    # Try with .// prefix
    el = element.find(".//" + plain)
    if el is not None and el.text:
        return el.text.strip()
    return ""


# ─── Formatting ──────────────────────────────────────────────────────────────

def _spolahliv_label(val: str) -> str:
    if not val:
        return "neznámy"
    return val


def _dlznik_label(je_dlznik: bool) -> str:
    return "DLŽNÍK" if je_dlznik else "bez dlhov"


def _format_eur(val) -> str:
    if val is None:
        return "N/A"
    return f"{val:,.2f} EUR".replace(",", " ").replace(".", ",")


# ─── CLI ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="TDD Enrichment — obohacuje Peppol TDD XML o firemne data"
    )
    parser.add_argument("path", nargs="?", help="Cesta k TDD XML suboru alebo adresaru")
    parser.add_argument("--supplier", help="IC DPH dodavatela (napr. SK2012345678)")
    parser.add_argument("--customer", help="IC DPH odberatela (napr. SK2098765432)")
    parser.add_argument("--level", choices=["fast", "full"], default="fast",
                        help="Uroven enrichmentu: fast (default) alebo full")
    parser.add_argument("--json", action="store_true", help="Vystup ako JSON")
    parser.add_argument("--db", default=str(DB_PATH), help="Cesta k SQLite databaze")
    args = parser.parse_args()

    if not args.path and not args.supplier:
        parser.print_help()
        sys.exit(1)

    if not os.path.exists(args.db):
        print(f"CHYBA: Databaza {args.db} neexistuje.", file=sys.stderr)
        sys.exit(1)

    # Initialize enricher
    t0 = time.time()
    enricher = TDDEnricher(args.db)
    stats = enricher.stats
    print(f"TDD Enrichment — loaded {stats['ic_dph_count']:,} IC DPH mappings, "
          f"{stats['ico_names_count']:,} company names ({stats['load_time_ms']}ms)")
    print()

    # Mode 1: Direct IC DPH pair
    if args.supplier or args.customer:
        result = enricher.enrich_pair(args.supplier or "", args.customer or "", args.level)
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            _print_pair_result(result)
        return

    # Mode 2: File or directory
    path = Path(args.path)
    if not path.exists():
        print(f"CHYBA: {path} neexistuje.", file=sys.stderr)
        sys.exit(1)

    if path.is_file():
        files = [path]
    elif path.is_dir():
        files = sorted(path.glob("*.xml"))
    else:
        print(f"CHYBA: {path} nie je subor ani adresar.", file=sys.stderr)
        sys.exit(1)

    if not files:
        print(f"Ziadne XML subory v {path}")
        sys.exit(0)

    results = []
    t_start = time.time()

    for i, f in enumerate(files, 1):
        xml_content = f.read_text(encoding="utf-8")
        result = enricher.enrich_tdd_xml(xml_content, args.level)
        result["filename"] = f.name
        results.append(result)

        if not args.json:
            _print_tdd_result(i, f.name, result)

    elapsed = time.time() - t_start

    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        print(f"\n{'=' * 70}")
        print(f"Spracovanych: {len(results)} TDD ({elapsed * 1000:.0f}ms, "
              f"{elapsed / max(len(results), 1) * 1000:.1f}ms/dok)")


def _print_tdd_result(index: int, filename: str, result: dict):
    """Print a single TDD enrichment result to console."""
    print(f"[{index}] {filename}")

    supplier = result.get("supplier", {})
    customer = result.get("customer", {})
    invoice = result.get("invoice", {})

    # Supplier line
    s_ic_dph = supplier.get("ic_dph", "?")
    s_ico = supplier.get("ico", "?")
    s_nazov = supplier.get("nazov", "")
    s_spolahliv = _spolahliv_label(supplier.get("spolahliv", ""))
    s_dlznik = _dlznik_label(supplier.get("je_dlznik", False))

    if supplier.get("error"):
        print(f"  Supplier: {s_ic_dph} — {supplier['error']}")
    else:
        print(f"  Supplier: {s_ic_dph} -> ICO {s_ico} ({s_nazov}) | {s_spolahliv} | {s_dlznik}")

    # Customer line
    c_ic_dph = customer.get("ic_dph", "?")
    c_ico = customer.get("ico", "?")
    c_nazov = customer.get("nazov", "")
    c_spolahliv = _spolahliv_label(customer.get("spolahliv", ""))
    c_dlznik = _dlznik_label(customer.get("je_dlznik", False))

    if customer.get("error"):
        print(f"  Customer: {c_ic_dph} — {customer['error']}")
    else:
        print(f"  Customer: {c_ic_dph} -> ICO {c_ico} ({c_nazov}) | {c_spolahliv} | {c_dlznik}")

    # Invoice line
    amount = invoice.get("payable_amount")
    currency = invoice.get("currency", "EUR")
    date = invoice.get("issue_date", "")
    if amount is not None:
        print(f"  Invoice: {_format_eur(amount)} | {date}")
    elif date:
        print(f"  Invoice: {date}")

    print()


def _print_pair_result(result: dict):
    """Print a direct IC DPH pair enrichment result."""
    supplier = result.get("supplier", {})
    customer = result.get("customer", {})

    print("Supplier:")
    if supplier.get("error"):
        print(f"  {supplier.get('ic_dph', '?')} — {supplier['error']}")
    else:
        print(f"  IC DPH:      {supplier.get('ic_dph', '?')}")
        print(f"  ICO:         {supplier.get('ico', '?')}")
        print(f"  Nazov:       {supplier.get('nazov', '?')}")
        print(f"  DIC:         {supplier.get('dic', '?')}")
        print(f"  Spolahliv:   {_spolahliv_label(supplier.get('spolahliv', ''))}")
        print(f"  Dlznik:      {_dlznik_label(supplier.get('je_dlznik', False))}")
        if supplier.get("trzby"):
            print(f"  Trzby:       {_format_eur(supplier['trzby'])}")
        if supplier.get("zisk"):
            print(f"  Zisk:        {_format_eur(supplier['zisk'])}")

    print()
    print("Customer:")
    if customer.get("error"):
        print(f"  {customer.get('ic_dph', '?')} — {customer['error']}")
    else:
        print(f"  IC DPH:      {customer.get('ic_dph', '?')}")
        print(f"  ICO:         {customer.get('ico', '?')}")
        print(f"  Nazov:       {customer.get('nazov', '?')}")
        print(f"  DIC:         {customer.get('dic', '?')}")
        print(f"  Spolahliv:   {_spolahliv_label(customer.get('spolahliv', ''))}")
        print(f"  Dlznik:      {_dlznik_label(customer.get('je_dlznik', False))}")
        if customer.get("trzby"):
            print(f"  Trzby:       {_format_eur(customer['trzby'])}")
        if customer.get("zisk"):
            print(f"  Zisk:        {_format_eur(customer['zisk'])}")


if __name__ == "__main__":
    main()
