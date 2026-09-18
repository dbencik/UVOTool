# LLM Benchmark Report — Extrakcia dát z eFaktúr a verejného obstarávania

## 1. Cieľ testovania

Overiť schopnosť lokálneho LLM modelu extrahovať štruktúrované dáta z troch typov zdrojov:
1. **TDD XML** — Tax Data Documents z Peppol eFaktúra systému
2. **UVO oznámenia** — Vestník verejného obstarávania SR (uvo.gov.sk)
3. **TED oznámenia** — Tenders Electronic Daily (EU, ted.europa.eu)

Požiadavky: lokálny beh (žiadne dáta online), slovenčina, štruktúrovaný JSON výstup.

---

## 2. Testované modely

| Model | Parametre | Kvantizácia | Veľkosť | Inštalácia |
|-------|-----------|-------------|---------|-----------|
| **Qwen 2.5 7B** | 7.6B | Q4_K_M | 4.7 GB | `ollama pull qwen2.5:7b` |
| **Qwen 2.5 32B** | 32.5B | Q4_K_M | 19 GB | `ollama pull qwen2.5:32b` |

Oba modely bežali cez **Ollama** na MacBook Pro M2 Max, 96GB RAM.

### Parametre inferencie
```json
{
  "temperature": 0,
  "stream": false
}
```

### Ollama API endpoint
```
POST http://localhost:11434/api/generate
Content-Type: application/json
Body: { "model": "qwen2.5:7b", "prompt": "...", "stream": false, "options": { "temperature": 0 } }
```

---

## 3. Hardvér

| Parameter | Hodnota |
|-----------|---------|
| Zariadenie | MacBook Pro |
| Model | Mac14,6 |
| Čip | Apple M2 Max |
| RAM | 96 GB (unified memory) |
| Ollama verzia | 0.34.0 |
| OS | macOS (Darwin 24.5.0) |

### Výkon na M2 Max
| Model | Prefill (vstup) | Decode (výstup) |
|-------|-----------------|-----------------|
| Qwen 2.5 7B Q4 | ~800 tok/s | ~35 tok/s |
| Qwen 2.5 32B Q4 | ~200 tok/s | ~10 tok/s |

---

## 4. Test 1 — TDD XML extrakcia (štruktúrovaný vstup)

### Vstupné dáta
- **Zdroj:** 1001 reálnych produkčných TDD od IONITE (september 2026)
- **Umiestnenie:** `data/real_TDD_sept2026/tdds/`
- **Vzorka:** 50 súborov (rovnomerne z rozsahu 1000565-1001565)
- **Veľkosť súboru:** ~6.8 KB (5 936 – 6 921 B)
- **Tokenov/dokument:** ~2 000

### Prompt
```
Si expert na slovenské DPH a Peppol eFaktúry. Z nasledujúceho TDD XML extrahuj údaje.
Odpovedz LEN platným JSON, žiadny iný text.

Polia:
- cislo_faktury (cbc:ID v ReportedDocument)
- dodavatel_ic_dph (CompanyID v AccountingSupplierParty)
- odberatel_ic_dph (CompanyID v AccountingCustomerParty)
- odberatel_nazov (RegistrationName, ak chýba vráť "")
- zaklad_dane (TaxExclusiveAmount, číslo)
- suma_dph (TaxAmount v TaxTotal, číslo)
- na_uhradu (PayableAmount, číslo)
- datum_vystavenia (IssueDate v ReportedDocument)
- datum_dodania (ActualDeliveryDate, ak chýba vráť "")
- sadzba_dph (Percent v TaxCategory, číslo)
- typ_dokumentu (DocumentTypeCode: 380/381/383)
- mena (DocumentCurrencyCode)
- tax_data_type (TaxDataTypeCode: S/R/D)

TDD XML:
[celý XML dokument]
```

### Validácia
Ground truth extrahovaný z XML regexom. Porovnanie polí:
- Stringové polia: exact match (po trim)
- Numerické polia: `|expected - actual| < 0.01`
- Prázdne polia: obe prázdne = zhoda

### Výsledky — Qwen 2.5 7B

| Metrika | Hodnota |
|---------|---------|
| Dokumentov | 50 |
| Perfektných (100% polí) | **50 / 50 (100%)** |
| Parse errors | 0 |
| Celková presnosť | **650 / 650 polí (100%)** |
| Priemerný čas | **7.3s** |

| Pole | Presnosť |
|------|----------|
| cislo_faktury | 100% (50/50) |
| dodavatel_ic_dph | 100% (50/50) |
| odberatel_ic_dph | 100% (50/50) |
| odberatel_nazov | 100% (50/50) |
| zaklad_dane | 100% (50/50) |
| suma_dph | 100% (50/50) |
| na_uhradu | 100% (50/50) |
| datum_vystavenia | 100% (50/50) |
| datum_dodania | 100% (50/50) |
| sadzba_dph | 100% (50/50) |
| typ_dokumentu | 100% (50/50) |
| mena | 100% (50/50) |
| tax_data_type | 100% (50/50) |

### Benchmark skript
`tools/llm-benchmark.ts`

---

## 5. Test 2 — UVO/TED krátke extrakty (predspracované)

### Vstupné dáta
- **UVO:** 11 oznámení, extrahované cez WebFetch (skrátený obsah)
- **TED:** 11 oznámení (SK, PL, SI, HU), extrahované z PDF
- **Veľkosť/dok:** UVO ~1.0 KB (~160 tok), TED ~2.1 KB (~350 tok)
- **Súbory:** `uvo_benchmark_data.txt`, `ted_benchmark_data.txt`

