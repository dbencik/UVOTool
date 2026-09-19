#!/usr/bin/env python3
"""
Legacy parser pre UVO Vestník dokumenty (2020 – 227/2023).
Starý HTML formát: <fieldset>, <legend>, ODDIEL I/II/III/IV/V/VI/VII.

Jednorazový nástroj — od ~228/2023 UVO prešlo na nový formát
(parsovaný cez vestnik-parser.py).

Usage:
  python3 tools/vestnik-parser-legacy.py 100/2022
  python3 tools/vestnik-parser-legacy.py /tmp/vestnik_legacy/
"""

import json
import os
import re
import sys
import time
import urllib.request
from pathlib import Path
from collections import Counter
from typing import Optional

OUT_DIR = Path(__file__).parent.parent / "data" / "results"
INPUT = sys.argv[1] if len(sys.argv) > 1 else ""

# Same code→action mapping as the main parser
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
    # Legacy-only codes (2020-2023)
    "INS": "vyhlasenie", "INP": "vyhlasenie", "INT": "suhrn", "INX": "oprava",
    "IVS": "vysledok", "IVT": "vysledok", "IVP": "vysledok", "IVX": "oprava",
    "IES": "vyhlasenie", "IET": "vyhlasenie", "IEP": "vyhlasenie",
    "POS": "vyhlasenie", "POT": "vyhlasenie", "POP": "vyhlasenie",
    "DES": "vyhlasenie", "DET": "vyhlasenie", "DEP": "vyhlasenie",
    "VZT": "vyhlasenie", "VZS": "vyhlasenie", "VZP": "vyhlasenie",
    "WNS": "vyhlasenie", "WNT": "vyhlasenie", "WNP": "vyhlasenie",
    "VBS": "vysledok", "VBT": "vysledok", "VBP": "vysledok",
    "MNA": "vyhlasenie", "MNS": "vyhlasenie", "MNT": "vyhlasenie",
    "ICT": "vyhlasenie", "ICS": "vyhlasenie", "ICP": "vyhlasenie",
    "IEX": "suhrn",
}


# ═══════════════════════════════════════════════════════
# HTML section extraction
# ═══════════════════════════════════════════════════════

def extract_sections(html: str) -> dict[str, str]:
    """Split HTML into ODDIEL sections by <legend> tags."""
    sections = {}
    # Split by fieldset blocks
    for m in re.finditer(r'<fieldset[^>]*>\s*<legend>\s*(.*?)\s*</legend>(.*?)</fieldset>', html, re.DOTALL):
        legend = re.sub(r'\s+', ' ', m.group(1)).strip()
        content = m.group(2)
        # Normalize: "ODDIEL I: ..." → "I"
        sec_match = re.match(r'ODDIEL\s+([IVX]+)', legend)
        if sec_match:
            key = sec_match.group(1)
            # Keep only first match per section (avoid overwriting with later inner fieldsets)
            if key not in sections:
                sections[key] = content
        elif 'HLAVIČKA' in legend:
            sections['HLAVICKA'] = content
    return sections


def strip_html(text: str) -> str:
    """Strip HTML tags and normalize whitespace."""
    text = re.sub(r'<br\s*/?>', '\n', text)
    text = re.sub(r'<[^>]+>', ' ', text)
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r'\n\s*\n', '\n', text)
    return text.strip()


# ═══════════════════════════════════════════════════════
# Document type classification
# ═══════════════════════════════════════════════════════

def classify_document(html: str) -> tuple[str, str, str]:
    """Classify document from MainHeader. Returns (action, code, type_name)."""
    headers = re.findall(r'<div class="MainHeader">(.*?)</div>', html)
    if not headers:
        return "unknown", "", ""

    # First header: "22333 - MST"
    code = ""
    num = ""
    m = re.match(r'(\d+)\s*-\s*(\w+)', headers[0])
    if m:
        num = m.group(1)
        code = m.group(2)

    # Second header: full type name
    type_name = headers[1] if len(headers) > 1 else ""

    action = CODE_ACTION.get(code, "unknown")
    return action, code, type_name


# ═══════════════════════════════════════════════════════
# Contact extraction (buyer / winner)
# ═══════════════════════════════════════════════════════

