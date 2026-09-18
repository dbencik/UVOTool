#!/usr/bin/env npx tsx
/**
 * LLM Benchmark on FULL UVO documents (not pre-extracted summaries)
 * Downloads complete HTML, extracts text, sends to Ollama.
 */
import { writeFileSync } from 'fs'
import { resolve } from 'path'

const OLLAMA_URL = 'http://localhost:11434/api/generate'
const MODEL = process.argv[2] || 'qwen2.5:7b'
const OUT_DIR = resolve(import.meta.dirname, '../data/CFE-Test')

const UVO_URLS = [
  'https://www.uvo.gov.sk/vestnik-a-registre/vestnik/oznamenie/detail/1410753?cHash=fbda2e06f2dae2cd762ab8b7bd8258a3',
  'https://www.uvo.gov.sk/vestnik-a-registre/vestnik/oznamenie/detail/1398513?cHash=93ac2a95a9845d14a93c5c7f7c82e957',
  'https://www.uvo.gov.sk/vestnik-a-registre/vestnik/oznamenie/detail/1383034?cHash=27015446e7d70f1d5410d368b8672417',
  'https://www.uvo.gov.sk/vestnik-a-registre/vestnik/oznamenie/detail/1411528?cHash=98698ea807ea79f4ea8e0a4536640c74',
  'https://www.uvo.gov.sk/vestnik-a-registre/vestnik/oznamenie/detail/1402332?cHash=2a83a9e93821938d2562b7709dd4ddf2',
  'https://www.uvo.gov.sk/vestnik-a-registre/vestnik/oznamenie/detail/1384552?cHash=0700325e73f61c08aacbc495f5e323e8',
  'https://www.uvo.gov.sk/vestnik-a-registre/vestnik/oznamenie/detail/1400218?cHash=1de2f09b60fe01b308287a067bbef73f',
  'https://www.uvo.gov.sk/vestnik-a-registre/vestnik/oznamenie/detail/1395981?cHash=ef698019149c9634a7779112ce4e001f',
  'https://www.uvo.gov.sk/vestnik-a-registre/vestnik/oznamenie/detail/1385309?cHash=fc7bd72a505924a141da5ec5717e08ee',
  'https://www.uvo.gov.sk/vestnik-a-registre/vestnik/oznamenie/detail/1402332?cHash=2a83a9e93821938d2562b7709dd4ddf2',
]

const PROMPT = `Si expert na verejné obstarávanie na Slovensku. Z nasledujúceho CELÉHO textu oznámenia z Vestníka VO extrahuj údaje ako JSON. Odpovedz LEN platným JSON.

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
- vitaz_nazov (ak je výsledok)
- vitaz_ico (ak je výsledok)
- vitaz_cena (ak je výsledok)
- pocet_ponuk (ak je výsledok)
- sumarizacia (3 vety max, vlastnými slovami)
- klasifikacia_odvetvia (IT/stavebnictvo/zdravotnictvo/energia/doprava/kultura/ine)

Celý text oznámenia:
`

/** Strip HTML tags, scripts, styles — keep only text */
function htmlToText(html: string): string {
  // Remove scripts, styles, nav, footer
  let text = html
    .replace(/<script[\s\S]*?<\/script>/gi, '')
    .replace(/<style[\s\S]*?<\/style>/gi, '')
    .replace(/<nav[\s\S]*?<\/nav>/gi, '')
    .replace(/<footer[\s\S]*?<\/footer>/gi, '')
    .replace(/<header[\s\S]*?<\/header>/gi, '')
  // Remove all HTML tags
  text = text.replace(/<[^>]+>/g, ' ')
  // Decode entities
  text = text.replace(/&amp;/g, '&').replace(/&lt;/g, '<').replace(/&gt;/g, '>')
    .replace(/&quot;/g, '"').replace(/&#(\d+);/g, (_, c) => String.fromCharCode(+c))
    .replace(/&nbsp;/g, ' ')
  // Collapse whitespace
  text = text.replace(/\s+/g, ' ').trim()
  // Remove common UVO boilerplate (menu, footer text)
  const mainStart = text.indexOf('Základné informácie')
  if (mainStart > 0) {
    // Find actual content after navigation
    const contentStart = text.indexOf('Oznámenie', mainStart)
    if (contentStart > 0) text = text.substring(contentStart)
  }
  return text
}

async function fetchFullPage(url: string): Promise<{ text: string; bytes: number }> {
  const res = await fetch(url, { signal: AbortSignal.timeout(15000) })
  const html = await res.text()
  const text = htmlToText(html)
  return { text, bytes: html.length }
}

async function queryOllama(text: string): Promise<{ json: any; timeMs: number; inputTokens: number; outputTokens: number }> {
  const prompt = PROMPT + text
  const inputTokens = Math.round(prompt.split(/\s+/).length * 1.3)
  const start = Date.now()
  const res = await fetch(OLLAMA_URL, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ model: MODEL, prompt, stream: false, options: { temperature: 0 } }),
  })
  const data = await res.json()
  const timeMs = Date.now() - start
  const raw = data.response || ''
  const outputTokens = Math.round(raw.split(/\s+/).length * 1.3)

  const jsonMatch = raw.match(/\{[\s\S]*\}/)
  if (!jsonMatch) return { json: null, timeMs, inputTokens, outputTokens }
  try { return { json: JSON.parse(jsonMatch[0]), timeMs, inputTokens, outputTokens } }
  catch { return { json: null, timeMs, inputTokens, outputTokens } }
}

