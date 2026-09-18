#!/bin/bash
# ═══════════════════════════════════════════════════════════════
# Vestník Cron — denný sťahovač a parser UVO vestníkov
# Nasadiť na Hostinger VPS (147.93.121.59)
#
# Čo robí:
#   1. Zistí aktuálne číslo vestníka z RSS
#   2. Stiahne listing stránku vestníka
#   3. Stiahne všetky HTML dokumenty
#   4. Spustí deterministický parser
#   5. Validuje výsledky
#   6. Pošle notifikáciu (úspech/zlyhanie)
#
# Inštalácia na VPS:
#   scp tools/vestnik-cron.sh tools/vestnik-parser.py tools/vestnik-validator.py user@147.93.121.59:/opt/vestnik/
#   ssh user@147.93.121.59
#   chmod +x /opt/vestnik/vestnik-cron.sh
#   # Pridaj do crontab:
#   crontab -e
#   0 23 * * 1-5 /opt/vestnik/vestnik-cron.sh >> /opt/vestnik/logs/cron.log 2>&1
#
# Požiadavky: python3, curl
# ═══════════════════════════════════════════════════════════════

set -euo pipefail

# ── Konfigurácia ──
BASE_DIR="/opt/vestnik"
ARCHIVE_DIR="$BASE_DIR/archive"
HTML_DIR="$BASE_DIR/html"
LOGS_DIR="$BASE_DIR/logs"
PARSER="$BASE_DIR/vestnik-parser.py"
VALIDATOR="$BASE_DIR/vestnik-validator.py"
RSS_URL="https://www.uvo.gov.sk/vestnik-a-registre/vestnik/rss"

# Monitoring — ntfy.sh (bezplatné push notifikácie)
# Nastav NTFY_TOPIC v /opt/vestnik/.env alebo tu:
NTFY_TOPIC="${NTFY_TOPIC:-vestnik-uvo}"
NTFY_URL="https://ntfy.sh/${NTFY_TOPIC}"

# Načítaj .env ak existuje
[ -f "$BASE_DIR/.env" ] && source "$BASE_DIR/.env"

# Vytvor adresáre ak neexistujú
mkdir -p "$ARCHIVE_DIR" "$HTML_DIR" "$LOGS_DIR"

DATE=$(date +%Y-%m-%d)
TIMESTAMP=$(date +%H:%M)
LOG_FILE="$LOGS_DIR/cron_${DATE}.log"

# ── Logging funkcie ──
log() { echo "[$(date +%H:%M:%S)] $*" | tee -a "$LOG_FILE"; }
log_error() { echo "[$(date +%H:%M:%S)] ❌ $*" | tee -a "$LOG_FILE" >&2; }

# ── Notifikácie ──
notify_success() {
    local title="$1" body="$2"
    curl -s -o /dev/null \
        -H "Title: ✅ $title" \
        -H "Priority: default" \
        -H "Tags: white_check_mark" \
        -d "$body" \
        "$NTFY_URL" 2>/dev/null || true
}

notify_error() {
    local title="$1" body="$2"
    curl -s -o /dev/null \
        -H "Title: ❌ $title" \
        -H "Priority: high" \
        -H "Tags: rotating_light" \
        -d "$body" \
        "$NTFY_URL" 2>/dev/null || true
}

# ── Cleanup pri chybe ──
on_error() {
    local line=$1
    log_error "Cron zlyhal na riadku $line"
    notify_error "Vestník Cron ZLYHAL" "Chyba na riadku $line, dátum $DATE. Pozri log: $LOG_FILE"
    exit 1
}
trap 'on_error $LINENO' ERR

# ── Log rotation (ponechaj posledných 30 dní) ──
find "$LOGS_DIR" -name "cron_*.log" -mtime +30 -delete 2>/dev/null || true
find "$LOGS_DIR" -name "parse_*.log" -mtime +30 -delete 2>/dev/null || true

log ""
log "═══════════════════════════════════════════════════"
log "Vestník Cron — $DATE $TIMESTAMP"
log "═══════════════════════════════════════════════════"

# ── 1. Stiahni RSS a zisti číslo vestníka ──
RSS_FILE="$ARCHIVE_DIR/rss_${DATE}.xml"
log "[1/6] Sťahujem RSS..."
curl -s -o "$RSS_FILE" --max-time 30 "$RSS_URL"

if [ ! -s "$RSS_FILE" ]; then
    log_error "RSS prázdne alebo nedostupné"
    notify_error "RSS nedostupné" "UVO RSS feed je prázdny alebo nedostupný ($DATE)"
    exit 1
fi

