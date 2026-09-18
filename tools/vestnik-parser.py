#!/usr/bin/env python3
"""
Deterministický parser pre UVO Vestník dokumenty.
Žiadny LLM, žiadny RAG — čistý regex na konzistentnej HTML štruktúre.

Vstup:  RSS XML + HTML súbory (lokálne alebo download)
Výstup: JSON s extrahovanými údajmi

Usage:
  python3 tools/vestnik-parser.py /tmp/uvo_rss.xml
  python3 tools/vestnik-parser.py                    # fetches RSS from UVO
"""

import json
import os
import re
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from collections import Counter
from dataclasses import dataclass, field, asdict
from typing import Optional

OUT_DIR = Path(__file__).parent.parent / "data" / "results"
# Input: RSS XML, vestník listing URL, local HTML directory, or vestník number (e.g. "190/2026")
INPUT = sys.argv[1] if len(sys.argv) > 1 else "https://www.uvo.gov.sk/vestnik-a-registre/vestnik/rss"

CODE_ACTION = {
    "VST": "vysledok", "VSS": "vysledok", "VSP": "vysledok",
    "VUT": "vysledok", "VUS": "vysledok", "VUP": "vysledok",
    "IPT": "vysledok", "IPS": "vysledok", "IPP": "vysledok",
    "MST": "vyhlasenie", "MSS": "vyhlasenie", "MSP": "vyhlasenie",
    "MUT": "vyhlasenie", "MUS": "vyhlasenie", "MUP": "vyhlasenie",
    "WYT": "vyhlasenie", "WYS": "vyhlasenie", "WYP": "vyhlasenie",
    "IOX": "oprava",
    "DOP": "zmena_zmluvy", "DOT": "zmena_zmluvy", "DOS": "zmena_zmluvy",
    "INT": "suhrn",
}


# ═══════════════════════════════════════════════════════
# HTML → structured items per container
# ═══════════════════════════════════════════════════════

def parse_containers(html: str) -> list[list[str]]:
    """Extract all <ul class="notice-list"> containers, each as a list of text items."""
    containers = []
    for block in re.findall(r'<ul class="notice-list">(.*?)</ul>', html, re.DOTALL):
        items = []
        for li in re.findall(r'<li[^>]*>(.*?)</li>', block, re.DOTALL):
            text = re.sub(r'<[^>]+>', '', li).strip()
            text = re.sub(r'\s+', ' ', text)
            if text:
                items.append(text)
        containers.append(items)
    return containers


def find_field(items: list[str], prefix: str) -> Optional[str]:
    """Find first item starting with prefix, return value after colon."""
    for item in items:
        if item.startswith(prefix):
            val = item[len(prefix):].strip()
            if val.startswith(':'):
                val = val[1:].strip()
            return val
    return None


def find_all_fields(items: list[str], prefix: str) -> list[str]:
    """Find all items starting with prefix."""
    results = []
    for item in items:
        if item.startswith(prefix):
            val = item[len(prefix):].strip()
            if val.startswith(':'):
                val = val[1:].strip()
            results.append(val)
    return results


def find_field_anywhere(containers: list[list[str]], prefix: str) -> Optional[str]:
    """Search across all containers."""
    for items in containers:
        val = find_field(items, prefix)
        if val:
            return val
    return None


# ═══════════════════════════════════════════════════════
# Organization registry from Container 1
# ═══════════════════════════════════════════════════════

