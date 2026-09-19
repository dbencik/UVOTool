# UVOTool

Platforma na analýzu verejného obstarávania SR s integráciou 14+ dátových zdrojov.

## Štatistiky

- **15 033 dokumentov** z UVO + TED (184 vestníkov 2026, legacy 2021-2022, TED SK+CZ+HU+PL)
- **3.5M záznamov** v SQLite DB (560 MB) z FR SR registrov
- **13 pipeline modulov**, 18 API endpointov, 76 commitov
- **107 unit testov**, 24 skriptov (12 800+ riadkov Python)

## Architektúra

```
tools/               — parsery, importy, analýzy, profiling (24 skriptov)
pipeline/            — modul registry, engine, DAG executor
dashboard/           — single-file HTML dashboard (4000 riadkov) + serve.py API (1450 riadkov)
config/              — pipeline templates, watchlist
data/results/        — JSON výstupy parserov
data/vestnik.db      — SQLite DB (v .gitignore, 560MB)
```

## Dátové zdroje (13 pipeline modulov)

| Modul | Zdroj | Dáta | API/Metóda |
|---|---|---|---|
| `orsf` | ORSF API | Názov, status, právna forma, NACE, veľkosť, adresa, DIČ | api.orsf.sk (free) |
| `ruz` | RÚZ API | Tržby, zisk, rok závierky | registeruz.sk |
| `rpvs` | RPVS OData | Koneční užívatelia výhod (UBO), oprávnené osoby | rpvs.gov.sk |
| `fs_dlznici` | FS SR | Daňoví dlžníci | web scraping (JS limited) |
| `sp_dlznici` | SP | Dlžníci soc. poistenie | web scraping (JS limited) |
| `uvo` | UVO Vestník | Zákazky, víťazi, hodnoty, zmluvy | HTML parser (3 formáty) |
| `ted` | TED eForms | EU nadlimitné zákazky (10 krajín) | ted.europa.eu API |
| `frsr_dph` | FR SR XML | DPH registrácia, IBAN účty, zrušenia | ds_dph_iban, ds_dphs, ds_dphz, ds_dphv |
| `frsr_dane` | FR SR XML | Daňoví dlžníci (82K), registrované subjekty (1.3M) | ds_dsdd, ds_dsrdp |
| `frsr_dph_odpocty` | FR SR XML | Nadmerné odpočty DPH, vlastná daň (679K) | ds_dphno |
| `frsr_spolahliv` | FR SR XML | Index spoľahlivosti daňovníka (660K) | ds_iz_ran |
| `input` / `output` | — | Vstup/výstup pipeline | — |

## Parsery

| Parser | Formát | Obdobie | Rýchlosť |
|---|---|---|---|
| `vestnik-parser.py` | UVO nový HTML (`notice-list`) | 228/2023+ | 1ms/dok |
| `vestnik-parser-legacy.py` | UVO starý HTML (`fieldset/ODDIEL`) | 2020-227/2023 | 0.5ms/dok |
| `ted-parser.py` | TED eForms XML | 2024+ (10 krajín) | 2ms/dok |

## API Endpointy

### Verejné API (REST)
```
GET  /api/v1/company/{ico}                    — profil firmy (všetky moduly)
GET  /api/v1/company/{ico}?modules=orsf,rpvs  — profil s výberom modulov
POST /api/v1/batch                            — batch lookup (až 500 IČO)
POST /api/v1/enrich-tdd                       — obohatenie TDD (IC DPH → profil)
POST /api/v1/enrich-tdd/batch                 — batch TDD enrichment (až 1000)
GET  /api/v1/enrich-tdd                       — API dokumentácia
```

