#!/bin/bash
# ═══════════════════════════════════════════════════════════════
# Vestník Cron — lokálna verzia pre Mac
# Rovnaká logika ako VPS cron, ale s lokálnymi cestami
#
# Manuálne spustenie:
#   bash tools/vestnik-cron-local.sh
#
# Crontab (Po-Pi 23:00):
#   crontab -e
#   0 23 * * 1-5 /Users/dodo/UVOTool/tools/vestnik-cron-local.sh
# ═══════════════════════════════════════════════════════════════

set -euo pipefail

# ── Konfigurácia (lokálne cesty) ──
BASE_DIR="/Users/dodo/UVOTool"
ARCHIVE_DIR="$BASE_DIR/data/results"
HTML_DIR="/tmp"
LOGS_DIR="$BASE_DIR/data/logs"
PARSER="$BASE_DIR/tools/vestnik-parser.py"
VALIDATOR="$BASE_DIR/tools/vestnik-validator.py"
RSS_URL="https://www.uvo.gov.sk/vestnik-a-registre/vestnik/rss"

# Monitoring — ntfy.sh
NTFY_TOPIC="${NTFY_TOPIC:-vestnik-uvo}"
NTFY_URL="https://ntfy.sh/${NTFY_TOPIC}"

[ -f "$BASE_DIR/.env" ] && source "$BASE_DIR/.env"

mkdir -p "$ARCHIVE_DIR" "$LOGS_DIR"

DATE=$(date +%Y-%m-%d)
TIMESTAMP=$(date +%H:%M)
LOG_FILE="$LOGS_DIR/cron_${DATE}.log"

log() { echo "[$(date +%H:%M:%S)] $*" | tee -a "$LOG_FILE"; }
log_error() { echo "[$(date +%H:%M:%S)] ❌ $*" | tee -a "$LOG_FILE" >&2; }

notify_success() {
    curl -s -o /dev/null -H "Title: ✅ $1" -H "Priority: default" -d "$2" "$NTFY_URL" 2>/dev/null || true
}
notify_error() {
    curl -s -o /dev/null -H "Title: ❌ $1" -H "Priority: high" -d "$2" "$NTFY_URL" 2>/dev/null || true
}

on_error() {
    local line=$1
    log_error "Cron zlyhal na riadku $line"
    notify_error "Vestník Cron ZLYHAL" "Chyba na riadku $line, dátum $DATE. Pozri log: $LOG_FILE"
    exit 1
}
trap 'on_error $LINENO' ERR

# Log rotation
find "$LOGS_DIR" -name "cron_*.log" -mtime +30 -delete 2>/dev/null || true

log ""
log "═══════════════════════════════════════════════════"
log "Vestník Cron (Mac) — $DATE $TIMESTAMP"
log "═══════════════════════════════════════════════════"

# ── 1. Stiahni RSS ──
RSS_FILE="/tmp/uvo_rss_${DATE}.xml"
log "[1/6] Sťahujem RSS..."
curl -s -o "$RSS_FILE" --max-time 30 "$RSS_URL"

if [ ! -s "$RSS_FILE" ]; then
    log_error "RSS prázdne alebo nedostupné"
    notify_error "RSS nedostupné" "UVO RSS feed je prázdny alebo nedostupný ($DATE)"
    exit 1
fi

