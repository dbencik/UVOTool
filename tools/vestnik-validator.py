#!/usr/bin/env python3
"""
Validátor výstupov vestník parsera.
Kontroluje povinné polia, formáty, dátové konzistencie.

Usage:
  python3 tools/vestnik-validator.py data/results/vestnik_191_parser_results.json
  python3 tools/vestnik-validator.py  # validuje posledný výstup v data/results/
"""

import json
import re
import sys
from pathlib import Path


# ═══════════════════════════════════════════════════════
# Validačné pravidlá
# ═══════════════════════════════════════════════════════

def validate_ico(ico: str) -> list[str]:
    """Validuj IČO — 8 číslic."""
    if not ico:
        return []  # prázdne IČO je OK (nie vždy dostupné)
    ico = ico.strip()
    if not re.match(r'^\d{6,8}$', ico):
        return [f"Neplatné IČO: '{ico}' (očakávaných 6-8 číslic)"]
    return []


def validate_number(value, field_name: str) -> list[str]:
    """Validuj číselné hodnoty — kladné, rozumný rozsah."""
    if value is None:
        return []
    if not isinstance(value, (int, float)):
        return [f"{field_name}: nie je číslo ({type(value).__name__}: {value})"]
    if value < 0:
        return [f"{field_name}: záporná hodnota ({value})"]
    return []


def validate_date(date_str: str, field_name: str) -> list[str]:
    """Validuj dátum — formát DD/MM/YYYY alebo DD.MM.YYYY alebo +/-."""
    if not date_str:
        return []
    # UVO používa rôzne formáty
    if re.match(r'^\d{1,2}[./]\d{1,2}[./]\d{4}$', date_str):
        return []
    if re.match(r'^\d{4}-\d{2}-\d{2}', date_str):
        return []
    return [f"{field_name}: neznámy formát dátumu '{date_str}'"]


def validate_document(doc: dict, index: int) -> list[str]:
    """Validuj jeden dokument."""
    errors = []
    warnings = []
    prefix = f"[{index}] {doc.get('id', '?')} ({doc.get('action', '?')})"

    # Základné povinné polia
    if 'extraction' not in doc:
        if doc.get('result') == 'fetch_error':
            warnings.append(f"{prefix}: fetch_error (sieťová chyba)")
            return errors, warnings
        errors.append(f"{prefix}: chýba 'extraction'")
        return errors, warnings

    ext = doc['extraction']

    if ext.get('error'):
        errors.append(f"{prefix}: parser error — {ext['error']}")
        return errors, warnings

    # Obstarávateľ
    buyer = ext.get('obstaravatel', {})
    if not buyer.get('nazov'):
        errors.append(f"{prefix}: chýba obstarávateľ (nazov)")
    errors.extend(f"{prefix}: {e}" for e in validate_ico(buyer.get('ico', '')))

    # Zákazka
    subject = ext.get('zakazka', {})
    if not subject.get('predmet'):
        warnings.append(f"{prefix}: chýba predmet zákazky")

    action = doc.get('action', '')

    # Výsledok
    if action in ('vysledok', 'suhrn'):
        vs = ext.get('vysledok', {})
        if not vs:
            errors.append(f"{prefix}: chýba 'vysledok' sekcia")
        else:
            errors.extend(f"{prefix}: {e}" for e in validate_number(vs.get('celkova_hodnota'), 'celkova_hodnota'))
            ucastnici = vs.get('ucastnici', [])
            if not ucastnici:
                warnings.append(f"{prefix}: žiadni účastníci")
            for j, u in enumerate(ucastnici):
                if not u.get('nazov'):
                    errors.append(f"{prefix}: účastník {j} — chýba názov")
                errors.extend(f"{prefix}: účastník {j} — {e}" for e in validate_ico(u.get('ico', '')))
                errors.extend(f"{prefix}: účastník {j} — {e}" for e in validate_number(u.get('cena'), 'cena'))

    # Vyhlásenie
    elif action == 'vyhlasenie':
        pr = ext.get('prilezitost', {})
        if not pr:
            errors.append(f"{prefix}: chýba 'prilezitost' sekcia")
        else:
            errors.extend(f"{prefix}: {e}" for e in validate_number(pr.get('hodnota'), 'hodnota'))
            warnings.extend(f"{prefix}: {e}" for e in validate_date(pr.get('lehota_datum', ''), 'lehota_datum'))

    # Zmena zmluvy
    elif action == 'zmena_zmluvy':
        zm = ext.get('zmena_zmluvy', {})
        if not zm:
            errors.append(f"{prefix}: chýba 'zmena_zmluvy' sekcia")
        else:
            dod = zm.get('dodavatel', {})
            if not dod.get('nazov'):
                warnings.append(f"{prefix}: chýba dodávateľ")
            errors.extend(f"{prefix}: {e}" for e in validate_ico(dod.get('ico', '')))
            errors.extend(f"{prefix}: {e}" for e in validate_number(zm.get('hodnota_po_zmene'), 'hodnota_po_zmene'))

    # Oprava
    elif action == 'oprava':
        if 'vysledok' not in ext and 'prilezitost' not in ext:
            warnings.append(f"{prefix}: oprava bez vysledok/prilezitost")

    return errors, warnings


