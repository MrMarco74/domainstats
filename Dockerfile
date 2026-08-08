# syntax=docker/dockerfile:1
#
# domainstats — Docker Image
#
# Code (src/) wird per Bind-Mount eingehängt, nicht ins Image gebaut.
# Das erlaubt Updates ohne Rebuild: git pull + docker compose restart.
#
# pylibs was previously a private GitLab repo — installed at build time via an
# SSH-Key BuildKit secret (adapt the host below to your own git remote):
#   docker compose build --secret id=ssh_key,src=/path/to/your/ssh/private_key
#
# NOTE: pylibs is now public on GitHub, so this SSH-secret build step could
# eventually be replaced with a plain `pip install` of the public package.
# That dependency-URL change is tracked separately — not done here, this
# Dockerfile still assumes an SSH-based git install of pylibs.
FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
        git \
        openssh-client \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# SSH known_hosts vorab eintragen damit git kein interactive prompt braucht
# Adapt this hostname to your own git server (e.g. gitlab.example.com or github.com).
RUN mkdir -p /root/.ssh && chmod 700 /root/.ssh && \
    ssh-keyscan git.example.com >> /root/.ssh/known_hosts 2>/dev/null

COPY requirements.txt .

# SSH-Key als BuildKit-Secret einbinden — wird nicht in Image-Layern gespeichert
RUN --mount=type=secret,id=ssh_key,target=/root/.ssh/id_ed25519,mode=0600 \
    GIT_SSH_COMMAND="ssh -i /root/.ssh/id_ed25519 -o StrictHostKeyChecking=no" \
    pip install --no-cache-dir -r requirements.txt

ENV PYTHONPATH=/app

CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000"]