def parse_organizations(items: list[str]) -> dict:
    """Parse container 1 into org_id → {name, ico, email, ...} map.
    Handles both explicit ORG-XXXX headers and implicit blocks.
    Also builds name_to_ico for fast lookup."""
    orgs = {}
    current_org = None
    # Track sequential ORG IDs for blocks without explicit header
    next_org_num = 0
    known_org_nums = set()

    # First pass: find all explicitly declared ORG-XXXX
    for item in items:
        m = re.search(r'\((ORG-(\d+))\)', item)
        if m:
            known_org_nums.add(int(m.group(2)))

    # Second pass: parse
    block_count = 0
    for item in items:
        # "Organizácia z kontextu: Názov firmy (ORG-0002)"
        m = re.match(r'Organizácia z kontextu:\s*(?:(.+?)\s+)?\((ORG-\d+)\)', item)
        if m:
            name = m.group(1) or ''
            org_id = m.group(2)
            current_org = org_id
            orgs[org_id] = {'name': name, 'ico': '', 'email': '', 'city': ''}
            continue

        # Just ORG-XXXX without name
        m = re.match(r'Organizácia z kontextu:\s*(ORG-\d+)', item)
        if m:
            current_org = m.group(1)
            orgs[current_org] = {'name': '', 'ico': '', 'email': '', 'city': ''}
            continue

        # "Zoznam organizácii" starts a new org block
        if 'Zoznam organizácii uvedených' in item:
            block_count += 1
            current_org = None
            continue

        # Detect new org by "Názov organizácie:" without prior ORG-XXXX header
        if item.startswith('Názov organizácie:'):
            name = item.split(':', 1)[1].strip()
            if current_org and current_org in orgs and not orgs[current_org]['name']:
                orgs[current_org]['name'] = name
            else:
                # Assign sequential ORG-XXXX based on block position
                # UVO convention: ORG-0001=UVO, ORG-0002=buyer, ORG-0003+=bidders
                org_id = f"ORG-{block_count:04d}"
                current_org = org_id
                if org_id not in orgs:
                    orgs[org_id] = {'name': name, 'ico': '', 'email': '', 'city': ''}
                else:
                    orgs[org_id]['name'] = name
            continue

        if current_org and current_org in orgs:
            if item.startswith('IČO:'):
                orgs[current_org]['ico'] = item.split(':', 1)[1].strip()
            elif item.startswith('E-mail:'):
                orgs[current_org]['email'] = item.split(':', 1)[1].strip()
            elif item.startswith('Mesto:'):
                orgs[current_org]['city'] = item.split(':', 1)[1].strip()

    # Build name→ico index (excluding UVO)
    orgs['_name_to_ico'] = {}
    for org in orgs.values():
        if isinstance(org, dict) and org.get('name') and org.get('ico') and org['ico'] != '31797903':
            orgs['_name_to_ico'][org['name']] = org['ico']

    return orgs


# ═══════════════════════════════════════════════════════
# Extract buyer from Container 2
# ═══════════════════════════════════════════════════════

def extract_buyer(containers: list[list[str]], orgs: dict) -> dict:
    """Extract buyer (kupujúci) info."""
    if len(containers) < 3:
        return {}

    items = containers[2]
    buyer = {'nazov': '', 'ico': '', 'email': ''}

    # "ID kupujúceho: ORG-0002 (Názov)"  or  "ID kupujúceho : ORG-0002 (Názov)"
    for item in items:
        m = re.match(r'ID kupujúceho\s*:\s*(?:(ORG-\d+)\s*\(([^)]+)\)|(ORG-\d+))', item)
        if m:
            org_id = m.group(1) or m.group(3)
            name_from_id = m.group(2) or ''
            if org_id in orgs:
                buyer['nazov'] = orgs[org_id]['name'] or name_from_id
                ico = orgs[org_id]['ico']
                # Skip UVO's own IČO
                buyer['ico'] = ico if ico != '31797903' else ''
                buyer['email'] = orgs[org_id]['email']
            else:
                buyer['nazov'] = name_from_id
            # If buyer IČO is empty, look for it by name
            if not buyer['ico'] and buyer['nazov']:
                name_idx = orgs.get('_name_to_ico', {})
                if buyer['nazov'] in name_idx:
                    buyer['ico'] = name_idx[buyer['nazov']]
            break

    return buyer


# ═══════════════════════════════════════════════════════
# Extract subject from Container 3
# ═══════════════════════════════════════════════════════

def extract_subject(containers: list[list[str]]) -> dict:
    """Extract zákazka info (predmet, CPV, druh)."""
    if len(containers) < 4:
        return {}

    items = containers[3]
    subject = {
        'predmet': '',
        'cpv_kod': '',
        'druh': '',
    }

    subject['predmet'] = find_field(items, 'Názov:') or find_field(items, 'Názov :') or ''
    subject['cpv_kod'] = find_field(items, 'Hlavný CPV kód:') or ''
    subject['druh'] = find_field(items, 'Druh zákazky:') or ''

    return subject