def extract_contact(html_block: str) -> dict:
    """Extract name, IČO, email, address, phone, web, DIČ from a ContactSelectList block."""
    contact = {'nazov': '', 'ico': '', 'email': '', 'mesto': '',
               'adresa': '', 'psc': '', 'telefon': '', 'web': '', 'dic': ''}

    m = re.search(r'<span class="bold">(.*?)</span>', html_block)
    if m:
        contact['nazov'] = strip_html(m.group(1)).strip()

    m = re.search(r'(?:identifikačné číslo|IČO):</span>\s*<span>\s*(\d+)', html_block)
    if m:
        contact['ico'] = m.group(1).strip()

    m = re.search(r'Email:</span>\s*<span>\s*([^<]+)', html_block)
    if m:
        contact['email'] = m.group(1).strip()

    m = re.search(r'<text>\s*(.+?)\s*</text>', html_block)
    if m:
        addr = strip_html(m.group(1)).strip()
        contact['adresa'] = addr
        # Last part after comma is usually the city
        parts = addr.rsplit(',', 1)
        if len(parts) > 1:
            city_part = parts[-1].strip()
            # Strip PSČ prefix from city (e.g. "01109 Žilina" → "Žilina")
            city_clean = re.sub(r'^\d{3}\s?\d{2}\s+', '', city_part)
            contact['mesto'] = city_clean if city_clean else city_part
        # PSČ: 5 digits before city name (pattern: "01109 Žilina" or "010 09")
        psc_m = re.search(r'(\d{3}\s?\d{2})\s+\S', addr)
        if psc_m:
            contact['psc'] = psc_m.group(1).replace(' ', '')

    # Phone
    m = re.search(r'Telefón:</span>\s*<span>\s*([^<]+)', html_block)
    if m:
        contact['telefon'] = m.group(1).strip()

    # Web URL
    m = re.search(r'(?:Hlavná adresa\(URL\)|Webové sídlo):</span>\s*<span>\s*([^<]+)', html_block)
    if not m:
        m = re.search(r'Hlavná adresa\(URL\):</span>\s*<span>\s*([^<]+)', html_block)
    if m:
        contact['web'] = m.group(1).strip()

    # DIČ
    m = re.search(r'DIČ:</span>\s*<span>\s*([^<]+)', html_block)
    if m:
        contact['dic'] = m.group(1).strip()

    return contact


def extract_all_contacts(section_html: str) -> list[dict]:
    """Extract all ContactSelectList blocks from a section (handles nested divs)."""
    contacts = []
    for m in re.finditer(r'<div class="ContactSelectList">', section_html):
        # Find matching closing </div> accounting for nesting
        start = m.end()
        depth = 0
        pos = start
        while pos < len(section_html):
            m_open = re.search(r'<div[\s>]', section_html[pos:])
            m_close = re.search(r'</div>', section_html[pos:])
            if m_close is None:
                break
            if m_open and m_open.start() < m_close.start():
                depth += 1
                pos += m_open.end()
            elif depth > 0:
                depth -= 1
                pos += m_close.end()
            else:
                block = section_html[start:pos + m_close.start()]
                contact = extract_contact(block)
                if contact['nazov']:
                    contacts.append(contact)
                break
    return contacts


# ═══════════════════════════════════════════════════════
# Value extraction
# ═══════════════════════════════════════════════════════

def parse_number(s: str) -> Optional[float]:
    """Parse '810 488,62' or '1 500 000' to float."""
    s = s.strip().replace(' ', '').replace('\xa0', '')
    if ',' in s and '.' in s:
        s = s.replace(',', '')
    elif ',' in s:
        parts = s.split(',')
        if len(parts[-1]) <= 2:
            s = s.replace(',', '.')
        else:
            s = s.replace(',', '')
    try:
        return float(s)
    except ValueError:
        return None


def find_value(section_html: str, label_pattern: str) -> Optional[float]:
    """Find a numeric value after a label in shorttext divs."""
    # Pattern: label text followed by <span>VALUE</span>
    m = re.search(label_pattern + r'.*?<span>\s*([\d\s,\.]+)\s*</span>', section_html, re.DOTALL)
    if m:
        return parse_number(m.group(1))
    return None


# ═══════════════════════════════════════════════════════
# Buyer extraction (ODDIEL I)
# ═══════════════════════════════════════════════════════

