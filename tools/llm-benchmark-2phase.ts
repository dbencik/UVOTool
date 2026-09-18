#!/usr/bin/env npx tsx
/**
 * Two-phase LLM extraction benchmark (per analyst requirements)
 *
 * Phase 1: Document classification — is this relevant? What type?
 *   (only first ~2000 chars to save tokens)
 *
 * Phase 2: Extract participants who ACTUALLY submitted tenders
 *   (not those who were invited/mentioned but didn't submit)
 *   Handle: consortia (groups of companies submitting together)
 *
 * Tests on full UVO HTML pages.
 */
import { writeFileSync } from 'fs'
import { resolve } from 'path'

const OLLAMA_URL = 'http://localhost:11434/api/generate'
const MODEL = process.argv[2] || 'qwen2.5:7b'
const OUT_DIR = resolve(import.meta.dirname, '../data/CFE-Test')

// ═══════════════════════════════════
// Phase 1: Classification prompt
// Uses only first ~2000 chars (first "pages")
// ═══════════════════════════════════

const PHASE1_PROMPT = `Si expert na verejné obstarávanie na Slovensku. Na základe začiatku dokumentu urči:

1. typ_dokumentu — jeden z:
   - "sprava_o_zakazke" (správa o zákazke, výsledky)
   - "zapisnica_otvaranie" (zápisnica z otvárania ponúk)
   - "zapisnica_vyhodnotenie" (zápisnica z vyhodnotenia ponúk)
   - "oznamenie_vyhlasenie" (oznámenie o vyhlásení VO)
   - "oznamenie_vysledok" (oznámenie o výsledku VO)
   - "sutazne_podklady" (súťažné podklady, podmienky účasti)
   - "zmluva" (zmluva, dodatok)
   - "iny" (iný typ)

2. je_relevantny — true ak dokument obsahuje informácie o účastníkoch/uchádzačoch a ich ponukách (správa o zákazke, zápisnica, oznámenie o výsledku). False ak ide o súťažné podklady, zmluvu, metodiku.

3. kratky_popis — 1 veta čo dokument obsahuje

Odpovedz LEN JSON. Začiatok dokumentu:
`

// ═══════════════════════════════════
// Phase 2: Participant extraction prompt
// Full document, careful about who actually submitted
// ═══════════════════════════════════

const PHASE2_PROMPT = `Si expert na verejné obstarávanie. Z dokumentu extrahuj VŠETKÝCH uchádzačov ktorí SKUTOČNE PODALI PONUKU.

DÔLEŽITÉ PRAVIDLÁ:
- Extrahuj LEN tých, čo reálne podali ponuku (nie tých čo boli len oslovení alebo zmienení v texte)
- Ak ponuku podala SKUPINA FIRIEM (konzorcium), uveď ich ako jeden záznam s poľom "skupina": true a "clenovia" obsahujúcim všetky firmy
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
`

function htmlToText(html: string): string {
  return html
    .replace(/<script[\s\S]*?<\/script>/gi, '')
    .replace(/<style[\s\S]*?<\/style>/gi, '')
    .replace(/<nav[\s\S]*?<\/nav>/gi, '')
    .replace(/<footer[\s\S]*?<\/footer>/gi, '')
    .replace(/<header[\s\S]*?<\/header>/gi, '')
    .replace(/<[^>]+>/g, ' ')
    .replace(/&amp;/g, '&').replace(/&lt;/g, '<').replace(/&gt;/g, '>')
    .replace(/&quot;/g, '"').replace(/&#(\d+);/g, (_, c) => String.fromCharCode(+c))
    .replace(/&nbsp;/g, ' ')
    .replace(/\s+/g, ' ').trim()
}

async function queryOllama(prompt: string): Promise<{ json: any; timeMs: number; raw: string }> {
  const start = Date.now()
  const res = await fetch(OLLAMA_URL, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ model: MODEL, prompt, stream: false, options: { temperature: 0 } }),
  })
  const data = await res.json()
  const timeMs = Date.now() - start
  const raw = data.response || ''
  const jsonMatch = raw.match(/\{[\s\S]*\}/)
  if (!jsonMatch) return { json: null, timeMs, raw }
  try { return { json: JSON.parse(jsonMatch[0]), timeMs, raw } }
  catch { return { json: null, timeMs, raw } }
}