# ═══════════════════════════════════════════════════════
# VÝSLEDOK: Extract winners from Containers 4-5
# ═══════════════════════════════════════════════════════

def extract_vysledok(containers: list[list[str]], orgs: dict) -> dict:
    """Extract result data: winners, values, stats."""
    result = {
        'celkova_hodnota': None,
        'mena': 'EUR',
        'pocet_ponuk': None,
        'ucastnici': [],
    }

    if len(containers) < 6:
        return result

    items5 = containers[5]

    # Total value: "Celková hodnota oznámenia (BT-161-NoticeResult) (hodnota): 990"
    for item in items5:
        m = re.match(r'Celková hodnota oznámenia.*?\(hodnota\):\s*([\d\s,.]+)', item)
        if m:
            result['celkova_hodnota'] = parse_number(m.group(1))
            break

    # Number of tenders: after "Typ prijatých ponúk: Ponuky" → next "Počet: X"
    for i, item in enumerate(items5):
        if 'Typ prijatých ponúk: Ponuky' == item.strip():
            if i + 1 < len(items5):
                m = re.match(r'Počet:\s*(\d+)', items5[i + 1])
                if m:
                    result['pocet_ponuk'] = int(m.group(1))
            break

    # Parse tenders section (6.3 Informácie o ponukách)
    # Pattern: ID uchádzača → values → poradie
    tenders = []
    current_tender = None

    for item in items5:
        # "Identifikátor ponuky: ..." = new tender
        m = re.match(r'Identifikátor ponuky:\s*(.+)', item)
        if m:
            if current_tender:
                tenders.append(current_tender)
            current_tender = {'nazov': '', 'ico': '', 'cena': None, 'poradie': None, 'je_vitaz': False}
            continue

        if current_tender is None:
            # Check for winner ID mapping
            m = re.match(r'Identifikátor úspešnej ponuky\s*:\s*(TEN-\d+)', item)
            if m:
                # Mark matching tenders as winners - handled below
                pass
            continue

        # "Poradie ponuky: 1"
        m = re.match(r'Poradie ponuky:\s*(\d+)', item)
        if m:
            current_tender['poradie'] = int(m.group(1))
            continue

        # "Hodnota ponuky (BT-720-Tender) (hodnota): 810 488.62"
        # "Hodnota ponuky po úprave (BT-720-Tender) (hodnota): 580"
        m = re.match(r'Hodnota ponuky.*?\(hodnota\):\s*([\d\s,.]+)', item)
        if m:
            current_tender['cena'] = parse_number(m.group(1))
            continue

        # "ID uchádzača: TPA-0002 (Slovenská autobusová doprava Lučenec, akciová spoločnosť) (uchádzač 2)"
        # "ID uchádzača: ORG-0003 (FEROSTA a spol., s.r.o.)"
        m = re.match(r'ID uchádzača:\s*(?:TPA|ORG)-(\d+)\s*\(([^)]+)\)', item)
        if m:
            name = m.group(2).strip()
            # Remove trailing "(uchádzač N)" suffix
            name = re.sub(r'\s*\(uchádzač\s*\d+\)\s*$', '', name)
            current_tender['nazov'] = name
            # Look up IČO: try ORG-XXXX first, then name index, then fuzzy
            org_key = f"ORG-{m.group(1)}"
            if org_key in orgs and isinstance(orgs[org_key], dict) and orgs[org_key].get('ico') and orgs[org_key]['ico'] != '31797903':
                current_tender['ico'] = orgs[org_key]['ico']
            else:
                name_idx = orgs.get('_name_to_ico', {})
                if name in name_idx:
                    current_tender['ico'] = name_idx[name]
                else:
                    # Fuzzy: check if name contains or is contained in org name
                    for org_name, ico in name_idx.items():
                        if name in org_name or org_name in name:
                            current_tender['ico'] = ico
                            break
            continue

    if current_tender:
        tenders.append(current_tender)

    # Find winning tender IDs
    winning_ids = set()
    for item in items5:
        m = re.match(r'Identifikátor úspešnej ponuky\s*:\s*(TEN-\d+)', item)
        if m:
            winning_ids.add(m.group(1))

    # Mark winners: poradie == 1 → víťaz
    for t in tenders:
        if t.get('poradie') == 1:
            t['je_vitaz'] = True

    # Deduplicate by name (same bidder can appear in multiple lots)
    seen = {}
    for t in tenders:
        key = t['nazov']
        if key in seen:
            # Keep the one with higher value or merge
            if t.get('cena') and (not seen[key].get('cena') or t['cena'] > seen[key]['cena']):
                seen[key]['cena'] = t['cena']
            if t.get('je_vitaz'):
                seen[key]['je_vitaz'] = True
        else:
            seen[key] = t

    result['ucastnici'] = list(seen.values())
    return result


