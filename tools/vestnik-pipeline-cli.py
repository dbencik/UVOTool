#!/usr/bin/env python3
"""
Vestník Pipeline — Claude Code CLI version
Uses `claude -p` instead of direct API calls.
No API key needed — uses your Claude subscription.

Usage:
  python3 tools/vestnik-pipeline-cli.py /tmp/uvo_rss.xml
  python3 tools/vestnik-pipeline-cli.py   # fetches RSS from UVO
"""

import json
import os
import re
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from collections import Counter

OUT_DIR = Path(__file__).parent.parent / "data" / "CFE-Test"
RSS_URL = sys.argv[1] if len(sys.argv) > 1 else "https://www.uvo.gov.sk/vestnik-a-registre/vestnik/rss"

# Same classification from RSS codes
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

PROMPT_VYSLEDOK = """Si expert na verejné obstarávanie na Slovensku. Z dokumentu extrahuj údaje. Odpovedz LEN platným JSON, žiadny iný text.

{
  "obstaravatel": {"nazov": "...", "ico": "...", "email": "..."},
  "zakazka": {"predmet": "max 200 znakov", "cpv_kod": "...", "druh": "tovary|sluzby|stavebne_prace", "hodnota": číslo, "mena": "EUR"},
  "ucastnici": [{"nazov": "...", "ico": "...", "cena": číslo, "je_vitaz": true/false, "skupina": false, "clenovia": []}],
  "pocet_ponuk": číslo
}

NEEXTRAHUJ obstarávateľa, UVO ani poradcov ako uchádzačov.
Ak žiadna firma nepodala ponuku, vráť prázdny zoznam ucastnici.

Dokument:
"""

PROMPT_VYHLASENIE = """Si expert na verejné obstarávanie na Slovensku. Z dokumentu extrahuj údaje o NOVEJ PRÍLEŽITOSTI. Odpovedz LEN platným JSON, žiadny iný text.

{
  "obstaravatel": {"nazov": "...", "ico": "...", "email": "..."},
  "prilezitost": {"predmet": "max 200 znakov", "cpv_kod": "...", "druh": "tovary|sluzby|stavebne_prace", "hodnota": číslo, "mena": "EUR", "lehota_na_ponuky": "...", "trvanie": "..."},
  "podmienky": {"minimalny_obrat": číslo, "referencie": true/false, "certifikacie": [], "ine": "..."},
  "kriteria": {"typ": "najnizsia_cena|ekonomicky_najvyhodnejsia", "vaha_cena": číslo, "vaha_kvalita": číslo},
  "struktura": {"pocet_lotov": číslo, "ramcova_dohoda": true/false, "portal": "...", "eu_fond": "...", "zabezpeka": číslo}
}

Dokument:
"""


def html_to_text(html):
    text = re.sub(r'<script[\s\S]*?</script>', '', html, flags=re.I)
    text = re.sub(r'<style[\s\S]*?</style>', '', text, flags=re.I)
    text = re.sub(r'<[^>]+>', ' ', text)
    text = re.sub(r'&amp;', '&', text)
    text = re.sub(r'&[a-z]+;', ' ', text)
    text = re.sub(r'&#(\d+);', lambda m: chr(int(m.group(1))), text)
    return re.sub(r'\s+', ' ', text).strip()


def call_claude_cli(prompt, text, max_chars=50000):
    """Call claude -p with prompt + text, return parsed JSON."""
    start = time.time()
    full_prompt = prompt + text[:max_chars]

    try:
        result = subprocess.run(
            ["claude", "-p", full_prompt, "--output-format", "json"],
            capture_output=True,
            text=True,
            timeout=120,
        )
        elapsed = time.time() - start

        if result.returncode != 0:
            return {"json": None, "time": elapsed, "error": result.stderr[:200]}

        # claude --output-format json returns {"type":"result","result":"..."}
        raw = result.stdout
        try:
            cli_response = json.loads(raw)
            content = cli_response.get("result", raw)
        except json.JSONDecodeError:
            content = raw

        # Extract JSON from response
        m = re.search(r'\{[\s\S]*\}', content)
        if m:
            try:
                return {"json": json.loads(m.group()), "time": elapsed}
            except json.JSONDecodeError:
                pass
        return {"json": None, "time": elapsed, "raw": content[:200]}

    except subprocess.TimeoutExpired:
        return {"json": None, "time": time.time() - start, "error": "timeout 120s"}
    except FileNotFoundError:
        return {"json": None, "time": 0, "error": "claude CLI not found — install: npm i -g @anthropic-ai/claude-code"}