# Extrahuj číslo vestníka z description: "VVO 191/2026"
VESTNIK_NUM=$(python3 -c "
import xml.etree.ElementTree as ET
root = ET.parse('$RSS_FILE').getroot()
items = root.findall('.//item')
if items:
    desc = items[0].find('description').text or ''
    parts = desc.replace('VVO ', '').split('/')
    if len(parts) == 2:
        print(parts[0].strip())
" 2>/dev/null)

VESTNIK_YEAR=$(python3 -c "
import xml.etree.ElementTree as ET
root = ET.parse('$RSS_FILE').getroot()
items = root.findall('.//item')
if items:
    desc = items[0].find('description').text or ''
    parts = desc.replace('VVO ', '').split('/')
    if len(parts) == 2:
        print(parts[1].strip())
" 2>/dev/null)

if [ -z "$VESTNIK_NUM" ] || [ -z "$VESTNIK_YEAR" ]; then
    log_error "Nepodarilo sa zistiť číslo vestníka z RSS"
    notify_error "RSS parse error" "Nepodarilo sa extrahovať číslo vestníka z RSS ($DATE)"
    exit 1
fi

log "   Vestník: VVO $VESTNIK_NUM/$VESTNIK_YEAR"

# ── 2. Skontroluj či už nie je spracovaný ──
RESULT_FILE="$ARCHIVE_DIR/vestnik_${VESTNIK_NUM}_${VESTNIK_YEAR}.json"
if [ -f "$RESULT_FILE" ]; then
    log "   ⏭️  Vestník $VESTNIK_NUM/$VESTNIK_YEAR už spracovaný, skipping"
    exit 0
fi

# ── 3. Extrahuj document IDs z RSS ──
DOC_DIR="$HTML_DIR/${VESTNIK_NUM}_${VESTNIK_YEAR}"
mkdir -p "$DOC_DIR"

DOC_IDS=$(python3 -c "
import xml.etree.ElementTree as ET
root = ET.parse('$RSS_FILE').getroot()
for item in root.findall('.//item'):
    link = item.find('link').text or ''
    doc_id = link.split('/')[-1].split('?')[0]
    if doc_id:
        print(doc_id)
" 2>/dev/null)

DOC_COUNT=$(echo "$DOC_IDS" | wc -l | tr -d ' ')
log "[2/6] $DOC_COUNT dokumentov v RSS"

# ── 4. Stiahni HTML dokumenty (skip existujúce) ──
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
        # Skontroluj či HTML nie je prázdne
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
    # Rate limit: 200ms medzi requestmi
    sleep 0.2
done

log "   Stiahnutých: $DOWNLOADED nových, $SKIPPED existujúcich, $FAILED zlyhaných"

if [ "$FAILED" -gt 0 ] && [ "$DOWNLOADED" -eq 0 ] && [ "$SKIPPED" -eq 0 ]; then
    log_error "Všetky sťahovania zlyhali"
    notify_error "Sťahovanie zlyhalo" "Všetkých $DOC_COUNT dokumentov zlyhalo pre VVO $VESTNIK_NUM/$VESTNIK_YEAR"
    exit 1
fi

# ── 5. Spusti parser ──
log "[4/6] Spúšťam parser..."
PARSE_LOG="$LOGS_DIR/parse_${VESTNIK_NUM}_${VESTNIK_YEAR}.log"
python3 "$PARSER" "$RSS_FILE" 2>&1 | tee "$PARSE_LOG"

# Nájdi a presuň výsledky do archívu
PARSER_OUTPUT=$(find "$(dirname "$PARSER")" -maxdepth 3 -name "vestnik_*_parser_results.json" -newer "$RSS_FILE" 2>/dev/null | head -1)
if [ -n "$PARSER_OUTPUT" ] && [ -f "$PARSER_OUTPUT" ]; then
    cp "$PARSER_OUTPUT" "$RESULT_FILE"
    log "   Výsledky: $RESULT_FILE"
else
    log_error "Parser nevytvoril výstupný súbor"
    notify_error "Parser bez výstupu" "Parser pre VVO $VESTNIK_NUM/$VESTNIK_YEAR nevytvoril JSON ($DATE)"
    exit 1
fi

# ── 6. Validácia ──
log "[5/6] Validujem výsledky..."
VALIDATION_OK=true
if [ -f "$VALIDATOR" ]; then
    if python3 "$VALIDATOR" "$RESULT_FILE" 2>&1 | tee -a "$LOG_FILE"; then
        log "   ✅ Validácia OK"
    else
        log "   ⚠️  Validácia našla chyby (pokračujem)"
        VALIDATION_OK=false
    fi
else
    log "   ⚠️  Validátor nenájdený, preskakujem"
fi

# ── 7. Súhrn + notifikácia ──
log ""
log "[6/6] Hotovo!"
log "   Vestník: VVO $VESTNIK_NUM/$VESTNIK_YEAR"
log "   Dokumentov: $DOC_COUNT"
log "   HTML: $DOC_DIR/"
log "   Výsledky: $RESULT_FILE"
log "   RSS: $RSS_FILE"
log "   Dátum: $DATE $(date +%H:%M)"
log ""

# Notifikácia o úspechu
SUMMARY="VVO $VESTNIK_NUM/$VESTNIK_YEAR: $DOC_COUNT dok, $DOWNLOADED nových HTML"
if [ "$VALIDATION_OK" = true ]; then
    notify_success "Vestník spracovaný" "$SUMMARY"
else
    notify_error "Vestník s chybami" "$SUMMARY — validácia našla problémy, pozri $LOG_FILE"
fi

# Žiadny auto-cleanup — HTML archív sa uchováva natrvalo