# ═══════════════════════════════════════════════════════
# VYHLÁSENIE: Extract opportunity from Container 4
# ═══════════════════════════════════════════════════════

def extract_vyhlasenie(containers: list[list[str]]) -> dict:
    """Extract opportunity data: value, deadline, conditions."""
    result = {
        'hodnota': None,
        'mena': 'EUR',
        'lehota_datum': '',
        'lehota_cas': '',
        'eu_fond': '',
        'ramcova_dohoda': False,
        'kriterium': '',
    }

    if len(containers) < 5:
        return result

    items4 = containers[4]

    # Value: various BT patterns
    for item in items4:
        m = re.match(r'(?:Predpokladaná hodnota|Celková predpokladaná hodnota).*?\(hodnota\):\s*([\d\s,.]+)', item)
        if m:
            result['hodnota'] = parse_number(m.group(1))
            break

    # If not found in lot, try container 3
    if result['hodnota'] is None and len(containers) > 3:
        for item in containers[3]:
            m = re.match(r'(?:Predpokladaná hodnota|Celková predpokladaná hodnota).*?\(hodnota\):\s*([\d\s,.]+)', item)
            if m:
                result['hodnota'] = parse_number(m.group(1))
                break

    # Deadline
    for item in items4:
        if item.startswith('Lehota na predkladanie ponúk (dátum):'):
            result['lehota_datum'] = item.split(':', 1)[1].strip()
        elif item.startswith('Lehota na predkladanie ponúk (čas):'):
            result['lehota_cas'] = item.split(':', 1)[1].strip()

    # EU fund
    for item in items4:
        if item.startswith('Finančné prostriedky EÚ:'):
            val = item.split(':', 1)[1].strip()
            result['eu_fond'] = val
            break

    # Framework agreement
    for item in items4:
        if item.startswith('Rámcová dohoda:'):
            val = item.split(':', 1)[1].strip()
            result['ramcova_dohoda'] = val != 'Žiadna' and val != 'Nie'
            break

    # Evaluation criteria
    for item in items4:
        if item.startswith('Typ kritéria na vyhodnotenie ponúk:'):
            result['kriterium'] = item.split(':', 1)[1].strip()
            break
        if item.startswith('Opis kritéria na vyhodnotenie ponúk:'):
            result['kriterium'] = item.split(':', 1)[1].strip()[:100]
            break

    return result


# ═══════════════════════════════════════════════════════
# OPRAVA: Extract correction info
# ═══════════════════════════════════════════════════════

def extract_oprava(containers: list[list[str]], orgs: dict) -> dict:
    """Extract from oprava — same structure as vyhlásenie or výsledok."""
    # Opravy have the same container structure
    # Try to detect if it's correcting a výsledok or vyhlásenie
    typ = find_field_anywhere(containers, 'Typ formulára:') or ''

    if 'Výsledok' in typ:
        return extract_vysledok(containers, orgs)
    else:
        return extract_vyhlasenie(containers)


# ═══════════════════════════════════════════════════════
# ZMENA ZMLUVY: Extract contract modification from Containers 4+6
# ═══════════════════════════════════════════════════════