### Interné API (dashboard)
```
GET  /api/data              — dokumenty (gzip, paginated)
GET  /api/analysis           — analytická správa
GET  /api/graph              — grafová analýza
GET  /api/latest             — najnovší vestník
GET  /api/profile            — profil dodávateľa/obstarávateľa
GET  /api/modules            — pipeline moduly
GET  /api/pipelines          — uložené pipeline konfigurácie
POST /api/pipelines/{id}/run — spustiť pipeline
GET  /api/watchlist          — pravidlá monitoringu
POST /api/watchlist          — uložiť pravidlá
GET  /api/watchdog-matches   — zhody monitoringu
```

## Dashboard (localhost:8080)

7 tabov: Aktualny vestnik, Dokumenty, Analyza, Graf, Pipeline, Profil, Monitoring

- **Dokumenty** — tabuľka zákaziek, filtre (rok, typ, zdroj UVO/TED), detail panel s rozpočet vs cena, časová os, loty, zmluvy (CRZ), email forward
- **Analýza** — single-bidder rate, cenové anomálie, top víťazi/obstarávatelia (klikateľné)
- **Graf** — D3.js force-directed graf vzťahov buyer↔winner, PageRank, co-bidding
- **Pipeline** — vizuálny editor: toggle moduly, CSV upload, batch spracovanie, export CSV, API test panel
- **Profil** — investigatívny profil firmy: Registre SR (ORSF+RÚZ), zákazky, zákaznícky mix, súťažné správanie, RPVS (UBO), dlhy, zákazky vs obrat
- **Monitoring** — pravidlá (IČO/CPV), automatické zhody, ntfy.sh notifikácie

## Nástroje

| Script | Účel |
|---|---|
| `vestnik-db.py` | JSON → SQLite loader (21 tabuliek) |
| `vestnik-analyze.py` | Single-bidder, repeated winners, cenové anomálie |
| `vestnik-graph.py` | NetworkX buyer↔winner, co-bidding, PageRank, GraphML |
| `vestnik-profile.py` | Investigatívny profil v žurnalistickom štýle (SK) |
| `vestnik-watchdog.py` | Monitoring nových zákaziek, ntfy.sh notifikácie |
| `vestnik-validator.py` | Validácia JSON výstupov |
| `company-enrichment.py` | ORSF + RÚZ lazy enrichment |
| `rpvs-lookup.py` | RPVS OData API (koneční užívatelia výhod) |
| `fs-dlznici-lookup.py` | FS dlžníci (web) |
| `sp-dlznici-lookup.py` | SP dlžníci (web) |
| `frsr-import.py` | Import 9 FR SR XML datasetov do SQLite (3.5M záznamov) |
| `tdd-enrichment.py` | TDD enrichment (IC DPH → profil, in-memory, <0.1ms) |
| `vestnik-batch-2026.sh` | Batch download všetkých vestníkov 2026 |
| `vestnik-cron-local.sh` | Mac cron (Po-Pi 23:00) |

## Konvencie

- **Jazyk kódu:** Python 3.10+
- **Jazyk UI/výstupov:** slovenčina
- **Formát dátumov:** DD.MM.YYYY
- **Primárny kľúč firmy:** IČO (8 číslic)
- **DPH identifikátor:** IC DPH = "SK" + DIČ
- **Cache TTL:** 30 dní pre externé API (ORSF, RPVS)
- **Rate limiting:** 1s medzi API calls
- **DB:** SQLite v `data/vestnik.db` (v .gitignore — 560MB)
- **HTML cache:** /tmp/vestnikXXX_full/

## Nasadenie

- **Lokálne:** `python3 dashboard/serve.py` → http://localhost:8080
- **Mac cron:** Po-Pi 23:00 (`vestnik-cron-local.sh`)
- **VPS:** Hostinger 147.93.121.59, `deploy-vps.sh`
- **Monitoring:** ntfy.sh topic `vestnik-uvo`

## Testy

```bash
python3 -m pytest tests/                           # 107 testov
python3 tools/vestnik-parser.py 191/2026           # parser test
python3 tools/frsr-import.py                       # FR SR import
curl http://localhost:8080/api/v1/company/36038351  # API test
```
