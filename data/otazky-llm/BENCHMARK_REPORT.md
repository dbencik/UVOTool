# Ollama Q&A Benchmark — VO dokumenty

**Dátum:** 2026-09-18
**HW:** MacBook Pro M2 Max, 96 GB RAM
**Modely:** Qwen 2.5 7B (4.5 GB), Qwen 2.5 32B (19 GB), Mistral Small 24B (13.7 GB)

---

## Súhrn výsledkov

| Súbor | Typ | Qwen 7B | Qwen 32B | Mistral Small |
|-------|-----|---------|----------|---------------|
| q1-1 CRZ link (788 zn.) | klasifikácia | **2.8s** | 9.6s | 9.2s |
| q1-2 zápisnica (2 450 zn.) | klasifikácia | **21.8s** | 6.6s | 4.1s |
| q1-5 IT dokument (8 301 zn.) | klasifikácia | **5.5s** | 26.3s | 15.5s |
| q2-2 zápisnica extrakcia (3 606 zn.) | extrakcia | **7.2s** | 29.6s | 20.2s |
| q2-5 IT extrakcia (26 860 zn.) | extrakcia | **23.6s** | 103.0s | 75.5s |
| **CELKOM** | | **60.9s** | **175.1s** | **124.5s** |
| **Priemer** | | **12.2s** | **35.0s** | **24.9s** |

### Rýchlosť generovania

| Model | Veľkosť | tok/s (krátky vstup) | tok/s (dlhý vstup) |
|-------|---------|---------------------|---------------------|
| Qwen 2.5 7B | 4.5 GB | ~115 | ~50 |
| Qwen 2.5 32B | 19 GB | ~30 | ~13 |
| Mistral Small 24B | 13.7 GB | ~40 | ~15 |

---

## Klasifikácia (otázky q1) — odpovede

Prompt: *Zisti kategóriu dokumentu, vráť len kód A-H.*

### q1-1: CRZ link (zmluva, 788 znakov, 0 uchádzačov)

| Model | Čas | Odpoveď | Správne? |
|-------|-----|---------|----------|
| Qwen 7B | 2.8s | `F` | ✅ |
| Qwen 32B | 9.6s | `F` | ✅ |
| Mistral Small | 9.2s | `Na základe poskytnutého textu dokumentu, ktorý obsahuje odkaz na zmluvu, je možné určiť, že dokument patrí do kategórie: F` | ✅ (ale nevrátil len kód) |

**Poznámka:** Mistral Small nevrátil len písmeno, ale aj vysvetlenie — nedodržal inštrukciu "Vráť len kód".

### q1-2: Stavebná zápisnica (2 450 znakov, 3 uchádzači)

| Model | Čas | Odpoveď | Správne? |
|-------|-----|---------|----------|
| Qwen 7B | 21.8s | `D` | ✅ |
| Qwen 32B | 6.6s | `D` | ✅ |
| Mistral Small | 4.1s | `D` | ✅ |

### q1-5: IT dokument (8 301 znakov, 2 uchádzači)

| Model | Čas | Odpoveď | Správne? |
|-------|-----|---------|----------|
| Qwen 7B | 5.5s | `D` | ✅ |
| Qwen 32B | 26.3s | `D` | ✅ |
| Mistral Small | 15.5s | `D` | ✅ |

**Klasifikácia súhrn:** Všetky 3 modely klasifikovali správne. Qwen 7B je najrýchlejší.

---

## Extrakcia účastníkov (otázky q2) — odpovede

Prompt: *Extrahuj účastníkov tendra, vráť JSON.*

### q2-2: Stavebná zápisnica (3 606 znakov, 3 uchádzači)

**Qwen 2.5 7B** (7.2s):
```json
{
  "Stav": 1,
  "Ucastnici": [
    {"ICO": "81222222", "NAME": "Sanácie SK s. r. o.", "COUNTRY": "SK", "DoruceniePonuky": "YES", "Poradie": "1", "CENA": "79 523,57 eur s DPH", "Excl_Flag": "N"},
    {"ICO": "27816222", "NAME": "KERASTAV, s.r.o.", "COUNTRY": "SK", "DoruceniePonuky": "YES", "Poradie": "2", "CENA": "75 029,99 eur s DPH", "Excl_Flag": "N"},
    {"ICO": "27816222", "NAME": "GLOBALTECH SLOVAKIA s.r.o.", "COUNTRY": "SK", "DoruceniePonuky": "YES", "Poradie": "3", "CENA": "74 563,15 eur s DPH", "Excl_Flag": "N"}
  ]
}
```
⚠️ **Problém: Vymyslel si IČO!** IČO "81222222" a "27816222" nie sú v dokumente — halucinácia. Navyše dal dvom firmám rovnaké IČO.

**Qwen 2.5 32B** (29.6s):
```json
{
  "Stav": 1,
  "Ucastnici": [
    {"ICO": "", "NAME": "Sanácie SK s. r. o.", "COUNTRY": "SK", "DoruceniePonuky": "YES", "Poradie": "#NA", "CENA": "79 523,57 eur s DPH", "Excl_Flag": "?"},
    {"ICO": "", "NAME": "KERASTAV, s.r.o.", "COUNTRY": "SK", "DoruceniePonuky": "YES", "Poradie": "#NA", "CENA": "75 029,99 eur s DPH", "Excl_Flag": "?"},
    {"ICO": "", "NAME": "GLOBALTECH SLOVAKIA s.r.o.", "COUNTRY": "SK", "DoruceniePonuky": "YES", "Poradie": "#NA", "CENA": "74 563,15 eur s DPH", "Excl_Flag": "?"}
  ]
}
```
✅ Správne nechal IČO prázdne (nie je v dokumente). Poradie a Excl_Flag nastavil na neurčité.

