#!/usr/bin/env npx tsx
/**
 * Generate Pipeline description as Word document
 */
import { Document, Packer, Paragraph, TextRun, Table, TableRow, TableCell, WidthType, HeadingLevel, AlignmentType, ShadingType } from 'docx'
import { writeFileSync } from 'fs'
import { resolve } from 'path'

const OUT = resolve(import.meta.dirname, '../data/CFE-Test')
const BLUE = '1F4E79'

function heading(text: string, level = HeadingLevel.HEADING_1) {
  return new Paragraph({ heading: level, spacing: { before: 300, after: 100 }, children: [new TextRun({ text, color: BLUE, bold: true })] })
}
function para(text: string, opts?: { bold?: boolean; italic?: boolean }) {
  return new Paragraph({ spacing: { after: 120 }, children: [new TextRun({ text, bold: opts?.bold, italics: opts?.italic, size: 22 })] })
}
function bullet(text: string, level = 0) {
  return new Paragraph({ bullet: { level }, spacing: { after: 60 }, children: [new TextRun({ text, size: 22 })] })
}
function code(text: string) {
  return new Paragraph({ spacing: { after: 80 }, shading: { type: ShadingType.SOLID, color: 'F5F5F5' },
    children: [new TextRun({ text, font: 'Courier New', size: 18 })] })
}
function tableRow(cells: string[], header = false) {
  return new TableRow({ children: cells.map(c => new TableCell({
    shading: header ? { type: ShadingType.SOLID, color: BLUE } : undefined,
    children: [new Paragraph({ children: [new TextRun({ text: c, bold: header, color: header ? 'FFFFFF' : '333333', size: 20 })] })],
    width: { size: Math.floor(9000 / cells.length), type: WidthType.DXA },
  }))})
}
function table(headers: string[], rows: string[][]) {
  return new Table({ width: { size: 9000, type: WidthType.DXA }, rows: [tableRow(headers, true), ...rows.map(r => tableRow(r))] })
}

