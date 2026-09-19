#!/usr/bin/env python3
"""
Parser pre TED (Tenders Electronic Daily) eForms XML dokumenty — slovenské zákazky.
Deterministický XML parser, žiadny LLM.

Vstup:  TED Search API → XML per notice
Výstup: JSON kompatibilný s vestnik-parser.py

Usage:
  python3 tools/ted-parser.py                    # last 30 days SK notices
  python3 tools/ted-parser.py 2025               # all SK notices from 2025
  python3 tools/ted-parser.py 2025-01-01 2025-06-30  # date range
  python3 tools/ted-parser.py 1477-2024           # specific notice
  MAX_DOCS=100 python3 tools/ted-parser.py 2026   # limit count
"""

import json
import os
import re
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from pathlib import Path
from collections import Counter
from typing import Optional
import urllib.request
import urllib.error

OUT_DIR = Path(__file__).parent.parent / "data" / "results"
CACHE_DIR = Path("/tmp/ted_cache")

TED_API = "https://api.ted.europa.eu/v3/notices/search"
TED_XML_URL = "https://ted.europa.eu/en/notice/{pub}/xml"

NS = {
    'cac': 'urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2',
    'cbc': 'urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2',
    'efac': 'http://data.europa.eu/p27/eforms-ubl-extension-aggregate-components/1',
    'efbc': 'http://data.europa.eu/p27/eforms-ubl-extension-basic-components/1',
    'efext': 'http://data.europa.eu/p27/eforms-ubl-extensions/1',
    'ext': 'urn:oasis:names:specification:ubl:schema:xsd:CommonExtensionComponents-2',
}

# BT-02-notice → action mapping
CODE_ACTION = {
    'cn-standard': 'vyhlasenie',
    'cn-desg': 'vyhlasenie',
    'cn-social': 'vyhlasenie',
    'pin-only': 'vyhlasenie',
    'pin-cfc-standard': 'vyhlasenie',
    'pin-cfc-social': 'vyhlasenie',
    'can-standard': 'vysledok',
    'can-social': 'vysledok',
    'can-desg': 'vysledok',
    'veat': 'vysledok',
    'can-modif': 'zmena_zmluvy',
}


# ═══════════════════════════════════════════════════════
# TED Search API
# ═══════════════════════════════════════════════════════

def search_notices(query: str, max_results: int = 0) -> list[dict]:
    """Search TED API for Slovak notices. Returns list of {publication-number, notice-type, publication-date}."""
    notices = []
    page = 1
    limit = 100

    while True:
        payload = json.dumps({
            'query': query,
            'page': page,
            'limit': limit,
            'fields': ['publication-number', 'notice-type', 'publication-date'],
        }).encode()

        req = urllib.request.Request(TED_API, data=payload,
                                     headers={'Content-Type': 'application/json'})
        try:
            resp = urllib.request.urlopen(req, timeout=30)
            result = json.loads(resp.read())
        except (urllib.error.HTTPError, urllib.error.URLError) as e:
            print(f"  API chyba (strana {page}): {e}")
            break

        batch = result.get('notices', [])
        if not batch:
            break

        total = result.get('totalNoticeCount', 0)
        if page == 1:
            print(f"  API: {total} výsledkov celkom")

        for n in batch:
            notices.append({
                'publication_number': n['publication-number'],
                'notice_type': n['notice-type'],
                'publication_date': n.get('publication-date', ''),
                'xml_url': n.get('links', {}).get('xml', {}).get('MUL', ''),
            })

        if max_results and len(notices) >= max_results:
            notices = notices[:max_results]
            break

        if len(batch) < limit:
            break

        page += 1

    return notices