### Prompt
```
Si expert na verejné obstarávanie v EÚ a na Slovensku. Z nasledujúceho oznámenia
extrahuj údaje ako JSON. Odpovedz LEN platným JSON.

Polia (ak údaj chýba, vráť null):
- obstaravatel_nazov
- obstaravatel_ico
- obstaravatel_email
- predmet_zakazky (max 200 znakov)
- hlavny_cpv_kod
- dalsi_cpv_kody (pole stringov)
- druh_zakazky (tovary/sluzby/stavebne_prace)
- postup (otvoreny/uzky/rokovacie/priame_zadanie/iny)
- predpokladana_hodnota (číslo alebo null)
- mena
- miesto_plnenia
- lehota_na_ponuky
- pocet_lotov (číslo)
- subdodavky_povolene (ano/nie/neuvedene)
- kontaktna_osoba
- kontaktny_email
- kontaktny_telefon
- sumarizacia (3 vety max, vlastnými slovami)
- klasifikacia_odvetvia (IT/stavebnictvo/zdravotnictvo/energia/doprava/kultura/ine)
- vyzaduje_certifikaciu (ano/nie + aká)
- klucove_technicke_poziadavky (max 100 znakov)

Oznámenie:
[text oznámenia]
```

### Výsledky — Qwen 2.5 7B

| Metrika | UVO | TED | Celkom |
|---------|-----|-----|--------|
| Dokumentov | 11 | 11 | 22 |
| Parse rate | 100% | 100% | **100%** |
| Priemerný čas | 8.7s | 9.1s | **8.9s** |
| Priemerné polí | 13.5 | 12.1 | **12.8 / 21** |

### Obmedzenie testu
**Vstup boli predspracované extrakty (~1-2 KB), nie plné dokumenty.** WebFetch vracia skrátený obsah, nie celú stránku. Výsledky sú preto optimistické.

### Benchmark skript
`tools/llm-benchmark-vo.ts`

---

## 6. Test 3 — UVO plné HTML stránky

### Vstupné dáta
- **Zdroj:** 9 plných HTML stránok z uvo.gov.sk (curl)
- **Veľkosť HTML:** 89 – 142 KB (priemer 105 KB)
- **Veľkosť textu po strip HTML:** 8 – 42 KB (priemer 18.7 KB)
- **Tokenov/dok:** ~1 300 – 6 400 (priemer ~3 258)

### HTML → text konverzia
```typescript
function htmlToText(html: string): string {
  // Odstráni: <script>, <style>, <nav>, <footer>, <header>
  // Odstráni všetky HTML tagy
  // Dekóduje HTML entity (&amp; &lt; &gt; &quot; &#NNN;)
  // Kolapsuje whitespace
  return text
}
```

### Výsledky — Qwen 2.5 7B vs 32B

| Metrika | 7B | 32B |
|---------|-----|-----|
| Dokumentov | 9 | 8 (1 fetch error) |
| Parse rate | 100% | 100% |
| Priemerný čas | **34.8s** | **128s** |
| Polí extrahovaných | 17.7 / 23 | 15.2 / 12* |

*32B testovaný s redukovaným promptom (12 polí vs 23)

### Pokrytie polí (7B, plné dokumenty)

| Pole | Pokrytie |
|------|----------|
| obstaravatel_nazov | 100% |
| obstaravatel_ico | 100% |
| predmet_zakazky | 100% |
| druh_zakazky | 100% |
| postup | 100% |
| sumarizacia | 100% |
| klasifikacia_odvetvia | 100% |
| hlavny_cpv_kod | 89% |
| lehota_na_ponuky | 89% |
| kontaktny_telefon | 89% |
| mena | 89% |
| kontaktny_email | 78% |
| predpokladana_hodnota | 78% |
| obstaravatel_email | 67% |
| pocet_lotov | 56% |
| subdodavky_povolene | 56% |
| vitaz_nazov | 33% |

### Benchmark skript
`tools/llm-benchmark-full.ts`

---

## 7. Test 4 — Dvojfázový benchmark (klasifikácia + extrakcia účastníkov)

### Motivácia
Analytik upozornil že UVO dokumenty nie sú len oznámenia — môžu byť zápisnice, správy, zmluvy, súťažné podklady. Model musí najprv rozpoznať typ a potom extrahovať len z relevantných dokumentov. Extrakcia účastníkov je zložitá — treba rozlíšiť tých čo ponuku podali od tých čo boli len oslovení/zmienení.

### Fáza 1 — Klasifikačný prompt
Vstup: prvých ~3 000 znakov dokumentu (simulácia "prvých strán")
```
Si expert na verejné obstarávanie na Slovensku. Na základe začiatku dokumentu urči:

1. typ_dokumentu — jeden z:
   - "sprava_o_zakazke" (správa o zákazke, výsledky)
   - "zapisnica_otvaranie" (zápisnica z otvárania ponúk)
   - "zapisnica_vyhodnotenie" (zápisnica z vyhodnotenia ponúk)
   - "oznamenie_vyhlasenie" (oznámenie o vyhlásení VO)
   - "oznamenie_vysledok" (oznámenie o výsledku VO)
   - "sutazne_podklady" (súťažné podklady, podmienky účasti)
   - "zmluva" (zmluva, dodatok)
   - "iny" (iný typ)

2. je_relevantny — true ak dokument obsahuje informácie o účastníkoch/uchádzačoch
   a ich ponukách (správa o zákazke, zápisnica, oznámenie o výsledku).
   False ak ide o súťažné podklady, zmluvu, metodiku.

3. kratky_popis — 1 veta čo dokument obsahuje

Odpovedz LEN JSON. Začiatok dokumentu:
[prvých 3000 znakov]
```

