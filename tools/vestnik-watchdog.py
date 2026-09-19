#!/usr/bin/env python3
"""
Watchdog — monitoring nových zákaziek podľa IČO obstarávateľa a CPV kódov.

Porovná nové dokumenty s pravidlami v config/watchlist.json.
Posiela ntfy.sh notifikácie pri zhode.

Usage:
  python3 tools/vestnik-watchdog.py                    # kontroluje posledný vestník
  python3 tools/vestnik-watchdog.py data/results/vestnik_191_2026_parser_results.json
  python3 tools/vestnik-watchdog.py --all               # kontroluje všetky neskenované
"""

import json
import os
import sys
import urllib.request
from pathlib import Path
from datetime import datetime

BASE_DIR = Path(__file__).parent.parent
CONFIG_PATH = BASE_DIR / "config" / "watchlist.json"
STATE_PATH = BASE_DIR / "data" / "watchdog_state.json"
RESULTS_DIR = BASE_DIR / "data" / "results"


def load_config() -> dict:
    """Load watchlist configuration."""
    if not CONFIG_PATH.exists():
        print(f"❌ Konfigurácia neexistuje: {CONFIG_PATH}")
        print(f"   Vytvorte config/watchlist.json s pravidlami.")
        sys.exit(1)
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def load_state() -> dict:
    """Load state — which files were already scanned."""
    if STATE_PATH.exists():
        with open(STATE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"scanned_files": [], "last_run": None}


def save_state(state: dict):
    """Save state."""
    state["last_run"] = datetime.now().isoformat()
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)


def send_notification(config: dict, title: str, message: str, url: str = ""):
    """Send ntfy.sh push notification."""
    ntfy = config.get("notifikacia", {})
    ntfy_url = ntfy.get("ntfy_url", "https://ntfy.sh/vestnik-uvo")

    try:
        data = message.encode("utf-8")
        req = urllib.request.Request(ntfy_url, data=data)
        req.add_header("Title", title.encode("utf-8").decode("latin-1", errors="replace"))
        req.add_header("Tags", "mag,new")
        if url:
            req.add_header("Click", url)
        urllib.request.urlopen(req, timeout=10)
        return True
    except Exception as e:
        print(f"  ⚠️  Notifikácia zlyhala: {e}")
        return False


def check_document(doc: dict, pravidla: list) -> list:
    """Check if a document matches any watchlist rules. Returns list of matches."""
    ext = doc.get("extraction", {})
    if "error" in ext:
        return []

    # Only check vyhlásenia (new tenders) and zmeny zmlúv
    action = doc.get("action", "")
    if action not in ("vyhlasenie", "zmena_zmluvy"):
        return []

    obst = ext.get("obstaravatel", {})
    zak = ext.get("zakazka", {})
    buyer_ico = obst.get("ico", "")
    cpv = zak.get("cpv_kod", "")

    matches = []

    for rule in pravidla:
        typ = rule.get("typ", "")
        hodnoty = rule.get("hodnoty", [])

        if typ == "ico_obstaravatela" and buyer_ico:
            if buyer_ico in hodnoty:
                matches.append(rule)

        elif typ == "cpv" and cpv:
            # Match CPV prefix — "72000000" matches "72210000-5"
            cpv_clean = cpv.split("-")[0] if "-" in cpv else cpv
            # Also try extracting numeric CPV from text description
            import re
            cpv_nums = re.findall(r'\d{8}', cpv)
            cpv_to_check = cpv_nums + [cpv_clean] if cpv_nums else [cpv_clean]

            for cpv_val in cpv_to_check:
                for pattern in hodnoty:
                    # Prefix matching: "72" matches "72210000"
                    prefix = pattern.rstrip("0")
                    if cpv_val.startswith(prefix):
                        matches.append(rule)
                        break
                if matches and matches[-1] == rule:
                    break

    return matches


def format_match_message(doc: dict, matches: list) -> tuple[str, str, str]:
    """Format notification title and message."""
    ext = doc.get("extraction", {})
    obst = ext.get("obstaravatel", {})
    zak = ext.get("zakazka", {})
    pril = ext.get("prilezitost", {})

    buyer = obst.get("nazov", "?")
    predmet = zak.get("predmet", "?")
    hodnota = pril.get("hodnota")
    lehota = pril.get("lehota_datum", "")
    cpv = zak.get("cpv_kod", "")
    rule_names = ", ".join(set(m["nazov"] for m in matches))

    title = f"Nová zákazka: {buyer[:50]}"

    lines = [
        f"Predmet: {predmet[:100]}",
        f"Obstarávateľ: {buyer} (IČO: {obst.get('ico', '?')})",
    ]
    if hodnota:
        lines.append(f"Hodnota: {hodnota:,.2f} EUR".replace(",", " "))
    if lehota:
        lines.append(f"Lehota: {lehota}")
    if cpv:
        lines.append(f"CPV: {cpv}")
    lines.append(f"Pravidlo: {rule_names}")

    url = doc.get("url", "")

    return title, "\n".join(lines), url


