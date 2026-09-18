# UVOTool — Extrakcia dát z Vestníka VO

Deterministický parser + LLM pipeline pre automatickú extrakciu štruktúrovaných dát
z dokumentov verejného obstarávania (UVO Vestník, TED).

## Čo to robí

- Stiahne dokumenty z UVO Vestníka (RSS alebo listing URL)
- Extrahuje: víťazov, IČO, ceny, príležitosti, lehoty, zmeny zmlúv
- **Parser:** 59 dokumentov za 62ms (bez LLM)
- **LLM pipeline:** fallback pre neštandardné dokumenty

## Rýchle použitie

```bash
# Aktuálny vestník (z RSS)
python3 tools/vestnik-parser.py

# Konkrétny vestník
python3 tools/vestnik-parser.py 190/2026

# Lokálny adresár s HTML
python3 tools/vestnik-parser.py /tmp/vestnik191_full/

# RSS súbor
python3 tools/vestnik-parser.py /tmp/uvo_rss.xml
```

## Porovnanie metód (Vestník 191, 59 dokumentov)

| Metóda | Čas | Priemer/dok | Cena |
|--------|-----|-------------|------|
| **Deterministický parser** | **62ms** | **1.1ms** | **$0** |
| Claude API | 9.7 min | 10.4s | $2.61 |
| Claude CLI | 13.5 min | 14.4s | $0 |
| Qwen 7B + RAG | 70 min | 75s | $0 |

## Štruktúra

```
UVOTool/
├── tools/
│   ├── vestnik-parser.py          # Deterministický parser (hlavný)
│   ├── vestnik-cron.sh            # Denný cron pre VPS
│   ├── vestnik-pipeline.py        # Qwen 7B + RAG pipeline
│   ├── vestnik-pipeline-claude.py # Claude API pipeline
│   ├── vestnik-pipeline-cli.py    # Claude CLI pipeline
│   ├── rag-benchmark.py           # RAG benchmark (v1-v5)
│   ├── ollama-qa-benchmark.py     # Ollama Q&A benchmark (3 modely)
│   ├── llm-benchmark*.ts          # LLM benchmarky (TypeScript)
│   └── generate-pipeline-docx.ts  # Generátor Pipeline DOCX dokumentu
├── data/
│   ├── results/                   # JSON výsledky z parserov
│   ├── otazky-llm/                # Q&A testové súbory + benchmark
│   ├── LLM_BENCHMARK_REPORT.md    # Kompletný technický report
│   └── Pipeline_VO_Extrakcia.docx # Pipeline dokumentácia
└── README.md
```

## Typy dokumentov

| Typ | RSS kódy | Extrahované údaje |
|-----|----------|-------------------|
| Výsledky | VUS, VST, VUT, IPP, IPT, IPS | Víťaz, IČO, cena, počet ponúk |
| Vyhlásenia | MST, MSS, MSP, WYP, WYT, MUS | Predmet, hodnota, lehota, CPV, kritériá |
| Opravy | IOX | Opravené údaje pôvodného oznámenia |
| Zmeny zmluvy | DOP, DOT, DOS | Dodávateľ, hodnota po zmene, dôvod |

## Cron (VPS)

```bash
# Deploy na VPS
scp tools/vestnik-cron.sh tools/vestnik-parser.py root@VPS:/opt/vestnik/
# Crontab: Po-Pi o 23:00
0 23 * * 1-5 /opt/vestnik/vestnik-cron.sh >> /opt/vestnik/logs/cron.log 2>&1
```

## Požiadavky

- Python 3.10+
- Pre LLM pipeline: Ollama (qwen2.5:7b), chromadb, sentence-transformers
- Pre Claude pipeline: ANTHROPIC_API_KEY alebo Claude CLI
- Pre DOCX generátor: Node.js + `npx tsx`