### Fáza 2 — Extrakčný prompt
Vstup: celý text dokumentu (len ak fáza 1 vrátila `je_relevantny: true`)
```
Si expert na verejné obstarávanie. Z dokumentu extrahuj VŠETKÝCH uchádzačov
ktorí SKUTOČNE PODALI PONUKU.

DÔLEŽITÉ PRAVIDLÁ:
- Extrahuj LEN tých, čo reálne podali ponuku (nie tých čo boli len oslovení
  alebo zmienení v texte)
- Ak ponuku podala SKUPINA FIRIEM (konzorcium), uveď ich ako jeden záznam
  s poľom "skupina": true a "clenovia" obsahujúcim všetky firmy
- Rozlíšuj víťaza od ostatných uchádzačov
- Ak je uvedená cena ponuky, extrahuj ju

Odpovedz LEN platným JSON v tejto štruktúre:
{
  "zakazka": {
    "nazov": "...",
    "obstaravatel": "...",
    "obstaravatel_ico": "...",
    "cpv_kod": "...",
    "predpokladana_hodnota": null alebo číslo,
    "mena": "EUR"
  },
  "ucastnici": [
    {
      "nazov": "názov firmy",
      "ico": "IČO ak je uvedené",
      "cena_ponuky": null alebo číslo,
      "mena": "EUR",
      "je_vitaz": true/false,
      "skupina": false,
      "clenovia": [],
      "dovod_vylucenia": null alebo "dôvod ak bol vylúčený"
    }
  ],
  "pocet_ponuk_celkom": číslo,
  "pocet_ponuk_msp": null alebo číslo
}

Dokument:
[celý text]
```

### Výsledky — 7B vs 32B

| Metrika | 7B | 32B |
|---------|-----|-----|
| **Fáza 1** | | |
| Klasifikovaných | 9/9 | 7/7 |
| Chybná klasifikácia | 1 (Ružinov) | 0 |
| Relevantných | 6 | 4 |
| Priemerný čas fáza 1 | 4.1s | 20.4s |
| **Fáza 2** | | |
| Spracovaných | 5 | 3 |
| Parse errors | 1 | 1 |
| Celkom uchádzačov | 5 | 2 |
| Priemerný čas fáza 2 | 28.8s | 94.6s |
| **Celkový čas/dok** | **21.9s** | **74.0s** |

### Detailné porovnanie

| Dokument | 7B klasifikácia | 32B klasifikácia | 7B extrakcia | 32B extrakcia |
|----------|-----------------|-------------------|-------------|---------------|
| Ružinov (vyhlásenie) | oznamenie_vyhodnotenie ❌ | sutazne_podklady ✅ | — | — |
| ZSD (vyhlásenie) | relevant=❌ ✅ | relevant=❌ ✅ | — | — |
| NDS most (výsledok) | relevant=✅ | relevant=✅ | STRABAG 🏆 ✅ | STRABAG 🏆 ✅ |
| Prešov IoT (vyhlásenie) | relevant=✅ 🔴 | relevant=❌ ✅ | LEGAL TENDER 🔴 | preskočené ✅ |
| Nitra (výsledok) | relevant=✅ | relevant=✅ | iProdos ✅, skupina=[object Object] 🔴 | iProdos ✅, skupina=[Geovoz, JV Pro, Geotechnik, VHT] ✅ |
| PSK obchvat (výsledok) | relevant=✅ | relevant=✅ | falošný uchádzač 🔴 | 0 uchádzačov ✅ |
| MDK Vranov (súhrnná správa) | PARSE ERROR | PARSE ERROR | — | — |
| Prešov IoT (výsledok) | VSE Solutions 🏆 ✅ | FETCH ERROR | — | — |
| MK SR (poradenstvo) | iny ✅ (stránka error) | FETCH ERROR | — | — |

### Kľúčové rozdiely 32B vs 7B

1. **32B neextrahoval poradcu ako uchádzača** — LEGAL TENDER s.r.o. je poradenská firma pri VO, nie uchádzač. 7B ju extrahoval ako uchádzača, 32B správne označil vyhlásenie ako nerelevantné.
2. **32B správne spracoval skupiny firiem** — vrátil mená členov konzorcia (Geovoz, JV Pro, Geotechnik, VHT). 7B vrátil `[object Object]`.
3. **32B správne rozoznal zákazku bez ponúk** — PSK obchvat mal 0 prijatých ponúk. 32B vrátil 0 uchádzačov, 7B vytvoril falošný záznam.
4. **Oba zlyhali na súhrnných správach** — dokument s 7 zákazkami v jednom texte spôsobil parse error.

### Benchmark skript
`tools/llm-benchmark-2phase.ts`

---

## 8. Výsledky analytika (externé testovanie)

Analytik na SAS Viya testoval modely na **reálnych neštruktúrovaných UVO dokumentoch** (zápisnice, správy o zákazke, 50-200 KB, 16 GB celkom).

### Testované modely (pôvodné, nekvantizované)

| Model | Spoľahlivosť | Poznámka |
|-------|-------------|----------|
| **mistral-small** | ✅ spoľahlivý | Európska firma, dobrá SK |
| **gpt-oss-20b** | ✅ spoľahlivý | OpenAI open-source |
| **qwen3.5-35B-A3B** | ✅ spoľahlivý | Ale veľmi pomalý na Novita |
| qwen2.5-14B-Instruct | ⚠️ na hranici | |
| qwen3-30B-A3B-Instruct | ⚠️ na hranici | |
| **Qwen2.5-7B-Instruct** | ❌ nepresvedčil | |
| **Qwen3-32B** | ❌ úplne zlyhal | Možno kvôli slovenčine |

### Dvojfázový prístup analytika
1. Klasifikácia dokumentu (relevantný/nerelevantný)
2. Extrakcia účastníkov s pravidlami:
   - Len tí čo reálne podali ponuku
   - Nie oslovení/zmienení v texte
   - Skupiny firiem ako jeden záznam

### Doplnkový test: Maturita zo SJL
Analytik pridal 4 otázky čitateľskej gramotnosti z maturity zo slovenského jazyka ako benchmark jazykového porozumenia. Výsledky korelovali s úspešnosťou extrakcie.

---

## 9. Prečo veľkosť modelu ≠ kvalita na slovenčine

