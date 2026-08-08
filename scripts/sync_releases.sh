#!/bin/bash
# Refreshes data/releases.json from the local yads git checkout and pushes it to worker.
#
# release_tracker.py needs a local clone of the yads repo (git log of releases/version.json
# is the source of truth for release history) - worker has no access to that repo, so this
# has to run here, not as part of run_daily.sh on the server.
set -e

TARGET="root@worker"
INSTALL_DIR="/opt/domainstats"

cd "$(dirname "${BASH_SOURCE[0]}")/.."
source venv/bin/activate

echo "[$(date)] Refreshing releases.json from yads repo..."
python3 src/release_tracker.py

echo "[$(date)] Syncing releases.json to $TARGET..."
rsync -avz data/releases.json "$TARGET:$INSTALL_DIR/data/releases.json"
ssh "$TARGET" "chown domainstats:domainstats $INSTALL_DIR/data/releases.json"

echo "[$(date)] Done."
