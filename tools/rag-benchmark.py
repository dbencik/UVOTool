#!/usr/bin/env python3
"""
RAG Benchmark — Chunking + Embedding + Focused extraction via Ollama

Compares: full-document extraction vs RAG-based extraction on same UVO pages.
Uses: sentence-transformers (e5-multilingual) + ChromaDB + Ollama (Qwen 7B)
"""

import json
import re
import time
import requests
from pathlib import Path

import chromadb
from sentence_transformers import SentenceTransformer

# Config
OLLAMA_URL = "http://localhost:11434/api/generate"
MODEL = "qwen2.5:7b"
EMBED_MODEL = "intfloat/multilingual-e5-small"
CHUNK_SIZE = 500  # tokens (approx words * 1.3)
CHUNK_OVERLAP = 50
TOP_K = 5
OUT_DIR = Path(__file__).parent.parent / "data" / "CFE-Test"

HTML_DIR = Path(__file__).parent.parent / "data" / "CFE-Test" / "uvo_html"

# ── Questions for VYHLÁSENIA (business intelligence) ──
QUESTIONS_VYHLASENIE = [
    {
        "id": "obstaravatel",
        "query": "obstarávateľ verejný obstarávateľ IČO adresa kontakt email telefón kupujúci zadávateľ",
        "prompt": """Extrahuj údaje o OBSTARÁVATEĽOVI. Odpovedz LEN JSON:
{"nazov": "...", "ico": "...", "email": "...", "telefon": "...", "adresa": "..."}"""
    },
    {
        "id": "prilezitost",
        "query": "predmet zákazky CPV kód predpokladaná hodnota druh postup lehota na predkladanie ponúk trvanie zákazky",
        "prompt": """Extrahuj údaje o NOVEJ PRÍLEŽITOSTI (budúca zákazka). Odpovedz LEN JSON:
{"predmet": "max 200 znakov", "cpv_kod": "...", "druh": "tovary|sluzby|stavebne_prace", "postup": "...", "hodnota": číslo alebo null, "mena": "EUR", "lehota_na_ponuky": "dátum a čas", "trvanie_zakazky": "..."}"""
    },
    {
        "id": "podmienky",
        "query": "podmienky účasti obrat referencie certifikácia ISO bezpečnostná previerka odborná spôsobilosť finančné ekonomické postavenie",
        "prompt": """Extrahuj PODMIENKY ÚČASTI — čo musí uchádzač spĺňať. Odpovedz LEN JSON:
{"minimalny_obrat": číslo alebo null, "mena_obratu": "EUR", "referencie_pozadovane": true/false, "referencie_popis": "...", "certifikacie": ["ISO 9001", "..."] alebo [], "bezpecnostna_previerka": true/false, "ine_podmienky": "max 200 znakov"}"""
    },
    {
        "id": "kriteria",
        "query": "kritériá hodnotenia cena kvalita váha bodovanie najnižšia ekonomicky najvýhodnejšia",
        "prompt": """Extrahuj KRITÉRIÁ HODNOTENIA ponúk. Odpovedz LEN JSON:
{"typ_kriteria": "najnizsia_cena|ekonomicky_najvyhodnejsia", "vaha_cena_percent": číslo alebo null, "vaha_kvalita_percent": číslo alebo null, "dalsie_kriteria": ["popis kritéria a váha"]}"""
    },
    {
        "id": "struktura",
        "query": "lot časť rámcová dohoda elektronické podanie portál Josephine EVO eZákazky subdodávky financovanie EU fond EFRR zábezpeka",
        "prompt": """Extrahuj ŠTRUKTURÁLNE ÚDAJE zákazky. Odpovedz LEN JSON:
{"pocet_lotov": číslo, "ramcova_dohoda": true/false, "max_dodavatelov_ramcova": číslo alebo null, "elektronicke_podanie": true/false, "portal": "...", "subdodavky_povolene": true/false/null, "financovanie_eu": true/false, "eu_fond": "..." alebo null, "zabezpeka_eur": číslo alebo null}"""
    },
]

