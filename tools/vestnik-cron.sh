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
#   5. Uloží výsledky do archívu
#
# Inštalácia na VPS:
#   scp tools/vestnik-cron.sh tools/vestnik-parser.py user@147.93.121.59:/opt/vestnik/
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
RSS_URL="https://www.uvo.gov.sk/vestnik-a-registre/vestnik/rss"

# Vytvor adresáre ak neexistujú
mkdir -p "$ARCHIVE_DIR" "$HTML_DIR" "$LOGS_DIR"

DATE=$(date +%Y-%m-%d)
echo ""
echo "═══════════════════════════════════════════════════"
echo "Vestník Cron — $DATE $(date +%H:%M)"
echo "═══════════════════════════════════════════════════"

# ── 1. Stiahni RSS a zisti číslo vestníka ──
RSS_FILE="$ARCHIVE_DIR/rss_${DATE}.xml"
echo "[1/5] Sťahujem RSS..."
curl -s -o "$RSS_FILE" "$RSS_URL"

if [ ! -s "$RSS_FILE" ]; then
    echo "❌ RSS prázdne alebo nedostupné"
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
    echo "❌ Nepodarilo sa zistiť číslo vestníka z RSS"
    exit 1
fi

echo "   Vestník: VVO $VESTNIK_NUM/$VESTNIK_YEAR"

# ── 2. Skontroluj či už nie je spracovaný ──
RESULT_FILE="$ARCHIVE_DIR/vestnik_${VESTNIK_NUM}_${VESTNIK_YEAR}.json"
if [ -f "$RESULT_FILE" ]; then
    echo "   ⏭️  Vestník $VESTNIK_NUM/$VESTNIK_YEAR už spracovaný, skipping"
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
echo "[2/5] $DOC_COUNT dokumentov v RSS"

# ── 4. Stiahni HTML dokumenty (skip existujúce) ──
echo "[3/5] Sťahujem HTML dokumenty..."
DOWNLOADED=0
SKIPPED=0

for DOC_ID in $DOC_IDS; do
    HTML_FILE="$DOC_DIR/${DOC_ID}.html"
    if [ -f "$HTML_FILE" ]; then
        SKIPPED=$((SKIPPED + 1))
        continue
    fi
    URL="https://www.uvo.gov.sk/vestnik-a-registre/vestnik/oznamenie/detail/${DOC_ID}"
    if curl -s -o "$HTML_FILE" --max-time 15 "$URL"; then
        DOWNLOADED=$((DOWNLOADED + 1))
    else
        echo "   ⚠️  Zlyhalo: $DOC_ID"
    fi
    # Rate limit: 200ms medzi requestmi
    sleep 0.2
done

echo "   Stiahnutých: $DOWNLOADED nových, $SKIPPED existujúcich"

# ── 5. Spusti parser ──
echo "[4/5] Spúšťam parser..."
python3 "$PARSER" "$RSS_FILE" 2>&1 | tee "$LOGS_DIR/parse_${VESTNIK_NUM}_${VESTNIK_YEAR}.log"

# Presun výsledky do archívu
PARSER_OUTPUT=$(find "$BASE_DIR" -maxdepth 1 -name "vestnik_*_parser_results.json" 2>/dev/null | head -1)
if [ -n "$PARSER_OUTPUT" ] && [ -f "$PARSER_OUTPUT" ]; then
    mv "$PARSER_OUTPUT" "$RESULT_FILE"
fi

# ── 6. Súhrn ──
echo ""
echo "[5/5] Hotovo!"
echo "   Vestník: VVO $VESTNIK_NUM/$VESTNIK_YEAR"
echo "   Dokumentov: $DOC_COUNT"
echo "   HTML: $DOC_DIR/"
echo "   Výsledky: $RESULT_FILE"
echo "   RSS: $RSS_FILE"
echo "   Dátum: $DATE $(date +%H:%M)"
echo ""

# Žiadny auto-cleanup — HTML archív sa uchováva natrvalo