def download_xml(pub_number: str) -> Optional[str]:
    """Download XML for a notice, with caching."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_file = CACHE_DIR / f"{pub_number}.xml"

    if cache_file.exists():
        return cache_file.read_text(encoding='utf-8', errors='ignore')

    url = TED_XML_URL.format(pub=pub_number)
    try:
        req = urllib.request.Request(url)
        resp = urllib.request.urlopen(req, timeout=30)
        xml_data = resp.read().decode('utf-8', errors='ignore')
        cache_file.write_text(xml_data, encoding='utf-8')
        return xml_data
    except Exception as e:
        print(f"  XML download chyba: {e}")
        return None


# ═══════════════════════════════════════════════════════
# XML Parsing — Organizations
# ═══════════════════════════════════════════════════════

def text(el, path: str, default: str = '') -> str:
    """Find text at XPath under element, preferring Slovak language."""
    if el is None:
        return default
    found = el.findall(path, NS)
    if not found:
        return default
    # Prefer SLK/slk language version
    for f in found:
        lang = f.get('languageID', '')
        if lang.upper() in ('SLK', 'SK'):
            return (f.text or '').strip()
    # Fallback to first
    return (found[0].text or '').strip()


def text_first(el, path: str, default: str = '') -> str:
    """Find first text at XPath."""
    if el is None:
        return default
    node = el.find(path, NS)
    if node is None:
        return default
    return (node.text or '').strip()


def iso_to_sk_date(s: str) -> str:
    """Convert ISO date (2026-01-02+01:00) to DD.MM.YYYY."""
    if not s:
        return s
    m = re.match(r'(\d{4})-(\d{2})-(\d{2})', s)
    if m:
        return f"{m.group(3)}.{m.group(2)}.{m.group(1)}"
    return s


def parse_organizations(root) -> dict:
    """Parse all organizations from eForms XML."""
    orgs = {}
    for org_el in root.findall('.//efac:Organizations/efac:Organization', NS):
        company = org_el.find('efac:Company', NS)
        if company is None:
            continue

        org_id = text_first(company, 'cac:PartyIdentification/cbc:ID')
        if not org_id:
            continue

        name = text(company, 'cac:PartyName/cbc:Name')

        # IČO — first CompanyID that looks like Slovak IČO (8 digits)
        ico = ''
        for cid in company.findall('cac:PartyLegalEntity/cbc:CompanyID', NS):
            val = (cid.text or '').strip()
            if re.match(r'^\d{8}$', val):
                ico = val
                break
        # If no 8-digit, take first one
        if not ico:
            ico = text_first(company, 'cac:PartyLegalEntity/cbc:CompanyID')

        addr = company.find('cac:PostalAddress', NS)
        street = text_first(addr, 'cbc:StreetName') if addr is not None else ''
        street_num = text_first(addr, 'cbc:AdditionalStreetName') if addr is not None else ''
        city = text_first(addr, 'cbc:CityName') if addr is not None else ''
        postal = text_first(addr, 'cbc:PostalZone') if addr is not None else ''
        country_code = text_first(addr, 'cac:Country/cbc:IdentificationCode') if addr is not None else ''
        nuts = text_first(addr, 'cbc:CountrySubentityCode') if addr is not None else ''

        contact = company.find('cac:Contact', NS)
        phone = text_first(contact, 'cbc:Telephone') if contact is not None else ''
        email = text_first(contact, 'cbc:ElectronicMail') if contact is not None else ''

        web = text_first(company, 'cbc:WebsiteURI')

        address = f"{street} {street_num}".strip() if street else ''

        orgs[org_id] = {
            'name': name,
            'ico': ico,
            'email': email,
            'city': city,
            'adresa': address,
            'psc': postal,
            'krajina': country_code,
            'nuts': nuts,
            'telefon': phone,
            'web': web,
        }

    return orgs


# ═══════════════════════════════════════════════════════
# XML Parsing — Buyer
# ═══════════════════════════════════════════════════════

def extract_buyer(root, orgs: dict) -> dict:
    """Extract buyer from ContractingParty."""
    buyer = {
        'nazov': '', 'ico': '', 'email': '', 'adresa': '', 'psc': '',
        'mesto': '', 'telefon': '', 'typ_kupujuceho': '', 'cinnost': '',
        'profil_url': '',
    }

    cp = root.find('.//cac:ContractingParty', NS)
    if cp is None:
        return buyer

    buyer_org_id = text_first(cp, 'cac:Party/cac:PartyIdentification/cbc:ID')

    if buyer_org_id and buyer_org_id in orgs:
        org = orgs[buyer_org_id]
        # Skip UVO (IČO 31797903) and find real buyer
        if org.get('ico') == '31797903':
            # Find first non-UVO org
            for oid, o in orgs.items():
                if o.get('ico') and o['ico'] != '31797903':
                    org = o
                    break
        buyer['nazov'] = org.get('name', '')
        buyer['ico'] = org.get('ico', '')
        buyer['email'] = org.get('email', '')
        buyer['adresa'] = org.get('adresa', '')
        buyer['psc'] = org.get('psc', '')
        buyer['mesto'] = org.get('city', '')
        buyer['telefon'] = org.get('telefon', '')

    # Buyer type
    buyer['typ_kupujuceho'] = text_first(cp, './/cbc:PartyTypeCode')

    # Activity
    buyer['cinnost'] = text_first(cp, './/cbc:ActivityTypeCode')

    # Profile URL
    buyer['profil_url'] = text_first(cp, 'cbc:BuyerProfileURI')

    return buyer


# ═══════════════════════════════════════════════════════
# XML Parsing — Subject (Zákazka)
# ═══════════════════════════════════════════════════════

def extract_subject(root) -> dict:
    """Extract procurement project info."""
    subject = {
        'predmet': '',
        'cpv_kod': '',
        'druh': '',
        'opis': '',
        'nuts': '',
        'miesto_plnenia': '',
        'druh_postupu': '',
        'pravny_zaklad': '',
        'pocet_casti': 1,
        'max_casti_ponuka': None,
        'max_casti_zadanie': None,
    }

    # Count real lots (exclude summary elements like LOT-0000 with empty name)
    all_lots = root.findall('.//cac:ProcurementProjectLot', NS)
    lot_count = sum(1 for el in all_lots if is_real_lot(el))
    if lot_count > 0:
        subject['pocet_casti'] = lot_count

    # Lot policy from LotDistribution
    lot_dist = root.find('.//cac:LotDistribution', NS)
    if lot_dist is not None:
        max_sub = text_first(lot_dist, 'cbc:MaximumLotsSubmittedNumeric')
        if max_sub and max_sub.isdigit():
            subject['max_casti_ponuka'] = int(max_sub)
        max_awd = text_first(lot_dist, 'cbc:MaximumLotsAwardedNumeric')
        if max_awd and max_awd.isdigit():
            subject['max_casti_zadanie'] = int(max_awd)

    pp = root.find('.//cac:ProcurementProject', NS)
    if pp is None:
        return subject

    subject['predmet'] = text(pp, 'cbc:Name')
    subject['opis'] = text(pp, 'cbc:Description')[:500]
    subject['cpv_kod'] = text_first(pp, 'cac:MainCommodityClassification/cbc:ItemClassificationCode')
    subject['druh'] = text_first(pp, 'cbc:ProcurementTypeCode')

    # NUTS from realized location
    loc = pp.find('cac:RealizedLocation/cac:Address', NS)
    if loc is not None:
        subject['nuts'] = text_first(loc, 'cbc:CountrySubentityCode')
        country = text_first(loc, 'cac:Country/cbc:IdentificationCode')
        if country:
            subject['miesto_plnenia'] = country

    # Procedure type
    subject['druh_postupu'] = text_first(root, './/cac:TenderingProcess/cbc:ProcedureCode')

    # Legal basis (regulatory domain)
    subject['pravny_zaklad'] = text_first(root, 'cbc:RegulatoryDomain')

    return subject


# ═══════════════════════════════════════════════════════
# XML Parsing — Lots
# ═══════════════════════════════════════════════════════

def is_real_lot(lot_el) -> bool:
    """Return True if this ProcurementProjectLot is a real lot (not a summary element).

    TED eForms sometimes includes a summary lot with ID 'LOT-0000' and an empty
    name at the start of the lot list.  This element should be excluded from
    the casti array and from pocet_casti counts.
    """
    lot_id = text_first(lot_el, 'cbc:ID')
    if lot_id == 'LOT-0000':
        return False
    pp = lot_el.find('cac:ProcurementProject', NS)
    if pp is not None:
        lot_name = text(pp, 'cbc:Name')
        if not lot_name:
            return False
    return True


def extract_lots(root) -> list[dict]:
    """Extract lot information. Returns empty list if only 1 real lot."""
    all_lot_els = root.findall('.//cac:ProcurementProjectLot', NS)
    lot_els = [el for el in all_lot_els if is_real_lot(el)]
    if len(lot_els) <= 1:
        return []

    lots = []
    for i, lot_el in enumerate(lot_els):
        lot_id = text_first(lot_el, 'cbc:ID') or f'LOT-{i+1:04d}'

        pp = lot_el.find('cac:ProcurementProject', NS)
        lot_name = text(pp, 'cbc:Name') if pp is not None else ''
        lot_desc = text(pp, 'cbc:Description') if pp is not None else ''
        lot_cpv = text_first(pp, 'cac:MainCommodityClassification/cbc:ItemClassificationCode') if pp is not None else ''

        # Estimated value — try at lot level first
        est_amount = text_first(lot_el, './/cbc:EstimatedOverallContractAmount')

        # Extract lot number from ID (LOT-0001 → 1)
        cislo = i + 1
        m_num = re.match(r'LOT-0*(\d+)', lot_id)
        if m_num:
            cislo = int(m_num.group(1))

        lots.append({
            'cislo': cislo,
            'lot_id': lot_id,
            'nazov': lot_name,
            'opis': lot_desc[:300],
            'cpv_kod': lot_cpv,
            'hodnota': parse_number(est_amount),
            'mena': 'EUR',
        })

    return lots


# ═══════════════════════════════════════════════════════
# XML Parsing — Results (CAN types)
# ═══════════════════════════════════════════════════════

def extract_vysledok(root, orgs: dict) -> dict:
    """Extract result data from ContractAwardNotice."""
    result = {
        'celkova_hodnota': None,
        'mena': 'EUR',
        'pocet_ponuk': None,
        'ucastnici': [],
        'zmluvy': [],
        'elektronicke_ponuky': None,
        'subdodavatelia': '',
    }

    nr = root.find('.//efac:NoticeResult', NS)
    if nr is None:
        return result

    # Total amount
    total = text_first(nr, 'cbc:TotalAmount')
    if total:
        result['celkova_hodnota'] = parse_number(total)

    # Build TenderingParty → org_id mapping
    tpa_to_orgs = {}
    for tp in nr.findall('efac:TenderingParty', NS):
        tpa_id = text_first(tp, 'cbc:ID')
        if tpa_id:
            tenderer_orgs = []
            for tenderer in tp.findall('efac:Tenderer', NS):
                org_id = text_first(tenderer, 'cbc:ID')
                if org_id:
                    tenderer_orgs.append(org_id)
            tpa_to_orgs[tpa_id] = tenderer_orgs

    # Parse LotTenders
    tenders = []
    for lt in nr.findall('efac:LotTender', NS):
        tender_id = text_first(lt, 'cbc:ID')
        rank = text_first(lt, 'cbc:RankCode')
        amount = text_first(lt, './/cbc:PayableAmount')
        currency = ''
        amount_el = lt.find('.//cbc:PayableAmount', NS)
        if amount_el is not None:
            currency = amount_el.get('currencyID', 'EUR')

        # Get tendering party
        tpa_id = text_first(lt, 'efac:TenderingParty/cbc:ID')
        org_ids = tpa_to_orgs.get(tpa_id, [])

        lot_id = text_first(lt, 'efac:TenderLot/cbc:ID')

        for org_id in org_ids:
            org = orgs.get(org_id, {})
            if org.get('ico') == '31797903':
                continue  # Skip UVO

            tender = {
                'nazov': org.get('name', ''),
                'ico': org.get('ico', ''),
                'cena': parse_number(amount),
                'poradie': int(rank) if rank and rank.isdigit() else None,
                'je_vitaz': rank == '1',
                'velkost_podniku': '',
                'subdodavatelia': '',
                'lot_id': lot_id,
            }
            tenders.append(tender)

        # If no org mapping found, still record the tender
        if not org_ids:
            tenders.append({
                'nazov': '',
                'ico': '',
                'cena': parse_number(amount),
                'poradie': int(rank) if rank and rank.isdigit() else None,
                'je_vitaz': rank == '1',
                'velkost_podniku': '',
                'subdodavatelia': '',
                'lot_id': lot_id,
            })

    # Deduplicate by name
    seen = {}
    for t in tenders:
        key = t['nazov'] or t.get('ico', '') or id(t)
        if key in seen:
            if t.get('cena') and (not seen[key].get('cena') or t['cena'] > seen[key]['cena']):
                seen[key]['cena'] = t['cena']
            if t.get('je_vitaz'):
                seen[key]['je_vitaz'] = True
        else:
            seen[key] = t
    result['ucastnici'] = list(seen.values())

    # Number of tenders from LotResult statistics
    for lr in nr.findall('efac:LotResult', NS):
        for stat in lr.findall('efac:ReceivedSubmissionsStatistics', NS):
            code = text_first(stat, 'efbc:StatisticsCode')
            if code == 'tenders':
                val = text_first(stat, 'efbc:StatisticsNumeric')
                if val and val.isdigit():
                    current = result['pocet_ponuk'] or 0
                    result['pocet_ponuk'] = current + int(val)
            elif code == 't-esubm':
                val = text_first(stat, 'efbc:StatisticsNumeric')
                if val and val.isdigit():
                    current = result['elektronicke_ponuky'] or 0
                    result['elektronicke_ponuky'] = current + int(val)

    # Contracts (SettledContract)
    zmluvy = []
    for sc in nr.findall('efac:SettledContract', NS):
        sc_id = text_first(sc, 'cbc:ID')
        # Skip the brief references inside LotResult (which only have ID)
        if sc.find('cbc:IssueDate', NS) is None and sc.find('cbc:Title', NS) is None:
            continue
        zmluva = {
            'id': text_first(sc, 'efac:ContractReference/cbc:ID') or sc_id,
            'datum': iso_to_sk_date(text_first(sc, 'cbc:IssueDate')),
            'nazov': text(sc, 'cbc:Title'),
            'url': text_first(sc, 'cbc:URI'),
        }
        zmluvy.append(zmluva)
    result['zmluvy'] = zmluvy

    return result


# ═══════════════════════════════════════════════════════
# XML Parsing — Opportunity (CN / PIN)
# ═══════════════════════════════════════════════════════

def extract_vyhlasenie(root) -> dict:
    """Extract opportunity data from ContractNotice / PriorInformationNotice."""
    result = {
        'hodnota': None,
        'mena': 'EUR',
        'lehota_datum': '',
        'lehota_cas': '',
        'eu_fond': '',
        'ramcova_dohoda': False,
        'kriterium': '',
        'elektronicka_aukcia': False,
        'dns': '',
        'trvanie_mesiace': None,
        'trvanie_dni': None,
    }

    # Estimated value — top-level ProcurementProject
    pp = root.find('.//cac:ProcurementProject', NS)
    if pp is not None:
        est = text_first(pp, 'cac:RequestedTenderTotal/cbc:EstimatedOverallContractAmount')
        if est:
            result['hodnota'] = parse_number(est)

    # If not found, sum lot values
    if result['hodnota'] is None:
        total = 0
        found = False
        for lot in root.findall('.//cac:ProcurementProjectLot', NS):
            val = text_first(lot, './/cbc:EstimatedOverallContractAmount')
            if val:
                v = parse_number(val)
                if v is not None:
                    total += v
                    found = True
        if found:
            result['hodnota'] = total

    # Deadline — check all TenderingProcess elements (top-level and lot-level)
    for tp in root.findall('.//cac:TenderingProcess', NS):
        dl = tp.find('cac:TenderSubmissionDeadlinePeriod', NS)
        if dl is not None:
            result['lehota_datum'] = iso_to_sk_date(text_first(dl, 'cbc:EndDate'))
            result['lehota_cas'] = text_first(dl, 'cbc:EndTime')
            break

    # Procedure code — from top-level TenderingProcess
    tp = root.find('.//cac:TenderingProcess', NS)
    if tp is not None:
        proc = text_first(tp, 'cbc:ProcedureCode')
        if proc:
            result['druh_postupu'] = proc

    # EU fund — check at lot level
    for lot in root.findall('.//cac:ProcurementProjectLot', NS):
        fund = text_first(lot, './/cbc:FundingProgramCode')
        if fund and fund != 'no-eu-funds':
            result['eu_fond'] = fund
            break
        elif fund == 'no-eu-funds':
            result['eu_fond'] = 'Nie'

    # Framework agreement
    for lot in root.findall('.//cac:ProcurementProjectLot', NS):
        fa = lot.find('.//cac:FrameworkAgreement', NS)
        if fa is not None:
            max_participants = text_first(fa, 'cbc:MaximumOperatorQuantity')
            if max_participants and max_participants != '0':
                result['ramcova_dohoda'] = True
                break

    # Evaluation criteria
    for lot in root.findall('.//cac:ProcurementProjectLot', NS):
        for ac in lot.findall('.//cac:AwardingTerms//cac:AwardingCriterion', NS):
            crit_type = text_first(ac, 'cbc:AwardingCriterionTypeCode')
            if crit_type:
                result['kriterium'] = crit_type
                break
        if result['kriterium']:
            break

    # Duration
    for lot in root.findall('.//cac:ProcurementProjectLot', NS):
        pp_lot = lot.find('cac:ProcurementProject', NS)
        if pp_lot is not None:
            dur = pp_lot.find('.//cac:PlannedPeriod', NS)
            if dur is not None:
                months = text_first(dur, 'cbc:DurationMeasure')
                unit = dur.find('cbc:DurationMeasure', NS)
                if unit is not None:
                    unit_code = unit.get('unitCode', '')
                    val = parse_number(months)
                    if val is not None:
                        if unit_code == 'MONTH':
                            result['trvanie_mesiace'] = int(val)
                        elif unit_code == 'DAY':
                            result['trvanie_dni'] = int(val)
                break

    return result


# ═══════════════════════════════════════════════════════
# XML Parsing — Contract Modification (can-modif)
# ═══════════════════════════════════════════════════════

def extract_zmena_zmluvy(root, orgs: dict) -> dict:
    """Extract contract modification data."""
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

    nr = root.find('.//efac:NoticeResult', NS)

    # Get supplier from tender → tendering party → org
    if nr is not None:
        # Build TPA mapping
        tpa_to_orgs = {}
        for tp in nr.findall('efac:TenderingParty', NS):
            tpa_id = text_first(tp, 'cbc:ID')
            if tpa_id:
                for tenderer in tp.findall('efac:Tenderer', NS):
                    org_id = text_first(tenderer, 'cbc:ID')
                    if org_id:
                        tpa_to_orgs.setdefault(tpa_id, []).append(org_id)

        # Get first tender's supplier
        for lt in nr.findall('efac:LotTender', NS):
            tpa_id = text_first(lt, 'efac:TenderingParty/cbc:ID')
            org_ids = tpa_to_orgs.get(tpa_id, [])
            for org_id in org_ids:
                org = orgs.get(org_id, {})
                if org.get('ico') != '31797903':
                    result['dodavatel']['nazov'] = org.get('name', '')
                    result['dodavatel']['ico'] = org.get('ico', '')
                    break
            if result['dodavatel']['nazov']:
                break

            # Also try tender amount
            amount = text_first(lt, './/cbc:PayableAmount')
            if amount:
                result['hodnota_po_zmene'] = parse_number(amount)

        # Contract details from SettledContract
        for sc in nr.findall('efac:SettledContract', NS):
            if sc.find('cbc:IssueDate', NS) is not None or sc.find('cbc:Title', NS) is not None:
                result['zmluva_id'] = text_first(sc, 'efac:ContractReference/cbc:ID')
                result['zmluva_datum'] = iso_to_sk_date(text_first(sc, 'cbc:IssueDate'))
                result['zmluva_url'] = text_first(sc, 'cbc:URI')
                break

    # Contract modification details
    cm = root.find('.//efac:ContractModification', NS)
    if cm is not None:
        # Reason
        reason = cm.find('efac:ChangeReason', NS)
        if reason is not None:
            result['dovod_zmeny'] = text_first(reason, 'cbc:ReasonCode')
            result['odovodnenie'] = text(reason, 'efbc:ReasonDescription')[:300]

        # Change description
        change = cm.find('efac:Change', NS)
        if change is not None:
            result['zhrnutie'] = text(change, 'efbc:ChangeDescription')[:300]

    return result


# ═══════════════════════════════════════════════════════
# Utility
# ═══════════════════════════════════════════════════════

def parse_number(s: str) -> Optional[float]:
    """Parse numeric string to float."""
    if not s:
        return None
    s = s.strip().replace(' ', '').replace('\xa0', '')
    if ',' in s and '.' in s:
        s = s.replace(',', '')
    elif ',' in s:
        parts = s.split(',')
        if len(parts[-1]) == 2:
            s = s.replace(',', '.')
        else:
            s = s.replace(',', '')
    try:
        return float(s)
    except ValueError:
        return None


def detect_root_type(xml_str: str) -> Optional[str]:
    """Detect root element type from XML string."""
    if '<TED_EXPORT' in xml_str[:500]:
        return 'TED_EXPORT'
    if '<ContractNotice' in xml_str[:500]:
        return 'ContractNotice'
    if '<ContractAwardNotice' in xml_str[:500]:
        return 'ContractAwardNotice'
    if '<PriorInformationNotice' in xml_str[:500]:
        return 'PriorInformationNotice'
    return None


# ═══════════════════════════════════════════════════════
# Main parse function
# ═══════════════════════════════════════════════════════

def parse_notice(xml_str: str, pub_number: str, notice_type: str = '') -> Optional[dict]:
    """Parse a single TED eForms XML notice."""
    root_type = detect_root_type(xml_str)

    if root_type == 'TED_EXPORT':
        return None  # Old format, skip

    try:
        root = ET.fromstring(xml_str)
    except ET.ParseError as e:
        return {'error': f'XML parse error: {e}'}

    # Detect notice type from XML if not provided
    if not notice_type:
        notice_type = text_first(root, 'cbc:NoticeTypeCode')

    action = CODE_ACTION.get(notice_type, 'unknown')

    # Parse common sections
    orgs = parse_organizations(root)
    buyer = extract_buyer(root, orgs)
    subject = extract_subject(root)
    lots = extract_lots(root)

    # Publication info
    pub_id = text_first(root, './/efac:Publication/efbc:NoticePublicationID')
    pub_date = text_first(root, './/efac:Publication/efbc:PublicationDate')
    issue_date = text_first(root, 'cbc:IssueDate')

    metadata = {
        'zdroj': 'TED',
        'publication_number': pub_number,
        'notice_type': notice_type,
        'publication_date': pub_date or issue_date,
        'sdk_version': text_first(root, 'cbc:CustomizationID'),
        'language': text_first(root, 'cbc:NoticeLanguageCode'),
    }

    extraction = {
        'metadata': metadata,
        'obstaravatel': buyer,
        'zakazka': subject,
    }

    extraction['casti'] = lots

    # Type-specific sections
    if action in ('vysledok',):
        vysledok = extract_vysledok(root, orgs)
        extraction['vysledok'] = vysledok
    elif action == 'vyhlasenie':
        vyhlasenie = extract_vyhlasenie(root)
        extraction['prilezitost'] = vyhlasenie
    elif action == 'zmena_zmluvy':
        zmena = extract_zmena_zmluvy(root, orgs)
        extraction['zmena_zmluvy'] = zmena

    return extraction


# ═══════════════════════════════════════════════════════
# CLI Input Parsing
# ═══════════════════════════════════════════════════════

def build_query(args: list[str]) -> tuple[str, str]:
    """Build TED API query from CLI arguments. Returns (query, label)."""
    base = 'buyer-country=SVK'

    if not args:
        # Last 30 days
        date_from = (datetime.now() - timedelta(days=30)).strftime('%Y%m%d')
        return f"{base} AND publication-date>{date_from}", "last-30d"

    if len(args) == 1:
        arg = args[0]

        # Specific notice: e.g. "1477-2024"
        if re.match(r'^\d+-\d{4}$', arg):
            return None, arg  # Special case: single notice

        # Year only: e.g. "2025"
        if re.match(r'^\d{4}$', arg):
            year = arg
            date_from = f"{year}0101"
            date_to = f"{year}1231"
            return f"{base} AND publication-date>={date_from} AND publication-date<={date_to}", f"ted_{year}"

        # Date: e.g. "2025-01-01"
        if re.match(r'^\d{4}-\d{2}-\d{2}$', arg):
            date_from = arg.replace('-', '')
            return f"{base} AND publication-date>={date_from}", f"ted_from_{arg}"

    if len(args) == 2:
        # Date range: "2025-01-01 2025-06-30"
        date_from = args[0].replace('-', '')
        date_to = args[1].replace('-', '')
        return f"{base} AND publication-date>={date_from} AND publication-date<={date_to}", f"ted_{args[0]}_{args[1]}"

    return f"{base}", "ted_all"


# ═══════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════

def main():
    print("=" * 60)
    print("TED PARSER — eForms XML (Slovenské zákazky)")
    print("=" * 60)

    args = sys.argv[1:]
    max_docs = int(os.environ.get("MAX_DOCS", "0"))

    query, label = build_query(args)

    # Special case: single notice by publication number
    if query is None:
        pub_number = label
        print(f"\n📡 Sťahujem oznámenie {pub_number}...")

        xml_data = download_xml(pub_number)
        if xml_data is None:
            print("❌ Nepodarilo sa stiahnuť XML")
            sys.exit(1)

        root_type = detect_root_type(xml_data)
        if root_type == 'TED_EXPORT':
            print("⚠️  Starý TED_EXPORT formát — preskakujem (iba eForms 2024+)")
            sys.exit(0)

        start = time.time()
        extraction = parse_notice(xml_data, pub_number)
        elapsed = time.time() - start

        if extraction is None:
            print("⚠️  Starý TED_EXPORT formát — preskakujem")
            sys.exit(0)

        notice_type = extraction.get('metadata', {}).get('notice_type', '')
        action = CODE_ACTION.get(notice_type, 'unknown')

        result = {
            'num': pub_number,
            'code': notice_type,
            'type': root_type or '',
            'action': action,
            'url': TED_XML_URL.format(pub=pub_number),
            'id': pub_number,
            'vestnik': 'TED',
            'extraction': extraction,
            'time_ms': round(elapsed * 1000, 1),
        }

        print_result(result, 1, 1, elapsed)

        out_file = OUT_DIR / f"ted_{pub_number}_results.json"
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        with open(out_file, 'w', encoding='utf-8') as f:
            json.dump([result], f, ensure_ascii=False, indent=2)
        print(f"\n💾 Výstup: {out_file}")
        return

    # Search API
    print(f"\n📡 Vyhľadávam: {query}")
    notices = search_notices(query, max_results=max_docs if max_docs else 0)

    if not notices:
        print("❌ Žiadne výsledky")
        sys.exit(0)

    if max_docs and len(notices) > max_docs:
        notices = notices[:max_docs]
        print(f"⚠️  Obmedzené na {max_docs} dokumentov (MAX_DOCS={max_docs})")

    print(f"\n📊 {len(notices)} oznámení na spracovanie\n")

    # Process notices
    results = []
    total_time = 0
    processed = 0
    errors = 0
    skipped = 0
    downloads = 0

    for i, notice in enumerate(notices):
        pub_number = notice['publication_number']
        notice_type = notice['notice_type']
        action = CODE_ACTION.get(notice_type, 'unknown')

        # Check cache
        cache_file = CACHE_DIR / f"{pub_number}.xml"
        cached = cache_file.exists()

        # Download XML
        if not cached:
            time.sleep(0.5)  # Rate limit
            downloads += 1

        xml_data = download_xml(pub_number)
        if xml_data is None:
            print(f"[{i+1}/{len(notices)}] {pub_number} | ❌ download chyba")
            errors += 1
            continue

        # Skip old format
        root_type = detect_root_type(xml_data)
        if root_type == 'TED_EXPORT':
            print(f"[{i+1}/{len(notices)}] {pub_number} | ⚠️  TED_EXPORT (starý formát, preskakujem)")
            skipped += 1
            continue

        # Parse
        start = time.time()
        extraction = parse_notice(xml_data, pub_number, notice_type)
        elapsed = time.time() - start
        total_time += elapsed
        processed += 1

        if extraction is None:
            print(f"[{i+1}/{len(notices)}] {pub_number} | ⚠️  nerozpoznaný formát")
            skipped += 1
            continue

        if 'error' in extraction:
            print(f"[{i+1}/{len(notices)}] {pub_number} | ❌ {extraction['error']}")
            errors += 1
            continue

        result = {
            'num': pub_number,
            'code': notice_type,
            'type': root_type or '',
            'action': action,
            'url': TED_XML_URL.format(pub=pub_number),
            'id': pub_number,
            'vestnik': 'TED',
            'extraction': extraction,
            'time_ms': round(elapsed * 1000, 1),
        }

        print_result(result, i + 1, len(notices), elapsed)
        results.append(result)

    # Summary
    print(f"\n{'=' * 60}")
    print(f"VÝSLEDKY — TED eForms Parser")
    print(f"{'=' * 60}")
    print(f"Celkom oznámení: {len(notices)}")
    print(f"Spracovaných:    {processed}")
    print(f"Preskočených:    {skipped} (starý formát)")
    print(f"Chýb:            {errors}")
    print(f"Stiahnutých:     {downloads} (nových)")
    print(f"Z cache:         {processed + skipped - downloads}")
    print(f"Celkový čas:     {total_time*1000:.0f}ms ({total_time:.2f}s)")
    if processed > 0:
        print(f"Priemer:         {total_time*1000/processed:.1f}ms/dok")
    print(f"Cena:            $0")

    # Action summary
    actions = Counter(r['action'] for r in results)
    print(f"\nPodľa typu:")
    for a, c in actions.most_common():
        emoji = {"vysledok": "📊", "vyhlasenie": "📢", "zmena_zmluvy": "📋"}.get(a, "❓")
        print(f"   {emoji} {a}: {c}")

    # Save
    if results:
        out_file = OUT_DIR / f"{label}_results.json"
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        with open(out_file, 'w', encoding='utf-8') as f:
            json.dump(results, f, ensure_ascii=False, indent=2)
        print(f"\n💾 Výstup: {out_file}")
    else:
        print("\n⚠️  Žiadne výsledky na uloženie")


def print_result(result: dict, idx: int, total: int, elapsed: float):
    """Print a single result line, matching vestnik-parser.py style."""
    extraction = result.get('extraction', {})
    action = result['action']
    pub = result['num']
    subject = extraction.get('zakazka', {})

    if action == 'vysledok':
        vs = extraction.get('vysledok', {})
        ucastnici = vs.get('ucastnici', [])
        vitazi = [u for u in ucastnici if u.get('je_vitaz')]
        print(f"[{idx}/{total}] {pub} | výsledok | {elapsed*1000:.0f}ms")
        for u in vitazi:
            print(f"  📊 🏆 {u['nazov']} ({u.get('ico','?')}) | {u.get('cena','')} EUR")
        if not vitazi and ucastnici:
            for u in ucastnici:
                print(f"  📊    {u['nazov']} ({u.get('ico','?')}) | {u.get('cena','')} EUR")
        if not ucastnici:
            ponuk = vs.get('pocet_ponuk', 0)
            print(f"  📊 {ponuk} ponúk, žiadny víťaz")

    elif action == 'vyhlasenie':
        pr = extraction.get('prilezitost', {})
        hodnota = pr.get('hodnota', '?')
        lehota = pr.get('lehota_datum', '?')
        print(f"[{idx}/{total}] {pub} | vyhlásenie | {elapsed*1000:.0f}ms")
        print(f"  📢 💡 {subject.get('predmet','?')[:70]}")
        print(f"       {hodnota} EUR | Lehota: {lehota} {pr.get('lehota_cas','')}")

    elif action == 'zmena_zmluvy':
        zm = extraction.get('zmena_zmluvy', {})
        dod = zm.get('dodavatel', {})
        print(f"[{idx}/{total}] {pub} | zmena zmluvy | {elapsed*1000:.0f}ms")
        print(f"  📋 {dod.get('nazov','?')} ({dod.get('ico','?')}) | {zm.get('hodnota_po_zmene','')} EUR")
        if zm.get('zhrnutie'):
            print(f"       Zmena: {zm['zhrnutie'][:80]}")

    else:
        print(f"[{idx}/{total}] {pub} | {action} | {elapsed*1000:.0f}ms")
        print(f"  ❓ {subject.get('predmet','?')[:70]}")


if __name__ == '__main__':
    main()