def extract_buyer(sections: dict) -> dict:
    """Extract buyer from ODDIEL I."""
    sec = sections.get('I', '')
    if not sec:
        return {'nazov': '', 'ico': '', 'email': ''}

    buyer = {'nazov': '', 'ico': '', 'email': '', 'adresa': '', 'psc': '',
             'mesto': '', 'telefon': '', 'typ_kupujuceho': '', 'cinnost': ''}

    contacts = extract_all_contacts(sec)
    if contacts:
        c = contacts[0]
        buyer['nazov'] = c['nazov']
        buyer['ico'] = c['ico']
        buyer['email'] = c['email']
        buyer['adresa'] = c.get('adresa', '')
        buyer['psc'] = c.get('psc', '')
        buyer['mesto'] = c.get('mesto', '')
        buyer['telefon'] = c.get('telefon', '')

    # Typ kupujúceho: "DRUH VEREJNÉHO OBSTARÁVATEĽA" subsection
    # Pattern 1: dropdownlist or selectList after the title
    m = re.search(r'Druh verejného obstarávateľa[^<]*</span>\s*<span>([^<]+)', sec)
    if m:
        buyer['typ_kupujuceho'] = m.group(1).strip()
    else:
        # Pattern 2: radioButtonList
        m = re.search(r'DRUH VEREJNÉHO OBSTARÁVATEĽA.*?(?:radioButtonList|dropdownlist_|selectList)[^>]*>.*?<span>([^<]+)</span>', sec, re.DOTALL)
        if m:
            val = strip_html(m.group(1)).strip()
            if val:
                buyer['typ_kupujuceho'] = val

    # Hlavná činnosť: shorttext or span after "HLAVNÁ ČINNOSŤ" subtitle
    m = re.search(r'HLAVNÁ ČINNOSŤ.*?(?:shorttext_|dropdownlist_)[^>]*>\s*(?:<span>)?\s*([^<]+)', sec, re.DOTALL)
    if m:
        val = m.group(1).strip()
        if val:
            buyer['cinnost'] = val

    return buyer


# ═══════════════════════════════════════════════════════
# Subject extraction (ODDIEL II)
# ═══════════════════════════════════════════════════════

def extract_lots(sections: dict) -> list[dict]:
    """Best-effort extraction of lots from ODDIEL II in legacy format.
    Returns empty list if only 1 lot (data already in zakazka)."""
    sec_ii = sections.get('II', '')
    if not sec_ii:
        return []

    # Look for repeated II.2 subsection patterns (II.2.1, II.2.2, etc.)
    # Each lot has an ordercomponent (title), textArea (description), selectList (CPV)
    lot_matches = list(re.finditer(
        r'II\.2(?:\.(\d+))?.*?<div class="ordercomponent_[^"]*">\s*(.*?)\s*</div>',
        sec_ii, re.DOTALL))

    if len(lot_matches) <= 1:
        return []  # Single lot or no lot subsections

    lots = []
    for i, m in enumerate(lot_matches):
        lot_num = int(m.group(1)) if m.group(1) else i + 1
        title = strip_html(m.group(2)).strip()

        # Try to find description and CPV after this match, before next match
        end_pos = lot_matches[i + 1].start() if i + 1 < len(lot_matches) else len(sec_ii)
        block = sec_ii[m.start():end_pos]

        # Description from textArea
        opis = ''
        dm = re.search(r'Stručný opis.*?<div class="textArea">\s*<span>.*?</span>\s*<span>(.*?)</span>',
                        block, re.DOTALL)
        if dm:
            opis = strip_html(dm.group(1)).strip()[:300]

        # CPV from selectList
        cpv = ''
        cm = re.search(r'<div class="selectList">.*?<span>(\d{8}-\d)</span>', block, re.DOTALL)
        if cm:
            cpv = cm.group(1)

        # Value
        hodnota = find_value(block, r'(?:Celková odhadovaná hodnota|Odhadovaná hodnota|Predpokladaná hodnota)')

        lots.append({
            'cislo': lot_num,
            'lot_id': f'LOT-{lot_num:04d}',
            'nazov': title,
            'opis': opis,
            'cpv_kod': cpv,
            'hodnota': hodnota,
            'mena': 'EUR',
        })

    return lots