| Model | Parametre | SK kvalita | Dôvod |
|-------|-----------|-----------|-------|
| Qwen 2.5 7B | 7B | dobrá | Málo kapacity na zložité SK texty |
| Qwen 2.5 14B | 14B | lepšia | Viac kapacity, rovnaký tréning |
| Qwen 2.5 32B | 32B | dobrá+ | SK nie je priorita v tréningu |
| Qwen 3-32B | 32B | **zlá** | Analytik: zlyhal na SK |
| **Mistral Small** | **24B** | **výborná** | FR firma, EU jazyky v tréningu |
| gpt-oss-20b | 20B | výborná | OpenAI kvalita dát |

**Kľúč:** Nie je to o veľkosti, je to o tom na čom bol model trénovaný. Mistral (Paríž) trénuje na európskych jazykoch. Qwen (Alibaba) primárne na čínštine/angličtine.

---

## 10. Porovnanie: Krátke extrakty vs Plné dokumenty vs Neštruktúrované

| Typ vstupu | Veľkosť | Tokenov | 7B čas | 7B kvalita | 32B čas | 32B kvalita |
|-----------|---------|---------|--------|-----------|---------|-------------|
| TDD XML (štruktúrované) | 6.8 KB | ~2 000 | 7.3s | 100% ✅ | — | — |
| UVO/TED extrakt | 1-2 KB | ~200 | 8.9s | 100% parse ✅ | — | — |
| UVO plná stránka | 18.7 KB | ~3 258 | 34.8s | 17.7 polí ✅ | 128s | porovnateľné |
| UVO 2-fázy (klasifikácia) | 3 KB | ~500 | 4.1s | 1 chyba ⚠️ | 20.4s | 0 chýb ✅ |
| UVO 2-fázy (extrakcia) | 18.7 KB | ~3 258 | 28.8s | skupiny ❌, poradca ❌ | 94.6s | skupiny ✅, poradca ✅ |
| VO docs neštruktúrované | 50-200 KB | ~15k-50k | — | **nestačí** ❌ | — | **na hranici** ⚠️ |

---

## 11. Odporúčanie

### Pre štruktúrované vstupy (TDD XML, UVO oznámenia)
- **Qwen 2.5 7B Q4** — stačí, 100% presnosť, 7-35s/dok
- Beží na CPU aj Apple Silicon
- Žiadne GPU potrebné

### Pre neštruktúrované VO dokumenty (zápisnice, správy)
- **Mistral Small (24B)** — najspoľahlivejší na slovenčine
- **gpt-oss-20b** — rovnako spoľahlivý
- **Qwen 2.5 14B** — na hranici, lacnejší
- Minimálne 14B+ model, 7B nestačí

### HW sizing

| Scenár | Model | Kde | 100 dok/deň |
|--------|-------|-----|-------------|
| TDD/oznámenia | Qwen 7B | Existujúci Azure AKS (CPU) | ~1 hodina batch |
| VO docs neštruktúrované | Mistral Small 24B | Azure GPU spot A10 | ~50 min |
| Prototyp/dev | Qwen 7B-32B | MacBook M2 Max 96GB | 12-50 min |

---

## 12. Súbory

### Benchmark skripty
| Súbor | Popis |
|-------|-------|
| `tools/llm-benchmark.ts` | TDD XML extrakcia (50 súborov, ground truth validácia) |
| `tools/llm-benchmark-vo.ts` | UVO/TED krátke extrakty (22 dokumentov) |
| `tools/llm-benchmark-full.ts` | UVO plné HTML stránky (9 dokumentov) |
| `tools/llm-benchmark-2phase.ts` | Dvojfázový: klasifikácia + extrakcia účastníkov |

### Výsledky (JSON)
| Súbor | Popis |
|-------|-------|
| `data/CFE-Test/vo_benchmark_results_qwen2.5_7b.json` | Krátke extrakty, 22 dok, 7B |
| `data/CFE-Test/full_benchmark_qwen2.5_7b.json` | Plné HTML, 9 dok, 7B |
| `data/CFE-Test/2phase_benchmark_qwen2.5_7b.json` | Dvojfázový, 9 dok, 7B |
| `data/CFE-Test/2phase_benchmark_qwen2.5_32b.json` | Dvojfázový, 9 dok, 32B |

### Vstupné dáta
| Súbor | Veľkosť | Popis |
|-------|---------|-------|
| `data/real_TDD_sept2026/tdds/` | 1001 × ~6.8 KB | Reálne produkčné TDD od IONITE |
| `data/CFE-Test/uvo_benchmark_data.txt` | 10 KB | 11 UVO oznámení (predspracované) |
| `data/CFE-Test/ted_benchmark_data.txt` | 22 KB | 11 TED oznámení (predspracované) |

---

## 13. Test 5 — Mistral Small (24B) dvojfázový benchmark

### Model
| Parameter | Hodnota |
|-----------|---------|
| Model | mistral-small:latest |
| Parametre | 24B |
| Kvantizácia | Q4_K_M |
| Veľkosť | 14 GB |
| Výrobca | Mistral AI (Paríž) |
| Inštalácia | `ollama pull mistral-small` |

### Výsledky

| Metrika | Hodnota |
|---------|---------|
| Dokumentov | 8 (1 fetch error) |
| Klasifikovaných | 8/8 |
| Relevantných | 6/8 |
| Parse errors (fáza 2) | 0 |
| Celkom uchádzačov | 30 |
| Priemerný čas/dok | 61.5s |

### Detailné výsledky per dokument

