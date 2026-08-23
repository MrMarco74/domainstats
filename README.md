# DomainStats

![License](https://img.shields.io/badge/license-MIT-blue.svg) ![Language](https://img.shields.io/badge/language-Python-informational.svg) ![AI generated](https://img.shields.io/badge/AI-generated-8A2BE2.svg)

DomainStats is a self-hosted web server log analytics and domain-reputation
tool. It syncs Apache/Nginx access logs from a remote FTP/FTPS/SFTP server,
parses and aggregates them into a local SQLite database, and exposes a
FastAPI dashboard with traffic statistics, security auditing, file
integrity monitoring, and optional LLM-powered ("Deep Insights") analysis
via [Ollama](https://ollama.com).

It was originally built as one of the log/evidence sources feeding
[CASSANDRA](https://github.com/MrMarco74/cassandra), a threat-fusion and
prediction engine that correlates signals from multiple security tools, but
it works standalone as a general-purpose log analytics dashboard for any
domain(s) you host.

## Features

- **Log sync & parsing** — pulls Apache/Nginx (Combined Log Format) access
  logs over FTPS or SFTP and parses them into SQLite
- **Traffic dashboard** — per-domain stats, top paths, referrers,
  user-agent/OS/browser breakdown, GeoIP country data (via MaxMind
  GeoLite2)
- **Security audit** — highlights suspicious request patterns, scanner
  traffic, and long-term "low and slow" IP behavior
- **File integrity monitoring** — detects unexpected changes to files under
  your configured web roots
- **Deep Insights (optional)** — LLM-generated summaries of the above via
  a local/self-hosted Ollama endpoint

## ⚠️ Security warning — no built-in authentication

**DomainStats does not implement any authentication or authorization on
its own.** Every dashboard page and API route is open to anyone who can
reach the port it listens on. You are responsible for putting it behind
your own reverse proxy (nginx, Caddy, Traefik, ...) with TLS and
authentication (Basic Auth, OAuth2 proxy, mTLS, or restrict access to a
private/VPN network). **Do not expose it directly to the internet.**

## Prerequisites

- Linux (Ubuntu 22.04+ / Debian 11+ recommended), or Docker
- Python 3.10+ (bare-metal install) or Docker + Docker Compose
- Outbound FTP/FTPS/SFTP access to the server hosting your logs
- Optionally, a reachable Ollama instance for the LLM-powered insights

## Quick Start

```bash
git clone https://github.com/MrMarco74/domainstats.git
cd domainstats
sudo bash setup.sh   # interactive bare-metal installer
```

or with Docker Compose — see [docs/setup.md](docs/setup.md#quick-start-docker-compose)
for the full walkthrough, including which values in `docker-compose.yml`
and `Dockerfile` you need to adapt to your own environment.

For the complete setup guide — prerequisites, both install paths, the full
environment variable reference, and troubleshooting — see
**[docs/setup.md](docs/setup.md)**.

## License

MIT — see [LICENSE](LICENSE).