VESTNIK_NUM=$(python3 -c "
import xml.etree.ElementTree as ET
root = ET.parse('$RSS_FILE').getroot()
items = root.findall('.//item')
if items:
    desc = items[0].find('description').text or ''
    parts = desc.replace('VVO ', '').split('/')
    if len(parts) == 2: print(parts[0].strip())
" 2>/dev/null)

VESTNIK_YEAR=$(python3 -c "
import xml.etree.ElementTree as ET
root = ET.parse('$RSS_FILE').getroot()
items = root.findall('.//item')
if items:
    desc = items[0].find('description').text or ''
    parts = desc.replace('VVO ', '').split('/')
    if len(parts) == 2: print(parts[1].strip())
" 2>/dev/null)

if [ -z "$VESTNIK_NUM" ] || [ -z "$VESTNIK_YEAR" ]; then
    log_error "Nepodarilo sa zistiť číslo vestníka z RSS"
    notify_error "RSS parse error" "Nepodarilo sa extrahovať číslo vestníka z RSS ($DATE)"
    exit 1
fi

log "   Vestník: VVO $VESTNIK_NUM/$VESTNIK_YEAR"

# ── 2. Skontroluj či už spracovaný ──
RESULT_FILE="$ARCHIVE_DIR/vestnik_${VESTNIK_NUM}_${VESTNIK_YEAR}_parser_results.json"
if [ -f "$RESULT_FILE" ]; then
    log "   ⏭️  Vestník $VESTNIK_NUM/$VESTNIK_YEAR už spracovaný, skipping"
    exit 0
fi

# ── 3. Extrahuj doc IDs ──
DOC_DIR="/tmp/vestnik${VESTNIK_NUM}_full"
mkdir -p "$DOC_DIR"

DOC_IDS=$(python3 -c "
import xml.etree.ElementTree as ET
root = ET.parse('$RSS_FILE').getroot()
for item in root.findall('.//item'):
    link = item.find('link').text or ''
    doc_id = link.split('/')[-1].split('?')[0]
    if doc_id: print(doc_id)
" 2>/dev/null)

DOC_COUNT=$(echo "$DOC_IDS" | wc -l | tr -d ' ')
log "[2/6] $DOC_COUNT dokumentov v RSS"

# ── 4. Stiahni HTML ──
log "[3/6] Sťahujem HTML dokumenty..."
DOWNLOADED=0
SKIPPED=0
FAILED=0

for DOC_ID in $DOC_IDS; do
    HTML_FILE="$DOC_DIR/${DOC_ID}.html"
    if [ -f "$HTML_FILE" ]; then
        SKIPPED=$((SKIPPED + 1))
        continue
    fi
    URL="https://www.uvo.gov.sk/vestnik-a-registre/vestnik/oznamenie/detail/${DOC_ID}"
    if curl -s -o "$HTML_FILE" --max-time 15 "$URL"; then
        if [ -s "$HTML_FILE" ]; then
            DOWNLOADED=$((DOWNLOADED + 1))
        else
            rm -f "$HTML_FILE"
            FAILED=$((FAILED + 1))
            log "   ⚠️  Prázdna odpoveď: $DOC_ID"
        fi
    else
        FAILED=$((FAILED + 1))
        log "   ⚠️  Zlyhalo stiahnutie: $DOC_ID"
    fi
    sleep 0.2
done

log "   Stiahnutých: $DOWNLOADED nových, $SKIPPED existujúcich, $FAILED zlyhaných"

if [ "$FAILED" -gt 0 ] && [ "$DOWNLOADED" -eq 0 ] && [ "$SKIPPED" -eq 0 ]; then
    log_error "Všetky sťahovania zlyhali"
    notify_error "Sťahovanie zlyhalo" "Všetkých $DOC_COUNT dokumentov zlyhalo pre VVO $VESTNIK_NUM/$VESTNIK_YEAR"
    exit 1
fi

# ── 5. Parser ──
log "[4/6] Spúšťam parser..."
python3 "$PARSER" "$RSS_FILE" 2>&1 | tee "$LOGS_DIR/parse_${VESTNIK_NUM}_${VESTNIK_YEAR}.log"

# ── 6. Validácia ──
log "[5/6] Validujem výsledky..."
# Parser ukladá do data/results/ — nájdi výstup
PARSER_OUTPUT=$(find "$ARCHIVE_DIR" -name "vestnik_*_parser_results.json" -newer "$RSS_FILE" 2>/dev/null | head -1)
VALIDATION_OK=true

if [ -n "$PARSER_OUTPUT" ] && [ -f "$PARSER_OUTPUT" ]; then
    log "   Výsledky: $PARSER_OUTPUT"
    if python3 "$VALIDATOR" "$PARSER_OUTPUT" 2>&1 | tee -a "$LOG_FILE"; then
        log "   ✅ Validácia OK"
    else
        log "   ⚠️  Validácia našla chyby"
        VALIDATION_OK=false
    fi
else
    log_error "Parser nevytvoril výstupný súbor"
    notify_error "Parser bez výstupu" "Parser pre VVO $VESTNIK_NUM/$VESTNIK_YEAR nevytvoril JSON ($DATE)"
    exit 1
fi

# ── 7. Súhrn ──
log ""
log "[6/6] Hotovo!"
log "   Vestník: VVO $VESTNIK_NUM/$VESTNIK_YEAR"
log "   Dokumentov: $DOC_COUNT"
log "   HTML: $DOC_DIR/"
log "   Dátum: $DATE $(date +%H:%M)"

SUMMARY="VVO $VESTNIK_NUM/$VESTNIK_YEAR: $DOC_COUNT dok, $DOWNLOADED nových HTML"
if [ "$VALIDATION_OK" = true ]; then
    notify_success "Vestník spracovaný" "$SUMMARY"
else
    notify_error "Vestník s chybami" "$SUMMARY — validácia našla problémy"
fi