def extract_zmena_zmluvy(containers: list[list[str]], orgs: dict) -> dict:
    """Extract contract modification data: supplier, value, reason."""
    result = {
        'dodavatel': {'nazov': '', 'ico': ''},
        'hodnota_po_zmene': None,
        'mena': 'EUR',
        'zmluva_id': '',
        'zmluva_datum': '',
        'zmluva_url': '',
        'dovod_zmeny': '',
        'odovodnenie': '',
        'zhrnutie': '',
        'eu_fond': '',
    }

    # Container 4: supplier + value
    if len(containers) > 4:
        items4 = containers[4]
        for item in items4:
            # Supplier: "ID uchádzača: ORG-0003 (YUCON, s.r.o.)"
            m = re.match(r'ID uchádzača:\s*(?:TPA|ORG)-\d+\s*\(([^)]+)\)', item)
            if m and not result['dodavatel']['nazov']:
                name = re.sub(r'\s*\(.*$', '', m.group(1)).strip()
                result['dodavatel']['nazov'] = name
                # Lookup IČO
                name_idx = orgs.get('_name_to_ico', {})
                if name in name_idx:
                    result['dodavatel']['ico'] = name_idx[name]
                else:
                    for org_name, ico in name_idx.items():
                        if name in org_name or org_name in name:
                            result['dodavatel']['ico'] = ico
                            break

            # Value after change
            m = re.match(r'Hodnota ponuky.*?\(hodnota\):\s*([\d\s,.]+)', item)
            if m:
                result['hodnota_po_zmene'] = parse_number(m.group(1))

            # Total value
            m = re.match(r'Celková hodnota oznámenia.*?\(hodnota\):\s*([\d\s,.]+)', item)
            if m and result['hodnota_po_zmene'] is None:
                result['hodnota_po_zmene'] = parse_number(m.group(1))

            # Contract ID
            if item.startswith('Identifikátor zmluvy:'):
                result['zmluva_id'] = item.split(':', 1)[1].strip()
            elif item.startswith('Dátum uzavretia zmluvy:'):
                result['zmluva_datum'] = item.split(':', 1)[1].strip()
            elif item.startswith('Odkaz na zverejnenú zmluvu (URL):'):
                result['zmluva_url'] = item.split(':', 1)[1].strip()
                # Fix: URL after split loses protocol
                if not result['zmluva_url'].startswith('http'):
                    result['zmluva_url'] = 'https:' + item.split('https:', 1)[1].strip() if 'https:' in item else result['zmluva_url']

            # EU fund
            if item.startswith('Názov programu alebo fondu'):
                result['eu_fond'] = item.split(':', 1)[1].strip()

    # Container 6: reason + summary
    if len(containers) > 6:
        items6 = containers[6]
        for item in items6:
            if item.startswith('Hlavný dôvod zmeny:'):
                result['dovod_zmeny'] = item.split(':', 1)[1].strip()
            elif item.startswith('Odôvodnenie zmeny zmluvy:'):
                result['odovodnenie'] = item.split(':', 1)[1].strip()[:300]
            elif item.startswith('Zhrnutie zmeny:'):
                result['zhrnutie'] = item.split(':', 1)[1].strip()[:300]

    return result


# ═══════════════════════════════════════════════════════
# Utility
# ═══════════════════════════════════════════════════════

def parse_number(s: str) -> Optional[float]:
    """Parse '810 488.62' or '1 500 000' to float."""
    s = s.strip().replace(' ', '').replace('\xa0', '')
    # Handle both . and , as decimal separator
    if ',' in s and '.' in s:
        s = s.replace(',', '')  # 1,234,567.89
    elif ',' in s:
        # Could be "79 523,57" (SK decimal) or "1,500,000"
        parts = s.split(',')
        if len(parts[-1]) == 2:  # decimal comma
            s = s.replace(',', '.')
        else:
            s = s.replace(',', '')
    try:
        return float(s)
    except ValueError:
        return None


# ═══════════════════════════════════════════════════════
# Main pipeline
# ═══════════════════════════════════════════════════════

