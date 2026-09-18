#!/usr/bin/env npx tsx
/**
 * LLM Benchmark — UVO + TED procurement notices
 * Tests Qwen 2.5 7B extraction on real procurement documents
 */
import { readFileSync, writeFileSync } from 'fs'
import { resolve } from 'path'

const OLLAMA_URL = 'http://localhost:11434/api/generate'
const MODEL = process.argv[2] || 'qwen2.5:7b'
const DATA_DIR = resolve(import.meta.dirname, '../data/CFE-Test')

const EXTRACTION_PROMPT = `Si expert na verejné obstarávanie v EÚ a na Slovensku. Z nasledujúceho oznámenia extrahuj údaje ako JSON. Odpovedz LEN platným JSON, žiadny iný text.

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
`

async function queryOllama(text: string): Promise<{ json: any; timeMs: number; rawResponse: string }> {
  const start = Date.now()
  const res = await fetch(OLLAMA_URL, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      model: MODEL,
      prompt: EXTRACTION_PROMPT + text,
      stream: false,
      options: { temperature: 0 },
    }),
  })
  const data = await res.json()
  const timeMs = Date.now() - start
  const rawResponse = data.response || ''

  const jsonMatch = rawResponse.match(/\{[\s\S]*\}/)
  if (!jsonMatch) return { json: null, timeMs, rawResponse }

  try {
    return { json: JSON.parse(jsonMatch[0]), timeMs, rawResponse }
  } catch {
    return { json: null, timeMs, rawResponse }
  }
}

function splitNotices(fileContent: string): { id: string; text: string }[] {
  const parts = fileContent.split(/---\nNOTICE \d+\n---/)
  return parts
    .filter(p => p.trim().length > 100)
    .map((text, i) => ({ id: `NOTICE-${i + 1}`, text: text.trim() }))
}

async function main() {
  console.log(`=== VO Extraction Benchmark ===`)
  console.log(`Model: ${MODEL}\n`)

  const results: any[] = []

  // UVO
  try {
    const uvoData = readFileSync(resolve(DATA_DIR, 'uvo_benchmark_data.txt'), 'utf-8')
    const uvoNotices = splitNotices(uvoData)
    console.log(`UVO: ${uvoNotices.length} oznámení\n`)

    for (let i = 0; i < uvoNotices.length; i++) {
      const notice = uvoNotices[i]
      process.stdout.write(`  UVO ${notice.id}...`)
      const { json, timeMs } = await queryOllama(notice.text)

      if (json) {
        const fields = Object.keys(json).filter(k => json[k] !== null && json[k] !== '' && json[k] !== 'null')
        process.stdout.write(` ✅ ${fields.length} polí extrahovaných (${(timeMs/1000).toFixed(1)}s)\n`)
        results.push({ source: 'UVO', id: notice.id, success: true, fields: fields.length, timeMs, data: json })
      } else {
        process.stdout.write(` ❌ PARSE ERROR (${(timeMs/1000).toFixed(1)}s)\n`)
        results.push({ source: 'UVO', id: notice.id, success: false, timeMs })
      }
    }
  } catch (e) { console.log('UVO file not found, skipping') }

  // TED
  try {
    const tedData = readFileSync(resolve(DATA_DIR, 'ted_benchmark_data.txt'), 'utf-8')
    const tedNotices = splitNotices(tedData)
    console.log(`\nTED: ${tedNotices.length} oznámení\n`)

    for (let i = 0; i < tedNotices.length; i++) {
      const notice = tedNotices[i]
      process.stdout.write(`  TED ${notice.id}...`)
      const { json, timeMs } = await queryOllama(notice.text)

      if (json) {
        const fields = Object.keys(json).filter(k => json[k] !== null && json[k] !== '' && json[k] !== 'null')
        process.stdout.write(` ✅ ${fields.length} polí (${(timeMs/1000).toFixed(1)}s)\n`)
        results.push({ source: 'TED', id: notice.id, success: true, fields: fields.length, timeMs, data: json })
      } else {
        process.stdout.write(` ❌ PARSE ERROR (${(timeMs/1000).toFixed(1)}s)\n`)
        results.push({ source: 'TED', id: notice.id, success: false, timeMs })
      }
    }
  } catch (e) { console.log('TED file not found, skipping') }

  // Summary
  const successful = results.filter(r => r.success)
  const failed = results.filter(r => !r.success)
  const avgTime = successful.length > 0 ? successful.reduce((s, r) => s + r.timeMs, 0) / successful.length : 0
  const avgFields = successful.length > 0 ? successful.reduce((s, r) => s + r.fields, 0) / successful.length : 0

  console.log('\n' + '='.repeat(60))
  console.log('VÝSLEDKY')
  console.log('='.repeat(60))
  console.log(`Model: ${MODEL}`)
  console.log(`Celkom dokumentov: ${results.length}`)
  console.log(`Úspešne parsované: ${successful.length} / ${results.length} (${(successful.length/results.length*100).toFixed(0)}%)`)
  console.log(`Parse errors: ${failed.length}`)
  console.log(`Priemerný čas: ${(avgTime/1000).toFixed(1)}s`)
  console.log(`Priemerný počet extrahovaných polí: ${avgFields.toFixed(1)} / 21`)

  // Per-field coverage
  const fieldCounts: Record<string, number> = {}
  for (const r of successful) {
    for (const [k, v] of Object.entries(r.data)) {
      if (v !== null && v !== '' && v !== 'null') {
        fieldCounts[k] = (fieldCounts[k] || 0) + 1
      }
    }
  }
  console.log(`\nPokrytie polí (z ${successful.length} dokumentov):`)
  const allFields = ['obstaravatel_nazov','obstaravatel_ico','obstaravatel_email','predmet_zakazky',
    'hlavny_cpv_kod','druh_zakazky','postup','predpokladana_hodnota','mena','miesto_plnenia',
    'lehota_na_ponuky','pocet_lotov','subdodavky_povolene','kontaktna_osoba','kontaktny_email',
    'sumarizacia','klasifikacia_odvetvia','vyzaduje_certifikaciu','klucove_technicke_poziadavky']
  for (const f of allFields) {
    const c = fieldCounts[f] || 0
    const pct = ((c / successful.length) * 100).toFixed(0)
    console.log(`  ${f.padEnd(30)} ${pct.padStart(4)}% (${c}/${successful.length})`)
  }

  // Save results
  writeFileSync(resolve(DATA_DIR, `vo_benchmark_results_${MODEL.replace(/[:/]/g,'_')}.json`),
    JSON.stringify(results, null, 2))
  console.log(`\nVýsledky uložené do: vo_benchmark_results_${MODEL.replace(/[:/]/g,'_')}.json`)
}

main().catch(err => { console.error('❌', err.message); process.exit(1) })
