#!/bin/bash

# DomainStats Installer
# Target: Ubuntu/Debian

set -e

echo "--- DomainStats Installer ---"

# 1. System Dependencies
echo "Installing system dependencies..."
sudo apt-get update
sudo apt-get install -y python3 python3-pip python3-venv lftp sqlite3 curl

# 2. Create Dedicated User (Least Privilege)
if ! id "domainstats" &>/dev/null; then
    echo "Creating dedicated user 'domainstats'..."
    sudo useradd -m -s /bin/bash domainstats
fi

# 3. Setup Directories and Permissions
echo "Setting up directories and permissions..."
sudo mkdir -p data/raw src/web/lib
sudo chown -R domainstats:domainstats .

# 4. SSH Key Setup (Fail2Ban Protection)
echo "Setting up SSH key for domainstats..."
sudo -u domainstats mkdir -p /home/domainstats/.ssh
if [ -f "hetzner_key" ]; then
    sudo cp hetzner_key /home/domainstats/.ssh/id_rsa
    sudo chown domainstats:domainstats /home/domainstats/.ssh/id_rsa
    sudo chmod 600 /home/domainstats/.ssh/id_rsa
    echo "SSH key installed."
else
    echo "WARNING: hetzner_key not found in current directory. Please provide it manually."
fi

# 5. Python Virtual Environment
echo "Setting up virtual environment..."
sudo -u domainstats python3 -m venv venv
sudo -u domainstats ./venv/bin/pip install -r requirements.txt

# 6. Local Assets (Offline mode)
echo "Downloading local assets..."
if [ ! -f src/web/lib/chart.js ]; then
    sudo -u domainstats curl -o src/web/lib/chart.js https://cdn.jsdelivr.net/npm/chart.js
fi

# 7. Systemd Service (FastAPI)
echo "Setting up systemd service..."
cat <<EOF | sudo tee /etc/systemd/system/domainstats-api.service
[Unit]
Description=DomainStats FastAPI API
After=network.target

[Service]
User=domainstats
Group=domainstats
WorkingDirectory=$(pwd)
ExecStart=$(pwd)/venv/bin/uvicorn src.main:app --host 0.0.0.0 --port 8000
Restart=always

[Install]
WantedBy=multi-user.target
EOF

# 8. Systemd Timer (Daily Sync & Parse)
echo "Setting up daily sync timer (02:00)..."

# Script to run both sync and parse
cat <<EOF | sudo -u domainstats tee scripts/run_daily.sh
#!/bin/bash
set -e
cd $(pwd)
source venv/bin/activate
export SFTP_KEY_PATH=/home/domainstats/.ssh/id_rsa
echo "Starting sync..."
python3 src/sync.py
echo "Starting parse..."
python3 src/parser.py
echo "Starting aggregation and cleanup..."
python3 src/aggregator.py
echo "Starting integrity scan..."
python3 -c "from src.integrity import IntegrityScanner; IntegrityScanner().scan_all()"
echo "Done."
EOF
sudo chmod +x scripts/run_daily.sh

cat <<EOF | sudo tee /etc/systemd/system/domainstats-sync.service
[Unit]
Description=DomainStats Daily Sync and Parse
After=network.target

[Service]
Type=oneshot
User=domainstats
Group=domainstats
WorkingDirectory=$(pwd)
ExecStart=$(pwd)/scripts/run_daily.sh
EOF

cat <<EOF | sudo tee /etc/systemd/system/domainstats-sync.timer
[Unit]
Description=Run DomainStats Sync Daily at 02:00

[Timer]
OnCalendar=*-*-* 02:00:00
Persistent=true

[Install]
WantedBy=timers.target
EOF

# 7. Enable and Start
echo "Reloading systemd daemons..."
sudo systemctl daemon-reload
echo "Enabling and restarting API service..."
sudo systemctl enable domainstats-api.service
sudo systemctl restart domainstats-api.service
echo "Enabling and restarting sync timer..."
sudo systemctl enable domainstats-sync.timer
sudo systemctl restart domainstats-sync.timer

echo "--- Installation Complete ---"
echo "API & Dashboard: http://localhost:8000"
echo "Daily Sync: 02:00 Uhr"
echo "Initial Load: Führe 'python3 src/parser.py' manuell aus, um bestehende Logs zu importieren."