def parse_document(html: str, action: str) -> dict:
    """Parse a single UVO HTML document."""
    containers = parse_containers(html)
    if not containers:
        return {"error": "no_containers"}

    orgs = parse_organizations(containers[1]) if len(containers) > 1 else {}
    buyer = extract_buyer(containers, orgs)
    subject = extract_subject(containers)

    result = {
        'obstaravatel': buyer,
        'zakazka': subject,
    }

    if action in ('vysledok', 'suhrn'):
        vysledok = extract_vysledok(containers, orgs)
        result['vysledok'] = vysledok
    elif action == 'vyhlasenie':
        vyhlasenie = extract_vyhlasenie(containers)
        result['prilezitost'] = vyhlasenie
    elif action == 'oprava':
        oprava = extract_oprava(containers, orgs)
        if 'ucastnici' in oprava:
            result['vysledok'] = oprava
        else:
            result['prilezitost'] = oprava
    elif action == 'zmena_zmluvy':
        zmena = extract_zmena_zmluvy(containers, orgs)
        result['zmena_zmluvy'] = zmena

    return result


def docs_from_rss(source: str) -> list[dict]:
    """Parse RSS XML (file or URL) into doc list."""
    if source.startswith("http"):
        import urllib.request
        rss_data = urllib.request.urlopen(source, timeout=15).read()
        root = ET.fromstring(rss_data)
    else:
        root = ET.parse(source).getroot()

    items = root.findall('.//item')
    docs = []
    for item in items:
        title = item.find('title').text or ''
        link = item.find('link').text or ''
        desc = item.find('description').text or ''
        doc_id = link.split('/')[-1].split('?')[0] if '/' in link else ''
        parts = title.split(' - ', 1)
        num = parts[0].strip()
        code = parts[1].split(' : ')[0].strip() if len(parts) > 1 and ' : ' in parts[1] else ''
        doc_type = parts[1].split(' : ')[1].strip()[:60] if len(parts) > 1 and ' : ' in parts[1] else ''
        action = CODE_ACTION.get(code, "unknown")
        docs.append({"num": num, "code": code, "type": doc_type, "action": action, "url": link, "id": doc_id, "vestnik": desc})
    return docs


def docs_from_listing(url: str) -> list[dict]:
    """Parse vestník listing page (HTML) to get doc IDs, then classify from each doc's HTML."""
    import urllib.request
    html = urllib.request.urlopen(url, timeout=15).read().decode('utf-8', errors='ignore')
    ids = sorted(set(re.findall(r'oznamenie/detail/(\d+)', html)))
    # Extract vestník number from URL
    m = re.search(r'order=(\d+).*year=(\d+)', url)
    vestnik = f"VVO {m.group(1)}/{m.group(2)}" if m else ""

    docs = []
    for doc_id in ids:
        doc_url = f"https://www.uvo.gov.sk/vestnik-a-registre/vestnik/oznamenie/detail/{doc_id}"
        docs.append({"num": "", "code": "", "type": "", "action": "unknown", "url": doc_url, "id": doc_id, "vestnik": vestnik})
    return docs


def docs_from_directory(dirpath: str) -> list[dict]:
    """Load docs from a directory of HTML files."""
    docs = []
    for f in sorted(Path(dirpath).glob("*.html")):
        doc_id = f.stem
        docs.append({"num": "", "code": "", "type": "", "action": "unknown",
                      "url": f"https://www.uvo.gov.sk/vestnik-a-registre/vestnik/oznamenie/detail/{doc_id}",
                      "id": doc_id, "vestnik": ""})
    return docs


def classify_from_html(html: str) -> tuple[str, str, str]:
    """Classify document type from its HTML content (Container 0)."""
    containers = parse_containers(html)
    if not containers:
        return "unknown", "", ""

    items0 = containers[0]
    typ_form = find_field(items0, 'Typ formulára:') or ''
    # Map form type to action
    if 'Výsledok' in typ_form:
        action = 'vysledok'
    elif 'Súťaž' in typ_form or 'Predbežné' in typ_form:
        action = 'vyhlasenie'
    elif 'Zmena zmluvy' in typ_form:
        action = 'zmena_zmluvy'
    else:
        action = 'unknown'

    # Get document type name
    typ_ozn = find_field(items0, 'Typ oznámenia:') or ''

    # Check version > 01 = oprava (correction of a previous notice)
    verzia = find_field(items0, 'Verzia oznámenia') or '01'
    verzia = verzia.strip().lstrip(':').strip()
    if verzia != '01' and action in ('vyhlasenie', 'unknown'):
        action = 'oprava'

    # Also check for "Sumarizácia vykonaných opráv" in later containers
    full_text = ' '.join(' '.join(c) for c in containers)
    if 'Sumarizácia vykonaných opráv' in full_text and action == 'unknown':
        action = 'oprava'

    return action, typ_form, typ_ozn