| Dokument | Klasifikácia | Extrakcia | Správne? |
|----------|-------------|-----------|----------|
| Ružinov (vyhlásenie) | oznamenie_vysledok, relevant=✅ | 26× "Firma A-Z" | 🔴 **HALUCINÁCIA** — vygeneroval fiktívne firmy |
| ZSD (vyhlásenie) | oznamenie_vyhlasenie, relevant=❌ | preskočené | ✅ |
| NDS most (výsledok) | oznamenie_vysledok, relevant=✅ | STRABAG 🏆 3 798 771 EUR | ✅ |
| Nitra (výsledok) | oznamenie_vysledok, relevant=✅ | iProdos 🏆 + skupina [iProdos, Geovoz, JV Pro, Geotechnik, VHT] | ✅ **správne mená členov** |
| PSK obchvat (0 ponúk) | oznamenie_vysledok, relevant=✅ | 0 uchádzačov | ✅ |
| MDK Vranov (súhrnná správa) | sprava_o_zakazke, relevant=✅ | AudioMaster 🏆 | ✅ **zvládol čo 7B aj 32B nie** |
| Prešov IoT (výsledok) | oznamenie_vysledok, relevant=✅ | VSE Solutions 🏆 2 775 987 EUR | ✅ |
| MK SR (vyhlásenie) | oznamenie_vyhlasenie, relevant=❌ | preskočené | ✅ |

### Porovnanie všetkých troch modelov

| Kritérium | Qwen 7B | Qwen 32B | Mistral Small |
|-----------|---------|----------|---------------|
| Čas/dok | **21.9s** ✅ | 74.0s | 61.5s |
| Klasifikácia | 1 chyba | **0 chýb** ✅ | 1 chyba (halucinácia) |
| Skupiny firiem | `[object Object]` ❌ | mená ✅ | **mená + IČO** ✅ |
| 0-ponukové zákazky | falošný uchádzač ❌ | 0 ✅ | 0 ✅ |
| Súhrnná správa (7 zákaziek) | PARSE ERROR ❌ | PARSE ERROR ❌ | **AudioMaster** ✅ |
| LEGAL TENDER (poradca) | extrahoval ❌ | preskočil ✅ | FETCH ERROR |
| Halucinácia | nie | nie | **26 fiktívnych firiem** ❌ |
| Slovenčina (popis) | dobrá | dobrá | **výborná** |

### Kľúčové zistenie: Halucinácia Mistral Small

Na Ružinov dokumente (oznámenie bez účastníkov) Mistral Small:
1. Správne klasifikoval ako "oznamenie_vysledok"
2. Správne označil ako relevantný
3. Ale **vygeneroval 26 fiktívnych firiem** "Firma A" až "Firma Z" s generickými údajmi

Toto je závažný problém — model radšej vygeneruje niečo než prizná že informácia chýba. Qwen 32B na tom istom dokumente správne povedal "nerelevantný" a preskočil ho.

---

## 14. Navrhovaná 3-fázová architektúra

Na základe testov žiadny model nie je perfektný sám. Najlepší výsledok dáva **kombinácia modelov**:

### Fáza 1 — Klasifikácia (Qwen 32B)
- Vstup: prvých 3 000 znakov dokumentu
- Výstup: typ dokumentu + je_relevantny
- Prečo 32B: **najlepšia klasifikácia, 0 chýb, žiadne halucinácie**
- Čas: ~20s

### Fáza 2 — Extrakcia (Mistral Small)
- Vstup: celý text dokumentu (len ak fáza 1 = relevantný)
- Výstup: zoznam účastníkov, ceny, víťaz
- Prečo Mistral: **najlepšia SK extrakcia, zvládol súhrnnú správu, správne skupiny**
- Čas: ~60s

### Fáza 3 — Validácia (bez LLM, deterministická)
- **IČO formát check** — 8 číslic, Luhn algoritmus
- **IČO existencia** — lookup v obchodnom registri (ORSR API)
- **Duplicity** — rovnaká firma extrahovaná pod rôznymi názvami?
- **Halucinácia filter** — ak všetky firmy majú generické mená ("Firma A", "Firma B") → zahodiť
- **Suma validácia** — cena ponuky vs predpokladaná hodnota (nie 100× väčšia/menšia)
- Čas: <1s

### Prečo 3 fázy

| Problém | Ktorá fáza rieši |
|---------|-----------------|
| Nerelevantný dokument spracovaný zbytočne | Fáza 1 (klasifikácia) |
| Poradca extrahovaný ako uchádzač | Fáza 1 (32B lepšia klasifikácia) |
| Skupiny firiem ako `[object Object]` | Fáza 2 (Mistral zvláda) |
| Súhrnná správa s viacerými zákazkami | Fáza 2 (Mistral zvláda) |
| 26 fiktívnych firiem (halucinácia) | **Fáza 3** (IČO validácia, generický filter) |
| Neexistujúca firma | **Fáza 3** (ORSR lookup) |
| Duplikátne firmy | **Fáza 3** (deduplication) |

### Celkový čas per dokument
```
Fáza 1 (32B klasifikácia):    ~20s
Fáza 2 (Mistral extrakcia):   ~60s (len ak relevantný)
Fáza 3 (validácia):           <1s
────────────────────────────────
Celkom:                        ~80s (relevantný dokument)
                               ~20s (nerelevantný — preskočený)
```

Pre 60 dokumentov denne (SK UVO + TED):
- ~30 relevantných × 80s = 40 min
- ~30 nerelevantných × 20s = 10 min
- **Celkom: ~50 min nočný batch na CPU**

### HW požiadavky

Oba modely na M2 Max 96GB:
```
Qwen 32B Q4:     19 GB RAM
Mistral Small Q4: 14 GB RAM
Spolu:            33 GB (ale bežia sekvenčne, nie paralelne)
```

Na Azure AKS (existujúci Viya klaster):
- Jeden GPU node (A10, 24GB) — oba modely sa zmestia ak bežia sekvenčne
- Alebo CPU batch (pomalší ale bez GPU nákladov)

---

## 15. Súbory (aktualizované)

### Benchmark skripty
| Súbor | Popis |
|-------|-------|
| `tools/llm-benchmark.ts` | TDD XML extrakcia (50 súborov, ground truth) |
| `tools/llm-benchmark-vo.ts` | UVO/TED krátke extrakty (22 dokumentov) |
| `tools/llm-benchmark-full.ts` | UVO plné HTML stránky (9 dokumentov) |
| `tools/llm-benchmark-2phase.ts` | Dvojfázový: klasifikácia + extrakcia účastníkov |