const UVO_URLS = [
  'https://www.uvo.gov.sk/vestnik-a-registre/vestnik/oznamenie/detail/1410753?cHash=fbda2e06f2dae2cd762ab8b7bd8258a3',
  'https://www.uvo.gov.sk/vestnik-a-registre/vestnik/oznamenie/detail/1398513?cHash=93ac2a95a9845d14a93c5c7f7c82e957',
  'https://www.uvo.gov.sk/vestnik-a-registre/vestnik/oznamenie/detail/1383034?cHash=27015446e7d70f1d5410d368b8672417',
  'https://www.uvo.gov.sk/vestnik-a-registre/vestnik/oznamenie/detail/1384552?cHash=0700325e73f61c08aacbc495f5e323e8',
  'https://www.uvo.gov.sk/vestnik-a-registre/vestnik/oznamenie/detail/1400218?cHash=1de2f09b60fe01b308287a067bbef73f',
  'https://www.uvo.gov.sk/vestnik-a-registre/vestnik/oznamenie/detail/1395981?cHash=ef698019149c9634a7779112ce4e001f',
  'https://www.uvo.gov.sk/vestnik-a-registre/vestnik/oznamenie/detail/1385309?cHash=fc7bd72a505924a141da5ec5717e08ee',
  'https://www.uvo.gov.sk/vestnik-a-registre/vestnik/oznamenie/detail/1402332?cHash=2a83a9e93821938d2562b7709dd4ddf2',
  'https://www.uvo.gov.sk/vestnik-a-registre/vestnik/oznamenie/detail/1411528?cHash=98698ea807ea79f4ea8e0a4536640c74',
]