**Mistral Small 24B** (20.2s):
```json
{
  "Stav": 1,
  "Ucastnici": [
    {"ICO": "", "NAME": "Sanácie SK s. r. o.", "COUNTRY": "SK", "DoruceniePonuky": "YES", "Poradie": "", "CENA": "79 523,57 eur s DPH", "Excl_Flag": "N"},
    {"ICO": "", "NAME": "KERASTAV, s.r.o.", "COUNTRY": "SK", "DoruceniePonuky": "YES", "Poradie": "", "CENA": "75 029,99 eur s DPH", "Excl_Flag": "N"},
    {"ICO": "", "NAME": "GLOBALTECH SLOVAKIA s.r.o.", "COUNTRY": "SK", "DoruceniePonuky": "YES", "Poradie": "", "CENA": "74 563,15 eur s DPH", "Excl_Flag": "N"}
  ]
}
```
✅ Správne nechal IČO prázdne. Čistý výstup.

### q2-5: IT dokument (26 860 znakov, 2 uchádzači)

**Qwen 2.5 7B** (23.6s):
```json
{
  "Stav": 1,
  "Ucastnici": [
    {"ICO": "54629331", "NAME": "Alanata a. s.", "COUNTRY": "SK", "DoruceniePonuky": "YES", "Poradie": "1", "CENA": "", "Excl_Flag": "N"},
    {"ICO": "46450424", "NAME": "WDS Solutions s.r.o.", "COUNTRY": "SK", "DoruceniePonuky": "YES", "Poradie": "1", "CENA": "", "Excl_Flag": "N"}
  ]
}
```
⚠️ Oba majú Poradie "1" — zrejme nevie rozlíšiť poradie. IČO vyzerá korektne extrahované z dokumentu.

**Qwen 2.5 32B** (103.0s):
```json
{
  "Stav": 1,
  "Ucastnici": [
    {"ICO": "54629331", "NAME": "Alanata a. s.", "COUNTRY": "SK", "DoruceniePonuky": "YES", "Poradie": "1", "CENA": "#NA", "Excl_Flag": "N"}
  ]
}
```
⚠️ **Chýba 2. uchádzač** (Datacomp / WDS Solutions). Extrahoval len 1 z 2.

**Mistral Small 24B** (75.5s):
```json
{
  "Stav": 1,
  "Ucastnici": [
    {"ICO": "54629331", "NAME": "Alanata a. s.", "COUNTRY": "SK", "DoruceniePonuky": "YES", "Poradie": "1", "CENA": "", "Excl_Flag": "N"},
    {"ICO": "36212466", "NAME": "Datacomp s.r.o.", "COUNTRY": "SK", "DoruceniePonuky": "YES", "Poradie": "", "CENA": "", "Excl_Flag": "?"}
  ]
}
```
✅ Extrahoval oboch uchádzačov. IČO Datacomp korektné. Najlepší výsledok z troch modelov.

---

## Hodnotenie kvality

| Kritérium | Qwen 7B | Qwen 32B | Mistral Small |
|-----------|---------|----------|---------------|
| Klasifikácia (q1) | 3/3 ✅ | 3/3 ✅ | 3/3 ✅ |
| Dodržanie formátu | ✅ len kód | ✅ len kód | ⚠️ pridáva text |
| Extrakcia — správny počet | 2/2 ✅ | 1/2 ⚠️ | 2/2 ✅ |
| Halucinácia IČO | ❌ vymýšľa IČO | ✅ nechá prázdne | ✅ nechá prázdne |
| JSON validita | ✅ | ✅ | ✅ |
| **Celkové hodnotenie** | **⚠️ Rýchly ale halucinácie** | **⚠️ Pomalý, stráca uchádzačov** | **✅ Najlepšia kvalita** |

---

## Záver

1. **Klasifikácia (A-H):** Všetky modely zvládajú spoľahlivo. Qwen 7B je najrýchlejší (2-6s).

2. **Extrakcia účastníkov:** 
   - **Mistral Small** = najlepšia kvalita (správny počet, žiadne halucinácie)
   - **Qwen 7B** = najrýchlejší, ale **vymýšľa si IČO** ak nie je v texte
   - **Qwen 32B** = pomalý a **stráca uchádzačov** v dlhých dokumentoch

3. **Odporúčanie pre produkciu:**
   - Klasifikácia: Qwen 7B (rýchly, spoľahlivý)
   - Extrakcia: Mistral Small (kvalitnejší) alebo Qwen 7B + post-validácia IČO
   - Hybridný prístup: Qwen 7B na klasifikáciu → Mistral Small na extrakciu

4. **Rýchlosť:**
   - Qwen 7B: ~12s/dok priemer
   - Mistral Small: ~25s/dok priemer  
   - Qwen 32B: ~35s/dok priemer
   - Pre 100 dok/deň: Qwen 7B = 20 min, Mistral Small = 42 min, Qwen 32B = 58 min
