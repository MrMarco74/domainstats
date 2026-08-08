#!/bin/bash
# DomainStats Setup Script
# Interactive installer for Ubuntu/Debian servers
# Usage: sudo bash setup.sh

set -e

# ─── Colors ────────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'
BOLD='\033[1m'; NC='\033[0m'

print_header() { echo -e "\n${BOLD}${BLUE}=== $1 ===${NC}\n"; }
print_ok()     { echo -e "${GREEN}[OK]${NC} $1"; }
print_warn()   { echo -e "${YELLOW}[WARN]${NC} $1"; }
print_err()    { echo -e "${RED}[ERROR]${NC} $1"; }
ask()          { echo -e "${BOLD}$1${NC}"; }

# ─── Preflight ─────────────────────────────────────────────────────────────────
if [ "$EUID" -ne 0 ]; then
    print_err "Please run as root: sudo bash setup.sh"
    exit 1
fi

INSTALL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
echo -e "\n${BOLD}DomainStats Setup${NC}"
echo "Install directory: ${INSTALL_DIR}"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# ─── Configuration Dialog ──────────────────────────────────────────────────────
print_header "Configuration"

# API Port
ask "API port [default: 8000]:"
read -r INPUT_PORT
API_PORT="${INPUT_PORT:-8000}"

# FTP / FTPS Settings
ask "FTP host (e.g. ftp.example.com):"
read -r FTP_HOST
while [ -z "$FTP_HOST" ]; do
    print_err "FTP host is required."
    ask "FTP host:"
    read -r FTP_HOST
done

ask "FTP user:"
read -r FTP_USER
while [ -z "$FTP_USER" ]; do
    print_err "FTP user is required."
    ask "FTP user:"
    read -r FTP_USER
done

ask "FTP password:"
read -rs FTP_PASS
echo
while [ -z "$FTP_PASS" ]; do
    print_err "FTP password is required."
    ask "FTP password:"
    read -rs FTP_PASS
    echo
done

ask "Remote log path on FTP server [default: /public_html/logs]:"
read -r INPUT_LOG_PATH
REMOTE_LOG_PATH="${INPUT_LOG_PATH:-/public_html/logs}"

ask "Remote base path for web roots [default: /public_html]:"
read -r INPUT_BASE_PATH
REMOTE_BASE_PATH="${INPUT_BASE_PATH:-/public_html}"

# SSH Key for remote operations (optional)
ask "Path to SSH private key for remote access (leave empty to skip):"
read -r SSH_KEY_PATH

# LLM / Ollama (optional)
ask "Ollama/LLM URL for AI insights (leave empty to disable) [default: disabled]:"
read -r INPUT_LLM_URL
LLM_URL="${INPUT_LLM_URL:-}"

if [ -n "$LLM_URL" ]; then
    ask "LLM model name [default: deepseek-r1:14b]:"
    read -r INPUT_LLM_MODEL
    LLM_MODEL="${INPUT_LLM_MODEL:-deepseek-r1:14b}"
fi

# GeoIP database
ask "MaxMind GeoLite2 license key (for GeoIP lookups, leave empty to skip):"
read -r GEOIP_LICENSE_KEY

# Ignored IPs (e.g. your own IP to exclude from stats)
ask "Your IP address to exclude from stats (leave empty to skip):"
read -r OWN_IP

# Sync timer
ask "Daily sync time in HH:MM format [default: 02:00]:"
read -r INPUT_SYNC_TIME
SYNC_TIME="${INPUT_SYNC_TIME:-02:00}"
SYNC_HOUR="${SYNC_TIME%%:*}"
SYNC_MIN="${SYNC_TIME##*:}"

