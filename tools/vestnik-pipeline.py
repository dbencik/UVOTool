#!/usr/bin/env python3
"""
Complete Vestník Pipeline — RSS → Download → Classify → Extract

1. Parse RSS feed (all document IDs + types)
2. Classify from RSS code (deterministic, no LLM)
3. Download relevant HTML pages
4. RAG extraction (Qwen 7B)
5. Output JSON results

Usage:
  tools/rag-env/bin/python3 tools/vestnik-pipeline.py [RSS_URL]
"""

import json
import re
import time
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from collections import Counter

import requests
import chromadb
from sentence_transformers import SentenceTransformer

OLLAMA_URL = "http://localhost:11434/api/generate"
MODEL = "qwen2.5:7b"
EMBED_MODEL = "intfloat/multilingual-e5-small"
CHUNK_SIZE = 500
CHUNK_OVERLAP = 50
TOP_K = 5
OUT_DIR = Path(__file__).parent.parent / "data" / "CFE-Test"

RSS_URL = sys.argv[1] if len(sys.argv) > 1 else "https://www.uvo.gov.sk/vestnik-a-registre/vestnik/rss"

# ═══════════════════════════════════
# RSS Code → Action mapping
# ═══════════════════════════════════

CODE_ACTION = {
    # Výsledky → extract participants
    "VST": "vysledok", "VSS": "vysledok", "VSP": "vysledok",
    "VUT": "vysledok", "VUS": "vysledok", "VUP": "vysledok",
    "IPT": "vysledok", "IPS": "vysledok", "IPP": "vysledok",
    # Vyhlásenia → business intelligence
    "MST": "vyhlasenie", "MSS": "vyhlasenie", "MSP": "vyhlasenie",
    "MUT": "vyhlasenie", "MUS": "vyhlasenie", "MUP": "vyhlasenie",
    "WYT": "vyhlasenie", "WYS": "vyhlasenie", "WYP": "vyhlasenie",
    # Opravy → business intelligence (updated info)
    "IOX": "oprava",
    # Zmeny zmlúv → track
    "DOP": "zmena_zmluvy", "DOT": "zmena_zmluvy", "DOS": "zmena_zmluvy",
    # Súhrnné správy
    "INT": "suhrn",
}

# Questions per action type
Q_VYSLEDOK = [
    {"id": "obstaravatel", "query": "obstarávateľ IČO adresa kontakt email",
     "prompt": "Extrahuj údaje o OBSTARÁVATEĽOVI. Odpovedz LEN JSON:\n{\"nazov\": \"..\", \"ico\": \"..\", \"email\": \"..\", \"telefon\": \"..\"}"},
    {"id": "zakazka", "query": "predmet zákazky CPV kód predpokladaná hodnota",
     "prompt": "Extrahuj údaje o zákazke. Odpovedz LEN JSON:\n{\"predmet\": \"max 200 znakov\", \"cpv_kod\": \"..\", \"druh\": \"tovary|sluzby|stavebne_prace\", \"hodnota\": číslo alebo null, \"mena\": \"EUR\"}"},
    {"id": "ucastnici", "query": "uchádzač ponuka víťaz cena dodávateľ zhotoviteľ konzorcium",
     "prompt": """Extrahuj FIRMY ktoré PODALI PONUKU alebo VYHRALI zákazku.
NEEXTRAHUJ: obstarávateľa, UVO, poradcov.
Ak žiadna firma nepodala ponuku, vráť prázdny zoznam.
Odpovedz LEN JSON:
{"ucastnici": [{"nazov": "..", "ico": "..", "cena": číslo, "je_vitaz": true/false, "skupina": false, "clenovia": []}], "pocet_ponuk": číslo}"""},
]

Q_VYHLASENIE = [
    {"id": "obstaravatel", "query": "obstarávateľ IČO adresa kontakt email",
     "prompt": "Extrahuj údaje o OBSTARÁVATEĽOVI. Odpovedz LEN JSON:\n{\"nazov\": \"..\", \"ico\": \"..\", \"email\": \"..\", \"telefon\": \"..\"}"},
    {"id": "prilezitost", "query": "predmet zákazky CPV hodnota lehota na ponuky trvanie",
     "prompt": "Extrahuj údaje o NOVEJ PRÍLEŽITOSTI. Odpovedz LEN JSON:\n{\"predmet\": \"max 200 znakov\", \"cpv_kod\": \"..\", \"druh\": \"tovary|sluzby|stavebne_prace\", \"hodnota\": číslo, \"mena\": \"EUR\", \"lehota_na_ponuky\": \"..\", \"trvanie\": \"..\"}"},
    {"id": "podmienky", "query": "podmienky účasti obrat referencie certifikácia",
     "prompt": "Extrahuj PODMIENKY ÚČASTI. Odpovedz LEN JSON:\n{\"minimalny_obrat\": číslo, \"referencie\": true/false, \"certifikacie\": [], \"ine\": \"..\"}"},
    {"id": "kriteria", "query": "kritériá hodnotenia cena kvalita váha",
     "prompt": "Extrahuj KRITÉRIÁ HODNOTENIA. Odpovedz LEN JSON:\n{\"typ\": \"najnizsia_cena|ekonomicky_najvyhodnejsia\", \"vaha_cena\": číslo, \"vaha_kvalita\": číslo}"},
    {"id": "struktura", "query": "lot rámcová dohoda portál financovanie EU zábezpeka",
     "prompt": "Extrahuj ŠTRUKTÚRU zákazky. Odpovedz LEN JSON:\n{\"pocet_lotov\": číslo, \"ramcova_dohoda\": true/false, \"portal\": \"..\", \"eu_fond\": \"..\", \"zabezpeka\": číslo}"},
]


