#!/bin/bash
# ═══════════════════════════════════════════════════════════════
# Deploy vestník nástrojov na VPS
#
# Použitie:
#   bash tools/deploy-vps.sh                    # deploy s kľúčom
#   bash tools/deploy-vps.sh user@147.93.121.59 # vlastný host
# ═══════════════════════════════════════════════════════════════

set -euo pipefail

VPS="${1:-root@147.93.121.59}"
REMOTE_DIR="/opt/vestnik"

echo "═══════════════════════════════════════════════════"
echo "Deploy Vestník na $VPS"
echo "═══════════════════════════════════════════════════"

# 1. Kopíruj súbory
echo "[1/4] Kopírujem súbory..."
scp tools/vestnik-parser.py \
    tools/vestnik-validator.py \
    tools/vestnik-cron.sh \
    "$VPS:$REMOTE_DIR/"

# 2. Nastav permissions
echo "[2/4] Nastavujem permissions..."
ssh "$VPS" "chmod +x $REMOTE_DIR/vestnik-cron.sh && mkdir -p $REMOTE_DIR/{archive,html,logs}"

# 3. Vytvor .env ak neexistuje
echo "[3/4] Kontrolujem .env..."
ssh "$VPS" "[ -f $REMOTE_DIR/.env ] || echo 'NTFY_TOPIC=vestnik-uvo' > $REMOTE_DIR/.env"

# 4. Nastav crontab
echo "[4/4] Nastavujem crontab..."
ssh "$VPS" "
    # Pridaj cron ak ešte nie je
    if ! crontab -l 2>/dev/null | grep -q vestnik-cron; then
        (crontab -l 2>/dev/null; echo '0 23 * * 1-5 $REMOTE_DIR/vestnik-cron.sh >> $REMOTE_DIR/logs/cron.log 2>&1') | crontab -
        echo '   ✅ Cron pridaný: Po-Pi 23:00'
    else
        echo '   ⏭️  Cron už existuje'
    fi
    crontab -l | grep vestnik
"

echo ""
echo "═══════════════════════════════════════════════════"
echo "✅ Deploy hotový!"
echo ""
echo "Overenie:"
echo "  ssh $VPS '$REMOTE_DIR/vestnik-cron.sh'   # manuálny test run"
echo "  ssh $VPS 'crontab -l | grep vestnik'      # overenie cronu"
echo ""
echo "Monitoring:"
echo "  Nainštaluj ntfy app a prihlás sa na topic: vestnik-uvo"
echo "  https://ntfy.sh/vestnik-uvo"
echo "═══════════════════════════════════════════════════"