def scan_file(filepath: str, pravidla: list) -> list:
    """Scan a JSON results file and return all matches."""
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            docs = json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        print(f"  ⚠️  Chyba pri čítaní {filepath}: {e}")
        return []

    all_matches = []
    for doc in docs:
        matches = check_document(doc, pravidla)
        if matches:
            all_matches.append((doc, matches))

    return all_matches


def main():
    config = load_config()
    state = load_state()
    pravidla = config.get("pravidla", [])

    if not pravidla:
        print("⚠️  Žiadne pravidlá v watchlist.json")
        return

    print("=" * 60)
    print("VESTNÍK WATCHDOG — Monitoring nových zákaziek")
    print("=" * 60)
    print(f"Pravidlá: {len(pravidla)}")
    for r in pravidla:
        print(f"  • {r['nazov']} ({r['typ']}: {len(r['hodnoty'])} hodnôt)")
    print()

    # Determine which files to scan
    files_to_scan = []

    if len(sys.argv) > 1 and sys.argv[1] != "--all":
        # Specific file
        files_to_scan = [sys.argv[1]]
    elif len(sys.argv) > 1 and sys.argv[1] == "--all":
        # All unscanned files
        all_files = sorted(
            list(RESULTS_DIR.glob("vestnik_*_parser_results.json")) +
            list(RESULTS_DIR.glob("vestnik_*_legacy_results.json")) +
            list(RESULTS_DIR.glob("ted_*_results.json"))
        )
        scanned = set(state.get("scanned_files", []))
        files_to_scan = [str(f) for f in all_files if str(f) not in scanned]
    else:
        # Latest file (by modification time)
        all_files = sorted(
            list(RESULTS_DIR.glob("vestnik_*_parser_results.json")) +
            list(RESULTS_DIR.glob("ted_*_results.json")),
            key=lambda f: f.stat().st_mtime,
            reverse=True
        )
        if all_files:
            files_to_scan = [str(all_files[0])]

    if not files_to_scan:
        print("Žiadne nové súbory na skenovanie.")
        return

    print(f"Skenovanie {len(files_to_scan)} súborov...\n")

    total_matches = 0
    total_notifications = 0

    for filepath in files_to_scan:
        fname = os.path.basename(filepath)
        matches = scan_file(filepath, pravidla)

        if matches:
            print(f"📂 {fname}: {len(matches)} zhôd")
            for doc, rules in matches:
                ext = doc.get("extraction", {})
                obst = ext.get("obstaravatel", {})
                zak = ext.get("zakazka", {})
                pril = ext.get("prilezitost", {})
                rule_names = ", ".join(set(r["nazov"] for r in rules))

                print(f"  🔔 {obst.get('nazov', '?')[:50]}")
                print(f"     {zak.get('predmet', '?')[:70]}")
                if pril.get("hodnota"):
                    print(f"     {pril['hodnota']:,.2f} EUR | Lehota: {pril.get('lehota_datum', '?')}".replace(",", " "))
                print(f"     Pravidlo: {rule_names}")
                print()

                total_matches += 1

                # Send notification if enabled
                should_notify = any(r.get("notifikacia", False) for r in rules)
                if should_notify:
                    title, message, url = format_match_message(doc, rules)
                    if send_notification(config, title, message, url):
                        total_notifications += 1
                        print(f"     ✅ Notifikácia odoslaná")
        else:
            print(f"📂 {fname}: žiadne zhody")

        # Mark as scanned
        state.setdefault("scanned_files", []).append(filepath)

    # Summary
    print(f"\n{'=' * 60}")
    print(f"Celkovo: {total_matches} zhôd, {total_notifications} notifikácií")
    print(f"{'=' * 60}")

    # Save results
    save_state(state)

    # Save matches to a report file
    if total_matches > 0:
        report_path = BASE_DIR / "data" / "results" / "watchdog_matches.json"
        existing = []
        if report_path.exists():
            try:
                existing = json.load(open(report_path, "r", encoding="utf-8"))
            except:
                pass

        for filepath in files_to_scan:
            matches = scan_file(filepath, pravidla)
            for doc, rules in matches:
                existing.append({
                    "timestamp": datetime.now().isoformat(),
                    "doc_id": doc.get("id", ""),
                    "action": doc.get("action", ""),
                    "url": doc.get("url", ""),
                    "vestnik": doc.get("vestnik", ""),
                    "obstaravatel": doc.get("extraction", {}).get("obstaravatel", {}).get("nazov", ""),
                    "ico": doc.get("extraction", {}).get("obstaravatel", {}).get("ico", ""),
                    "predmet": doc.get("extraction", {}).get("zakazka", {}).get("predmet", ""),
                    "hodnota": doc.get("extraction", {}).get("prilezitost", {}).get("hodnota"),
                    "lehota": doc.get("extraction", {}).get("prilezitost", {}).get("lehota_datum", ""),
                    "pravidla": [r["nazov"] for r in rules],
                })

        with open(report_path, "w", encoding="utf-8") as f:
            json.dump(existing, f, indent=2, ensure_ascii=False)
        print(f"\nZhody uložené: {report_path}")


if __name__ == "__main__":
    main()