def html_to_text(html):
    text = re.sub(r'<script[\s\S]*?</script>', '', html, flags=re.I)
    text = re.sub(r'<style[\s\S]*?</style>', '', text, flags=re.I)
    text = re.sub(r'<[^>]+>', ' ', text)
    text = re.sub(r'&amp;', '&', text)
    text = re.sub(r'&[a-z]+;', ' ', text)
    text = re.sub(r'&#(\d+);', lambda m: chr(int(m.group(1))), text)
    return re.sub(r'\s+', ' ', text).strip()


def chunk_text(text, size=CHUNK_SIZE, overlap=CHUNK_OVERLAP):
    words = text.split()
    step = max(size - overlap, 1)
    return [' '.join(words[i:i+size]) for i in range(0, len(words), step) if len(' '.join(words[i:i+size]).strip()) > 50]


def query_ollama(prompt, context):
    full = f"{prompt}\n\nText:\n{context}"
    start = time.time()
    try:
        resp = requests.post(OLLAMA_URL, json={"model": MODEL, "prompt": full, "stream": False, "options": {"temperature": 0}}, timeout=120)
        raw = resp.json().get("response", "")
    except:
        return {"json": None, "time": time.time() - start}
    elapsed = time.time() - start
    m = re.search(r'\{[\s\S]*\}', raw)
    if m:
        try: return {"json": json.loads(m.group()), "time": elapsed}
        except: pass
    return {"json": None, "time": elapsed}