def main():
    print("=" * 60)
    print("VESTNÍK PARSER — Deterministický (bez LLM)")
    print("=" * 60)

    # Determine input mode
    source = INPUT
    local_dir = None

    if re.match(r'^\d+/\d{4}$', source):
        # Format: "190/2026" → build listing URL
        num, year = source.split('/')
        source = f"https://www.uvo.gov.sk/vestnik-a-registre/vestnik?order={num}&year={year}&date="
        local_dir = f"/tmp/vestnik{num}_full"

    if os.path.isdir(source):
        # Local directory with HTML files
        docs = docs_from_directory(source)
        local_dir = source
        print(f"\n📂 {len(docs)} HTML súborov v {source}\n")
    elif 'order=' in source and 'year=' in source:
        # Vestník listing URL
        docs = docs_from_listing(source)
        m = re.search(r'order=(\d+)', source)
        if m and not local_dir:
            local_dir = f"/tmp/vestnik{m.group(1)}_full"
        print(f"\n📡 {len(docs)} dokumentov v listing stránke\n")
    elif source.endswith('.xml') or 'rss' in source:
        # RSS XML
        docs = docs_from_rss(source)
        print(f"\n📡 {len(docs)} dokumentov v RSS\n")
    else:
        print(f"❌ Neznámy vstup: {source}")
        print("   Použitie: python3 vestnik-parser.py [RSS.xml | 190/2026 | /tmp/dir | URL]")
        sys.exit(1)

    # Limit for testing
    max_docs = int(os.environ.get("MAX_DOCS", "0")) or len(docs)
    if max_docs < len(docs):
        print(f"\n⚠️  Obmedzené na {max_docs} dokumentov (MAX_DOCS={max_docs})")
        docs = docs[:max_docs]

    # Process
    results = []
    total_time = 0
    processed = 0
    errors = 0

    for i, doc in enumerate(docs):
        label = f"{doc['num']}-{doc['code']}" if doc['num'] else doc['id']
        print(f"\n[{i+1}/{len(docs)}] {label} | {doc['type'][:50] or '...'}", end=" ", flush=True)

        # Load HTML — try local dir first, then vestnik191_full, then download
        local_path = None
        if local_dir:
            local_path = Path(local_dir) / f"{doc['id']}.html"
        if not local_path or not local_path.exists():
            local_path = Path(f"/tmp/vestnik191_full/{doc['id']}.html")
        if local_path.exists():
            html = local_path.read_text(encoding='utf-8', errors='ignore')
        else:
            import urllib.request
            try:
                html = urllib.request.urlopen(doc["url"], timeout=15).read().decode('utf-8', errors='ignore')
                local_path.parent.mkdir(parents=True, exist_ok=True)
                local_path.write_text(html, encoding='utf-8')
            except Exception as e:
                print(f"❌ FETCH: {e}")
                results.append({**doc, "result": "fetch_error"})
                errors += 1
                continue

        # Auto-classify from HTML if no RSS code
        if doc["action"] == "unknown":
            action, typ_form, typ_ozn = classify_from_html(html)
            doc["action"] = action
            doc["type"] = typ_form

        start = time.time()
        extraction = parse_document(html, doc["action"])
        elapsed = time.time() - start
        total_time += elapsed
        processed += 1

        # Print results
        buyer = extraction.get('obstaravatel', {})
        subject = extraction.get('zakazka', {})

        if doc["action"] in ("vysledok", "suhrn"):
            vs = extraction.get('vysledok', {})
            ucastnici = vs.get('ucastnici', [])
            vitazi = [u for u in ucastnici if u.get('je_vitaz')]
            print(f"| {elapsed*1000:.0f}ms")
            for u in vitazi:
                print(f"  📊 🏆 {u['nazov']} ({u.get('ico','?')}) | {u.get('cena','')} EUR")
            if not vitazi and ucastnici:
                for u in ucastnici:
                    print(f"  📊    {u['nazov']} ({u.get('ico','?')}) | {u.get('cena','')} EUR")
            if not ucastnici:
                ponuk = vs.get('pocet_ponuk', 0)
                print(f"  📊 {ponuk} ponúk, žiadny víťaz")

        elif doc["action"] == "vyhlasenie":
            pr = extraction.get('prilezitost', {})
            hodnota = pr.get('hodnota', '?')
            lehota = pr.get('lehota_datum', '?')
            print(f"| {elapsed*1000:.0f}ms")
            print(f"  📢 💡 {subject.get('predmet','?')[:70]}")
            print(f"       {hodnota} EUR | Lehota: {lehota} {pr.get('lehota_cas','')}")

        elif doc["action"] == "oprava":
            print(f"| {elapsed*1000:.0f}ms")
            if 'vysledok' in extraction:
                vs = extraction['vysledok']
                for u in vs.get('ucastnici', []):
                    mark = "🏆" if u.get('je_vitaz') else "  "
                    print(f"  🔄 {mark} {u['nazov']} | {u.get('cena','')} EUR")
            elif 'prilezitost' in extraction:
                pr = extraction['prilezitost']
                print(f"  🔄 💡 {subject.get('predmet','?')[:70]}")
                print(f"       {pr.get('hodnota','?')} EUR | Lehota: {pr.get('lehota_datum','?')}")

        elif doc["action"] == "zmena_zmluvy":
            zm = extraction.get('zmena_zmluvy', {})
            dod = zm.get('dodavatel', {})
            print(f"| {elapsed*1000:.0f}ms")
            print(f"  📋 {dod.get('nazov','?')} ({dod.get('ico','?')}) | {zm.get('hodnota_po_zmene','')} EUR")
            print(f"       Dôvod: {zm.get('dovod_zmeny','?')[:80]}")
            if zm.get('zhrnutie'):
                print(f"       Zmena: {zm['zhrnutie'][:80]}")

        results.append({**doc, "extraction": extraction, "time_ms": round(elapsed * 1000, 1)})

    # Summary
    print(f"\n{'=' * 60}")
    print(f"VÝSLEDKY — Deterministický parser")
    print(f"{'=' * 60}")
    print(f"Dokumentov: {len(docs)}")
    print(f"Spracovaných: {processed}")
    print(f"Chyby: {errors}")
    print(f"Celkový čas: {total_time*1000:.0f}ms ({total_time:.2f}s)")
    if processed > 0:
        print(f"Priemer: {total_time*1000/processed:.1f}ms/dok")
    print(f"Cena: $0")

    # Compare with LLM
    print(f"\n--- Porovnanie ---")
    print(f"{'Metóda':<30} {'Čas':<15} {'Priemer/dok':<15} {'Cena'}")
    print(f"{'Deterministický parser':<30} {f'{total_time:.1f}s':<15} {f'{total_time*1000/max(processed,1):.0f}ms':<15} $0")
    print(f"{'Claude API':<30} {'584s (9.7 min)':<15} {'10.4s':<15} $2.61")
    print(f"{'Claude CLI':<30} {'809s (13.5 min)':<15} {'14.4s':<15} $0")
    print(f"{'Qwen 7B + RAG':<30} {'4201s (70 min)':<15} {'75s':<15} $0")

    # Action summary
    actions = Counter(d["action"] for d in results if "extraction" in d)
    for a, c in actions.most_common():
        emoji = {"vysledok": "📊", "vyhlasenie": "📢", "oprava": "🔄", "zmena_zmluvy": "📋"}.get(a, "❓")
        print(f"   {emoji} {a}: {c}")

    # Output file name based on vestník
    vestnik_label = ""
    for d in docs:
        if d.get("vestnik"):
            m = re.search(r'(\d+)/(\d+)', d["vestnik"])
            if m:
                vestnik_label = f"_{m.group(1)}_{m.group(2)}"
            break
    if not vestnik_label:
        m = re.search(r'order=(\d+).*year=(\d+)', INPUT)
        if m:
            vestnik_label = f"_{m.group(1)}_{m.group(2)}"

    out_file = OUT_DIR / f"vestnik{vestnik_label}_parser_results.json"
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with open(out_file, "w") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    print(f"\nUložené: {out_file}")


if __name__ == "__main__":
    main()
