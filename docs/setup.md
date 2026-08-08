# DomainStats — Setup Guide

## Overview

DomainStats is a self-hosted web server log analytics dashboard, developed
alongside the [CASSANDRA](../README.md) threat-fusion project as one of its
evidence sources. It syncs Apache/Nginx access logs from a remote
FTP/FTPS/SFTP server, parses them into a local SQLite database, and exposes
a FastAPI dashboard with traffic stats, security auditing, file integrity
monitoring, and optional LLM-powered insights.

---

## ⚠️ Security warning — no built-in authentication

**DomainStats has no built-in authentication on any route.** Every API
endpoint and dashboard page is reachable by anyone who can connect to the
port it listens on. You **must** run it behind your own reverse proxy
(nginx, Caddy, Traefik, ...) that terminates TLS and enforces
authentication (e.g. Basic Auth, OAuth2 proxy, mTLS, or a VPN-only
network). **Never expose the app's port directly to the internet or an
untrusted LAN.**

---

## Prerequisites

| Requirement | Version |
|---|---|
| OS | Ubuntu 22.04+ / Debian 11+ (or any Linux with Docker) |
| Python | 3.10+ (only needed for the non-Docker install path) |
| Docker + Docker Compose | if using the container deployment |
| Disk | ~500 MB + log data |
| Network | Outbound FTP/FTPS/SFTP to your web host; optionally outbound access to an Ollama endpoint for LLM insights |

If installing directly on a host (non-Docker), run the setup script as
**root** — it creates a dedicated `domainstats` system user.

---

## Quick Start (bare-metal / systemd)

```bash
# 1. Clone the repository to the target server
git clone https://github.com/MrMarco74/domainstats.git /opt/domainstats
cd /opt/domainstats

# 2. Run the interactive setup
sudo bash setup.sh
```

The script will ask you for all required configuration values (FTP/SSH
credentials, GeoIP license key, sync schedule, etc.) and install everything
automatically — system packages, a dedicated `domainstats` user, a Python
venv, systemd units, and the initial `.env` file.

### What the Setup Script Does

| Step | Description |
|---|---|
| System packages | Installs `python3`, `python3-venv`, `sqlite3`, `curl`, `unzip` via apt |
| Dedicated user | Creates `domainstats` system user (no login shell, least privilege) |
| Directories | Creates `data/raw`, `data/integrity_cache`, `scripts/`, `src/web/lib/` |
| SSH key | Copies your SSH private key to `/home/domainstats/.ssh/id_rsa` (optional) |
| Python venv | Creates `venv/` and installs all `requirements.txt` dependencies |
| Chart.js | Downloads `chart.js` to `src/web/lib/chart.js` for offline use |
| GeoIP DB | Downloads MaxMind GeoLite2-Country database (requires free license key) |
| `.env` file | Writes FTP credentials and optional settings to `.env` (mode `600`) |
| Database | Initializes SQLite schema and writes FTP/path settings |
| IP filter | Adds your own IP address to the ignore list (optional) |
| Sync script | Writes `scripts/run_daily.sh` (sync → parse → aggregate → integrity scan) |
| Systemd | Installs and enables `domainstats-api.service` and `domainstats-sync.timer` |

---

## Quick Start (Docker Compose)

```bash
git clone https://github.com/MrMarco74/domainstats.git
cd domainstats
```

1. Copy/create an `.env` file with the environment variables from the table
   below (at minimum `FTP_HOST`/`FTP_USER`/`FTP_PASS` or the `SFTP_*`
   equivalents).
2. Edit `docker-compose.yml` — every path, hostname, and IP in there
   (bind mounts, the `.env` file location, the port binding, the SSH-key
   secret path) is an **example** and must be adapted to your own
   environment. In particular, adjust the `ports:` binding so the
   container's port is reachable only by your reverse proxy, never
   directly by the internet or LAN (see the security warning above).
3. The `pylibs` dependency is currently installed at Docker build time via
   an SSH-key BuildKit secret (see comments in `Dockerfile` and
   `docker-compose.yml`). Point the secret at your own SSH private key, or
   adapt the git remote if you're pulling `pylibs` from a different host.
   `pylibs` is now public on GitHub, so this build step could eventually be
   simplified to a plain `pip install` — that change is tracked separately
   and not yet done.
