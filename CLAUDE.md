# CLAUDE.md

Guidance for Claude Code (and other coding agents) working in this repo.

## What this is

DomainStats is a self-hosted web server log analytics and domain-reputation
dashboard (FTP/FTPS/SFTP log sync → SQLite → FastAPI dashboard, with
optional LLM-powered insights via Ollama). See `README.md` and
`docs/setup.md` for the full picture.

## Deployment

This repo ships a `Dockerfile` and `docker-compose.yml` for a container
deployment, and `setup.sh` for a bare-metal/systemd install — see
`docs/setup.md` for both paths. Deployment automation (CI/CD, Ansible
roles, etc.) is intentionally not part of this repo; wire it up however
fits your own infrastructure. Never edit code directly on a production
host — always deploy from a git checkout so changes stay tracked.

**No built-in authentication:** this app has no auth on any route. Never
deploy it with its port exposed directly to the internet or an untrusted
network — always put it behind a reverse proxy that enforces
authentication. See the security warning in `README.md` / `docs/setup.md`.
