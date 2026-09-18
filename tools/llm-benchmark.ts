#!/usr/bin/env npx tsx
/**
 * LLM Extraction Benchmark — test Qwen 2.5 on 50 real TDD XML files
 *
 * For each TDD: send to Ollama, parse JSON response, compare with DB values.
 * Reports: accuracy per field, total accuracy, average time.
 *
 * Usage: npx tsx tools/llm-benchmark.ts
 */
import { readdirSync, readFileSync } from 'fs'
import { resolve } from 'path'

const TDD_DIR = resolve(import.meta.dirname, '../data/real_TDD_sept2026/tdds')
const OLLAMA_URL = 'http://localhost:11434/api/generate'
const MODEL = 'qwen2.5:7b'
const SAMPLE_SIZE = 50

const PROMPT_TEMPLATE = `Si expert na slovenské DPH a Peppol eFaktúry. Z nasledujúceho TDD XML extrahuj údaje. Odpovedz LEN platným JSON, žiadny iný text.

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
`

// Simple XML extraction for ground truth
function extractFromXml(xml: string) {
  const get = (tag: string) => {
    const m = xml.match(new RegExp(`<(?:[a-z]+:)?${tag}[^>]*>([^<]+)`, 'i'))
    return m?.[1]?.trim() || ''
  }

  const docBlock = xml.match(/ReportedDocument[\s\S]*?<\/.*?ReportedDocument>/i)?.[0] || xml
  const supBlock = xml.match(/AccountingSupplierParty[\s\S]*?<\/.*?AccountingSupplierParty>/i)?.[0] || ''
  const custBlock = xml.match(/AccountingCustomerParty[\s\S]*?<\/.*?AccountingCustomerParty>/i)?.[0] || ''
  const taxBlock = xml.match(/TaxTotal[\s\S]*?<\/.*?TaxTotal>/i)?.[0] || ''
  const mtBlock = xml.match(/MonetaryTotal[\s\S]*?<\/.*?MonetaryTotal>/i)?.[0] || ''
  const delBlock = xml.match(/Delivery[\s\S]*?<\/.*?Delivery>/i)?.[0] || ''
  const catBlock = xml.match(/TaxCategory[\s\S]*?<\/.*?TaxCategory>/i)?.[0] || ''

  const getFrom = (block: string, tag: string) => {
    const m = block.match(new RegExp(`<(?:[a-z]+:)?${tag}[^>]*>([^<]+)`, 'i'))
    return m?.[1]?.trim() || ''
  }

  return {
    cislo_faktury: getFrom(docBlock, 'ID'),
    dodavatel_ic_dph: getFrom(supBlock, 'CompanyID'),
    odberatel_ic_dph: getFrom(custBlock, 'CompanyID'),
    odberatel_nazov: getFrom(custBlock, 'RegistrationName'),
    zaklad_dane: getFrom(mtBlock, 'TaxExclusiveAmount'),
    suma_dph: getFrom(taxBlock, 'TaxAmount'),
    na_uhradu: getFrom(mtBlock, 'PayableAmount'),
    datum_vystavenia: (() => {
      const m = docBlock.match(/<cbc:IssueDate>([^<]+)/i)
      return m?.[1]?.trim() || ''
    })(),
    datum_dodania: getFrom(delBlock, 'ActualDeliveryDate'),
    sadzba_dph: getFrom(catBlock, 'Percent'),
    typ_dokumentu: getFrom(docBlock, 'DocumentTypeCode'),
    mena: getFrom(docBlock, 'DocumentCurrencyCode'),
    tax_data_type: get('TaxDataTypeCode'),
  }
}

async function queryOllama(xml: string): Promise<{ json: any; timeMs: number }> {
  const start = Date.now()
  const res = await fetch(OLLAMA_URL, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      model: MODEL,
      prompt: PROMPT_TEMPLATE + xml,
      stream: false,
      options: { temperature: 0 },
    }),
  })
  const data = await res.json()
  const timeMs = Date.now() - start
  const text = data.response || ''

  // Extract JSON from response
  const jsonMatch = text.match(/\{[\s\S]*\}/)
  if (!jsonMatch) return { json: null, timeMs }

  try {
    return { json: JSON.parse(jsonMatch[0]), timeMs }
  } catch {
    return { json: null, timeMs }
  }
}