def main():
    print("=" * 60)
    print("VESTNÍK PIPELINE — RSS → Download → Extract")
    print("=" * 60)

    # 1. Parse RSS (local file or URL)
    print(f"\n📡 RSS zdroj: {RSS_URL}")
    if RSS_URL.startswith("http"):
        rss_resp = requests.get(RSS_URL, timeout=15, verify=False)
        root = ET.fromstring(rss_resp.content)
    else:
        tree = ET.parse(RSS_URL)
        root = tree.getroot()
    items = root.findall('.//item')
    print(f"   {len(items)} dokumentov v RSS\n")

    # 2. Classify from RSS code
    docs = []
    for item in items:
        title = item.find('title').text or ''
        link = item.find('link').text or ''
        desc = item.find('description').text or ''
        doc_id = link.split('/')[-1] if '/' in link else ''

        # Parse code from title: "13386 - VSS : Oznámenie..."
        parts = title.split(' - ', 1)
        num = parts[0].strip() if parts else ''
        code_and_type = parts[1] if len(parts) > 1 else ''
        code = code_and_type.split(' : ')[0].strip() if ' : ' in code_and_type else ''
        doc_type = code_and_type.split(' : ')[1].strip() if ' : ' in code_and_type else ''

        action = CODE_ACTION.get(code, "unknown")

        docs.append({
            "num": num, "code": code, "type": doc_type[:60],
            "action": action, "url": link, "id": doc_id, "vestnik": desc,
        })

    # Stats
    actions = Counter(d["action"] for d in docs)
    print("📊 Klasifikácia z RSS (deterministická):")
    for a, c in actions.most_common():
        emoji = {"vysledok": "📊", "vyhlasenie": "📢", "oprava": "🔄", "zmena_zmluvy": "📋", "suhrn": "📑"}.get(a, "❓")
        print(f"   {emoji} {a}: {c}")

    # 3. Load embedding model
    print(f"\n🔄 Loading embedding model...", flush=True)
    embedder = SentenceTransformer(EMBED_MODEL)
    print(f"   ✅ Embedding model loaded", flush=True)

    # 4. Process each document
    results = []
    total_time = 0
    processed = 0
    skipped = 0

    for i, doc in enumerate(docs):
        print(f"\n{'─' * 60}")
        print(f"[{i+1}/{len(docs)}] {doc['num']}-{doc['code']} | {doc['type'][:50]}")

        action = doc["action"]

        # Skip zmena_zmluvy for now
        if action in ("zmena_zmluvy",):
            print(f"  📋 Zmena zmluvy → skip (len tracking)")
            skipped += 1
            results.append({**doc, "result": "skipped"})
            continue

        # Choose questions
        if action in ("vysledok", "suhrn"):
            questions = Q_VYSLEDOK
            print(f"  📊 VÝSLEDOK → extrakcia účastníkov")
        elif action in ("vyhlasenie", "oprava"):
            questions = Q_VYHLASENIE
            print(f"  📢 PRÍLEŽITOSŤ → business intelligence")
        else:
            # Unknown code → LLM fallback would go here
            print(f"  ❓ Neznámy kód '{doc['code']}' → skip")
            skipped += 1
            results.append({**doc, "result": "unknown_code"})
            continue

        # Load HTML (local file or download)
        local_path = Path(f"/tmp/vestnik191_full/{doc['id']}.html")
        if local_path.exists():
            html = local_path.read_text(encoding='utf-8', errors='ignore')
        else:
            try:
                resp = requests.get(doc["url"], timeout=15, verify=False)
                html = resp.text
            except:
                print(f"  ❌ FETCH ERROR")
                results.append({**doc, "result": "fetch_error"})
                continue

        text = html_to_text(html)
        if len(text) < 500:
            print(f"  ❌ Prázdna stránka ({len(text)} znakov)")
            results.append({**doc, "result": "empty"})
            continue

        print(f"  📄 {len(html)//1024}KB HTML → {len(text)//1024}KB text")

        # Chunk + embed
        chunks = chunk_text(text)
        if not chunks:
            print(f"  ❌ Žiadne chunky")
            results.append({**doc, "result": "no_chunks"})
            continue

        embeddings = embedder.encode(chunks, show_progress_bar=False)
        client = chromadb.Client()
        coll = client.create_collection(f"doc_{i}", metadata={"hnsw:space": "cosine"})
        coll.add(documents=chunks, embeddings=embeddings.tolist(), ids=[f"c{j}" for j in range(len(chunks))])

        # Extract
        doc_result = {**doc, "extractions": {}}
        doc_time = 0
        obstaravatel_name = None

        for q in questions:
            qe = embedder.encode([q["query"]], show_progress_bar=False)
            search = coll.query(query_embeddings=qe.tolist(), n_results=TOP_K)
            context = "\n\n".join(search["documents"][0]) if search["documents"] else ""

            prompt = q["prompt"]
            if q["id"] == "ucastnici" and obstaravatel_name:
                prompt = f"NEEXTRAHUJ {obstaravatel_name} — to je obstarávateľ.\n\n" + prompt

            r = query_ollama(prompt, context)
            doc_time += r["time"]

            if q["id"] == "obstaravatel" and r["json"]:
                obstaravatel_name = r["json"].get("nazov")

            doc_result["extractions"][q["id"]] = r["json"]

            # Print key results
            if q["id"] == "ucastnici" and r["json"]:
                for u in r["json"].get("ucastnici", []):
                    mark = "🏆" if u.get("je_vitaz") else "  "
                    cena = f" | {u.get('cena','')} EUR" if u.get("cena") else ""
                    print(f"    {mark} {u.get('nazov','?')} ({u.get('ico','?')}){cena}")
            elif q["id"] == "prilezitost" and r["json"]:
                d = r["json"]
                print(f"    💡 {d.get('predmet','?')[:70]}")
                print(f"       {d.get('hodnota','?')} {d.get('mena','EUR')} | Lehota: {d.get('lehota_na_ponuky','?')}")

        doc_result["time"] = doc_time
        total_time += doc_time
        processed += 1
        print(f"  ⏱️  {doc_time:.1f}s")

        client.delete_collection(f"doc_{i}")
        results.append(doc_result)

    # Summary
    print(f"\n{'=' * 60}")
    print(f"VÝSLEDKY")
    print(f"{'=' * 60}")
    print(f"Vestník: {docs[0]['vestnik'] if docs else '?'}")
    print(f"Celkom dokumentov: {len(docs)}")
    print(f"Spracovaných: {processed}")
    print(f"Preskočených: {skipped}")
    print(f"Celkový čas: {total_time:.0f}s ({total_time/60:.1f} min)")
    if processed > 0:
        print(f"Priemer: {total_time/processed:.1f}s/dok")

    # Save
    out_file = OUT_DIR / "vestnik_191_results.json"
    with open(out_file, "w") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    print(f"\nUložené: {out_file}")


if __name__ == "__main__":
    main()