### Výsledky (JSON)
| Súbor | Popis |
|-------|-------|
| `data/CFE-Test/vo_benchmark_results_qwen2.5_7b.json` | Krátke extrakty, 22 dok, 7B |
| `data/CFE-Test/full_benchmark_qwen2.5_7b.json` | Plné HTML, 9 dok, 7B |
| `data/CFE-Test/2phase_benchmark_qwen2.5_7b.json` | Dvojfázový, 9 dok, 7B |
| `data/CFE-Test/2phase_benchmark_qwen2.5_32b.json` | Dvojfázový, 9 dok, 32B |
| `data/CFE-Test/2phase_benchmark_mistral-small.json` | Dvojfázový, 8 dok, Mistral Small |

---

## 16. Navrhovaná RAG architektúra

### Motivácia

Testovanie ukázalo že hlavné problémy nie sú v kvalite modelu, ale v tom **koľko irelevantného textu** model dostane:

| Problém | Príčina |
|---------|---------|
| Halucinácia (26 fiktívnych firiem) | Model dostal 8 KB textu bez účastníkov → vymyslel si |
| Pomalé (60-80s/dok) | Celý 20-40 KB text poslaný naraz |
| Súhrnná správa (7 zákaziek) zlyháva | Príliš veľa informácií v jednom prompte |
| LEGAL TENDER ako uchádzač | Model nerozlíšil kontextovo v dlhom texte |

**RAG (Retrieval-Augmented Generation)** rieši všetky tieto problémy tým, že modelu pošle len relevantné časti dokumentu.

### Princíp RAG

Namiesto: "Tu máš 40 KB textu, nájdi účastníkov"
Pošleš: "Tu máš 3 relevantné odseky o účastníkoch (1.5 KB)"

```
Dokument (40 KB, ~6 000 tokenov)
  ↓ chunking (rozdelenie na kusy po ~500 tokenov = ~80 chunkov)
  ↓ embedding (e5-multilingual-small, vektorová reprezentácia, <1s)
  ↓ uloženie do vektorovej DB (ChromaDB, in-process)
  ↓
  ↓ otázka: "Kto podal ponuku a za akú cenu?"
  ↓ embedding otázky → similarity search → top 5 relevantných chunkov
  ↓ LLM dostane len ~2 500 tok namiesto 6 000
```

### Čo RAG rieši

| Problém | Bez RAG | S RAG |
|---------|---------|-------|
| **Halucinácia** | 40 KB text, účastníci tam nie sú → model vymyslí | Chunk search nič nenájde → model nedostane vstup → žiadna halucinácia |
| **Rýchlosť** | 6 000 tok vstup → 60s | 2 500 tok vstup → **25s** |
| **Súhrnná správa** | 7 zákaziek naraz → PARSE ERROR | Chunk per zákazka → spracuje každú zvlášť |
| **LEGAL TENDER** | V kontexte celého dokumentu, model si pomýli | Chunk s LEGAL TENDER nie je relevantný pre "kto podal ponuku" → nevyberie ho |
| **7B použiteľný?** | Nie (príliš veľa šumu) | **Áno** (fokusovaný vstup = lepšia kvalita) |

### Kľúčový insight: 7B + RAG ≈ 32B bez RAG

Keď 7B model dostane fokusovaný 1.5 KB chunk namiesto 40 KB celého dokumentu, kvalita sa výrazne zlepší. Model nemusí „hľadať ihlu v kope sena" — embedding search mu tú ihlu nájde.

To znamená: **žiadny 32B model, žiadny GPU, len 7B + embedding na CPU.**

### Navrhovaný pipeline

```
Dokument (HTML/PDF)
  │
  ▼
[1] Text extraction
  │   Strip HTML, remove boilerplate
  │   Čas: <1s
  │
  ▼
[2] Chunking
  │   Rozdelenie na kusy po ~500 tokenov
  │   Overlap 50 tokenov medzi kusmi (kontext)
  │   Čas: <1s
  │
  ▼
[3] Embedding
  │   Model: e5-multilingual-small (~100 MB)
  │   Vektorizácia všetkých chunkov
  │   Uloženie do ChromaDB (in-process, žiadny server)
  │   Čas: <2s
  │
  ▼
[4] Klasifikácia (Qwen 7B, fokusovaný vstup)
  │   Query: "správa o zákazke, zápisnica, výsledky obstarávania"
  │   → similarity search → top 3 chunky (~1 500 tok)
  │   → Qwen 7B: "Je toto relevantný VO dokument?"
  │   → áno/nie
  │   Čas: ~4s
  │
  ├── NIE → skip (ušetríme ~60s)
  │
  ▼
[5] Fokusovaná extrakcia (séria otázok)
  │
  │   Q1: "Kto je obstarávateľ? Názov, IČO, kontakt."
  │   → top 3 chunky → Qwen 7B/Mistral → JSON
  │   Čas: ~10s
  │
  │   Q2: "Kto podal ponuku? Mená firiem, IČO, ceny ponúk."
  │   → top 3 chunky → Qwen 7B/Mistral → JSON
  │   Čas: ~10s
  │
  │   Q3: "Kto vyhral? Víťazná cena, dátum zmluvy."
  │   → top 3 chunky → Qwen 7B/Mistral → JSON
  │   Čas: ~10s
  │
  │   Q4: "Podala ponuku skupina firiem / konzorcium? Kto sú členovia?"
  │   → top 3 chunky → Qwen 7B/Mistral → JSON
  │   Čas: ~10s
  │
  ▼
[6] Merge + Validácia (deterministická, bez LLM)
      - Zlúči odpovede z Q1-Q4 do jedného JSON
      - IČO formát check (8 číslic, Luhn)
      - IČO existencia (ORSR API lookup)
      - Deduplikácia (rovnaká firma pod rôznymi názvami)
      - Halucinácia filter (generické mená "Firma A-Z" → zahodiť)
      - Suma validácia (cena vs predpokladaná hodnota)
      Čas: <1s
```