4. Build and start:

   ```bash
   docker compose build --secret id=ssh_key,src=/path/to/your/ssh/private_key
   docker compose up -d
   ```

---

## Environment Variables

All variables are optional unless noted; sensible generic defaults are
shown, but you should override credentials and hostnames for your own
environment. FTP/SSH credentials can also be set later via the
**Konfiguration** tab in the dashboard (stored in the SQLite `settings`
table), which take precedence over these env vars once set.

| Variable | Used by | Default | Description |
|---|---|---|---|
| `FTP_HOST` | `src/ftp_utils.py` | `ftp.example.com` | FTP/FTPS server hostname for log sync |
| `FTP_USER` | `src/ftp_utils.py` | `changeme` | FTP username |
| `FTP_PASS` | `src/ftp_utils.py` | *(empty)* | FTP password |
| `SFTP_HOST` | `src/ssh_utils.py` | `ftp.example.com` | SFTP server hostname (alternative to FTP) |
| `SFTP_USER` | `src/ssh_utils.py` | `changeme` | SFTP username |
| `SFTP_PASS` | `src/ssh_utils.py` | *(empty)* | SFTP password (if not using a key) |
| `SFTP_KEY_PATH` | `src/ssh_utils.py` | `/home/domainstats/.ssh/id_rsa` | Path to SSH private key for SFTP auth |
| `SFTP_KEY_PASS` | `src/ssh_utils.py` | *(unset)* | Passphrase for the SSH private key |
| `SFTP_REMOTE_DIR` | `src/sync.py` | `/www_logs` | Remote directory on the SFTP server containing logs |
| `LLM_URL` | `src/llm_utils.py` | `http://localhost:11434/api/generate` | Ollama API endpoint for Deep Insights (legacy `/api/generate` suffix accepted and normalized) |
| `LLM_MODEL` | `src/llm_utils.py` | `deepseek-r1:14b` | Default Ollama model name |
| `LLM_TIMEOUT_SECONDS` | `src/llm_utils.py` | `300` | Timeout (seconds) for LLM requests |
| `LLM_CACHE_TTL_HOURS` | `src/llm_utils.py` | `24` | How long LLM analysis results are cached |
| `LLMPROXY_TOKEN` | `src/llm_utils.py` | *(empty)* | Optional bearer token for the Ollama/LLM proxy |
| `INTERNAL_DOMAIN_SUFFIX` | `src/parser.py` | *(empty — nothing excluded)* | Domain suffix (e.g. `.internal.example.com`) whose traffic is excluded entirely from parsing/statistics — useful to hide internal admin/tool vhosts from reports |
| `YADS_REPO_PATH` | `src/release_tracker.py` | *(empty — feature disabled)* | Optional path to a local checkout of the sibling `yads` repo, used only by the release-tracker helper script to derive a changelog from its git log; leave unset unless you use that integration |

The Ollama/LLM base URL and token can also be changed live from the
dashboard's **Konfiguration** tab without restarting the service (stored in
the settings table, takes precedence over `LLM_URL`/`LLMPROXY_TOKEN`).

### GeoIP (optional)

Geo-location of visitors requires a MaxMind GeoLite2-Country database.