async function main() {
  console.log(`=== TWO-PHASE LLM Benchmark ===`)
  console.log(`Model: ${MODEL}`)
  console.log(`Dokumentov: ${UVO_URLS.length}\n`)

  const results: any[] = []

  for (let i = 0; i < UVO_URLS.length; i++) {
    const url = UVO_URLS[i]
    console.log(`\n${'─'.repeat(60)}`)
    console.log(`[${i + 1}/${UVO_URLS.length}] ${url.split('/').pop()?.split('?')[0]}`)

    // Fetch full page
    let html: string
    try {
      const res = await fetch(url, { signal: AbortSignal.timeout(15000) })
      html = await res.text()
    } catch {
      console.log('  ❌ FETCH ERROR')
      results.push({ url, phase1: null, phase2: null, error: 'fetch' })
      continue
    }
    const fullText = htmlToText(html)
    console.log(`  HTML: ${Math.round(html.length / 1024)}KB → Text: ${Math.round(fullText.length / 1024)}KB (~${Math.round(fullText.split(/\s+/).length * 1.3)} tok)`)

    // ── PHASE 1: Classification (first ~2000 chars) ──
    const firstPage = fullText.substring(0, 3000)
    process.stdout.write('  Phase 1 (klasifikácia): ')
    const p1 = await queryOllama(PHASE1_PROMPT + firstPage)

    if (!p1.json) {
      console.log(`❌ PARSE ERROR (${(p1.timeMs / 1000).toFixed(1)}s)`)
      results.push({ url, phase1: { error: 'parse', timeMs: p1.timeMs }, phase2: null })
      continue
    }

    const typ = p1.json.typ_dokumentu || 'unknown'
    const relevant = p1.json.je_relevantny
    const popis = p1.json.kratky_popis || ''
    console.log(`${typ} | relevant=${relevant} (${(p1.timeMs / 1000).toFixed(1)}s)`)
    console.log(`  → "${popis}"`)

    // ── PHASE 2: Extract participants (only if relevant) ──
    if (!relevant) {
      console.log('  Phase 2: PRESKOČENÉ (nerelevantný dokument)')
      results.push({ url, phase1: { ...p1.json, timeMs: p1.timeMs }, phase2: { skipped: true, reason: 'not relevant' } })
      continue
    }

    process.stdout.write('  Phase 2 (extrakcia účastníkov): ')
    const p2 = await queryOllama(PHASE2_PROMPT + fullText)

    if (!p2.json) {
      console.log(`❌ PARSE ERROR (${(p2.timeMs / 1000).toFixed(1)}s)`)
      results.push({ url, phase1: { ...p1.json, timeMs: p1.timeMs }, phase2: { error: 'parse', timeMs: p2.timeMs } })
      continue
    }

    const ucastnici = p2.json.ucastnici || []
    const vitazi = ucastnici.filter((u: any) => u.je_vitaz)
    const skupiny = ucastnici.filter((u: any) => u.skupina)
    console.log(`✅ ${ucastnici.length} uchádzačov, ${vitazi.length} víťazov, ${skupiny.length} skupín (${(p2.timeMs / 1000).toFixed(1)}s)`)

    for (const u of ucastnici) {
      const mark = u.je_vitaz ? '🏆' : u.dovod_vylucenia ? '❌' : '  '
      const cena = u.cena_ponuky ? ` | ${u.cena_ponuky} ${u.mena || 'EUR'}` : ''
      const skupina = u.skupina ? ` [SKUPINA: ${(u.clenovia || []).join(', ')}]` : ''
      const vyluc = u.dovod_vylucenia ? ` (vylúčený: ${u.dovod_vylucenia})` : ''
      console.log(`    ${mark} ${u.nazov} (${u.ico || 'bez IČO'})${cena}${skupina}${vyluc}`)
    }

    results.push({
      url,
      phase1: { ...p1.json, timeMs: p1.timeMs },
      phase2: {
        ...p2.json,
        timeMs: p2.timeMs,
        textLength: fullText.length,
        tokensEstimate: Math.round(fullText.split(/\s+/).length * 1.3),
      },
    })
  }

  // Summary
  console.log('\n' + '='.repeat(60))
  console.log('VÝSLEDKY — DVOJFÁZOVÝ BENCHMARK')
  console.log('='.repeat(60))
  console.log(`Model: ${MODEL}`)
  console.log(`Dokumentov: ${results.length}`)

  const classified = results.filter(r => r.phase1?.typ_dokumentu)
  const relevant = results.filter(r => r.phase1?.je_relevantny)
  const extracted = results.filter(r => r.phase2?.ucastnici)
  const totalParticipants = extracted.reduce((s, r) => s + (r.phase2.ucastnici?.length || 0), 0)
  const totalTime = results.reduce((s, r) => s + (r.phase1?.timeMs || 0) + (r.phase2?.timeMs || 0), 0)

  console.log(`\nFáza 1 (klasifikácia):`)
  console.log(`  Klasifikované: ${classified.length} / ${results.length}`)
  console.log(`  Relevantné: ${relevant.length} / ${classified.length}`)
  const types: Record<string, number> = {}
  for (const r of classified) { const t = r.phase1.typ_dokumentu; types[t] = (types[t] || 0) + 1 }
  for (const [t, c] of Object.entries(types)) console.log(`    ${t}: ${c}`)

  console.log(`\nFáza 2 (extrakcia):`)
  console.log(`  Spracované: ${extracted.length}`)
  console.log(`  Celkom uchádzačov: ${totalParticipants}`)
  console.log(`  Celkový čas: ${(totalTime / 1000).toFixed(0)}s`)
  console.log(`  Priemerný čas/dok: ${(totalTime / 1000 / results.length).toFixed(1)}s`)

  // Save
  const outFile = `2phase_benchmark_${MODEL.replace(/[:/]/g, '_')}.json`
  writeFileSync(resolve(OUT_DIR, outFile), JSON.stringify(results, null, 2))
  console.log(`\nUložené: ${outFile}`)
}

main().catch(err => { console.error('❌', err.message); process.exit(1) })