# ─── Summary ───────────────────────────────────────────────────────────────────
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo -e "${BOLD}Configuration Summary${NC}"
echo "  Install dir:      ${INSTALL_DIR}"
echo "  API port:         ${API_PORT}"
echo "  FTP host:         ${FTP_HOST}"
echo "  FTP user:         ${FTP_USER}"
echo "  Remote log path:  ${REMOTE_LOG_PATH}"
echo "  Remote base path: ${REMOTE_BASE_PATH}"
echo "  SSH key:          ${SSH_KEY_PATH:-none}"
echo "  LLM URL:          ${LLM_URL:-disabled}"
[ -n "$LLM_URL" ] && echo "  LLM model:        ${LLM_MODEL}"
echo "  GeoIP license:    ${GEOIP_LICENSE_KEY:+configured}${GEOIP_LICENSE_KEY:-skipped}"
echo "  Own IP filter:    ${OWN_IP:-none}"
echo "  Sync time:        ${SYNC_HOUR}:${SYNC_MIN}"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

ask "Proceed with installation? [y/N]:"
read -r CONFIRM
if [[ ! "$CONFIRM" =~ ^[Yy]$ ]]; then
    echo "Aborted."
    exit 0
fi

# ─── System Dependencies ───────────────────────────────────────────────────────
print_header "System Dependencies"
apt-get update -q
apt-get install -y python3 python3-pip python3-venv sqlite3 curl unzip
print_ok "System packages installed"

# ─── Dedicated User ────────────────────────────────────────────────────────────
print_header "System User"
if ! id "domainstats" &>/dev/null; then
    useradd -m -s /bin/bash domainstats
    print_ok "User 'domainstats' created"
else
    print_ok "User 'domainstats' already exists"
fi

# ─── Directory Structure ───────────────────────────────────────────────────────
print_header "Directories"
mkdir -p "${INSTALL_DIR}/data/raw"
mkdir -p "${INSTALL_DIR}/data/integrity_cache"
mkdir -p "${INSTALL_DIR}/scripts"
mkdir -p "${INSTALL_DIR}/src/web/lib"
chown -R domainstats:domainstats "${INSTALL_DIR}"
print_ok "Directories created"

# ─── SSH Key ───────────────────────────────────────────────────────────────────
if [ -n "$SSH_KEY_PATH" ] && [ -f "$SSH_KEY_PATH" ]; then
    print_header "SSH Key"
    mkdir -p /home/domainstats/.ssh
    cp "$SSH_KEY_PATH" /home/domainstats/.ssh/id_rsa
    chown domainstats:domainstats /home/domainstats/.ssh/id_rsa
    chmod 600 /home/domainstats/.ssh/id_rsa
    print_ok "SSH key installed to /home/domainstats/.ssh/id_rsa"
elif [ -f "${INSTALL_DIR}/hetzner_key" ]; then
    print_header "SSH Key"
    mkdir -p /home/domainstats/.ssh
    cp "${INSTALL_DIR}/hetzner_key" /home/domainstats/.ssh/id_rsa
    chown domainstats:domainstats /home/domainstats/.ssh/id_rsa
    chmod 600 /home/domainstats/.ssh/id_rsa
    print_ok "SSH key installed from bundled hetzner_key"
else
    print_warn "No SSH key provided — remote SSH operations will not be available"
fi

# ─── Python Virtual Environment ────────────────────────────────────────────────
print_header "Python Environment"
sudo -u domainstats python3 -m venv "${INSTALL_DIR}/venv"
sudo -u domainstats "${INSTALL_DIR}/venv/bin/pip" install --upgrade pip -q
sudo -u domainstats "${INSTALL_DIR}/venv/bin/pip" install -r "${INSTALL_DIR}/requirements.txt" -q
print_ok "Virtual environment ready"

