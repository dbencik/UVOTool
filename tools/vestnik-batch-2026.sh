#!/bin/bash
# Batch download a parsovanie všetkých vestníkov z roku 2026
# Spúšťa sa na pozadí: nohup bash tools/vestnik-batch-2026.sh &

set -e
cd "$(dirname "$0")/.."

LOG="data/logs/batch_2026.log"
mkdir -p data/logs

echo "$(date): Batch 2026 started" >> "$LOG"

# Už stiahnuté vestníky (preskočiť)
DONE=(188 189 190 191)

for NUM in $(seq 1 191); do
    # Preskočiť už stiahnuté
    SKIP=0
    for D in "${DONE[@]}"; do
        if [ "$NUM" -eq "$D" ]; then SKIP=1; break; fi
    done
    if [ "$SKIP" -eq 1 ]; then
        echo "$(date): Skipping $NUM/2026 (already done)" >> "$LOG"
        continue
    fi

    OUTFILE="data/results/vestnik_${NUM}_2026_parser_results.json"
    if [ -f "$OUTFILE" ]; then
        echo "$(date): Skipping $NUM/2026 (output exists)" >> "$LOG"
        continue
    fi

    echo "$(date): Processing $NUM/2026..." >> "$LOG"

    # Retry up to 3 times on failure
    SUCCESS=0
    for ATTEMPT in 1 2 3; do
        if python3 tools/vestnik-parser.py "$NUM/2026" >> "$LOG" 2>&1; then
            SUCCESS=1
            echo "$(date): Done $NUM/2026 (attempt $ATTEMPT)" >> "$LOG"
            break
        else
            echo "$(date): Failed $NUM/2026 attempt $ATTEMPT, waiting 30s..." >> "$LOG"
            sleep 30
        fi
    done

    if [ "$SUCCESS" -eq 0 ]; then
        echo "$(date): FAILED $NUM/2026 after 3 attempts" >> "$LOG"
    fi

    # Pauza medzi vestníkmi aby sme nezaťažovali UVO server
    sleep 5
done

# Súhrn
TOTAL=$(ls data/results/vestnik_*_2026_parser_results.json 2>/dev/null | wc -l)
echo "$(date): Batch 2026 DONE — $TOTAL vestníkov spracovaných" >> "$LOG"