async function main() {
  const doc = new Document({
    sections: [{
      properties: { page: { margin: { top: 1200, bottom: 1200, left: 1200, right: 1200 } } },
      children: [
        // Title
        new Paragraph({ alignment: AlignmentType.CENTER, spacing: { after: 200 }, children: [
          new TextRun({ text: 'Pipeline pre extrakciu dát z VO dokumentov', size: 40, bold: true, color: BLUE }),
        ]}),
        new Paragraph({ alignment: AlignmentType.CENTER, spacing: { after: 600 }, children: [
          new TextRun({ text: 'Deterministická klasifikácia + RAG + LLM extrakcia', size: 28, color: '666666' }),
        ]}),

        // 1. Prečo pipeline
        heading('1. Prečo pipeline a nie len "lepší model"'),
        para('Testovali sme 5 prístupov na rovnakých 8 UVO dokumentoch:'),
        table(
          ['Prístup', 'Správne', 'Halucinácia', 'Čas/dok'],
          [
            ['Qwen 7B, celý dokument', '4/8', 'nie', '35s'],
            ['Qwen 32B, celý dokument', '6/8', 'nie', '128s'],
            ['Mistral Small, celý dokument', '6/8', '26 fiktívnych firiem', '62s'],
            ['RAG + Qwen 7B, LLM klasifikácia', '7/8', 'nie', '56s'],
            ['RAG + Qwen 7B, regex klasifikácia', '8/8 ✅', 'nie ✅', '8s ✅'],
          ],
        ),
        para('Najmenší model (7B) s najlepším pipeline vyhral. Väčšie modely bez pipeline prehrali — a Mistral dokonca halucinal.', { bold: true }),

        // 2. Pipeline
        heading('2. Pipeline — 5 krokov'),

        heading('Krok 1: Stiahnutie dokumentu', HeadingLevel.HEADING_2),
        para('Curl UVO stránky → HTML → strip tagy → čistý text. Čas: <1s.'),

        heading('Krok 2: Deterministická klasifikácia (regex, žiadny LLM)', HeadingLevel.HEADING_2),
        para('UVO dokumenty majú štruktúrované polia ktoré jednoznačne identifikujú typ:'),
        bullet('Úroveň 1 — Vestník index: Kód dokumentu (VST, VSS, VUT, VUS, IPT...)'),
        bullet('Úroveň 2 — Detail: "Typ formulára", "Podtyp oznámenia"'),
        para('Rozhodovacie pravidlá:', { bold: true }),
        table(
          ['Marker v texte', 'Typ dokumentu', 'Akcia'],
          [
            ['"Typ formulára: Výsledok"', 'Oznámenie o výsledku', '→ EXTRAHUJ'],
            ['"Súhrnná správa"', 'Správa o zákazke', '→ EXTRAHUJ'],
            ['"Lehota na predkladanie" (bez "Výsledok")', 'Vyhlásenie', '→ SKIP'],
            ['"Nebol vybratý žiadny víťaz"', 'Výsledok bez víťaza', '→ EXTRAHUJ (0 ponúk)'],
            ['Nič z vyššie', 'Neznámy typ', '→ LLM FALLBACK'],
          ],
        ),
        para('Čas: <1ms. Presnosť: 100% na UVO dokumentoch.', { italic: true }),
        para('Regex eliminuje ~50% dokumentov (vyhlásenia) bez jediného volania LLM.', { bold: true }),

        heading('Krok 2b: LLM Fallback klasifikácia', HeadingLevel.HEADING_2),
        para('Len ak regex nenašiel žiadne UVO markery (neštruktúrované dokumenty — zápisnice z PDF, dokumenty z iných portálov ako Josephine, eZákazky).'),
        bullet('Model: Mistral Small (24B) — najlepšia slovenčina pre neštruktúrované texty'),
        bullet('Vstup: top-5 relevantných chunkov cez RAG (nie celý dokument)'),
        bullet('Čas: ~15s'),

        heading('Krok 3: Chunking + Embedding', HeadingLevel.HEADING_2),
        para('Rozdelenie textu na kusy po ~500 tokenov s overlapom 50 tokenov.'),
        bullet('Embedding model: e5-multilingual-small (~100 MB)'),
        bullet('Vektorová DB: ChromaDB (in-process, žiadny server)'),
        bullet('Čas: <0.2s'),
        para('Prečo: Namiesto celého 40 KB dokumentu model dostane len ~2.5 KB relevantných odsekov. Menej šumu = menej chýb = menej halucinácie.'),

        heading('Krok 4: RAG extrakcia (Qwen 7B)', HeadingLevel.HEADING_2),
        para('Séria fokusovaných otázok — každá hľadá iné chunky:'),
        table(
          ['Otázka', 'Čo extrahuje', 'Čas'],
          [
            ['Q1: "Kto je obstarávateľ?"', 'Názov, IČO, email, adresa', '~2s'],
            ['Q2: "Predmet zákazky, CPV, hodnota?"', 'Predmet, CPV kód, suma, mena', '~2s'],
            ['Q3: "Kto podal ponuku, za koľko, kto vyhral?"', 'Uchádzači, ceny, víťaz, konzorciá', '~2s'],
          ],
        ),
        para('Kľúčový mechanizmus — Obstarávateľ injection:', { bold: true }),
        para('Meno obstarávateľa z Q1 sa injektuje do promptu Q3 ako explicitná exclusion ("TOTO NIE JE UCHÁDZAČ: Národná diaľničná spoločnosť"). Tým sa predchádza chybe kde model extrahuje obstarávateľa ako uchádzača.'),
        para('Celkový čas: ~6-8s.'),

        heading('Krok 5: Post-processing validácia (deterministická)', HeadingLevel.HEADING_2),
        para('LLM nie je deterministický — výstup treba vždy validovať:'),
        bullet('IČO formát check — 8 číslic, kontrolný súčet'),
        bullet('IČO existencia — lookup v ORSR API'),
        bullet('Halucinácia filter — ak všetky firmy majú generické mená ("Firma A", "Firma B") → zahodiť'),
        bullet('Deduplikácia — rovnaká firma pod rôznymi názvami'),
        bullet('Suma check — cena ponuky vs predpokladaná hodnota'),
        para('Čas: <1s.'),

        // 3. Prečo je to lepšie
        heading('3. Prečo je pipeline lepší než celý dokument do LLM'),

        heading('3.1 Regex klasifikácia eliminuje 50% dokumentov zadarmo', HeadingLevel.HEADING_2),
        para('Vyhlásenia (budúce zákazky) nemajú uchádzačov. Regex ich identifikuje za <1ms a preskočí. Bez pipeline by LLM strávil 30-60s na každom len aby zistil že tam nikto nie je.'),

        heading('3.2 RAG dáva modelu len relevantné časti', HeadingLevel.HEADING_2),
        para('Namiesto 40 KB textu (6 000 tokenov) dostane 2.5 KB (400 tokenov). Menší vstup = rýchlejšie + presnejšie + menej halucinácie.'),

        heading('3.3 Fokusované otázky sú presnejšie', HeadingLevel.HEADING_2),
        para('"Kto podal ponuku?" nájde iné chunky než "Aká je hodnota zákazky?". Každá otázka dostane svoje relevantné odseky namiesto celého dokumentu.'),

        heading('3.4 Obstarávateľ injection', HeadingLevel.HEADING_2),
        para('Q1 extrahuje obstarávateľa → Q3 ho explicitne vylúči. Preto model neextrahuje UVO ani VPSR ako uchádzačov. Bez tohto mechanizmu 7B model extrahoval poradcu (LEGAL TENDER) ako uchádzača.'),

        heading('3.5 Fallback pre neznáme dokumenty', HeadingLevel.HEADING_2),
        para('Regex pokryje UVO vestník. Zápisnice z PDF, dokumenty z Josephine, eZákazky → LLM fallback (Mistral Small). Systém nie je závislý na jednom zdroji.'),

        // 4. Výber modelu
        heading('4. Výber modelu podľa typu vstupu'),
        para('Nie je jeden "najlepší model". Závisí od typu dokumentu:', { bold: true }),
        table(
          ['Typ vstupu', 'Model', 'Prečo'],
          [
            ['TDD XML (štruktúrované)', 'Qwen 7B', '100% presnosť, 7s/dok'],
            ['UVO oznámenia (HTML s markermi)', 'Qwen 7B + regex', 'Regex klasifikácia, RAG extrakcia'],
            ['UVO dokumenty (zápisnice, PDF)', 'Mistral Small / Qwen 14B', 'Neštruktúrované, bez markerov'],
            ['TED dokumenty (EN/PL/SI)', 'Qwen 7B', 'Multilingválny, štruktúrované'],
          ],
        ),
        para('Mistral Small (24B, Paríž) má lepšiu slovenčinu než Qwen (Alibaba), ale v teste halucinal 26 fiktívnych firiem. S RAG pipeline aj menší Qwen 7B dosahuje lepšie výsledky bez halucinácie.'),

        // 5. HW
        heading('5. HW požiadavky'),
        table(
          ['Komponent', 'RAM', 'Poznámka'],
          [
            ['e5-multilingual-small', '100 MB', 'Embedding model'],
            ['ChromaDB', '50 MB', 'In-process vektorová DB'],
            ['Qwen 7B Q4', '4.7 GB', 'Hlavný LLM'],
            ['Mistral Small Q4', '14 GB', 'Len pre fallback (neštruktúrované docs)'],
            ['Celkom', '~5 GB (bez fallbacku)', 'GPU NIE JE POTREBNÝ'],
          ],
        ),
        para('Beží na existujúcom Azure AKS klastri kde je Viya — žiadne dodatočné HW náklady.'),

        // 6. Denný objem
        heading('6. Denný objem pre Slovensko'),
        code('UVO Vestník: ~40-60 oznámení/deň'),
        code('  └─ Regex: ~20 relevantných (výsledky, správy)'),
        code('  └─ ~20 skip (vyhlásenia) → 0s'),
        code(''),
        code('20 dokumentov × 8s = 160s = ~3 minúty'),
        code(''),
        code('Nočný cron o 22:00:'),
        code('  22:00:00 — stiahni index vestníka'),
        code('  22:00:01 — parsuj linky, filtruj (regex)'),
        code('  22:00:02 — stiahni 20 relevantných HTML'),
        code('  22:00:32 — RAG extrakcia (20 × 8s)'),
        code('  22:03:12 — validácia + uloženie do DB'),
        code('  22:03:13 — hotovo'),

        // 7. Testované
        heading('7. Výsledky testovania'),
        para('Testované na MacBook Pro M2 Max (96 GB RAM), Ollama, 8 reálnych UVO dokumentov.'),
        para('Evolúcia promptov:', { bold: true }),
        table(
          ['Verzia', 'Klasifikácia', 'Správne', 'Čas/dok'],
          [
            ['v1 — LLM, vágny prompt', 'LLM (7B)', '4/8', '101s'],
            ['v2 — LLM, lepšie pravidlá', 'LLM (7B)', '6/8', '49.6s'],
            ['v3 — LLM, explicitné vylúčenia', 'LLM (7B)', '5/8 (regres)', '—'],
            ['v4 — LLM, UVO markery v prompte', 'LLM (7B)', '7/8', '55.8s'],
            ['v5 — Regex + LLM fallback', 'Regex', '8/8 ✅', '8s ✅'],
          ],
        ),
        para('Konkrétne výsledky v5:', { bold: true }),
        table(
          ['Dokument', 'Regex klasifikácia', 'LLM extrakcia', 'Čas'],
          [
            ['NDS most D2-069', 'oznamenie_vysledok → EXTRAHUJ', 'STRABAG 🏆 3.8M EUR', '7.4s'],
            ['MDK Vranov (7 zákaziek)', 'sprava_o_zakazke → EXTRAHUJ', 'AudioMaster + LEdit + Lindha + GUTEKLIMA', '12.1s'],
            ['PSK obchvat (0 ponúk)', 'oznamenie_vysledok → EXTRAHUJ', '0 uchádzačov (správne)', '8.1s'],
            ['ZSD odpojovače', 'oznamenie_vyhlasenie → SKIP', '—', '0s'],
            ['Nitra projektant', 'oznamenie_vysledok → EXTRAHUJ', 'iProdos 🏆 8 100 EUR', '7.3s'],
            ['Prešov IoT SMART', 'oznamenie_vysledok → EXTRAHUJ', 'VSE Solutions 🏆 2.78M EUR', '7.5s'],
            ['Ružinov stromy', 'oznamenie_vyhlasenie → SKIP', '—', '0s'],
            ['MK SR poradenstvo', 'oznamenie_vyhlasenie → SKIP', '—', '0s'],
          ],
        ),
        // 8. Option B — Anthropic API
        heading('8. Option B — Anthropic Claude API'),
        para('Ak sú spracovávané dokumenty verejne dostupné (Vestník VO, TED), je možné namiesto lokálneho LLM použiť Anthropic Claude API. Toto výrazne zjednodušuje architektúru a zvyšuje kvalitu.'),

        heading('8.1 Porovnanie: Lokálny LLM vs Claude API', HeadingLevel.HEADING_2),
        table(
          ['Metrika', 'Lokálny (Qwen 7B + RAG)', 'Claude API (Sonnet)'],
          [
            ['Kvalita SK', 'Dobrá (s RAG pipeline)', 'Výborná (natívne)'],
            ['Halucinácia', '0 (s RAG + regex)', 'Veľmi nízka'],
            ['Rýchlosť', '~68s/dok (M2 Max CPU)', '~2-3s/dok (cloud)'],
            ['RAG potrebný?', 'ÁNO (bez neho 4/8)', 'NIE (zvládne celý dokument)'],
            ['Regex klasifikácia', 'ÁNO (kritická)', 'Voliteľná (šetrí tokeny)'],
            ['Setup', 'Ollama + ChromaDB + embedding model + venv', '1 API kľúč'],
            ['Údržba', 'Model updates, HW monitoring', 'Žiadna'],
            ['Dáta opúšťajú infraštruktúru?', 'NIE', 'ÁNO (Anthropic cloud)'],
            ['Cena', '0 € (existujúci HW)', '~€30-50/mesiac (60 dok/deň)'],
            ['Offline', 'ÁNO', 'NIE'],
          ],
        ),

        heading('8.2 Zjednodušený pipeline s Claude API', HeadingLevel.HEADING_2),
        para('Claude Sonnet zvládne celý 40 KB dokument naraz bez RAG — nepotrebuje chunking ani embedding. Pipeline sa zjednodušuje z 5 krokov na 3:'),
        code('Dokument (HTML)'),
        code('  │'),
        code('  ▼'),
        code('[1] Text extraction (strip HTML) .......... <1s'),
        code('  │'),
        code('  ▼'),
        code('[2] REGEX klasifikácia (voliteľná) ........ <1ms'),
        code('  │   Šetrí tokeny — vyhlásenia majú iný prompt'),
        code('  │   než výsledky. Bez klasifikácie funguje'),
        code('  │   tiež, len drahšie (viac tokenov).'),
        code('  │'),
        code('  ▼'),
        code('[3] Claude API — 1 volanie ................ ~2-3s'),
        code('      Celý text + prompt → JSON odpoveď'),
        code('      Žiadny chunking, žiadny embedding'),
        code('      Žiadny ChromaDB, žiadny Ollama'),
        code(''),
        code('Odpadá: Chunking, Embedding, ChromaDB, Ollama, venv'),
        code('Zostáva: curl + regex + 1 API call'),

        heading('8.3 Čo odpadá oproti lokálnemu pipeline', HeadingLevel.HEADING_2),
        table(
          ['Komponent', 'Lokálny', 'Claude API'],
          [
            ['Ollama (LLM server)', 'Vyžadovaný (4.7-19 GB RAM)', 'Nepotrebný'],
            ['e5-multilingual-small (embedding)', 'Vyžadovaný (100 MB)', 'Nepotrebný'],
            ['ChromaDB (vektorová DB)', 'Vyžadovaná', 'Nepotrebná'],
            ['Python venv', 'Vyžadovaný', 'Nepotrebný'],
            ['RAG chunking logika', '~100 riadkov kódu', 'Nepotrebná'],
            ['Regex klasifikácia', 'Kritická (inak 4/8)', 'Voliteľná (šetrí cenu)'],
            ['Post-processing validácia', 'Nevyhnutná', 'Odporúčaná'],
          ],
        ),

        heading('8.4 Cena Claude API', HeadingLevel.HEADING_2),
        para('Claude Sonnet 4 pricing (september 2026):'),
        table(
          ['', 'Vstup', 'Výstup'],
          [
            ['Cena za 1M tokenov', '$3.00', '$15.00'],
          ],
        ),
        para('Kalkulácia pre denný objem:'),
        table(
          ['Metrika', 'Hodnota'],
          [
            ['Dokumentov/deň', '~60 (UVO + TED)'],
            ['Priemer vstup/dok', '~5 000 tokenov (celý text + prompt)'],
            ['Priemer výstup/dok', '~500 tokenov (JSON)'],
            ['Tokeny/deň vstup', '300 000'],
            ['Tokeny/deň výstup', '30 000'],
            ['Cena/deň', '~$0.90 + $0.45 = ~$1.35'],
            ['Cena/mesiac', '~$30-40 (~€28-37)'],
          ],
        ),
        para('S regex klasifikáciou (skip 50% dokumentov): ~€15-20/mesiac.'),

        heading('8.5 Integrácia s existujúcou infraštruktúrou', HeadingLevel.HEADING_2),
        para('Claude API je OpenAI-kompatibilné — rovnaký formát ako Ollama:'),
        code('POST https://api.anthropic.com/v1/messages'),
        code('Authorization: x-api-key YOUR_API_KEY'),
        code('Content-Type: application/json'),
        code(''),
        code('{'),
        code('  "model": "claude-sonnet-4-20250514",'),
        code('  "max_tokens": 1024,'),
        code('  "messages": [{'),
        code('    "role": "user",'),
        code('    "content": "Extrahuj z UVO oznámenia... [celý text]"'),
        code('  }]'),
        code('}'),
        para('Na SAS Viya: PROC HTTP volá Claude API rovnako ako Ollama — zmena je len URL a API kľúč.'),

        heading('8.6 Kedy použiť lokálny LLM vs Claude API', HeadingLevel.HEADING_2),
        table(
          ['Scenár', 'Odporúčanie'],
          [
            ['Verejné VO dokumenty (UVO, TED)', 'Claude API — rýchlejšie, kvalitnejšie, lacnejšie na údržbu'],
            ['Interné firemné dokumenty', 'Lokálny LLM — dáta nesmú opustiť infraštruktúru'],
            ['TDD XML (eFaktúry)', 'Lokálny Qwen 7B — 100% presnosť, žiadna potreba API'],
            ['Prototypovanie a vývoj', 'Claude API — rýchly feedback loop'],
            ['Offline prostredie', 'Lokálny LLM — žiadna závislosť na internete'],
            ['Maximálna kvalita SK', 'Claude API — najlepšia slovenčina zo všetkých modelov'],
          ],
        ),

        heading('8.7 Hybridný prístup (odporúčaný)', HeadingLevel.HEADING_2),
        para('Kombinácia oboch prístupov podľa typu dát:'),
        code('Verejné VO dokumenty (UVO, TED)'),
        code('  → Regex klasifikácia (lokálne, 0ms)'),
        code('  → Claude API extrakcia (cloud, 2-3s)'),
        code('  → Post-processing validácia (lokálne)'),
        code(''),
        code('TDD XML z eFaktúr (citlivé firemné dáta)'),
        code('  → Lokálny Qwen 7B (on-premise, 7s)'),
        code(''),
        code('Neštruktúrované interné docs (zápisnice, zmluvy)'),
        code('  → Lokálny Mistral Small (on-premise, 60s)'),
        para('Tento hybridný prístup kombinuje najlepšie z oboch svetov: rýchlosť a kvalitu Claude API pre verejné dáta s bezpečnosťou lokálneho LLM pre citlivé dáta.'),

        // 9. Vestník test — lokálny Qwen RAG
        heading('9. Lokálny LLM — kompletný Vestník 191/2026 (Qwen 7B + RAG)'),
        para('Pipeline bol otestovaný na kompletnom vestníku č. 191/2026 — 59 dokumentov, 70 minút, MacBook Pro M2 Max.'),

        heading('9.1 Prehľad dokumentov', HeadingLevel.HEADING_2),
        table(
          ['Typ', 'Počet', 'Akcia'],
          [
            ['Výsledky (VST/VUS/VUT/IPT/IPS/IPP)', '18', 'Extrakcia víťazov + uchádzačov'],
            ['Vyhlásenia (MST/MSS/MSP/MUS/WYT/WYP)', '22', 'Business intelligence (príležitosti)'],
            ['Opravy (IOX)', '16', 'Opravy existujúcich oznámení'],
            ['Zmeny zmluvy (DOP/DOT/DOS)', '3', 'Preskočené'],
          ],
        ),

        heading('9.2 Extrahovaní víťazi (Qwen 7B)', HeadingLevel.HEADING_2),
        table(
          ['Oznámenie', 'Víťaz', 'IČO', 'Cena EUR'],
          [
            ['13373-VUS', 'Slovenská autobusová doprava Lučenec', '37828100', '410'],
            ['13377-VUS', 'BOOTIQ SK s.r.o.', '90265424', '80 546'],
            ['13378-VUS', 'Soimco a. s.', 'N/A', '89 395'],
            ['13379-IPP', 'FEROSTA a spol., s.r.o.', '48282235', '810 489'],
            ['13387-VST', 'Ružinovský domov seniorov', '00510173', '152 183'],
            ['13392-VUT', 'KORAKO, s. r. o.', '43959954', '1 742'],
            ['13394-VUT', 'Henrich Sonnenschein - ITSK', '37212931', '18 922'],
            ['13396-VUS', 'REMPO, s.r.o.', '35819081', '550 341'],
            ['13397-VUT', 'PORTAS GROUP spol. s r.o.', 'N/A', '469 990'],
            ['13404-IPT', 'BLUEMED s.r.o.', '51835827', 'neuvedené'],
            ['13408-IPS', 'Ledvák spol. s r.o.', '48206326', '89'],
            ['13415-IPP', 'COLAS Slovakia, a.s.', '31651402', '483 191'],
            ['13420-IPT', 'KOBIT - SK, s.r.o.', '31641440', '49 960'],
            ['13425-IPP', 'ProBuild SK, s.r.o.', '00310905', '1 051 638'],
            ['13426-IPT', 'PRAGOLAB s.r.o.', '31352839', '69 866'],
            ['13428-IPP', 'Davetec s.r.o.', '46884769', '196 685'],
          ],
        ),

        heading('9.3 Extrahované príležitosti — business intelligence (Qwen 7B)', HeadingLevel.HEADING_2),
        para('22 vyhlásení nových súťaží spracovaných lokálnym Qwen 7B + RAG pipeline:'),
        table(
          ['Oznámenie', 'Predmet', 'Hodnota EUR', 'Lehota'],
          [
            ['13375-MSS', 'Regionálna autobusová doprava', '93 367 000', '30.10.2026'],
            ['13413-WYP', 'Rekonštrukcia futbalového štadióna Sereď', '2 511 403', '—'],
            ['13376-WYP', 'Rozšírenie ČOV Banská Belá', '1 500 000', '08.10.2026'],
            ['13391-MSS', 'Stravovacie karty pre zamestnancov', '1 500 000', '16.10.2026'],
            ['13414-WYP', 'Objekt pre športové účely', '1 234 568', '08.10.2026'],
            ['13380-MSP', 'Stavebné práce – výstavba ciest', '1 200 000', '27.10.2026'],
            ['13374-WYP', 'Adaptácia administratívnej budovy AB-2', '1 152 768', '—'],
            ['13381-MST', 'Dodávka zemného plynu', '11 500 000', '27.10.2026'],
            ['13410-MST', 'Sanitné a zásahové vozidlá', '268 100', '19.10.2026'],
            ['13427-WYP', 'Rekonštrukcia strechy Právnická fakulta TU', '222 958', '16.10.2026'],
            ['13390-MSP', 'Rekonštrukcia športovej haly Dubnica n/V', '200 000', '—'],
            ['13384-MST', 'Univerzálny kolesový traktor', '150 000', '19.10.2026'],
            ['13430-WYT', 'Sedlový ťahač návesov 3-nápravový', '150 000', '01.10.2026'],
            ['13409-MST', 'Veľkoobjemové prostriedky na nebezpečné látky', '71 370', '22.10.2026'],
            ['13389-MSS', 'Poistenie služieb a cestovné poistenie', '57 700', '19.10.2026'],
            ['13400-WYP', 'Hajenka Biele Vody "Turisti a príroda spolu"', '—', '09.10.2026'],
            ['13431-WYP', 'Oprava žumpy a kanalizácie', '36 000', '12.10.2026'],
            ['13388-MST', 'Traktorová kosačka (opakované VO)', '26 333', '—'],
            ['13385-MST', 'Vysúvací systém sedačiek – KD Oščadnica', '15 000', '—'],
            ['13406-MST', 'Úžitkový elektromobil (2 ks)', '6 000', '31.12.2026'],
            ['13382-MUS', 'Lesnícke činnosti OZ Východ, OZ Šariš', '—', '21.10.2026'],
            ['13383-MSS', 'Mobilné telekomunikačné služby', '—', '23.10.2026'],
          ],
        ),
        para('Celkový čas lokálne: 70 minút (75s/dok). Spracovaných: 56 z 59 (3 zmeny zmluvy preskočené).', { bold: true }),

        // 10. Claude API — reálne výsledky
        heading('10. Reálne výsledky — Claude API na Vestníku 191/2026'),
        para('Kompletný vestník (59 dokumentov) spracovaný cez Claude Sonnet 4.6 API:'),

        heading('10.1 Porovnanie lokálny vs Claude API', HeadingLevel.HEADING_2),
        table(
          ['Metrika', 'Qwen 7B + RAG (lokálny)', 'Claude Sonnet 4.6 (API)'],
          [
            ['Dokumentov', '59', '59'],
            ['Spracovaných', '56', '56'],
            ['Celkový čas', '70 min', '9.7 min'],
            ['Priemer/dok', '75s', '10.4s'],
            ['Parse errors', '0', '2'],
            ['Víťazov extrahovaných', '17', '19'],
            ['RAG potrebný', 'ÁNO', 'NIE'],
            ['Cena', '€0', '$2.61 (€2.40)'],
            ['Setup', 'Ollama + ChromaDB + venv', '1 API kľúč'],
          ],
        ),
        para('Claude API je 7× rýchlejší, nepotrebuje RAG, a extrahoval viac údajov.', { bold: true }),

        heading('10.2 Cenová kalkulácia z reálneho testu', HeadingLevel.HEADING_2),
        table(
          ['Metrika', 'Hodnota'],
          [
            ['Model', 'claude-sonnet-4-6'],
            ['Vstupné tokeny (59 dok)', '750 637'],
            ['Výstupné tokeny', '23 944'],
            ['Cena vstup ($3/1M tok)', '$2.25'],
            ['Cena výstup ($15/1M tok)', '$0.36'],
            ['Celkom za 1 vestník', '$2.61'],
            ['Mesačne (22 pracovných dní)', '~$57 (~€52)'],
            ['S regex pre-filtrom (skip 50%)', '~$29 (~€27)'],
          ],
        ),

        heading('10.3 Ukážka extrahovaných výsledkov — Claude API', HeadingLevel.HEADING_2),
        para('Výsledky (📊 víťazi):'),
        table(
          ['Oznámenie', 'Víťaz', 'IČO', 'Cena EUR'],
          [
            ['13373-VUS', 'Slovenská autobusová doprava Lučenec', '36054259', '990'],
            ['13377-VUS', 'BOOTIQ SK s.r.o.', '52485692', '80 546'],
            ['13378-VUS', 'Soimco a. s.', '44367465', '89 395'],
            ['13379-IPP', 'FEROSTA a spol., s.r.o.', '48282235', '810 489'],
            ['13387-VST', 'DEMIFOOD spol. s r.o.', '36324124', '152 183'],
            ['13392-VUT', 'KORAKO, s. r. o.', '43959954', '1 742'],
            ['13394-VUT', 'Henrich Sonnenschein - ITSK', '37212931', '18 922'],
            ['13396-VUS', 'REMPO, s.r.o.', '35819081', '550 341'],
            ['13397-VUT', 'PORTAS GROUP spol. s r.o.', '53567951', '469 990'],
            ['13404-IPT', 'BLUEMED s.r.o. + INTES Poprad', '51835827', '73 580 + 88 700'],
            ['13415-IPP', 'COLAS Slovakia, a.s.', '31651402', '483 191'],
            ['13420-IPT', 'KOBIT - SK, s.r.o.', '31641440', '49 960'],
            ['13425-IPP', 'ProBuild SK, s.r.o.', '53948858', '1 051 638'],
            ['13426-IPT', 'PRAGOLAB s.r.o.', '31352839', '69 866'],
            ['13428-IPP', 'Davetec s.r.o.', '46884769', '196 685'],
          ],
        ),

        para('Príležitosti (📢 business intelligence) — Claude API extrahované vyhlásenia:'),
        table(
          ['Oznámenie', 'Predmet', 'Hodnota EUR', 'Lehota'],
          [
            ['13375-MSS', 'Regionálna autobusová doprava', '153 651 000', '30.10.2026'],
            ['13381-MST', 'Dodávka zemného plynu', '902 060', '20.10.2026'],
            ['13390-MSP', 'Rekonštrukcia športovej haly Dubnica n/V', '8 876 989', '16.10.2026'],
            ['13380-MSP', 'I/65 Stará Kremnička – protihluková stena', '8 256 460', '27.10.2026'],
            ['13410-MST', 'Sanitné a zásahové vozidlá ZZS', '3 236 100', '19.10.2026'],
            ['13413-WYP', 'Rekonštrukcia futbalového štadióna Sereď', '2 511 403', '07.10.2026'],
            ['13391-MSS', 'Stravovacie karty pre zamestnancov', '1 500 000', '16.10.2026'],
            ['13374-WYP', 'Adaptácia admin. budovy AB-2', '1 152 768', '08.10.2026'],
            ['13384-MST', 'Univerzálny kolesový traktor', '843 539', '19.10.2026'],
            ['13389-MSS', 'Poisťovacie služby', '767 679', '19.10.2026'],
            ['13427-WYP', 'Rekonštrukcia strechy PF TU', '222 958', '16.10.2026'],
            ['13430-WYT', 'Sedlový ťahač návesov 3-nápravový', '189 675', '01.10.2026'],
            ['13385-MST', 'Vysúvací systém sedačiek KD Oščadnica', '155 919', '—'],
            ['13406-MST', 'Úžitkový elektromobil (2 ks)', '137 000', '19.10.2026'],
            ['13409-MST', 'Prostriedky na skladovanie pohonných látok', '230 646', '22.10.2026'],
            ['13431-WYP', 'Oprava žumpy a kanalizácie', '36 000', '12.10.2026'],
            ['13388-MST', 'Traktorová kosačka', '26 333', '20.10.2026'],
          ],
        ),

        para('Opravy (IOX) — top príležitosti z opravných oznámení:'),
        table(
          ['Oznámenie', 'Predmet', 'Hodnota EUR', 'Lehota'],
          [
            ['13399-IOX', 'R2 Šaca – Košické Oľšany (rýchlostná cesta)', '162 524 578', '22.10.2026'],
            ['13411-IOX', 'Rekonštrukcia štátnej opery Banská Bystrica', '9 920 889', '09.10.2026'],
            ['13407-IOX', 'Servis klimatizácií železničné vozidlá', '8 367 030', '29.09.2026'],
            ['13416-IOX', 'Rekonštrukcia TJ SLAVOJ Sládkovičovo', '2 237 787', '25.09.2026'],
            ['13412-IOX', 'Fotovoltická elektráreň Petržalka + Senica', '1 495 865', '25.09.2026'],
          ],
        ),

        heading('10.4 Porovnanie kvality: Qwen vs Claude na vyhláseniach', HeadingLevel.HEADING_2),
        para('Claude API extrahoval presnejšie hodnoty zákaziek — Qwen 7B mal chyby pri niektorých poliach:'),
        table(
          ['Oznámenie', 'Qwen hodnota', 'Claude hodnota', 'Poznámka'],
          [
            ['13375-MSS Regionálna doprava', '93 367 000', '153 651 000', 'Claude zachytil celkový objem'],
            ['13380-MSP Protihluková stena', '1 200 000', '8 256 460', 'Qwen extrahoval len 1 lot'],
            ['13390-MSP Športová hala', '200 000', '8 876 989', 'Qwen extrahoval zábezpeku namiesto hodnoty'],
            ['13410-MST Sanitné vozidlá', '268 100', '3 236 100', 'Claude zachytil celkovú PHZ'],
            ['13385-MST Sedačky Oščadnica', '15 000', '155 919', 'Qwen extrahoval min. hodnotu'],
            ['13384-MST Traktor', '150 000', '843 539', 'Claude zachytil PHZ vrátane príslušenstva'],
            ['13406-MST Elektromobil', '6 000', '137 000', 'Qwen extrahoval len zálohu'],
          ],
        ),
        para('Claude API extrahoval realistickejšie hodnoty, pretože spracoval celý dokument naraz bez RAG chunking straty kontextu.', { italic: true }),

        heading('10.5 Záver porovnania', HeadingLevel.HEADING_2),
        para('Pre verejné VO dokumenty je Claude API jednoznačne lepšia voľba:'),
        bullet('7× rýchlejší (10s vs 75s/dok)'),
        bullet('Nepotrebuje RAG, embedding, ChromaDB — jednoduchší pipeline'),
        bullet('Viac extrahovaných údajov (19 vs 16 víťazov)'),
        bullet('Presnejšie hodnoty zákaziek (celý dokument naraz, žiadna strata kontextu z RAG)'),
        bullet('€52/mesiac za denné spracovanie celého vestníka'),
        bullet('Lokálny LLM zostáva pre citlivé dáta (TDD z eFaktúr) kde dáta nesmú opustiť infraštruktúru'),
      ],
    }],
  })

  const buf = await Packer.toBuffer(doc)
  const path = resolve(OUT, 'Pipeline_VO_Extrakcia.docx')
  writeFileSync(path, buf)
  console.log(`✅ ${path}`)
}

main().catch(err => { console.error('❌', err.message); process.exit(1) })