async function main() {
  console.log(`=== FULL DOCUMENT LLM Benchmark ===`)
  console.log(`Model: ${MODEL}`)
  console.log(`Dokumentov: ${UVO_URLS.length}\n`)

  const results: any[] = []

  for (let i = 0; i < UVO_URLS.length; i++) {
    const url = UVO_URLS[i]
    process.stdout.write(`[${i + 1}/${UVO_URLS.length}] Sťahujem...`)

    let page: { text: string; bytes: number }
    try {
      page = await fetchFullPage(url)
    } catch (err) {
      process.stdout.write(` ❌ FETCH ERROR\n`)
      results.push({ url, success: false, error: 'fetch failed' })
      continue
    }

    process.stdout.write(` ${(page.bytes / 1024).toFixed(0)}KB HTML → ${(page.text.length / 1024).toFixed(1)}KB text → `)

    const { json, timeMs, inputTokens, outputTokens } = await queryOllama(page.text)

    if (json) {
      const fields = Object.keys(json).filter(k => json[k] !== null && json[k] !== '' && json[k] !== 'null')
      console.log(`✅ ${fields.length} polí (${(timeMs / 1000).toFixed(1)}s, ~${inputTokens} tok vstup)`)
      results.push({
        url, success: true, fields: fields.length, timeMs,
        htmlBytes: page.bytes, textChars: page.text.length,
        inputTokens, outputTokens, data: json,
      })
    } else {
      console.log(`❌ PARSE ERROR (${(timeMs / 1000).toFixed(1)}s)`)
      results.push({ url, success: false, timeMs, htmlBytes: page.bytes, textChars: page.text.length })
    }
  }

  // Summary
  const ok = results.filter(r => r.success)
  const fail = results.filter(r => !r.success)
  const avgTime = ok.length > 0 ? ok.reduce((s, r) => s + r.timeMs, 0) / ok.length : 0
  const avgFields = ok.length > 0 ? ok.reduce((s, r) => s + r.fields, 0) / ok.length : 0
  const avgInput = ok.length > 0 ? ok.reduce((s, r) => s + r.inputTokens, 0) / ok.length : 0
  const avgHtml = ok.length > 0 ? ok.reduce((s, r) => s + r.htmlBytes, 0) / ok.length : 0
  const avgText = ok.length > 0 ? ok.reduce((s, r) => s + r.textChars, 0) / ok.length : 0

  console.log('\n' + '='.repeat(60))
  console.log('VÝSLEDKY — PLNÉ DOKUMENTY')
  console.log('='.repeat(60))
  console.log(`Model: ${MODEL}`)
  console.log(`Dokumentov: ${results.length}`)
  console.log(`Úspešne: ${ok.length} / ${results.length} (${(ok.length / results.length * 100).toFixed(0)}%)`)
  console.log(`Parse errors: ${fail.length}`)
  console.log(`\nVeľkosti:`)
  console.log(`  Priemer HTML: ${(avgHtml / 1024).toFixed(0)} KB`)
  console.log(`  Priemer text: ${(avgText / 1024).toFixed(1)} KB`)
  console.log(`  Priemer tokenov vstup: ${avgInput.toFixed(0)}`)
  console.log(`\nRýchlosť:`)
  console.log(`  Priemerný čas: ${(avgTime / 1000).toFixed(1)}s`)
  console.log(`  Priemerný počet polí: ${avgFields.toFixed(1)} / 23`)

  // Per-field coverage
  if (ok.length > 0) {
    const fieldCounts: Record<string, number> = {}
    for (const r of ok) {
      for (const [k, v] of Object.entries(r.data)) {
        if (v !== null && v !== '' && v !== 'null') fieldCounts[k] = (fieldCounts[k] || 0) + 1
      }
    }
    console.log(`\nPokrytie polí:`)
    for (const [f, c] of Object.entries(fieldCounts).sort((a, b) => b[1] - a[1])) {
      console.log(`  ${f.padEnd(28)} ${((c / ok.length) * 100).toFixed(0).padStart(4)}% (${c}/${ok.length})`)
    }
  }

  // Save
  const outFile = `full_benchmark_${MODEL.replace(/[:/]/g, '_')}.json`
  writeFileSync(resolve(OUT_DIR, outFile), JSON.stringify(results, null, 2))
  console.log(`\nUložené: ${outFile}`)
}

main().catch(err => { console.error('❌', err.message); process.exit(1) })