# ── Questions for VÝSLEDKY (existing) ──
QUESTIONS = [
    {
        "id": "classify",
        "query": "Typ formulára Výsledok víťaz uchádzač ponuka predložená súhrnná správa zákazka dodávateľ zhotoviteľ zmluva uzavretá počet prijatých ponúk",
        "prompt": """Klasifikuj dokument z verejného obstarávania podľa KONKRÉTNYCH ZNAKOV v texte.

HĽADAJ TIETO ZNAKY:

1. Ak text obsahuje "Typ formulára: Výsledok" alebo "Informácia o výbere víťaza" alebo "Bol vybratý aspoň jeden víťaz":
   → typ = "oznamenie_vysledok", relevantny = true

2. Ak text obsahuje "Nebol vybratý žiadny víťaz" alebo "súťaž je ukončená" bez víťaza:
   → typ = "oznamenie_vysledok", relevantny = true (ale počet uchádzačov môže byť 0)

3. Ak text obsahuje "Súhrnná správa" alebo "Typ súhrnnej správy":
   → typ = "sprava_o_zakazke", relevantny = true

4. Ak text obsahuje "Lehota na predkladanie ponúk" alebo "Lehota na prijímanie žiadostí" ALE NEOBSAHUJE "Typ formulára: Výsledok":
   → typ = "oznamenie_vyhlasenie", relevantny = false (ešte nikto nepodal ponuku)

5. Ak text obsahuje "zápisnica z otvárania" alebo "komisia otvorila":
   → typ = "zapisnica", relevantny = true

6. Ak text obsahuje len podmienky účasti, technické špecifikácie:
   → typ = "sutazne_podklady", relevantny = false

Odpovedz LEN JSON:
{"typ": "...", "relevantny": true/false, "popis": "aký konkrétny znak som našiel"}"""
    },
    {
        "id": "obstaravatel",
        "query": "obstarávateľ verejný obstarávateľ IČO adresa kontakt email telefón kupujúci zadávateľ",
        "prompt": """Extrahuj údaje o OBSTARÁVATEĽOVI (ten kto zákazku vyhlásil, nie kto ponuku podal).
Odpovedz LEN JSON:
{"nazov": "...", "ico": "...", "email": "...", "telefon": "...", "adresa": "..."}"""
    },
    {
        "id": "zakazka",
        "query": "predmet zákazky CPV kód predpokladaná hodnota druh postup lehota na predkladanie ponúk",
        "prompt": """Extrahuj údaje o zákazke. Odpovedz LEN JSON:
{"predmet": "max 200 znakov", "cpv_kod": "...", "druh": "tovary|sluzby|stavebne_prace", "postup": "...", "hodnota": číslo alebo null, "mena": "EUR", "lehota": "..."}"""
    },
    {
        "id": "ucastnici",
        "query": "uchádzač ponuka víťaz cena ponuky dodávateľ zhotoviteľ skupina firiem konzorcium podal ponuku úspešný",
        "prompt": """Extrahuj FIRMY ktoré PODALI PONUKU alebo VYHRALI túto zákazku.

DÔLEŽITÉ — TOTO NIE SÚ UCHÁDZAČI (neextrahuj ich):
- Obstarávateľ (ten kto zákazku vyhlásil)
- Úrad pre verejné obstarávanie (UVO) — štátny orgán
- Poradenské firmy pri obstarávaní (napr. LEGAL TENDER, advokátske kancelárie)
- Firmy len zmienené v texte bez podania ponuky

UCHÁDZAČ = firma ktorá podala cenovú ponuku alebo vyhrala zákazku.
Ak žiadna firma nepodala ponuku, vráť prázdny zoznam.
Ak ponuku podala SKUPINA FIRIEM (konzorcium), uveď "skupina": true a "clenovia" s názvami.

Odpovedz LEN JSON:
{"ucastnici": [{"nazov": "...", "ico": "...", "cena": číslo alebo null, "je_vitaz": true/false, "skupina": false, "clenovia": []}], "pocet_ponuk": číslo alebo null}"""
    },
]