1. Register for free at [maxmind.com](https://www.maxmind.com/en/geolite2/signup)
2. Generate a license key in your account
3. Paste it into the setup dialog (bare-metal install) or download the
   `.mmdb` manually and place it at `data/geoip/GeoLite2-Country.mmdb`
   (Docker install)

Without GeoIP the geo-stats tab will show no country data but everything
else works.

---

## File Layout After Installation

```
domainstats/
├── .env                        # Credentials (mode 600) — never commit this
├── data/
│   ├── logs.db                 # SQLite database
│   ├── raw/                    # Downloaded raw log files
│   ├── geoip/                  # GeoLite2-Country.mmdb (if configured)
│   └── integrity_cache/        # Cached file snapshots for integrity monitoring
├── scripts/
│   └── run_daily.sh            # Daily sync + parse + aggregate + scan
├── src/
│   ├── main.py                 # FastAPI application
│   ├── sync.py                 # FTP/SFTP log downloader
│   ├── parser.py                # Log parser → SQLite
│   ├── aggregator.py           # Daily stats aggregation
│   ├── integrity.py            # File integrity scanner
│   ├── ftp_utils.py            # FTP/FTPS client
│   ├── ssh_utils.py            # SFTP client
│   ├── db_utils.py             # SQLite schema + connection
│   ├── llm_utils.py            # LLM integration
│   ├── monitor.py              # Uptime monitoring loop
│   └── web/                    # Frontend (HTML/CSS/JS)
└── venv/                       # Python virtual environment (bare-metal install only)
```

---

## Systemd Services (bare-metal install)

Two units are installed:

### `domainstats-api.service`

Runs the FastAPI dashboard continuously.

```bash
systemctl status domainstats-api
systemctl restart domainstats-api
journalctl -u domainstats-api -f
```

### `domainstats-sync.timer` + `domainstats-sync.service`

Runs the daily sync at the configured time.

```bash
systemctl status domainstats-sync.timer
systemctl list-timers domainstats-sync.timer

# Run sync manually right now:
systemctl start domainstats-sync.service
journalctl -u domainstats-sync -f
```

---

## Initial Data Import

After setup the database is empty. Run the first sync manually:

```bash
sudo -u domainstats bash -c '
  cd /opt/domainstats
  source venv/bin/activate
  python3 src/sync.py        # downloads logs from FTP/SFTP
  python3 src/parser.py      # parses into SQLite
  python3 src/aggregator.py  # builds daily_stats table
'
```

Depending on log volume this can take several minutes.

---

## Post-Install Configuration

Open the dashboard (behind your reverse proxy — see the security warning
above) and navigate to **Konfiguration**:

1. **Domain paths** — map each domain to its web root on the FTP/SFTP server
   (used by the Validator and Integrity Scanner)
2. **FTP/SFTP settings** — can be changed without re-running setup
3. **Ollama/LLM settings** — base URL, token, model — for Deep Insights
4. **Ignored IPs** — add/remove IP addresses from statistics

---

## Updating

```bash
cd /opt/domainstats
git pull
sudo chown -R domainstats:domainstats .   # bare-metal install only
sudo systemctl restart domainstats-api    # bare-metal install only
# or, for Docker:
docker compose build && docker compose up -d
```

No database migrations are needed — the schema is auto-created/updated by
`db_utils.py` on startup.

---

## Uninstall (bare-metal)

```bash
sudo systemctl disable --now domainstats-api.service domainstats-sync.timer
sudo rm /etc/systemd/system/domainstats-api.service
sudo rm /etc/systemd/system/domainstats-sync.service
sudo rm /etc/systemd/system/domainstats-sync.timer
sudo systemctl daemon-reload
sudo userdel -r domainstats
sudo rm -rf /opt/domainstats
```

---

## Troubleshooting

| Symptom | Check |
|---|---|
| Dashboard not reachable | `systemctl status domainstats-api` (or `docker compose ps`) — port conflict? firewall? reverse proxy misconfigured? |
| Dashboard reachable but unauthenticated from outside | Your reverse proxy is not enforcing auth, or the app's port is bound to a public/LAN interface — see the security warning above |
| FTP sync fails with auth error | Verify `FTP_HOST`/`FTP_USER`/`FTP_PASS` (or `SFTP_*`) in `.env` or dashboard settings; FTPS (port 21 + TLS) must be supported by your host |
| SFTP sync fails with auth error | Check `SFTP_KEY_PATH` exists and is readable by the `domainstats` user/container; check `SFTP_KEY_PASS` if the key is passphrase-protected |
| GeoIP shows no countries | `data/geoip/GeoLite2-Country.mmdb` missing — re-run setup or download manually |
| LLM insights timeout | Ollama not running, wrong `LLM_URL`, or model not pulled — `ollama pull deepseek-r1:14b` (or your configured model) |
| Parser finds no files | Check the remote log path in dashboard settings — must point to a directory containing log files |
| Integrity scan fails | Domain paths not configured in the Konfiguration tab |
