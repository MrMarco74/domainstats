#!/bin/bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HOOKS_DIR="$REPO_ROOT/.git/hooks"

if [ ! -d "$HOOKS_DIR" ]; then
    echo "Fehler: .git/hooks Verzeichnis nicht gefunden." >&2
    exit 1
fi

cat << 'HOOK_EOF' > "$HOOKS_DIR/pre-commit"
#!/bin/bash
# Auto-installiert von scripts/install-git-hooks.sh
# Blockt Commits mit Private Keys, bekannten Secret-Dateinamen oder
# offensichtlichen Credential-Zuweisungen in den gestagten Änderungen.
set -euo pipefail

STAGED="$(git diff --cached --name-only --diff-filter=ACM)"
[ -z "$STAGED" ] && exit 0

FORBIDDEN_NAMES='(^|/)(hetzner_key|id_rsa|id_ed25519(_.*)?|.*\.pem|.*\.db|.*\.db-journal|\.env)$'
if echo "$STAGED" | grep -qE "$FORBIDDEN_NAMES"; then
    echo "pre-commit: gesperrte Datei(en) im Commit gefunden:" >&2
    echo "$STAGED" | grep -E "$FORBIDDEN_NAMES" >&2
    echo "Falls das wirklich gewollt ist: git commit --no-verify" >&2
    exit 1
fi

SECRET_PATTERN='BEGIN (RSA |OPENSSH |PGP )?PRIVATE KEY|ftp_pass["'"'"']?\s*[:=]\s*["'"'"'][^"'"'"']+|AKIA[0-9A-Z]{16}|ghp_[0-9A-Za-z]{36}|sk-[0-9A-Za-z]{20,}'
if git diff --cached -U0 | grep -qE "$SECRET_PATTERN"; then
    echo "pre-commit: möglicher Secret-Wert im Diff gefunden:" >&2
    git diff --cached -U0 | grep -E "$SECRET_PATTERN" >&2
    echo "Falls das ein False Positive ist: git commit --no-verify" >&2
    exit 1
fi

exit 0
HOOK_EOF

chmod +x "$HOOKS_DIR/pre-commit"
echo "Pre-commit Secret-Scan-Hook installiert."

cat << 'HOOK_EOF' > "$HOOKS_DIR/pre-push"
#!/bin/bash
# Auto-installiert von scripts/install-git-hooks.sh
REPO_ROOT="$(git rev-parse --show-toplevel)"
nohup "$REPO_ROOT/scripts/mirror_to_github.sh" >> "$REPO_ROOT/.git/mirror.log" 2>&1 &
disown
exit 0
HOOK_EOF

chmod +x "$HOOKS_DIR/pre-push"
echo "Pre-push GitHub-Mirror-Hook installiert."