# ─── Chart.js (offline asset) ──────────────────────────────────────────────────
print_header "Frontend Assets"
CHARTJS_PATH="${INSTALL_DIR}/src/web/lib/chart.js"
if [ ! -f "$CHARTJS_PATH" ]; then
    echo "Downloading Chart.js..."
    if curl -fsSL -o "$CHARTJS_PATH" "https://cdn.jsdelivr.net/npm/chart.js/dist/chart.umd.min.js"; then
        chown domainstats:domainstats "$CHARTJS_PATH"
        print_ok "Chart.js downloaded"
    else
        print_warn "Could not download Chart.js — will fall back to CDN"
    fi
else
    print_ok "Chart.js already present"
fi

# ─── GeoIP Database ────────────────────────────────────────────────────────────
if [ -n "$GEOIP_LICENSE_KEY" ]; then
    print_header "GeoIP Database"
    GEOIP_DIR="${INSTALL_DIR}/data/geoip"
    mkdir -p "$GEOIP_DIR"
    GEOIP_URL="https://download.maxmind.com/app/geoip_download?edition_id=GeoLite2-Country&license_key=${GEOIP_LICENSE_KEY}&suffix=tar.gz"
    GEOIP_TMP="/tmp/geoip.tar.gz"
    if curl -fsSL -o "$GEOIP_TMP" "$GEOIP_URL"; then
        tar -xzf "$GEOIP_TMP" -C "$GEOIP_DIR" --strip-components=1 --wildcards "*.mmdb"
        chown -R domainstats:domainstats "$GEOIP_DIR"
        print_ok "GeoLite2-Country database installed to ${GEOIP_DIR}"
        # Write path to env file (picked up by systemd)
        GEOIP_DB_PATH="${GEOIP_DIR}/GeoLite2-Country.mmdb"
    else
        print_warn "GeoIP download failed — check license key. Skipping."
    fi
    rm -f "$GEOIP_TMP"
fi

# ─── Environment File ──────────────────────────────────────────────────────────
print_header "Environment Configuration"
ENV_FILE="${INSTALL_DIR}/.env"
cat > "$ENV_FILE" <<EOF
# DomainStats Environment
# Generated by setup.sh — edit as needed

FTP_HOST=${FTP_HOST}
FTP_USER=${FTP_USER}
FTP_PASS=${FTP_PASS}
EOF

if [ -n "$LLM_URL" ]; then
    echo "LLM_URL=${LLM_URL}" >> "$ENV_FILE"
    echo "LLM_MODEL=${LLM_MODEL}" >> "$ENV_FILE"
fi

if [ -n "$GEOIP_DB_PATH" ]; then
    echo "GEOIP_DB=${GEOIP_DB_PATH}" >> "$ENV_FILE"
fi

chmod 600 "$ENV_FILE"
chown domainstats:domainstats "$ENV_FILE"
print_ok "Environment file written to ${ENV_FILE}"

# ─── Initial DB Bootstrap ──────────────────────────────────────────────────────
print_header "Database Initialization"
sudo -u domainstats bash -c "
    cd '${INSTALL_DIR}'
    source venv/bin/activate
    python3 -c \"
from src.db_utils import get_db
import sqlite3

conn = get_db()
cursor = conn.cursor()

