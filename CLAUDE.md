# UVOTool

## Popis projektu

Nástroj na automatickú extrakciu štruktúrovaných dát z dokumentov verejného obstarávania
na Slovensku (UVO Vestník). Oddelený z peppol-faktura repozitára.

## Hlavný nástroj

**tools/vestnik-parser.py** — deterministický regex parser na UVO HTML stránky.
- 59 dokumentov za 62ms (1.1ms/dok), žiadny LLM
- Parsuje `<ul class="notice-list">` kontajnery (6 pre výsledky, 5 pre vyhlásenia, 7 pre zmeny)
- Vstup: RSS XML, listing URL, lokálny adresár, alebo "190/2026" shorthand
- Výstup: JSON s obstarávateľom, zákazkou, víťazmi (IČO), príležitosťami, zmenami zmlúv

## Alternatívne pipeline (pre neštandardné dokumenty)

- **vestnik-pipeline.py** — lokálny Qwen 7B + RAG (ChromaDB, venv: tools/rag-env/)
- **vestnik-pipeline-claude.py** — Claude API (Sonnet 4.6), $2.61/vestník
- **vestnik-pipeline-cli.py** — Claude CLI (`claude -p`), $0

## Konvencie

- **Jazyk kódu:** Python 3.10+ a TypeScript
- **Jazyk UI/výstupov:** slovenčina
- Výstup vždy JSON
- HTML cache v /tmp/vestnikXXX_full/

## VPS nasadenie

- Hostinger VPS: 147.93.121.59
- Cron: tools/vestnik-cron.sh (Po-Pi 23:00)
- Cieľ: /opt/vestnik/

## Testované vestníky

- 188, 189, 190, 191 (2026) — 229 dokumentov, 0 chýb
- Zoznam všetkých vestníkov od 2020: data/results/vestnik_urls.csv
