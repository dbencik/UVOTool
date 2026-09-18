#!/usr/bin/env python3
"""
Vestník Pipeline — Claude API version
Same RSS parsing + regex classification, but uses Claude API instead of local LLM.
No RAG needed — Claude handles full documents natively.

Usage:
  ANTHROPIC_API_KEY=sk-... python3 tools/vestnik-pipeline-claude.py /tmp/uvo_rss.xml
"""

import json
import os
import re
import time
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from collections import Counter

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
MODEL = "claude-sonnet-4-6"
OUT_DIR = Path(__file__).parent.parent / "data" / "CFE-Test"
RSS_URL = sys.argv[1] if len(sys.argv) > 1 else "https://www.uvo.gov.sk/vestnik-a-registre/vestnik/rss"

# Same classification from RSS
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

PROMPT_VYSLEDOK = """Si expert na verejné obstarávanie na Slovensku. Z dokumentu extrahuj údaje. Odpovedz LEN platným JSON.

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

PROMPT_VYHLASENIE = """Si expert na verejné obstarávanie na Slovensku. Z dokumentu extrahuj údaje o NOVEJ PRÍLEŽITOSTI. Odpovedz LEN platným JSON.

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


def call_claude(prompt, text):
    import urllib.request
    start = time.time()
    body = json.dumps({
        "model": MODEL,
        "max_tokens": 2048,
        "messages": [{"role": "user", "content": prompt + text[:50000]}]
    }).encode()

    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=body,
        headers={
            "Content-Type": "application/json",
            "x-api-key": ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read())
    except Exception as e:
        return {"json": None, "time": time.time() - start, "error": str(e)}

    elapsed = time.time() - start
    raw = data.get("content", [{}])[0].get("text", "")
    usage = data.get("usage", {})
    input_tokens = usage.get("input_tokens", 0)
    output_tokens = usage.get("output_tokens", 0)

    m = re.search(r'\{[\s\S]*\}', raw)
    if m:
        try:
            return {"json": json.loads(m.group()), "time": elapsed, "input_tokens": input_tokens, "output_tokens": output_tokens}
        except:
            pass
    return {"json": None, "time": elapsed, "raw": raw[:200], "input_tokens": input_tokens, "output_tokens": output_tokens}


def main():
    if not ANTHROPIC_API_KEY:
        print("❌ ANTHROPIC_API_KEY nie je nastavený")
        print("   export ANTHROPIC_API_KEY=sk-ant-...")
        sys.exit(1)

    print("=" * 60)
    print("VESTNÍK PIPELINE — Claude API")
    print(f"Model: {MODEL}")
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

    # Process
    results = []
    total_time = 0
    total_input = 0
    total_output = 0
    processed = 0

    for i, doc in enumerate(docs):
        print(f"\n[{i+1}/{len(docs)}] {doc['num']}-{doc['code']} | {doc['type'][:50]}", flush=True)

        if doc["action"] in ("zmena_zmluvy",):
            print(f"  📋 skip", flush=True)
            results.append({**doc, "result": "skipped"})
            continue

        # Load HTML (local or download)
        local_path = Path(f"/tmp/vestnik191_full/{doc['id']}.html")
        if local_path.exists():
            html = local_path.read_text(encoding='utf-8', errors='ignore')
        else:
            import urllib.request
            try:
                html = urllib.request.urlopen(doc["url"], timeout=15).read().decode('utf-8', errors='ignore')
            except:
                print(f"  ❌ FETCH ERROR", flush=True)
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

        # Single Claude API call (no RAG needed)
        r = call_claude(prompt, text)
        total_time += r["time"]
        total_input += r.get("input_tokens", 0)
        total_output += r.get("output_tokens", 0)
        processed += 1

        if r["json"]:
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

        print(f"  ⏱️  {r['time']:.1f}s | {r.get('input_tokens',0)} in + {r.get('output_tokens',0)} out", flush=True)
        results.append({**doc, "extraction": r["json"], "time": r["time"], "tokens": r.get("input_tokens", 0) + r.get("output_tokens", 0)})

    # Summary
    print(f"\n{'=' * 60}")
    print(f"VÝSLEDKY — Claude API")
    print(f"{'=' * 60}")
    print(f"Model: {MODEL}")
    print(f"Dokumentov: {len(docs)}")
    print(f"Spracovaných: {processed}")
    print(f"Celkový čas: {total_time:.0f}s ({total_time/60:.1f} min)")
    if processed > 0:
        print(f"Priemer: {total_time/processed:.1f}s/dok")
    print(f"Tokeny: {total_input} vstup + {total_output} výstup = {total_input+total_output}")
    cost_in = total_input / 1_000_000 * 3.0
    cost_out = total_output / 1_000_000 * 15.0
    print(f"Cena: ${cost_in:.2f} (vstup) + ${cost_out:.2f} (výstup) = ${cost_in+cost_out:.2f}")

    out_file = OUT_DIR / "vestnik_191_claude_results.json"
    with open(out_file, "w") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    print(f"\nUložené: {out_file}")


if __name__ == "__main__":
    main()