# Write FTP settings
cursor.execute(\\\"INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)\\\", ('ftp_host', '${FTP_HOST}'))
cursor.execute(\\\"INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)\\\", ('ftp_user', '${FTP_USER}'))
cursor.execute(\\\"INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)\\\", ('ftp_pass', '${FTP_PASS}'))
cursor.execute(\\\"INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)\\\", ('remote_log_path', '${REMOTE_LOG_PATH}'))
cursor.execute(\\\"INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)\\\", ('base_path', '${REMOTE_BASE_PATH}'))
conn.commit()
conn.close()
print('DB settings written.')
\"
"
print_ok "Database initialized"

# Write own IP filter if provided
if [ -n "$OWN_IP" ]; then
    sudo -u domainstats bash -c "
        cd '${INSTALL_DIR}'
        source venv/bin/activate
        python3 -c \"
from src.db_utils import get_db
conn = get_db()
conn.execute(\\\"INSERT OR IGNORE INTO ignored_ips (ip) VALUES (?)\\\", ('${OWN_IP}',))
conn.commit()
conn.close()
print('Own IP added to ignore list.')
\"
    "
    print_ok "Own IP ${OWN_IP} added to ignore list"
fi

# ─── Daily Sync Script ─────────────────────────────────────────────────────────
print_header "Sync Script"
cat > "${INSTALL_DIR}/scripts/run_daily.sh" <<EOF
#!/bin/bash
set -e
cd ${INSTALL_DIR}
source venv/bin/activate

# Load env vars
set -a; [ -f .env ] && source .env; set +a

echo "[\$(date)] Starting sync..."
python3 src/sync.py
echo "[\$(date)] Parsing logs..."
python3 src/parser.py
echo "[\$(date)] Aggregating..."
python3 src/aggregator.py
echo "[\$(date)] Integrity scan..."
python3 -c "from src.integrity import IntegrityScanner; IntegrityScanner().scan_all()"
echo "[\$(date)] Done."
EOF
chmod +x "${INSTALL_DIR}/scripts/run_daily.sh"
chown domainstats:domainstats "${INSTALL_DIR}/scripts/run_daily.sh"
print_ok "Daily sync script written"

# ─── Systemd: API Service ──────────────────────────────────────────────────────
print_header "Systemd Services"

cat > /etc/systemd/system/domainstats-api.service <<EOF
[Unit]
Description=DomainStats FastAPI Dashboard
After=network.target

[Service]
User=domainstats
Group=domainstats
WorkingDirectory=${INSTALL_DIR}
EnvironmentFile=${INSTALL_DIR}/.env
ExecStart=${INSTALL_DIR}/venv/bin/uvicorn src.main:app --host 0.0.0.0 --port ${API_PORT}
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

# ─── Systemd: Sync Timer ───────────────────────────────────────────────────────
cat > /etc/systemd/system/domainstats-sync.service <<EOF
[Unit]
Description=DomainStats Daily Sync and Parse
After=network.target

[Service]
Type=oneshot
User=domainstats
Group=domainstats
WorkingDirectory=${INSTALL_DIR}
EnvironmentFile=${INSTALL_DIR}/.env
ExecStart=${INSTALL_DIR}/scripts/run_daily.sh
EOF

cat > /etc/systemd/system/domainstats-sync.timer <<EOF
[Unit]
Description=Run DomainStats Sync Daily at ${SYNC_HOUR}:${SYNC_MIN}

[Timer]
OnCalendar=*-*-* ${SYNC_HOUR}:${SYNC_MIN}:00
Persistent=true

[Install]
WantedBy=timers.target
EOF

systemctl daemon-reload
systemctl enable --now domainstats-api.service
systemctl enable --now domainstats-sync.timer
print_ok "Services enabled and started"

# ─── Final Status ──────────────────────────────────────────────────────────────
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo -e "${GREEN}${BOLD}Installation complete!${NC}"
echo ""
echo "  Dashboard:    http://$(hostname -I | awk '{print $1}'):${API_PORT}"
echo "  API service:  systemctl status domainstats-api"
echo "  Sync timer:   systemctl status domainstats-sync.timer"
echo "  Logs:         journalctl -u domainstats-api -f"
echo ""
echo -e "${YELLOW}Next steps:${NC}"
echo "  1. Run initial log import:"
echo "     sudo -u domainstats bash -c 'cd ${INSTALL_DIR} && source venv/bin/activate && python3 src/sync.py && python3 src/parser.py'"
echo "  2. Configure domain paths in the dashboard under 'Konfiguration'"
if [ -z "$GEOIP_LICENSE_KEY" ]; then
    echo "  3. For GeoIP lookups, add a MaxMind license key and re-run setup or"
    echo "     download GeoLite2-Country.mmdb manually to data/geoip/"
fi
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