def extract_subject(sections: dict, html: str) -> dict:
    """Extract subject from ODDIEL II."""
    sec = sections.get('II', '')
    subject = {'predmet': '', 'cpv_kod': '', 'druh': '',
               'opis': '', 'nuts': '', 'miesto_plnenia': '', 'druh_postupu': '',
               'pocet_casti': 1, 'max_casti_ponuka': None, 'max_casti_zadanie': None}

    # Subject: ordercomponent div
    m = re.search(r'<div class="ordercomponent_[^"]*">\s*(.*?)\s*</div>', sec, re.DOTALL)
    if m:
        subject['predmet'] = strip_html(m.group(1)).strip()

    # CPV code: selectList
    m = re.search(r'<div class="selectList">.*?<span>(\d{8}-\d)</span>', sec, re.DOTALL)
    if m:
        subject['cpv_kod'] = m.group(1)

    # Contract type from header area (before ODDIEL I)
    m = re.search(r'<strong>Druh zákazky:\s*</strong>\s*(\w+)', html)
    if m:
        subject['druh'] = m.group(1)
    else:
        # Try from dropdownlist in ODDIEL II
        m = re.search(r'II\.1\.3.*?<div class="dropdownlist_[^"]*"><span>(.*?)</span>', sec, re.DOTALL)
        if m:
            subject['druh'] = m.group(1)

    # Opis: II.1.4 "Stručný opis" textArea, truncate to 500 chars
    m = re.search(r'(?:II\.1\.4|Stručný opis).*?<div class="textArea">\s*<span>.*?</span>\s*<span>(.*?)</span>', sec, re.DOTALL)
    if m:
        subject['opis'] = strip_html(m.group(1)).strip()[:500]

    # NUTS code: multiSelectList
    m = re.search(r'<div class="multiSelectList">\s*<div>([^<]+)</div>', sec)
    if m:
        subject['nuts'] = m.group(1).strip()

    # Miesto plnenia: textArea after "Hlavné miesto dodania alebo plnenia"
    m = re.search(r'Hlavné miesto dodania alebo plnenia.*?<div class="textArea">\s*<span>.*?</span>\s*<span>(.*?)</span>', sec, re.DOTALL)
    if m:
        subject['miesto_plnenia'] = strip_html(m.group(1)).strip()

    # Druh postupu: from ODDIEL IV
    sec_iv = sections.get('IV', '')
    if sec_iv:
        m = re.search(r'Druh postupu.*?<div class="dropdownlist_[^"]*">\s*<span>\s*(.*?)\s*</span>', sec_iv, re.DOTALL)
        if m:
            subject['druh_postupu'] = strip_html(m.group(1)).strip()

    # Count lots from II.2 subsections
    if sec:
        lot_matches = re.findall(r'II\.2(?:\.\d+)?.*?<div class="ordercomponent_', sec, re.DOTALL)
        if len(lot_matches) > 1:
            subject['pocet_casti'] = len(lot_matches)

    # Max lots: look for "Maximálny počet" text patterns in ODDIEL II
    if sec:
        m = re.search(r'maximálny počet častí.*?predložiť ponuku.*?<span>\s*(\d+)', sec, re.DOTALL | re.IGNORECASE)
        if m:
            subject['max_casti_ponuka'] = int(m.group(1))
        m = re.search(r'maximálny počet.*?zadaných jednému.*?<span>\s*(\d+)', sec, re.DOTALL | re.IGNORECASE)
        if m:
            subject['max_casti_zadanie'] = int(m.group(1))

    return subject


# ═══════════════════════════════════════════════════════
# VYHLÁSENIE: Extract opportunity (ODDIEL II + IV)
# ═══════════════════════════════════════════════════════