def html_to_text(html: str) -> str:
    text = re.sub(r'<script[\s\S]*?</script>', '', html, flags=re.I)
    text = re.sub(r'<style[\s\S]*?</style>', '', text, flags=re.I)
    text = re.sub(r'<nav[\s\S]*?</nav>', '', text, flags=re.I)
    text = re.sub(r'<footer[\s\S]*?</footer>', '', text, flags=re.I)
    text = re.sub(r'<[^>]+>', ' ', text)
    text = re.sub(r'&amp;', '&', text)
    text = re.sub(r'&lt;', '<', text)
    text = re.sub(r'&gt;', '>', text)
    text = re.sub(r'&quot;', '"', text)
    text = re.sub(r'&#(\d+);', lambda m: chr(int(m.group(1))), text)
    text = re.sub(r'&nbsp;', ' ', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text


def chunk_text(text: str, chunk_size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    words = text.split()
    chunks = []
    step = max(chunk_size - overlap, 1)
    for i in range(0, len(words), step):
        chunk = ' '.join(words[i:i + chunk_size])
        if len(chunk.strip()) > 50:
            chunks.append(chunk)
    return chunks


def query_ollama(prompt: str, context: str) -> dict:
    full_prompt = f"{prompt}\n\nText:\n{context}"
    start = time.time()
    resp = requests.post(OLLAMA_URL, json={
        "model": MODEL,
        "prompt": full_prompt,
        "stream": False,
        "options": {"temperature": 0},
    }, timeout=120)
    elapsed = time.time() - start
    raw = resp.json().get("response", "")
    match = re.search(r'\{[\s\S]*\}', raw)
    if match:
        try:
            return {"json": json.loads(match.group()), "time": elapsed, "tokens_in": len(full_prompt.split())}
        except json.JSONDecodeError:
            pass
    return {"json": None, "time": elapsed, "tokens_in": len(full_prompt.split()), "raw": raw[:200]}


def main():
    print("=== RAG Benchmark ===")
    print(f"LLM: {MODEL}")
    print(f"Embedding: {EMBED_MODEL}")
    print(f"Chunk: {CHUNK_SIZE} tok, overlap {CHUNK_OVERLAP}")
    print(f"Top-K: {TOP_K}\n")

    # Load embedding model
    print("Loading embedding model...")
    t0 = time.time()
    embedder = SentenceTransformer(EMBED_MODEL)
    print(f"  Loaded in {time.time() - t0:.1f}s\n")

    results = []
    html_files = sorted(HTML_DIR.glob("*.html"))
    print(f"Lokálne HTML súbory: {len(html_files)}\n")

    for idx, html_path in enumerate(html_files):
        doc_id = html_path.stem
        print(f"\n{'═' * 60}")
        print(f"[{idx + 1}/{len(html_files)}] {doc_id}")

        html = html_path.read_text(encoding="utf-8", errors="ignore")
        text = html_to_text(html)
        url = f"local:{doc_id}"
        print(f"  HTML: {len(html) // 1024}KB → Text: {len(text) // 1024}KB ({len(text.split())} words)")

        if len(text) < 100:
            print(f"  ❌ Text príliš krátky ({len(text)} znakov)")
            results.append({"url": url, "error": "empty"})
            continue

        # Chunk
        chunks = chunk_text(text)
        print(f"  Chunks: {len(chunks)}")

        # Embed chunks
        t0 = time.time()
        chunk_embeddings = embedder.encode(chunks, show_progress_bar=False)
        embed_time = time.time() - t0
        print(f"  Embedding: {embed_time:.1f}s")

        # ChromaDB collection (ephemeral, per document)
        client = chromadb.Client()
        collection = client.create_collection(name=f"doc_{idx}", metadata={"hnsw:space": "cosine"})
        collection.add(
            documents=chunks,
            embeddings=chunk_embeddings.tolist(),
            ids=[f"c{i}" for i in range(len(chunks))],
        )

        # Process each question
        # ── DETERMINISTIC CLASSIFICATION (no LLM needed) ──
        # Parse UVO structural fields from HTML/text
        def find_field(pattern, source=text):
            m = re.search(pattern, source, re.I)
            return m.group(1).strip() if m else None

        # Level 1: Document code (VST, VSS, VUT, VUS, IPT, IPS, IOX, INT, MST, MUT...)
        doc_code = find_field(r'Oznámenie\s+\d+\s*-\s*(\w+)', html) or find_field(r'(\w{2,3})\s+výsledok', text)

        # Level 2: Typ oznámenia, Typ formulára, Podtyp
        typ_oznamenia = find_field(r'Typ oznámenia:\s*([^\n<]+)')
        typ_formulara = find_field(r'Typ formulára:\s*([^\n<]+)')
        podtyp = find_field(r'Podtyp oznámenia:\s*([^\n<]+)')

        # Level 3: Specific markers
        has_vysledok = bool(typ_formulara and 'ýsledok' in typ_formulara) or bool(typ_oznamenia and 'ýsledku' in typ_oznamenia)
        has_vyhlasenie = bool(typ_oznamenia and 'yhlásení' in typ_oznamenia) or bool(typ_formulara and 'úťaž' in typ_formulara)
        has_suhrn = bool(re.search(r'[Ss]úhrnná správa', text))
        has_zapisnica = bool(re.search(r'[Zz]ápisnica', text[:2000]))
        has_vitaz = bool(re.search(r'víťaz|vybratý.*dodávateľ|zmluva uzavretá', text, re.I))
        has_lehota = bool(re.search(r'[Ll]ehota na predkladanie ponúk', text))

        # Classify
        if has_suhrn:
            det_typ = "sprava_o_zakazke"
            det_relevant = True
        elif has_vysledok and has_vitaz:
            det_typ = "oznamenie_vysledok"
            det_relevant = True
        elif has_vysledok and not has_vitaz:
            det_typ = "oznamenie_vysledok_bez_vitaza"
            det_relevant = True  # still relevant (0 tenders case)
        elif has_vyhlasenie or (has_lehota and not has_vysledok):
            det_typ = "oznamenie_vyhlasenie"
            det_relevant = False
        elif has_zapisnica:
            det_typ = "zapisnica"
            det_relevant = True
        else:
            det_typ = "iny"
            det_relevant = False

        print(f"  📋 Deterministická klasifikácia: {det_typ} | relevant={det_relevant}")
        if typ_formulara: print(f"     Typ formulára: {typ_formulara}")
        if typ_oznamenia: print(f"     Typ oznámenia: {typ_oznamenia}")
        if doc_code: print(f"     Kód: {doc_code}")

        doc_result = {"url": url, "doc_id": doc_id, "text_kb": len(text) / 1024, "chunks": len(chunks), "embed_time": embed_time, "questions": {},
                      "det_classification": {"typ": det_typ, "relevant": det_relevant, "typ_formulara": typ_formulara, "typ_oznamenia": typ_oznamenia, "doc_code": doc_code}}
        total_llm_time = 0
        total_tokens = 0
        obstaravatel_name = None

        # If regex found nothing (typ="iny") → fallback to LLM classification
        if det_typ == "iny":
            print(f"  🔄 Regex nenašiel UVO markery → fallback na LLM klasifikáciu")
            fallback_query = "správa o zákazke výsledok víťaz uchádzač ponuka zápisnica vyhodnotenie dodávateľ zmluva uzavretá"
            fallback_embedding = embedder.encode([fallback_query], show_progress_bar=False)
            fallback_search = collection.query(query_embeddings=fallback_embedding.tolist(), n_results=TOP_K)
            fallback_context = "\n\n".join(fallback_search["documents"][0]) if fallback_search["documents"] else ""

            fallback_result = query_ollama(
                """Urči typ tohto dokumentu z verejného obstarávania. Odpovedz LEN JSON.

Typy:
- "oznamenie_vysledok" — obsahuje mená firiem ktoré podali ponuku alebo vyhrali zákazku
- "sprava_o_zakazke" — správa s dodávateľmi a cenami zmlúv
- "zapisnica" — zápisnica z otvárania alebo vyhodnotenia ponúk
- "oznamenie_vyhlasenie" — vyhlásenie novej zákazky, ešte žiadne ponuky
- "iny" — iné (zmluva, podmienky, metodika)

relevantny = true IBA ak dokument obsahuje KONKRÉTNE MENÁ FIRIEM ktoré podali ponuku alebo vyhrali.

{"typ": "...", "relevantny": true/false, "popis": "1 veta"}""",
                fallback_context
            )

            if fallback_result["json"]:
                det_typ = fallback_result["json"].get("typ", "iny")
                det_relevant = fallback_result["json"].get("relevantny", False)
                total_llm_time += fallback_result["time"]
                print(f"  📋 LLM fallback klasifikácia: {det_typ} | relevant={det_relevant} ({fallback_result['time']:.1f}s)")
                doc_result["det_classification"]["fallback"] = "llm"
                doc_result["det_classification"]["typ"] = det_typ
                doc_result["det_classification"]["relevant"] = det_relevant
            else:
                print(f"  ❌ LLM fallback zlyhal → preskakujem")

        # Choose question set based on document type
        is_vyhlasenie = det_typ in ("oznamenie_vyhlasenie",)
        if is_vyhlasenie:
            active_questions = QUESTIONS_VYHLASENIE
            print(f"  📢 VYHLÁSENIE → extrakcia príležitosti (business intelligence)")
        elif not det_relevant:
            print(f"  ⏭️  Nerelevantný (zmluva/podklady) → preskakujem")
            results.append(doc_result)
            continue
        else:
            active_questions = [q for q in QUESTIONS if q["id"] != "classify"]
            print(f"  📊 VÝSLEDOK → extrakcia účastníkov")

        for q in active_questions:
            # Retrieve relevant chunks
            query_embedding = embedder.encode([q["query"]], show_progress_bar=False)
            search = collection.query(query_embeddings=query_embedding.tolist(), n_results=TOP_K)
            relevant_chunks = search["documents"][0] if search["documents"] else []
            context = "\n\n".join(relevant_chunks)
            context_tokens = len(context.split())

            # Inject obstarávateľ name into participant prompt to prevent false extraction
            prompt = q["prompt"]
            if q["id"] == "ucastnici" and obstaravatel_name:
                prompt = prompt.replace(
                    "TOTO NIE SÚ UCHÁDZAČI (neextrahuj ich):",
                    f"TOTO NIE SÚ UCHÁDZAČI (neextrahuj ich):\n- {obstaravatel_name} — to je OBSTARÁVATEĽ tejto zákazky"
                )

            # Query LLM
            result = query_ollama(prompt, context)
            total_llm_time += result["time"]
            total_tokens += result["tokens_in"]

            success = result["json"] is not None
            marker = "✅" if success else "❌"

            if q["id"] == "obstaravatel" and result["json"]:
                obstaravatel_name = result["json"].get("nazov")

            if q["id"] == "prilezitost" and result["json"]:
                d = result["json"]
                print(f"  {marker} Príležitosť: {d.get('predmet', '?')[:80]}")
                print(f"     CPV: {d.get('cpv_kod', '?')} | Hodnota: {d.get('hodnota', '?')} {d.get('mena', '')} | Lehota: {d.get('lehota_na_ponuky', '?')}")
            elif q["id"] == "podmienky" and result["json"]:
                d = result["json"]
                certs = ', '.join(d.get('certifikacie', [])) or 'žiadne'
                print(f"  {marker} Podmienky: obrat={d.get('minimalny_obrat', '?')}, referencie={'áno' if d.get('referencie_pozadovane') else 'nie'}, certifikácie=[{certs}]")
            elif q["id"] == "kriteria" and result["json"]:
                d = result["json"]
                print(f"  {marker} Kritériá: {d.get('typ_kriteria', '?')} | cena={d.get('vaha_cena_percent', '?')}% kvalita={d.get('vaha_kvalita_percent', '?')}%")
            elif q["id"] == "struktura" and result["json"]:
                d = result["json"]
                print(f"  {marker} Štruktúra: lotov={d.get('pocet_lotov', '?')} | rámcová={d.get('ramcova_dohoda', '?')} | portál={d.get('portal', '?')} | EU={d.get('eu_fond', 'nie')}")
            elif q["id"] == "ucastnici" and result["json"]:
                ucast = result["json"].get("ucastnici", [])
                vitazi = [u for u in ucast if u.get("je_vitaz")]
                skupiny = [u for u in ucast if u.get("skupina")]
                print(f"  {marker} Účastníci: {len(ucast)} uchádzačov, {len(vitazi)} víťazov, {len(skupiny)} skupín ({result['time']:.1f}s, {context_tokens} tok)")
                for u in ucast:
                    mark = "🏆" if u.get("je_vitaz") else "  "
                    cena = f" | {u.get('cena', '')} EUR" if u.get("cena") else ""
                    skupina = f" [SKUPINA: {', '.join(u.get('clenovia', []))}]" if u.get("skupina") else ""
                    print(f"    {mark} {u.get('nazov', '?')} ({u.get('ico', 'bez IČO')}){cena}{skupina}")
            else:
                fields = len([v for v in (result["json"] or {}).values() if v is not None and v != "" and v != "null"]) if result["json"] else 0
                print(f"  {marker} {q['id']}: {fields} polí ({result['time']:.1f}s, {context_tokens} tok kontext)")

            doc_result["questions"][q["id"]] = {
                "success": success,
                "time": result["time"],
                "context_tokens": context_tokens,
                "data": result["json"],
            }

            # (classify is handled deterministically above)

        doc_result["total_llm_time"] = total_llm_time
        doc_result["total_tokens"] = total_tokens
        print(f"  ⏱️  Celkom: {total_llm_time:.1f}s LLM + {embed_time:.1f}s embedding")

        # Cleanup
        client.delete_collection(f"doc_{idx}")
        results.append(doc_result)

    # Summary
    print(f"\n{'═' * 60}")
    print("VÝSLEDKY — RAG BENCHMARK")
    print(f"{'═' * 60}")
    print(f"Model: {MODEL}")
    print(f"Embedding: {EMBED_MODEL}")
    print(f"Dokumentov: {len(results)}")

    successful = [r for r in results if "questions" in r]
    relevant = [r for r in successful if r.get("det_classification", {}).get("relevant")]
    avg_time = sum(r["total_llm_time"] for r in successful) / len(successful) if successful else 0
    avg_embed = sum(r.get("embed_time", 0) for r in successful) / len(successful) if successful else 0

    print(f"Úspešne spracované: {len(successful)}")
    print(f"Relevantné: {len(relevant)}")
    print(f"Priemerný čas LLM: {avg_time:.1f}s")
    print(f"Priemerný čas embedding: {avg_embed:.1f}s")
    print(f"Priemerný celkový čas: {avg_time + avg_embed:.1f}s")

    # Save
    out_file = OUT_DIR / f"rag_benchmark_{MODEL.replace(':', '_').replace('/', '_')}.json"
    with open(out_file, "w") as f:
        json.dump(results, f, indent=2, ensure_ascii=False, default=str)
    print(f"\nUložené: {out_file}")


if __name__ == "__main__":
    main()