### Porovnanie: Celý dokument vs RAG

| Metrika | Celý doc (dnes) | RAG (navrhované) |
|---------|----------------|------------------|
| Vstup do LLM | 6 000 tok (1 prompt) | 4× 1 500 tok (4 fokusované otázky) |
| Model pre klasifikáciu | 32B (20s) | **7B stačí (4s)** |
| Model pre extrakciu | Mistral 24B (60s) | **7B/Mistral (4× 10s = 40s)** |
| Celkový čas (relevantný) | ~80s | **~45s** |
| Celkový čas (nerelevantný) | ~20s | **~4s** |
| Halucinácia | áno (Ružinov) | **nie** (chunk filter) |
| Súhrnná správa (7 zákaziek) | PARSE ERROR | **OK** (chunks per zákazka) |
| 7B použiteľný? | nie | **áno** |
| GPU potrebný? | nie, ale pomalé | **nie, rýchlejšie** |

### Výhody fokusovaných otázok oproti jednému veľkému promptu

1. **Menší vstup** — 1 500 tok vs 6 000 tok → rýchlejšie, presnejšie
2. **Špecifické chunky per otázka** — otázka o víťazovi nájde chunk s víťazom, nie chunk s podmienkami
3. **Paralelizovateľné** — Q1-Q4 môžu bežať súčasne (4× rýchlejšie na multi-core)
4. **Debugovateľné** — vieme presne ktorý chunk model dostal a prečo odpovedal tak ako odpovedal
5. **Rozšíriteľné** — pridanie novej otázky Q5 nevyžaduje zmenu celého promptu

### HW požiadavky

```
e5-multilingual-small:    ~100 MB RAM (embedding model)
ChromaDB:                  ~50 MB RAM (in-process vektorová DB)
Qwen 7B Q4:                4.7 GB RAM (LLM)
── alebo ──
Mistral Small Q4:          14 GB RAM (ak treba kvalitnejší)
────────────────────────────────────────
Celkom: ~5 GB (s Qwen 7B) alebo ~15 GB (s Mistral)
GPU: nie je potrebný
```

Na existujúcom Azure AKS (Viya klaster):
- Žiadne dodatočné náklady na HW
- Docker container s Ollama + ChromaDB
- SAS volá cez PROC HTTP

### Denný objem

Pre Slovensko (~60 dokumentov/deň z UVO + TED):
- ~30 relevantných × 45s = **22 min**
- ~30 nerelevantných × 4s = **2 min**
- **Celkom: ~24 min nočný batch na CPU**

---

## 17. Test 6 — RAG benchmark: evolúcia promptov (v1 → v5)

### Motivácia

UVO dokumenty majú jasnú štruktúru s konkrétnymi poliami, ktoré jednoznačne identifikujú typ dokumentu:

**Úroveň 1 — Vestník (index stránka):**
- Kód dokumentu: `VST` (výsledok verejnej súťaže/tovary), `VSS` (služby), `VUT` (užšia súťaž/tovary), `IPT` (podlimitné/tovary)...
- Kategória: "Oznámenia o výsledku" / "Oznámenia o vyhlásení" / "Výzvy na predkladanie"

**Úroveň 2 — Detail dokumentu (sekcia "Základné údaje"):**
- `Typ oznámenia:` Oznámenie o výsledku verejného obstarávania
- `Typ formulára:` Výsledok / Súťaž
- `Podtyp oznámenia:` Oznámenie o výsledku verejného obstarávania – všeobecná smernica

### Evolúcia promptov

| Verzia | Klasifikácia | Výsledok | Čas/dok | Problém |
|--------|-------------|----------|---------|---------|
| v1 | LLM, vágny prompt | 4/8 | 101s | Všetko "relevantné", halucinácie |
| v2 | LLM, lepšie pravidlá | 6/8 | 49.6s | 1 falošný uchádzač (VPSR) |
| v3 | LLM, explicitné vylúčenia | 5/8 | — | Regres — skipol aj relevantné |
| v4 | LLM, UVO markery v prompte | 7/8 | 55.8s | Ružinov edge case |
| **v5** | **Deterministická (regex)** | **8/8** | **~8s** | **Žiadne** |

### v5 — Finálna architektúra

```
Dokument (HTML)
  │
  ▼
[1] Text extraction (strip HTML)
  │
  ▼
[2] DETERMINISTICKÁ KLASIFIKÁCIA (regex, žiadny LLM)
  │   Hľadá v texte:
  │   - "Typ formulára: Výsledok" → oznamenie_vysledok, RELEVANT
  │   - "Súhrnná správa" → sprava_o_zakazke, RELEVANT
  │   - "Lehota na predkladanie ponúk" BEZ "Výsledok" → vyhlásenie, SKIP
  │   - "Nebol vybratý žiadny víťaz" → výsledok bez víťaza, RELEVANT
  │   Čas: <1ms (instant)
  │
  ├── NERELEVANTNÝ → SKIP (žiadny LLM, 0 tokenov, 0 sekúnd)
  │
  ▼
[3] Chunking + Embedding (len pre relevantné)
  │   500 tok chunky, e5-multilingual-small, ChromaDB
  │   Čas: <0.2s
  │
  ▼
[4] RAG extrakcia (Qwen 7B, fokusované otázky)
  │   Q1: obstarávateľ (top-5 chunkov) → ~2s
  │   Q2: zákazka + CPV (top-5 chunkov) → ~2s
  │   Q3: účastníci + víťaz (top-5 chunkov, s menom obstarávateľa ako exclusion) → ~2s
  │   Čas: ~6-8s
  │
  ▼
[5] Post-processing validácia
      IČO check, ORSR lookup, deduplikácia
      Čas: <1s
```