def extract_vyhlasenie(sections: dict) -> dict:
    """Extract opportunity data from announcement."""
    result = {
        'hodnota': None,
        'mena': 'EUR',
        'lehota_datum': '',
        'lehota_cas': '',
        'elektronicka_aukcia': None,
        'trvanie': '',
    }

    sec_ii = sections.get('II', '')
    sec_iv = sections.get('IV', '')

    # Value: various patterns in ODDIEL II
    for pattern in [
        r'Celková odhadovaná hodnota',
        r'Odhadovaná hodnota',
        r'Predpokladaná hodnota',
    ]:
        val = find_value(sec_ii, pattern)
        if val:
            result['hodnota'] = val
            break

    # Deadline: date field in ODDIEL IV (IV.2.2)
    m = re.search(r'Dátum a čas:\s*(\d{2}\.\d{2}\.\d{4})\s+(\d{2}:\d{2})', sec_iv)
    if m:
        result['lehota_datum'] = m.group(1)
        result['lehota_cas'] = m.group(2)
    else:
        # Sometimes just date without time
        m = re.search(r'class="date_[^"]*"[^>]*>.*?(\d{2}\.\d{2}\.\d{4})', sec_iv, re.DOTALL)
        if m:
            result['lehota_datum'] = m.group(1)

    # Elektronická aukcia: in ODDIEL IV, dropdownlist after "elektronickej aukcii" title
    if sec_iv:
        m = re.search(r'elektronick.*?aukci.*?<div class="dropdownlist_[^"]*">(.*?)</div>', sec_iv, re.DOTALL | re.IGNORECASE)
        if m:
            text = strip_html(m.group(1)).lower()
            if 'použije sa' in text or 'použila sa' in text or 'áno' in text:
                result['elektronicka_aukcia'] = True
            elif 'nepoužije' in text or 'nepoužila' in text or 'nie' in text:
                result['elektronicka_aukcia'] = False
        else:
            # Fallback: radioButtonList pattern
            m = re.search(r'elektronick.*?aukci.*?radioButtonList.*?<span>(.*?)</span>', sec_iv, re.DOTALL | re.IGNORECASE)
            if m:
                text = strip_html(m.group(1)).lower()
                if 'áno' in text:
                    result['elektronicka_aukcia'] = True
                elif 'nie' in text:
                    result['elektronicka_aukcia'] = False

    # Trvanie: from ODDIEL II — "v mesiacoch" or "v dňoch" pattern
    if sec_ii:
        m = re.search(r'v mesiacoch.*?(?:shorttext_|<span>)\s*[^>]*>\s*(?:<span>)?\s*(\d+)', sec_ii, re.DOTALL)
        if m:
            result['trvanie'] = f"{m.group(1)} mesiacov"
        else:
            m = re.search(r'v dňoch.*?(?:shorttext_|<span>)\s*[^>]*>\s*(?:<span>)?\s*(\d+)', sec_ii, re.DOTALL)
            if m:
                result['trvanie'] = f"{m.group(1)} dní"

    return result


# ═══════════════════════════════════════════════════════
# VÝSLEDOK: Extract winners (ODDIEL V)
# ═══════════════════════════════════════════════════════

def extract_vysledok(sections: dict) -> dict:
    """Extract result data: winners, values from ODDIEL V."""
    result = {
        'celkova_hodnota': None,
        'mena': 'EUR',
        'pocet_ponuk': None,
        'ucastnici': [],
    }

    sec_v = sections.get('V', '')
    if not sec_v:
        return result

    # Check if contract was not awarded
    if 'nebola pridelená' in sec_v or 'NEPRIDELENÍ' in sec_v:
        return result

    # Number of bids
    m = re.search(r'Počet prijatých ponúk.*?<span>\s*(\d+)\s*</span>', sec_v, re.DOTALL)
    if m:
        result['pocet_ponuk'] = int(m.group(1))

    # Total value
    result['celkova_hodnota'] = find_value(sec_v, r'Celková hodnota zákazky')

    # Winners: ContactSelectList blocks in ODDIEL V
    winners = extract_all_contacts(sec_v)

    # Electronic bids count
    m = re.search(r'Počet ponúk prijatých elektronicky.*?<span>\s*(\d+)\s*</span>', sec_v, re.DOTALL)
    if not m:
        m = re.search(r'ponúk doručených elektronicky.*?<span>\s*(\d+)\s*</span>', sec_v, re.DOTALL)
    if m:
        result['elektronicke_ponuky'] = int(m.group(1))
    else:
        result['elektronicke_ponuky'] = None

    # Contract info (date from V.2.1)
    zmluvy = []
    for dm in re.finditer(r'Dátum uzatvorenia zmluvy.*?<div class="date_[^"]*">\s*(\d{2}\.\d{2}\.\d{4})', sec_v, re.DOTALL):
        zmluvy.append({'datum': dm.group(1).strip()})
    result['zmluvy'] = zmluvy

    # Winner value — try to find value near each winner
    # Simple approach: find all values in section
    values = re.findall(r'Hodnota zákazky/časti.*?<span>\s*([\d\s,\.]+)\s*</span>', sec_v, re.DOTALL)
    if not values:
        values = re.findall(r'Celková hodnota zákazky.*?<span>\s*([\d\s,\.]+)\s*</span>', sec_v, re.DOTALL)

    # MSP (small/medium enterprise) indicators — find all in section
    msp_flags = re.findall(r'Dodávateľom je MSP.*?<span>\s*(.*?)\s*</span>', sec_v, re.DOTALL)

    for i, w in enumerate(winners):
        tender = {
            'nazov': w['nazov'],
            'ico': w['ico'],
            'cena': parse_number(values[i]) if i < len(values) else result['celkova_hodnota'],
            'poradie': 1,
            'je_vitaz': True,
        }
        # Velkost podniku from MSP flag
        if i < len(msp_flags):
            val = strip_html(msp_flags[i]).strip()
            if 'Áno' in val or 'áno' in val:
                tender['velkost_podniku'] = 'MSP'
            elif 'Nie' in val or 'nie' in val:
                tender['velkost_podniku'] = 'Veľký podnik'
            else:
                tender['velkost_podniku'] = ''
        else:
            tender['velkost_podniku'] = ''
        result['ucastnici'].append(tender)

    # If we have a total value but no winner-level values
    if not result['celkova_hodnota'] and result['ucastnici']:
        prices = [u['cena'] for u in result['ucastnici'] if u['cena']]
        if prices:
            result['celkova_hodnota'] = sum(prices)

    return result