# ═══════════════════════════════════════════════════════
# Štatistiky
# ═══════════════════════════════════════════════════════

def compute_stats(docs: list[dict]) -> dict:
    """Spočítaj štatistiky pre validačný report."""
    stats = {
        'total': len(docs),
        'actions': {},
        'with_buyer': 0,
        'with_ico': 0,
        'with_subject': 0,
        'fetch_errors': 0,
        'parse_errors': 0,
    }

    for doc in docs:
        action = doc.get('action', 'unknown')
        stats['actions'][action] = stats['actions'].get(action, 0) + 1

        if doc.get('result') == 'fetch_error':
            stats['fetch_errors'] += 1
            continue

        ext = doc.get('extraction', {})
        if ext.get('error'):
            stats['parse_errors'] += 1
            continue

        buyer = ext.get('obstaravatel', {})
        if buyer.get('nazov'):
            stats['with_buyer'] += 1
        if buyer.get('ico'):
            stats['with_ico'] += 1

        subject = ext.get('zakazka', {})
        if subject.get('predmet'):
            stats['with_subject'] += 1

    return stats


# ═══════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════

def validate_file(filepath: str) -> dict:
    """Validuj celý JSON súbor. Vráti report dict."""
    with open(filepath) as f:
        docs = json.load(f)

    all_errors = []
    all_warnings = []

    for i, doc in enumerate(docs):
        errs, warns = validate_document(doc, i + 1)
        all_errors.extend(errs)
        all_warnings.extend(warns)

    stats = compute_stats(docs)

    processed = stats['total'] - stats['fetch_errors']
    quality_score = 0
    if processed > 0:
        error_rate = len(all_errors) / processed
        quality_score = max(0, round((1 - error_rate) * 100, 1))

    report = {
        'file': str(filepath),
        'stats': stats,
        'quality_score': quality_score,
        'errors': len(all_errors),
        'warnings': len(all_warnings),
        'error_details': all_errors,
        'warning_details': all_warnings,
        'status': 'OK' if not all_errors else 'ERRORS',
    }

    return report


def main():
    # Nájdi súbor na validáciu
    if len(sys.argv) > 1:
        filepath = Path(sys.argv[1])
    else:
        # Posledný výsledok v data/results/
        results_dir = Path(__file__).parent.parent / "data" / "results"
        files = sorted(results_dir.glob("vestnik_*_parser_results.json"), key=lambda f: f.stat().st_mtime)
        if not files:
            print("❌ Žiadne výsledky na validáciu")
            print("   Použi: python3 tools/vestnik-validator.py <subor.json>")
            sys.exit(1)
        filepath = files[-1]

    if not filepath.exists():
        print(f"❌ Súbor neexistuje: {filepath}")
        sys.exit(1)

    print("═" * 60)
    print("VESTNÍK VALIDÁTOR")
    print("═" * 60)
    print(f"\nSúbor: {filepath}")

    report = validate_file(str(filepath))
    stats = report['stats']

    # Štatistiky
    print(f"\n📊 Štatistiky:")
    print(f"   Dokumentov: {stats['total']}")
    for action, count in sorted(stats['actions'].items()):
        emoji = {"vysledok": "📊", "vyhlasenie": "📢", "oprava": "🔄", "zmena_zmluvy": "📋"}.get(action, "❓")
        print(f"   {emoji} {action}: {count}")
    print(f"   S obstarávateľom: {stats['with_buyer']}/{stats['total']}")
    print(f"   S IČO: {stats['with_ico']}/{stats['total']}")
    print(f"   S predmetom: {stats['with_subject']}/{stats['total']}")
    if stats['fetch_errors']:
        print(f"   ⚠️  Fetch errors: {stats['fetch_errors']}")
    if stats['parse_errors']:
        print(f"   ⚠️  Parse errors: {stats['parse_errors']}")

    # Kvalita
    print(f"\n🎯 Kvalita: {report['quality_score']}%")

    # Chyby
    if report['error_details']:
        print(f"\n❌ Chyby ({report['errors']}):")
        for err in report['error_details'][:20]:
            print(f"   • {err}")
        if report['errors'] > 20:
            print(f"   ... a ďalších {report['errors'] - 20}")

    # Varovania
    if report['warning_details']:
        print(f"\n⚠️  Varovania ({report['warnings']}):")
        for warn in report['warning_details'][:10]:
            print(f"   • {warn}")
        if report['warnings'] > 10:
            print(f"   ... a ďalších {report['warnings'] - 10}")

    # Verdikt
    print(f"\n{'═' * 60}")
    if report['status'] == 'OK':
        print(f"✅ VALIDÁCIA OK — {stats['total']} dokumentov, kvalita {report['quality_score']}%")
    else:
        print(f"❌ VALIDÁCIA ZLYHALA — {report['errors']} chýb, {report['warnings']} varovaní")
    print(f"{'═' * 60}")

    # Uložiť report JSON
    report_file = filepath.parent / filepath.name.replace('.json', '_validation.json')
    with open(report_file, 'w') as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    print(f"\nReport: {report_file}")

    # Exit code pre cron
    sys.exit(0 if report['status'] == 'OK' else 1)


if __name__ == "__main__":
    main()
