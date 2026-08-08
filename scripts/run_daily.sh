#!/bin/bash
set -e
cd /opt/domainstats
source venv/bin/activate
export PYTHONPATH=/opt/domainstats

# Load env vars
set -a; [ -f .env ] && source .env; set +a

echo "[$(date)] Starting sync..."
python3 src/sync.py
echo "[$(date)] Parsing logs..."
python3 src/parser.py
echo "[$(date)] Aggregating..."
python3 src/aggregator.py
echo "[$(date)] Integrity scan..."
python3 -c "from src.integrity import IntegrityScanner; IntegrityScanner().scan_all()"
echo "[$(date)] Done."