# ═══════════════════════════════════════════════════════
# ZMENA ZMLUVY: Extract modification (ODDIEL V + VII)
# ═══════════════════════════════════════════════════════

def extract_zmena_zmluvy(sections: dict) -> dict:
    """Extract contract modification from ODDIEL V + VII."""
    result = {
        'dodavatel': {'nazov': '', 'ico': ''},
        'hodnota_po_zmene': None,
        'mena': 'EUR',
        'dovod_zmeny': '',
        'odovodnenie': '',
        'zhrnutie': '',
    }

    # Supplier from ODDIEL V
    sec_v = sections.get('V', '')
    if sec_v:
        contacts = extract_all_contacts(sec_v)
        if contacts:
            result['dodavatel'] = {'nazov': contacts[0]['nazov'], 'ico': contacts[0]['ico']}

    # Modification details from ODDIEL VII
    sec_vii = sections.get('VII', '')
    if sec_vii:
        # Value after modification
        result['hodnota_po_zmene'] = find_value(sec_vii, r'Celková hodnota zákazky po úprave')

        # Reason
        m = re.search(r'Opis úpravy.*?<span>\s*</span>\s*<span>(.*?)</span>', sec_vii, re.DOTALL)
        if m:
            result['zhrnutie'] = strip_html(m.group(1))[:300]

        # Check reason type
        if 'Nutnosť zmeny spôsobená' in sec_vii:
            result['dovod_zmeny'] = 'Nepredvídané okolnosti'
        elif 'Doplnkové práce' in sec_vii or 'dodatočné' in sec_vii.lower():
            result['dovod_zmeny'] = 'Doplnkové práce/služby/tovary'

    return result


# ═══════════════════════════════════════════════════════
# Metadata extraction (header area)
# ═══════════════════════════════════════════════════════

def extract_metadata(html: str) -> dict:
    """Extract document metadata from MainHeader area."""
    meta = {
        'cislo_dokumentu': '',
        'kod': '',
        'typ_oznamenia': '',
        'druh_zakazky': '',
        'vestnik_cislo': '',
    }

    headers = re.findall(r'<div class="MainHeader">(.*?)</div>', html)
    if headers:
        # First header: "22333 - MST"
        m = re.match(r'(\d+)\s*-\s*(\w+)', headers[0])
        if m:
            meta['cislo_dokumentu'] = m.group(1)
            meta['kod'] = m.group(2)

        # Second header: full type name
        if len(headers) > 1:
            meta['typ_oznamenia'] = strip_html(headers[1]).strip()

    # Vestník č.: div between first and second MainHeader
    m = re.search(r'<div class="MainHeader">.*?</div>\s*<div>(Vestník č\.[^<]+)</div>', html, re.DOTALL)
    if m:
        meta['vestnik_cislo'] = strip_html(m.group(1)).strip()

    # Druh zákazky
    m = re.search(r'<strong>Druh zákazky:\s*</strong>\s*([^<]+)', html)
    if m:
        meta['druh_zakazky'] = m.group(1).strip()

    return meta


# ═══════════════════════════════════════════════════════
# Main parse function
# ═══════════════════════════════════════════════════════