def main():
    # Check claude CLI is available
    try:
        v = subprocess.run(["claude", "--version"], capture_output=True, text=True, timeout=5)
        version = v.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        print("❌ claude CLI nie je nainštalovaný")
        print("   npm install -g @anthropic-ai/claude-code")
        sys.exit(1)

    print("=" * 60)
    print("VESTNÍK PIPELINE — Claude Code CLI")
    print(f"CLI: {version}")
    print("Cena: $0 (súčasť Claude subscription)")
    print("=" * 60)

    # Parse RSS
    if RSS_URL.startswith("http"):
        import urllib.request
        rss_data = urllib.request.urlopen(RSS_URL, timeout=15).read()
        root = ET.fromstring(rss_data)
    else:
        root = ET.parse(RSS_URL).getroot()

    items = root.findall('.//item')
    print(f"\n📡 {len(items)} dokumentov v RSS\n")

    # Classify
    docs = []
    for item in items:
        title = item.find('title').text or ''
        link = item.find('link').text or ''
        desc = item.find('description').text or ''
        doc_id = link.split('/')[-1] if '/' in link else ''
        parts = title.split(' - ', 1)
        num = parts[0].strip()
        code = parts[1].split(' : ')[0].strip() if len(parts) > 1 and ' : ' in parts[1] else ''
        doc_type = parts[1].split(' : ')[1].strip()[:60] if len(parts) > 1 and ' : ' in parts[1] else ''
        action = CODE_ACTION.get(code, "unknown")
        docs.append({"num": num, "code": code, "type": doc_type, "action": action, "url": link, "id": doc_id, "vestnik": desc})

    actions = Counter(d["action"] for d in docs)
    for a, c in actions.most_common():
        emoji = {"vysledok": "📊", "vyhlasenie": "📢", "oprava": "🔄", "zmena_zmluvy": "📋"}.get(a, "❓")
        print(f"   {emoji} {a}: {c}")

    # Limit for testing
    max_docs = int(os.environ.get("MAX_DOCS", "0")) or len(docs)
    if max_docs < len(docs):
        print(f"\n⚠️  Obmedzené na {max_docs} dokumentov (MAX_DOCS={max_docs})")
        docs = docs[:max_docs]

    # Process
    results = []
    total_time = 0
    processed = 0

    for i, doc in enumerate(docs):
        print(f"\n[{i+1}/{len(docs)}] {doc['num']}-{doc['code']} | {doc['type'][:50]}", flush=True)

        if doc["action"] in ("zmena_zmluvy",):
            print(f"  📋 skip", flush=True)
            results.append({**doc, "result": "skipped"})
            continue

        # Load HTML (local cache or download)
        local_path = Path(f"/tmp/vestnik191_full/{doc['id']}.html")
        if local_path.exists():
            html = local_path.read_text(encoding='utf-8', errors='ignore')
        else:
            import urllib.request
            try:
                html = urllib.request.urlopen(doc["url"], timeout=15).read().decode('utf-8', errors='ignore')
                # Cache locally
                local_path.parent.mkdir(parents=True, exist_ok=True)
                local_path.write_text(html, encoding='utf-8')
            except Exception as e:
                print(f"  ❌ FETCH ERROR: {e}", flush=True)
                results.append({**doc, "result": "fetch_error"})
                continue

        text = html_to_text(html)
        if len(text) < 500:
            print(f"  ❌ empty ({len(text)} chars)", flush=True)
            results.append({**doc, "result": "empty"})
            continue

        # Choose prompt
        if doc["action"] in ("vysledok", "suhrn"):
            prompt = PROMPT_VYSLEDOK
            label = "📊"
        else:
            prompt = PROMPT_VYHLASENIE
            label = "📢"

        # Claude CLI call
        r = call_claude_cli(prompt, text)
        total_time += r["time"]
        processed += 1

        if r.get("error"):
            print(f"  ❌ {r['error']}", flush=True)
        elif r["json"]:
            d = r["json"]
            if "ucastnici" in d:
                for u in d.get("ucastnici", []):
                    mark = "🏆" if u.get("je_vitaz") else "  "
                    cena = f" | {u.get('cena','')} EUR" if u.get("cena") else ""
                    print(f"  {label} {mark} {u.get('nazov','?')} ({u.get('ico','?')}){cena}", flush=True)
                if not d.get("ucastnici"):
                    print(f"  {label} 0 uchádzačov", flush=True)
            elif "prilezitost" in d:
                p = d["prilezitost"]
                print(f"  {label} 💡 {p.get('predmet','?')[:70]}", flush=True)
                print(f"       {p.get('hodnota','?')} {p.get('mena','EUR')} | Lehota: {p.get('lehota_na_ponuky','?')}", flush=True)
        else:
            print(f"  ❌ parse error", flush=True)

        print(f"  ⏱️  {r['time']:.1f}s", flush=True)
        results.append({**doc, "extraction": r["json"], "time": r["time"]})

    # Summary
    print(f"\n{'=' * 60}")
    print(f"VÝSLEDKY — Claude Code CLI")
    print(f"{'=' * 60}")
    print(f"Dokumentov: {len(docs)}")
    print(f"Spracovaných: {processed}")
    print(f"Celkový čas: {total_time:.0f}s ({total_time/60:.1f} min)")
    if processed > 0:
        print(f"Priemer: {total_time/processed:.1f}s/dok")
    print(f"Cena: $0 (Claude subscription)")

    out_file = OUT_DIR / "vestnik_191_cli_results.json"
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with open(out_file, "w") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    print(f"\nUložené: {out_file}")


if __name__ == "__main__":
    main()