function normalize(val: any): string {
  if (val === null || val === undefined) return ''
  return String(val).trim().replace(/\.00$/, '')
}

function compareField(expected: string, actual: any): boolean {
  const e = normalize(expected)
  const a = normalize(actual)
  if (!e && !a) return true
  if (e === a) return true
  // Numeric comparison (460.00 vs 460)
  if (e && a && !isNaN(Number(e)) && !isNaN(Number(a))) {
    return Math.abs(Number(e) - Number(a)) < 0.01
  }
  return false
}

async function main() {
  const files = readdirSync(TDD_DIR).filter(f => f.endsWith('.xml')).sort()
  // Sample evenly across the range
  const step = Math.floor(files.length / SAMPLE_SIZE)
  const sample = Array.from({ length: SAMPLE_SIZE }, (_, i) => files[Math.min(i * step, files.length - 1)])

  console.log(`=== LLM Extraction Benchmark ===`)
  console.log(`Model: ${MODEL}`)
  console.log(`Sample: ${sample.length} / ${files.length} TDD files\n`)

  const fieldStats: Record<string, { correct: number; total: number }> = {}
  const fields = ['cislo_faktury', 'dodavatel_ic_dph', 'odberatel_ic_dph', 'odberatel_nazov',
    'zaklad_dane', 'suma_dph', 'na_uhradu', 'datum_vystavenia', 'datum_dodania',
    'sadzba_dph', 'typ_dokumentu', 'mena', 'tax_data_type']

  for (const f of fields) fieldStats[f] = { correct: 0, total: 0 }

  let totalCorrect = 0
  let totalFields = 0
  let totalTime = 0
  let parseErrors = 0
  let perfectDocs = 0

  for (let i = 0; i < sample.length; i++) {
    const fname = sample[i]
    const xml = readFileSync(resolve(TDD_DIR, fname), 'utf-8')
    const expected = extractFromXml(xml)

    process.stdout.write(`\r  [${i + 1}/${sample.length}] ${fname}...`)

    const { json: actual, timeMs } = await queryOllama(xml)
    totalTime += timeMs

    if (!actual) {
      parseErrors++
      process.stdout.write(` PARSE ERROR (${timeMs}ms)\n`)
      for (const f of fields) fieldStats[f].total++
      totalFields += fields.length
      continue
    }

    let docCorrect = 0
    for (const f of fields) {
      fieldStats[f].total++
      totalFields++
      const match = compareField((expected as any)[f], actual[f])
      if (match) {
        fieldStats[f].correct++
        totalCorrect++
        docCorrect++
      }
    }

    if (docCorrect === fields.length) perfectDocs++

    const pct = ((docCorrect / fields.length) * 100).toFixed(0)
    if (docCorrect < fields.length) {
      process.stdout.write(` ${pct}% (${timeMs}ms)`)
      // Show mismatches
      for (const f of fields) {
        if (!compareField((expected as any)[f], actual[f])) {
          process.stdout.write(` ✗${f}`)
        }
      }
      process.stdout.write('\n')
    } else {
      process.stdout.write(` 100% (${timeMs}ms)\n`)
    }
  }

  // Results
  console.log('\n' + '='.repeat(60))
  console.log('VÝSLEDKY')
  console.log('='.repeat(60))
  console.log(`\nDokumenty: ${sample.length}`)
  console.log(`Perfektné (100% polí): ${perfectDocs} / ${sample.length} (${(perfectDocs / sample.length * 100).toFixed(1)}%)`)
  console.log(`Parse errors: ${parseErrors}`)
  console.log(`Priemerný čas: ${(totalTime / sample.length / 1000).toFixed(1)}s`)
  console.log(`Celková presnosť: ${totalCorrect} / ${totalFields} (${(totalCorrect / totalFields * 100).toFixed(1)}%)`)

  console.log(`\nPresnosť podľa polí:`)
  for (const f of fields) {
    const s = fieldStats[f]
    const pct = ((s.correct / s.total) * 100).toFixed(1)
    const bar = pct === '100.0' ? '████████████' : '████████░░░░'
    console.log(`  ${f.padEnd(22)} ${pct.padStart(6)}%  ${bar}  (${s.correct}/${s.total})`)
  }
}

main().catch(err => { console.error('❌', err.message); process.exit(1) })