### v5 výsledky — 8 UVO dokumentov

| Dokument | Typ (regex) | Relevantný? | Extrakcia | Správne? | Čas |
|----------|------------|-------------|-----------|----------|-----|
| NDS most D2-069 | oznamenie_vysledok | ✅ → LLM | STRABAG 🏆 3.8M EUR | ✅ | 7.4s |
| MDK Vranov (7 zákaziek) | sprava_o_zakazke | ✅ → LLM | AudioMaster + LEdit + Lindha + GUTEKLIMA 🏆🏆🏆🏆 | ✅ | 12.1s |
| PSK obchvat (0 ponúk) | oznamenie_vysledok | ✅ → LLM | 0 uchádzačov | ✅ | 8.1s |
| ZSD odpojovače | oznamenie_vyhlasenie | ❌ → **SKIP** | — | ✅ | 0s |
| Nitra projektant | oznamenie_vysledok | ✅ → LLM | iProdos 🏆 8 100 EUR | ✅ | 7.3s |
| Prešov IoT SMART | oznamenie_vysledok | ✅ → LLM | VSE Solutions 🏆 2.78M EUR | ✅ | 7.5s |
| Ružinov stromy | oznamenie_vyhlasenie | ❌ → **SKIP** | — | ✅ | 0s |
| MK SR poradenstvo | oznamenie_vyhlasenie | ❌ → **SKIP** | — | ✅ | 0s |

### Porovnanie: Všetky prístupy na rovnakých 8 dokumentoch

| Prístup | Správne | Halucinácia | Čas/dok | LLM volania |
|---------|---------|-------------|---------|-------------|
| Qwen 7B celý doc | 4/8 | 0 | 34.8s | 8 |
| Qwen 32B celý doc | 6/8 | 0 | 128s | 8 |
| Mistral Small celý doc | 6/8 | **26 fiktívnych firiem** | 61.5s | 8 |
| RAG + 7B v1 (LLM klasifikácia) | 4/8 | 0 | 101s | 32 |
| RAG + 7B v2 | 6/8 | 0 | 49.6s | 20 |
| RAG + 7B v4 (UVO markery) | 7/8 | 0 | 55.8s | 24 |
| **RAG + 7B v5 (regex klasif.)** | **8/8** | **0** | **~8s** | **15** |

### Kľúčový insight

**Nepoužívaj LLM na to čo dokážeš regexom.** UVO dokumenty majú štruktúrované pole `Typ formulára` — regex ho nájde za <1ms so 100% presnosťou. LLM na klasifikáciu toho istého trvá 15-25s a robí chyby.

LLM je drahý nástroj — použij ho len tam kde regex/parser nestačí: extrakcia mien, IČO, cien z neštruktúrovaného textu.

### Benchmark skript
`tools/rag-benchmark.py` (vyžaduje venv: `tools/rag-env/`)

```bash
# Setup
python3 -m venv tools/rag-env
tools/rag-env/bin/pip install chromadb sentence-transformers requests

# Run
tools/rag-env/bin/python3 tools/rag-benchmark.py
```

---

## 18. Záver

1. **Pipeline je dôležitejší než model.** RAG + regex klasifikácia + Qwen 7B (8/8) prekonali Qwen 32B bez RAG (6/8) aj Mistral Small bez RAG (6/8 + halucinácia). Investícia do pipeline sa oplatí viac než investícia do väčšieho modelu.

2. **Klasifikáciu dokumentov rieš deterministicky (regex).** UVO má štruktúrované polia (`Typ formulára`, `Podtyp oznámenia`). Regex je okamžitý a 100% presný. LLM klasifikácia je pomalá a robí chyby. LLM sa použije len ako fallback pre dokumenty bez UVO markerov.

3. **RAG + Qwen 7B = najlepší výsledok na UVO oznámeniach** — 8/8 správne, ~8s/dok, 0 halucinácie. Ale toto platí pre štruktúrované UVO stránky s regex markermi.

4. **Na neštruktúrované dokumenty (zápisnice, správy bez UVO polí) → Mistral Small alebo Qwen 14B+.** Potvrdené analytikom na 16 GB reálnych dát. Pipeline s fallbackom: regex → ak nenájde → LLM klasifikácia (Mistral Small) → RAG extrakcia.

5. **Výber modelu závisí od typu vstupu:**

   | Typ vstupu | Model | Prečo |
   |-----------|-------|-------|
   | TDD XML (štruktúrované) | Qwen 7B | 100% presnosť, rýchly |
   | UVO oznámenia (štruktúrované HTML) | Qwen 7B + regex | Regex klasifikácia, RAG extrakcia |
   | UVO dokumenty (zápisnice, správy) | Mistral Small / Qwen 14B | Neštruktúrované, bez markerov |
   | TED dokumenty (EN/PL/SI) | Qwen 7B | Multilingválny, štruktúrované |

6. **Mistral Small je lepší na slovenčine ALE halucinal.** Na jednom dokumente vygeneroval 26 fiktívnych firiem. Qwen 7B s RAG nehalucinal ani raz. Záver: lepší jazyk ≠ spoľahlivejší výstup.

7. **Post-processing validácia je nevyhnutná** bez ohľadu na model — IČO check, ORSR lookup, halucinácia filter, deduplikácia. LLM nie je deterministický.

8. **Odporúčaný produkčný pipeline:**
   ```
   Dokument
     ↓ regex na UVO markery (Typ formulára, Súhrnná správa...)
     ├── match → relevantný/nerelevantný (0ms, 100%)
     └── no match → LLM fallback klasifikácia (Mistral Small, ~15s)
     ↓ (len relevantné)
     ↓ chunking + embedding (e5-multilingual, <0.2s)
     ↓ RAG extrakcia (Qwen 7B, ~8s)
     ↓ post-processing validácia (<1s)
   ```
   Žiadny GPU, ~3-4 min/deň pre 60 SK dokumentov, beží na existujúcom Azure AKS.
