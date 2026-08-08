# DomainStats — Installation

The full installation and configuration guide has moved to
**[docs/setup.md](docs/setup.md)** — it covers prerequisites, the
bare-metal (`setup.sh`/systemd) and Docker Compose install paths, the
complete environment variable reference, and troubleshooting.

## ⚠️ Security warning

DomainStats has **no built-in authentication** on any route. Always deploy
it behind your own reverse proxy with authentication (TLS + Basic Auth,
OAuth2 proxy, mTLS, or a VPN-only network) — never expose it directly to
the internet. See [docs/setup.md](docs/setup.md) for details.

## Quick Start

```bash
git clone https://github.com/MrMarco74/domainstats.git /opt/domainstats
cd /opt/domainstats
sudo bash setup.sh
```

For everything else — configuration values, Docker Compose, environment
variables, systemd units, updating, and troubleshooting — see
[docs/setup.md](docs/setup.md).