def parse_document(html: str) -> dict:
    """Parse a single legacy UVO HTML document."""
    action, code, type_name = classify_document(html)
    sections = extract_sections(html)

    if not sections:
        return {"error": "no_sections", "action": action}

    buyer = extract_buyer(sections)
    subject = extract_subject(sections, html)
    metadata = extract_metadata(html)
    lots = extract_lots(sections)

    result = {
        'metadata': metadata,
        'obstaravatel': buyer,
        'zakazka': subject,
        'casti': lots,
    }

    if action in ('vysledok', 'suhrn'):
        result['vysledok'] = extract_vysledok(sections)
    elif action == 'vyhlasenie':
        result['prilezitost'] = extract_vyhlasenie(sections)
    elif action == 'oprava':
        # Opravy can be either
        if 'V' in sections:
            result['vysledok'] = extract_vysledok(sections)
        else:
            result['prilezitost'] = extract_vyhlasenie(sections)
    elif action == 'zmena_zmluvy':
        result['zmena_zmluvy'] = extract_zmena_zmluvy(sections)

    return result


# ═══════════════════════════════════════════════════════
# Input handling (same modes as main parser)
# ═══════════════════════════════════════════════════════

def docs_from_listing(url: str) -> list[dict]:
    """Parse vestník listing page to get doc IDs."""
    html = urllib.request.urlopen(url, timeout=15).read().decode('utf-8', errors='ignore')
    ids = sorted(set(re.findall(r'oznamenie/detail/(\d+)', html)))
    m = re.search(r'order=(\d+).*year=(\d+)', url)
    vestnik = f"VVO {m.group(1)}/{m.group(2)}" if m else ""
    docs = []
    for doc_id in ids:
        doc_url = f"https://www.uvo.gov.sk/vestnik-a-registre/vestnik/oznamenie/detail/{doc_id}"
        docs.append({"num": "", "code": "", "type": "", "action": "unknown",
                      "url": doc_url, "id": doc_id, "vestnik": vestnik})
    return docs


def docs_from_directory(dirpath: str) -> list[dict]:
    """Load docs from a directory of HTML files."""
    docs = []
    for f in sorted(Path(dirpath).glob("*.html")):
        doc_id = f.stem.split('_')[0]  # Handle "473176_vyhlasenie.html"
        docs.append({"num": "", "code": "", "type": "", "action": "unknown",
                      "url": f"https://www.uvo.gov.sk/vestnik-a-registre/vestnik/oznamenie/detail/{doc_id}",
                      "id": doc_id, "vestnik": ""})
    return docs


def main():
    print("=" * 60)
    print("VESTNÍK LEGACY PARSER — Starý formát (2020–2023)")
    print("=" * 60)

    if not INPUT:
        print("Použitie: python3 vestnik-parser-legacy.py [100/2022 | /tmp/dir]")
        sys.exit(1)

    source = INPUT
    local_dir = None

    if re.match(r'^\d+/\d{4}$', source):
        num, year = source.split('/')
        source = f"https://www.uvo.gov.sk/vestnik-a-registre/vestnik?order={num}&year={year}&date="
        local_dir = f"/tmp/vestnik{num}_{year}_legacy"

    if os.path.isdir(source):
        docs = docs_from_directory(source)
        local_dir = source
        print(f"\n📂 {len(docs)} HTML súborov v {source}\n")
    elif 'order=' in source and 'year=' in source:
        docs = docs_from_listing(source)
        m = re.search(r'order=(\d+).*year=(\d+)', source)
        if m and not local_dir:
            local_dir = f"/tmp/vestnik{m.group(1)}_{m.group(2)}_legacy"
        print(f"\n📡 {len(docs)} dokumentov v listing stránke\n")
    else:
        print(f"❌ Neznámy vstup: {source}")
        sys.exit(1)

    # Process
    results = []
    total_time = 0
    processed = 0
    errors = 0

    for i, doc in enumerate(docs):
        label = doc['id']
        print(f"\n[{i+1}/{len(docs)}] {label}", end=" ", flush=True)

        # Load HTML
        local_path = Path(local_dir) / f"{doc['id']}.html" if local_dir else None
        if local_path and local_path.exists():
            html = local_path.read_text(encoding='utf-8', errors='ignore')
        else:
            try:
                html = urllib.request.urlopen(doc["url"], timeout=15).read().decode('utf-8', errors='ignore')
                if local_path:
                    local_path.parent.mkdir(parents=True, exist_ok=True)
                    local_path.write_text(html, encoding='utf-8')
            except Exception as e:
                print(f"❌ FETCH: {e}")
                results.append({**doc, "result": "fetch_error"})
                errors += 1
                continue

        # Check if this is actually new format
        if '<ul class="notice-list">' in html:
            print("⏭️  Nový formát — preskočené")
            results.append({**doc, "result": "new_format"})
            continue

        # Check if this is legacy format
        if '<fieldset' not in html:
            print("❌ Neznámy formát")
            results.append({**doc, "result": "unknown_format"})
            errors += 1
            continue

        start = time.time()

        # Classify from HTML
        action, code, type_name = classify_document(html)
        doc["action"] = action
        doc["code"] = code
        doc["type"] = type_name

        extraction = parse_document(html)
        elapsed = time.time() - start
        total_time += elapsed
        processed += 1

        # Print results
        buyer = extraction.get('obstaravatel', {})
        subject = extraction.get('zakazka', {})

        if action in ('vysledok', 'suhrn'):
            vs = extraction.get('vysledok', {})
            vitazi = vs.get('ucastnici', [])
            print(f"| {elapsed*1000:.0f}ms | {action}")
            print(f"  📊 {buyer.get('nazov','?')} ({buyer.get('ico','?')})")
            for u in vitazi:
                print(f"     🏆 {u['nazov']} ({u.get('ico','?')}) | {u.get('cena','')} EUR")
            if not vitazi:
                print(f"     Žiadny víťaz (ponúk: {vs.get('pocet_ponuk', '?')})")

        elif action == 'vyhlasenie':
            pr = extraction.get('prilezitost', {})
            print(f"| {elapsed*1000:.0f}ms | {action}")
            print(f"  📢 {buyer.get('nazov','?')} ({buyer.get('ico','?')})")
            print(f"     💡 {subject.get('predmet','?')[:70]}")
            print(f"     {pr.get('hodnota','?')} EUR | Lehota: {pr.get('lehota_datum','?')} {pr.get('lehota_cas','')}")

        elif action == 'zmena_zmluvy':
            zm = extraction.get('zmena_zmluvy', {})
            dod = zm.get('dodavatel', {})
            print(f"| {elapsed*1000:.0f}ms | {action}")
            print(f"  📋 {buyer.get('nazov','?')} → {dod.get('nazov','?')} ({dod.get('ico','?')})")
            print(f"     {zm.get('hodnota_po_zmene','')} EUR | {zm.get('dovod_zmeny','?')}")

        else:
            print(f"| {elapsed*1000:.0f}ms | {action}")
            print(f"  ❓ {buyer.get('nazov','?')}")

        results.append({**doc, "extraction": extraction, "time_ms": round(elapsed * 1000, 1)})

    # Summary
    print(f"\n{'=' * 60}")
    print(f"VÝSLEDKY — Legacy parser (2020–2023)")
    print(f"{'=' * 60}")
    print(f"Dokumentov: {len(docs)}")
    print(f"Spracovaných: {processed}")
    print(f"Chyby: {errors}")
    if processed > 0:
        print(f"Celkový čas: {total_time*1000:.0f}ms ({total_time:.2f}s)")
        print(f"Priemer: {total_time*1000/processed:.1f}ms/dok")

    actions = Counter(d["action"] for d in results if "extraction" in d)
    for a, c in actions.most_common():
        emoji = {"vysledok": "📊", "vyhlasenie": "📢", "oprava": "🔄", "zmena_zmluvy": "📋"}.get(a, "❓")
        print(f"   {emoji} {a}: {c}")

    # Validation
    uvo_count = sum(1 for d in results
                    if d.get('extraction', {}).get('obstaravatel', {}).get('nazov', '') == 'Úrad pre verejné obstarávanie')
    no_buyer = sum(1 for d in results
                   if 'extraction' in d and not d['extraction'].get('obstaravatel', {}).get('nazov'))
    print(f"\n   Validácia: UVO ako obstarávateľ={uvo_count}, bez obstarávateľa={no_buyer}")

    # Save
    vestnik_label = ""
    m = re.search(r'order=(\d+).*year=(\d+)', INPUT if 'order=' in INPUT else '')
    if m:
        vestnik_label = f"_{m.group(1)}_{m.group(2)}"
    elif re.match(r'^\d+/\d{4}$', INPUT):
        num, year = INPUT.split('/')
        vestnik_label = f"_{num}_{year}"

    out_file = OUT_DIR / f"vestnik{vestnik_label}_legacy_results.json"
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with open(out_file, "w") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    print(f"\nUložené: {out_file}")


if __name__ == "__main__":
    main()
