# Ollama Config & Settings Sub-Tabs Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an Ollama configuration section (Base-URL, API token, model picker fed by the real `/api/tags` listing) to domainstats' settings page, and split that page into sub-tabs so it stops being one long scroll.

**Architecture:** Reuse the existing generic `settings` key/value table for the three new keys (`ollama_url`, `ollama_token`, `ollama_model`) — no schema change. Use `pylibs.ollama` (sibling project, added as a git dependency) for all Ollama HTTP calls, both in a new `/stats/ollama/models` endpoint and inside the existing `analyze_with_llm()`. On the frontend, re-nest the existing config-tab markup into 5 sub-tab panels driven by a new, generic `initSubTabs()` helper (kept separate from the existing top-level tab click handler, since both share the same `data-tab`-less button markup problem if merged).

**Tech Stack:** FastAPI + sqlite3 (backend, `src/main.py`, `src/llm_utils.py`), vanilla JS + HTML/CSS, no build step (`src/web/app.js`, `src/web/index.html`, `src/web/style.css`), `pylibs[ollama]` (git dependency from `git@gitlab.internal.familie-frischkorn.de:apps/pylibs.git`).

## Global Constraints

- No new database tables/columns — everything goes into the existing `settings` key/value table (`src/db_utils.py:35`).
- `ollama_token` must never be echoed back by `GET /stats/settings` handling on the frontend, and must not be overwritten server-side when the save payload sends an empty string (same rule as existing `ftp_pass`/`key_pass`).
- Do not touch `deploy.sh` or `install.sh`. Deployment for this feature happens via labcontrol's `domainstats-api` Ansible playbook (`update` task), not covered by this plan.
- No test framework exists in this repo — verification steps use `curl`/`sqlite3`/manual browser checks instead of `pytest`.
- Follow existing code style: German UI copy/labels, existing CSS custom properties (`--accent-color`, `--glass-bg`, `--glass-border`, `--text-secondary`), existing `alert(...)`-based save confirmations.

---

## Task 1: Add pylibs[ollama] dependency and verify it imports

**Files:**
- Modify: `requirements.txt`

**Interfaces:**
- Produces: `pylibs.ollama.OllamaClient`, `pylibs.ollama.OllamaEndpoints`, `pylibs.ollama.models.list_models`, `pylibs.ollama.models.ModelInfo` — all consumed by Tasks 2 and 3.

- [ ] **Step 1: Add the dependency line**

Append to `requirements.txt`:

```
pylibs[ollama] @ git+ssh://git@gitlab.internal.familie-frischkorn.de/apps/pylibs.git
```

- [ ] **Step 2: Install into the local venv**

Run: `venv/bin/pip install -r requirements.txt`
Expected: `pylibs` installs successfully (clones over SSH — requires the same SSH key access already used for this machine's other internal GitLab clones).

- [ ] **Step 3: Verify the import surface**

Run:
```bash
venv/bin/python -c "from pylibs.ollama import OllamaClient, OllamaEndpoints; from pylibs.ollama.models import list_models, ModelInfo; print('ok')"
```
Expected: prints `ok` with no `ImportError`/`ModuleNotFoundError`.

- [ ] **Step 4: Commit**

```bash
git add requirements.txt
git commit -m "Add pylibs[ollama] dependency for Ollama client/model-listing"
```

---

## Task 2: Backend — `/stats/ollama/models` endpoint

**Files:**
- Modify: `src/main.py` (add endpoint near the other `/stats/settings`-adjacent routes, i.e. right after `update_settings` at `src/main.py:372`)

**Interfaces:**
- Consumes: `pylibs.ollama.OllamaClient`, `OllamaEndpoints`, `pylibs.ollama.models.list_models` (Task 1). `get_db()` from `src.db_utils` (already imported in `main.py`).
- Produces: `POST /stats/ollama/models` with JSON body `{"base_url": <str|omit>, "token": <str|omit>}` → `200 {"models": ["name1", "name2", ...]}` (alphabetically sorted) or `400`/`502 {"detail": "<message>"}`. Consumed by Task 6 (frontend "Modelle laden" button).

**Security note (added after automated security review of an earlier GET-based draft of this endpoint):** the endpoint is `POST` with the URL/token in a JSON body, not a `GET` query string, so a bearer token never lands in server access logs or browser history. It also validates the resolved hostname before contacting it — resolved addresses in the loopback range (`127.0.0.0/8`, `::1`) or the link-local/cloud-metadata range (`169.254.0.0/16`, which includes `169.254.169.254`) are rejected. Ordinary private ranges (`10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`) are deliberately **not** blocked — this tool's actual Ollama proxy (`llmproxy.internal.familie-frischkorn.de`) is itself an internal/private-range host, so a standard SSRF private-range blocklist would break the feature's real use case. This endpoint remains reachable by anyone who can reach domainstats at all (the whole app has no auth on any route), so this is a narrowing of blast radius, not a full SSRF fix.

- [ ] **Step 1: Add the imports**

At the top of `src/main.py`, alongside the existing `from src.llm_utils import ...` (line 16), add:

```python
import ipaddress
import socket
from urllib.parse import urlparse

from pylibs.ollama import OllamaClient, OllamaEndpoints
from pylibs.ollama.models import list_models
```

- [ ] **Step 2: Add a hostname-validation helper and the endpoint**

Insert directly after the `update_settings` function (after line 372, before the `/stats/my_ip` route):

```python
def _reject_unsafe_ollama_host(base_url: str):
    """Raise ValueError if base_url resolves to loopback or link-local/metadata addresses.

    Ordinary private ranges (10/8, 172.16/12, 192.168/16) are intentionally allowed —
    this tool's real Ollama proxy is itself an internal host.
    """
    hostname = urlparse(base_url).hostname
    if not hostname:
        raise ValueError("Ungültige URL")

    try:
        infos = socket.getaddrinfo(hostname, None)
    except socket.gaierror as e:
        raise ValueError(f"Hostname nicht auflösbar: {hostname} ({e})")

    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_loopback or ip.is_link_local:
            raise ValueError(f"Host '{hostname}' löst zu einer nicht erlaubten Adresse auf ({ip}).")


@app.post("/stats/ollama/models")
async def get_ollama_models(data: Dict):
    base_url = data.get('base_url')
    token = data.get('token')

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT key, value FROM settings WHERE key IN ('ollama_url', 'ollama_token')")
    saved = {row['key']: row['value'] for row in cursor.fetchall()}
    conn.close()

    resolved_url = base_url or saved.get('ollama_url')
    resolved_token = token or saved.get('ollama_token')

    if not resolved_url:
        raise HTTPException(status_code=400, detail="Keine Ollama Base-URL angegeben oder gespeichert.")

    try:
        _reject_unsafe_ollama_host(resolved_url)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    try:
        session = requests.Session()
        session.max_redirects = 0  # a redirect could point at a loopback/link-local address the check above already rejected
        client = OllamaClient(OllamaEndpoints(base_url=resolved_url), token=resolved_token, timeout=10.0, session=session)
        models = list_models(client)
        names = sorted(m.name for m in models)
        return {"models": names}
    except Exception:
        raise HTTPException(status_code=502, detail="Ollama nicht erreichbar oder Anfrage fehlgeschlagen.")
```

**Follow-up hardening (round 2, after task review):** the first hardened version validated the hostname once via `_reject_unsafe_ollama_host` but let `OllamaClient`'s underlying `requests.Session` follow HTTP redirects with its default settings — a malicious `base_url` could pass the check (resolves to a public address) and then redirect the actual request to `http://127.0.0.1/...` or the metadata address, bypassing the guard entirely. Passing a `requests.Session` with `max_redirects = 0` into `OllamaClient`'s existing `session` constructor parameter closes that path without modifying pylibs. `OllamaClient.__init__` already accepts `session: requests.Session | None = None` (see `pylibs/src/pylibs/ollama/client.py`), so this uses its existing extension point rather than bypassing it.

`requests` must be imported in `src/main.py` for this (it already is — `import requests` is not currently present at the top of `main.py`; add it alongside the other new imports in Step 1 if it's missing).

**Known residual risk, accepted as out of scope:** this only closes the redirect-based bypass. A DNS-rebinding attack (a hostname that resolves to a public IP at validation time but to a private/loopback IP moments later when `requests` performs its own independent DNS lookup) is not closed — fully closing it would require pinning the connection to the already-resolved IP (e.g. a custom `HTTPAdapter`/transport), which is a materially bigger change for a single-admin, already-unauthenticated internal tool. Not required for this plan.

**Follow-up hardening (round 3, found during Task 3's review):** `ollama_url`/`ollama_token` are read from the same attacker-writable `settings` table by BOTH this endpoint and `analyze_with_llm` (Task 3). Task 3's review correctly pointed out that a guard defined only in `main.py` doesn't protect `analyze_with_llm` in `llm_utils.py`, reopening the same SSRF class there. Fix: move `_reject_unsafe_ollama_host` (rename to the public `reject_unsafe_ollama_host`, no behavior change) into `src/llm_utils.py` — it already owns the `ipaddress`/`socket`/`urlparse` imports need, and `main.py` already imports from `llm_utils`, so this avoids a circular import. `main.py`'s endpoint then does `from src.llm_utils import reject_unsafe_ollama_host` instead of defining its own copy, and calls it exactly as before. `analyze_with_llm` calls the same function on `settings['url']` before constructing its `OllamaClient`, and also passes a `requests.Session()` with `max_redirects = 0` (same reasoning as the endpoint) — if the guard raises `ValueError`, `analyze_with_llm` catches it and returns the existing `"Fehler: ..."`-style string (its established error contract), not an HTTP exception (it isn't a route handler).

`Optional`, `Dict`, and `HTTPException` are already imported/used in `main.py` (used by other routes in this file, e.g. `update_settings(data: Dict)`) — no additional import needed for those. Note the exception handler intentionally returns a generic message instead of `str(e)` — the original draft leaked exception text (which can include connection internals) into an HTTP response; this was also flagged by the automated security review.

- [ ] **Step 3: Verify with curl against a bad URL (error path)**

Run:
```bash
venv/bin/uvicorn src.main:app --port 8001 &
sleep 2
curl -s -X POST "http://127.0.0.1:8001/stats/ollama/models" -H "Content-Type: application/json" -d '{"base_url": "http://127.0.0.1:1"}'
kill %1
```
Expected: HTTP 400 JSON body with a `detail` message about a non-allowed loopback address (`127.0.0.1` resolves to itself and is rejected by `_reject_unsafe_ollama_host` before any network call is attempted).

- [ ] **Step 4: Verify with curl against the real proxy (happy path)**

Run:
```bash
venv/bin/uvicorn src.main:app --port 8001 &
sleep 2
curl -s -X POST "http://127.0.0.1:8001/stats/ollama/models" -H "Content-Type: application/json" -d '{"base_url": "https://llmproxy.internal.familie-frischkorn.de:11435"}'
kill %1
```
Expected: HTTP 200 with `{"models": [...]}` containing real model names (e.g. `deepseek-r1:14b`). This host resolves to a private (non-loopback, non-link-local) address, so it passes `_reject_unsafe_ollama_host` and reaches the proxy. If the token is required for this proxy in the current environment, add `, "token": "<token>"` to the JSON body and re-run.

- [ ] **Step 5: Commit**

```bash
git add src/main.py
git commit -m "Add /stats/ollama/models endpoint using pylibs.ollama"
```

---

## Task 3: Backend — settings skip-empty rule + migrate `analyze_with_llm` to `OllamaClient`

**Files:**
- Modify: `src/main.py:360` (the `update_settings` skip-if-empty tuple)
- Modify: `src/llm_utils.py`

**Interfaces:**
- Consumes: `OllamaClient` (Task 1), `get_db()` (`src/db_utils.py`, already imported in `llm_utils.py`).
- Produces: `analyze_with_llm(prompt, model=None)` — same public signature and return-string contract as before (cached string, or `"Fehler: ..."`/`"Fehler in analyze_with_llm: ..."` strings on failure). Consumed unchanged by `src/main.py:541,579,623` (`analyze_with_llm(prompt)` calls — no changes needed there since `model` now defaults to `None` and is resolved internally).

- [ ] **Step 1: Extend the skip-if-empty rule**

In `src/main.py`, change line 360 from:

```python
            if key in ('ftp_pass', 'key_pass') and not value:
```

to:

```python
            if key in ('ftp_pass', 'key_pass', 'ollama_token') and not value:
```

- [ ] **Step 2: Add a settings reader and OllamaClient factory to `llm_utils.py`**

In `src/llm_utils.py`, add after the existing module-level constants (after line 13, before `def analyze_with_llm`):

```python
from pylibs.ollama import OllamaClient, OllamaEndpoints


def _get_ollama_settings():
    """Read ollama_url/ollama_token/ollama_model from the settings table, falling back to env vars."""
    conn = get_db()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT key, value FROM settings WHERE key IN ('ollama_url', 'ollama_token', 'ollama_model')"
        )
        saved = {row['key']: row['value'] for row in cursor.fetchall()}
    finally:
        conn.close()

    return {
        'url': saved.get('ollama_url') or LLM_URL,
        'token': saved.get('ollama_token') or LLMPROXY_TOKEN,
        'model': saved.get('ollama_model') or DEFAULT_MODEL,
    }
```

Note: `LLM_URL` here is the proxy's `generate` base — the existing constant is `"https://llmproxy.internal.familie-frischkorn.de:11435/api/generate"` (a full path, not a base URL). `OllamaClient`/`OllamaEndpoints` expect a base URL without `/api/generate`. Handle this in Step 3 by stripping the path.

- [ ] **Step 3: Rewrite `analyze_with_llm` to use `OllamaClient`**

Replace the whole body of `analyze_with_llm` in `src/llm_utils.py` (lines 15–76) with:

```python
def analyze_with_llm(prompt, model=None):
    settings = _get_ollama_settings()
    resolved_model = model or settings['model']
    base_url = settings['url']
    if base_url.endswith('/api/generate'):
        base_url = base_url[: -len('/api/generate')]

    # Build a stable cache key from model + prompt content
    prompt_hash = hashlib.sha256(f"{resolved_model}:{prompt}".encode()).hexdigest()

    conn = None
    try:
        conn = get_db()
        cursor = conn.cursor()

        # Check cache
        cursor.execute(
            "SELECT response, analyzed_at FROM llm_cache WHERE prompt_hash = ?",
            (prompt_hash,)
        )
        row = cursor.fetchone()
        if row:
            try:
                analyzed_at = datetime.fromisoformat(row['analyzed_at'])
                if datetime.utcnow() - analyzed_at < timedelta(hours=LLM_CACHE_TTL):
                    date_str = analyzed_at.strftime("%d.%m.%Y %H:%M")
                    return f"*(Gecachte Analyse vom {date_str})*\n\n{row['response']}"
            except (ValueError, TypeError):
                pass  # invalid cache entry — fall through to fresh LLM call

        # No valid cache — call LLM
        try:
            client = OllamaClient(
                OllamaEndpoints(base_url=base_url),
                token=settings['token'] or None,
                timeout=LLM_TIMEOUT,
            )
            result = client.generate(resolved_model, prompt)
            if not result:
                return "Fehler: Keine Antwort vom Modell."
        except requests.exceptions.Timeout:
            return f"Fehler: LLM-Anfrage timed out nach {LLM_TIMEOUT}s"
        except Exception as e:
            return f"Fehler bei LLM-Anfrage: {str(e)}"

        # Store result in cache
        try:
            cursor.execute(
                "INSERT OR REPLACE INTO llm_cache (prompt_hash, response, analyzed_at) VALUES (?, ?, ?)",
                (prompt_hash, result, datetime.utcnow().isoformat())
            )
            conn.commit()
        except Exception:
            pass  # cache write failure is non-fatal

        return result

    except Exception as e:
        return f"Fehler in analyze_with_llm: {str(e)}"
    finally:
        if conn:
            conn.close()
```

This keeps the exact same cache table, TTL, and error-string contract as before — only the actual LLM call goes through `OllamaClient` instead of a raw `requests.post`.

- [ ] **Step 4: Verify with a direct call**

Run:
```bash
venv/bin/python -c "
from src.llm_utils import analyze_with_llm
print(analyze_with_llm('Sag nur das Wort Test.'))
"
```
Expected: prints a real model response (not a `Fehler: ...` string), assuming the default `LLM_URL` env var / proxy is reachable from this machine. If it's not reachable from the dev machine, expected output is a `Fehler bei LLM-Anfrage: ...` string (not a Python traceback) — confirming the error path still degrades gracefully.

- [ ] **Step 5: Commit**

```bash
git add src/main.py src/llm_utils.py
git commit -m "Migrate analyze_with_llm to pylibs OllamaClient, read config from settings table"
```

---

## Task 4: CSS for sub-tabs

**Files:**
- Modify: `src/web/style.css`

**Interfaces:**
- Produces: `.subtab-btn`, `.subtab-content` classes consumed by Tasks 5 and 6.

- [ ] **Step 1: Add sub-tab styles**

In `src/web/style.css`, right after the existing `.tab-content.active` rule (after line 137), add:

```css
.subtabs {
    border-bottom: 1px solid var(--glass-border);
    margin-bottom: 1.5rem;
    display: flex;
    gap: 0.5rem;
}

.subtab-btn {
    background: transparent;
    border: none;
    color: var(--text-secondary);
    padding: 0.6rem 1.1rem;
    cursor: pointer;
    font-weight: 600;
    font-size: 0.9rem;
    transition: all 0.3s ease;
    border-bottom: 2px solid transparent;
}

.subtab-btn:hover {
    color: var(--text-primary);
}

.subtab-btn.active {
    color: var(--accent-color);
    border-bottom: 2px solid var(--accent-color);
}

.subtab-content {
    display: none;
}

.subtab-content.active {
    display: block;
}
```

- [ ] **Step 2: Verify no existing selector collides**

Run: `grep -n "subtab" src/web/style.css`
Expected: exactly the 6 new rules just added, nothing pre-existing.

- [ ] **Step 3: Commit**

```bash
git add src/web/style.css
git commit -m "Add sub-tab CSS classes for config page"
```

---

## Task 5: Restructure `#config-tab` markup into 5 sub-tab panels (incl. new KI panel)

**Files:**
- Modify: `src/web/index.html:272-414` (the entire `#config-tab` block)

**Interfaces:**
- Produces: DOM elements `#config-subtabs` (nav), `#verbindung-subtab`, `#pfade-subtab`, `#ipfilter-subtab`, `#ki-subtab`, `#wartung-subtab` (panels), plus new KI-panel inputs `#ollama-url`, `#ollama-token`, `#ollama-model`, button `#ollama-load-models-btn`, result span `#ollama-models-result`, form `#ollama-settings-form`. Consumed by Task 6.

- [ ] **Step 1: Replace the `#config-tab` block**

Replace the full block from `<div id="config-tab" class="tab-content">` (line 272) through its matching closing `</div>` (line 414) with:

```html
        <div id="config-tab" class="tab-content">
            <nav class="subtabs" id="config-subtabs">
                <button class="subtab-btn active" data-subtab="verbindung">Verbindung</button>
                <button class="subtab-btn" data-subtab="pfade">Pfade</button>
                <button class="subtab-btn" data-subtab="ipfilter">IP-Filter</button>
                <button class="subtab-btn" data-subtab="ki">KI</button>
                <button class="subtab-btn" data-subtab="wartung">Wartung</button>
            </nav>

            <div id="verbindung-subtab" class="subtab-content active">
                <div class="stat-card" style="margin-bottom: 2rem; display: flex; justify-content: space-between; align-items: center;">
                    <div>
                        <h2 style="margin-bottom: 0.5rem;">Domain-Konfiguration</h2>
                        <p class="stat-label">Lege hier die Web-Root-Pfade für deine Domains fest, damit der Validator korrekt prüfen kann.</p>
                    </div>
                    <button id="suggest-all-btn" class="stat-card" style="background: var(--accent-color); color: var(--bg-color); font-weight: 700; cursor: pointer; padding: 0.75rem 1.5rem; border: none;">Pre-fill All (SSH Magic)</button>
                </div>

                <div class="stat-card" style="margin-bottom: 2rem; border: 1px solid var(--accent-color);">
                    <form id="connection-settings-form" onsubmit="event.preventDefault(); saveConnectionSettings();">
                        <h3 style="margin-bottom: 1rem;">Verbindungs-Typ</h3>
                        <div style="display: flex; gap: 2rem; margin-bottom: 1.5rem;">
                            <label style="display: flex; align-items: center; gap: 0.5rem; cursor: pointer;">
                                <input type="radio" name="conn_type" value="ssh" checked onchange="toggleConnFields()"> SSH / SFTP
                            </label>
                            <label style="display: flex; align-items: center; gap: 0.5rem; cursor: pointer;">
                                <input type="radio" name="conn_type" value="ftp" onchange="toggleConnFields()"> FTP / FTPS
                            </label>
                        </div>

                        <div id="ssh-fields">
                            <h3 style="margin-bottom: 0.5rem;">SSH Key Passphrase</h3>
                            <p class="stat-label" style="margin-bottom: 1rem;">Falls dein SSH-Key passwortgeschützt ist, trage es hier ein.</p>
                            <div style="display: flex; gap: 1rem; align-items: center; margin-bottom: 1.5rem;">
                                <input type="text" name="username" value="securie" style="display:none;" autocomplete="username">
                                <input type="password" id="global-key-pass" class="config-input" placeholder="Passphrase leer lassen" style="max-width: 400px;" autocomplete="current-password">
                            </div>
                        </div>

                        <div id="ftp-fields" style="display: none; border-left: 3px solid var(--accent-color); padding-left: 1rem; margin-bottom: 1.5rem;">
                            <h3 style="margin-bottom: 0.5rem;">FTP Zugangsdaten</h3>
                            <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 1rem; margin-bottom: 1rem;">
                                <div>
                                    <label class="stat-label">Host</label>
                                    <input type="text" id="ftp-host" class="config-input" placeholder="yads-security.com">
                                </div>
                                <div>
                                    <label class="stat-label">User</label>
                                    <input type="text" id="ftp-user" class="config-input" placeholder="ftpuser">
                                </div>
                            </div>
                            <div>
                                <label class="stat-label">Passwort</label>
                                <input type="password" id="ftp-pass" class="config-input" placeholder="Passwort">
                            </div>
                        </div>

                        <div style="display: flex; gap: 1rem; align-items: center; margin-bottom: 2rem;">
                            <button type="submit" class="save-btn">Verbindung Speichern</button>
                            <button type="button" id="test-conn-btn" class="ignore-btn" style="border-color: var(--accent-color); color: var(--accent-color);">Verbindung Testen</button>
                            <span id="test-conn-result" style="font-size: 0.9rem;"></span>
                        </div>
                    </form>
                </div>
            </div>

            <div id="pfade-subtab" class="subtab-content">
                <div class="stat-card" style="margin-bottom: 2rem; border: 1px solid var(--accent-color);">
                    <form id="path-settings-form" onsubmit="event.preventDefault(); savePathSettings();">
                        <h3 style="margin-bottom: 0.5rem;">Pfad-Konfiguration</h3>
                        <p class="stat-label" style="margin-bottom: 1rem;">Standard-Basispfade für die Validator-Suche.</p>
                        <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 1rem; margin-bottom: 1.5rem;">
                            <div>
                                <label class="stat-label">Web-Root Basispfad (für Browser)</label>
                                <div style="display: flex; gap: 0.5rem;">
                                    <input type="text" id="global-base-path" class="config-input" value="/public_html">
                                    <button type="button" class="ignore-btn" onclick="openFileBrowser(null, 'global-base-path')" title="Browse Remote Server">📂</button>
                                </div>
                            </div>
                            <div>
                                <label class="stat-label">Remote Log Pfad (Hoster Logs)</label>
                                <div style="display: flex; gap: 0.5rem;">
                                    <input type="text" id="remote-log-path" class="config-input" value="/public_html/logs">
                                    <button type="button" class="ignore-btn" onclick="openFileBrowser(null, 'remote-log-path')" title="Browse Remote Server">📂</button>
                                </div>
                            </div>
                        </div>

                        <button type="submit" class="save-btn">Pfade Speichern</button>
                    </form>
                </div>
            </div>

            <div id="ipfilter-subtab" class="subtab-content">
                <div class="stat-card" style="margin-bottom: 2rem; border: 1px solid var(--accent-color);">
                    <div id="ip-filter-section">
                        <h3 style="margin-bottom: 0.5rem;">IP-Filter</h3>
                        <p class="stat-label" style="margin-bottom: 1rem;">Schließe deine eigenen IP-Adressen von der Statistik aus (IPv4 & IPv6).</p>

                        <div style="display: flex; gap: 1rem; align-items: center; margin-bottom: 1.5rem; background: var(--glass-bg); padding: 1rem; border-radius: 8px; border: 1px solid var(--glass-border);">
                            <div>
                                <span class="stat-label">Deine erkannte IP:</span>
                                <span id="current-ip" style="font-weight: bold; color: var(--accent-color); margin-left: 0.5rem;">Lade...</span>
                            </div>
                            <button type="button" onclick="ignoreCurrentIP()" class="save-btn" style="padding: 0.5rem 1rem; font-size: 0.9rem;">Diese IP ignorieren</button>
                        </div>

                        <div style="display: flex; gap: 1rem; align-items: center; margin-bottom: 1.5rem;">
                            <input type="text" id="manual-ip-input" class="config-input" placeholder="IP manuell eingeben (IPv4/IPv6)" style="max-width: 300px;">
                            <button type="button" onclick="addManualIP()" class="save-btn" style="padding: 0.5rem 1rem; font-size: 0.9rem;">Hinzufügen</button>
                        </div>

                        <div id="ignored-ips-list" style="display: flex; flex-wrap: wrap; gap: 0.5rem; margin-bottom: 1rem;">
                            <!-- IPs will be added here -->
                        </div>

                        <div style="display: flex; gap: 1rem; align-items: center; margin-top: 1.5rem; padding-top: 1.5rem; border-top: 1px solid var(--glass-border);">
                            <div>
                                <h3 style="margin-bottom: 0.25rem; font-size: 1rem;">Statistiken bereinigen</h3>
                                <p class="stat-label">Berechnet die historischen Charts (daily_stats) neu, um die Filter auf vergangene Tage anzuwenden.</p>
                            </div>
                            <button type="button" onclick="refreshDailyStats()" class="ignore-btn" id="refresh-stats-btn" style="border-color: #34d399; color: #34d399; margin-left: auto;">Charts jetzt bereinigen</button>
                        </div>
                    </div>
                </div>
            </div>

            <div id="ki-subtab" class="subtab-content">
                <div class="stat-card" style="margin-bottom: 2rem; border: 1px solid var(--accent-color);">
                    <form id="ollama-settings-form" onsubmit="event.preventDefault(); saveOllamaSettings();">
                        <h3 style="margin-bottom: 0.5rem;">Ollama-Konfiguration</h3>
                        <p class="stat-label" style="margin-bottom: 1rem;">Verbindung zum Ollama-Host/-Proxy für die Deep-Insights-Analysen.</p>
                        <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 1rem; margin-bottom: 1rem;">
                            <div>
                                <label class="stat-label">Base-URL</label>
                                <input type="text" id="ollama-url" class="config-input" placeholder="https://llmproxy.internal.familie-frischkorn.de:11435">
                            </div>
                            <div>
                                <label class="stat-label">API-Token</label>
                                <input type="password" id="ollama-token" class="config-input" placeholder="Token leer lassen">
                            </div>
                        </div>

                        <div style="display: flex; gap: 1rem; align-items: center; margin-bottom: 1rem;">
                            <button type="button" id="ollama-load-models-btn" class="ignore-btn" style="border-color: var(--accent-color); color: var(--accent-color);">Modelle laden</button>
                            <span id="ollama-models-result" style="font-size: 0.9rem;"></span>
                        </div>

                        <div style="margin-bottom: 1.5rem; max-width: 400px;">
                            <label class="stat-label">Modell</label>
                            <select id="ollama-model" class="config-input">
                                <option value="">— Modelle laden, dann auswählen —</option>
                            </select>
                        </div>

                        <button type="submit" class="save-btn">Ollama-Einstellungen Speichern</button>
                    </form>
                </div>
            </div>

            <div id="wartung-subtab" class="subtab-content">
                <div class="chart-container wide">
                    <div class="table-container">
                        <table id="config-table">
                            <thead>
                                <tr>
                                    <th>Domain</th>
                                    <th>Aktueller Pfad auf dem Server</th>
                                    <th>Aktion</th>
                                </tr>
                            </thead>
                            <tbody>
                                <!-- Wird dynamisch befüllt -->
                            </tbody>
                        </table>
                    </div>
                </div>

                <div class="card" style="margin-top: 2rem; border: 1px solid #f87171;">
                    <h3 style="color: #f87171; margin-bottom: 1rem;">Speicherplatz & Cache</h3>
                    <div style="display: flex; justify-content: space-between; align-items: center;">
                        <div>
                            <p class="stat-label" id="disk-usage-text">Lade Speicherplatz-Info...</p>
                            <div style="width: 200px; height: 8px; background: rgba(255,255,255,0.1); border-radius: 4px; margin-top: 0.5rem;">
                                <div id="disk-usage-bar" style="width: 0%; height: 100%; background: var(--accent-color); border-radius: 4px;"></div>
                            </div>
                        </div>
                        <button onclick="clearOfflineCache()" class="ignore-btn" style="border-color: #f87171; color: #f87171;">Offline Cache leeren</button>
                    </div>
                </div>
            </div>
        </div>
```

Note: the original block had the domain-config header/pre-fill button, the connection form, the path form, the IP-filter section, the domain-path table, and the disk/cache card as flat siblings of `#config-tab`. This step nests each one into its matching sub-tab `<div>` with no attribute/id/onclick changes — every existing `getElementById`/`querySelector` call in `app.js` keeps working unmodified, except the brand-new `#ollama-*` elements added for the KI panel.

- [ ] **Step 2: Sanity-check the HTML is well-formed**

Run: `venv/bin/python -c "from html.parser import HTMLParser; HTMLParser().feed(open('src/web/index.html').read()); print('parsed ok')"`
Expected: `parsed ok` (this only checks the parser doesn't choke, not full validation — follow with the browser check in Task 6 Step 6 for real verification).

- [ ] **Step 3: Commit**

```bash
git add src/web/index.html
git commit -m "Restructure config-tab into sub-tab panels, add KI panel markup"
```

---

## Task 6: JS — sub-tab switching, Ollama settings load/save, model loading

**Files:**
- Modify: `src/web/app.js`

**Interfaces:**
- Consumes: `#config-subtabs`, `#*-subtab` elements, `#ollama-url`, `#ollama-token`, `#ollama-model`, `#ollama-load-models-btn`, `#ollama-models-result`, `#ollama-settings-form` (Task 5). `GET/POST /stats/settings` (existing), `POST /stats/ollama/models` (Task 2).
- Produces: `initSubTabs(navSelector, contentSelector)`, `loadOllamaModels()`, `saveOllamaSettings()` — global functions, same pattern as existing `saveConnectionSettings`/`savePathSettings`.

- [ ] **Step 1: Add `initSubTabs` and call it from `initDashboard`**

In `src/web/app.js`, add this function right before `async function initDashboard()` (before line 1316):

```javascript
function initSubTabs(navSelector, contentSelectorPrefix) {
    document.querySelectorAll(`${navSelector} .subtab-btn`).forEach(btn => {
        btn.addEventListener('click', () => {
            document.querySelectorAll(`${navSelector} .subtab-btn`).forEach(b => b.classList.remove('active'));
            document.querySelectorAll(`.subtab-content`).forEach(c => c.classList.remove('active'));
            btn.classList.add('active');
            const subtabId = `${btn.getAttribute('data-subtab')}${contentSelectorPrefix}`;
            document.getElementById(subtabId).classList.add('active');
        });
    });
}
```

Then, inside `initDashboard()`, right after the existing `.tab-btn` click-handler wiring block (after line 1326, before `const domains = await fetchData('domains');`), add:

```javascript
        initSubTabs('#config-subtabs', '-subtab');
```

- [ ] **Step 2: Add `loadOllamaModels`**

Add after `testConnection()` (after line 1212, before `window.saveConfig = saveConfig;` on line 1214):

```javascript
async function loadOllamaModels() {
    const resultEl = document.getElementById('ollama-models-result');
    const select = document.getElementById('ollama-model');
    const url = document.getElementById('ollama-url').value;
    const token = document.getElementById('ollama-token').value;

    resultEl.textContent = "⏳ Lade Modelle...";
    resultEl.style.color = "var(--text-secondary)";

    try {
        const body = {};
        if (url) body.base_url = url;
        if (token) body.token = token;

        const response = await fetch('/stats/ollama/models', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body)
        });
        const data = await response.json();

        if (!response.ok) {
            resultEl.textContent = "❌ " + (data.detail || 'Unbekannter Fehler');
            resultEl.style.color = "#fb7185";
            return;
        }

        const previousValue = select.value;
        select.innerHTML = '';
        data.models.forEach(name => {
            const option = document.createElement('option');
            option.value = name;
            option.textContent = name;
            select.appendChild(option);
        });
        if (data.models.includes(previousValue)) {
            select.value = previousValue;
        }

        resultEl.textContent = `✅ ${data.models.length} Modelle geladen`;
        resultEl.style.color = "#34d399";
    } catch (e) {
        resultEl.textContent = "❌ Request failed: " + e.message;
        resultEl.style.color = "#fb7185";
    }
}
```

- [ ] **Step 3: Add `saveOllamaSettings`**

Add directly after `loadOllamaModels()`:

```javascript
async function saveOllamaSettings() {
    const settings = {
        ollama_url: document.getElementById('ollama-url').value,
        ollama_token: document.getElementById('ollama-token').value,
        ollama_model: document.getElementById('ollama-model').value
    };

    try {
        const response = await fetch('/stats/settings', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(settings)
        });
        if (response.ok) {
            alert("Ollama-Einstellungen gespeichert!");
            if (settings.ollama_token) document.getElementById('ollama-token').value = '';
            await updateConfigTable(); // Refresh placeholders + selected model
        }
    } catch (e) {
        console.error("Failed to save Ollama settings:", e);
    }
}

window.loadOllamaModels = loadOllamaModels;
window.saveOllamaSettings = saveOllamaSettings;
```

- [ ] **Step 4: Wire the "Modelle laden" button and seed saved values in `updateConfigTable`**

In `initDashboard()`, find the existing line (currently around 1396):
```javascript
        document.getElementById('test-conn-btn').addEventListener('click', testConnection);
```
Add directly after it:
```javascript
        document.getElementById('ollama-load-models-btn').addEventListener('click', loadOllamaModels);
```

In `updateConfigTable()`, right after the existing `if (settings.ftp_pass) { ... }` block (after line 1100, before `const tbody = document.querySelector('#config-table tbody');`), add:

```javascript
    if (settings.ollama_url) document.getElementById('ollama-url').value = settings.ollama_url;
    if (settings.ollama_token) {
        document.getElementById('ollama-token').placeholder = "******** (Gespeichert)";
    }
    if (settings.ollama_model) {
        const select = document.getElementById('ollama-model');
        if (![...select.options].some(o => o.value === settings.ollama_model)) {
            const option = document.createElement('option');
            option.value = settings.ollama_model;
            option.textContent = settings.ollama_model;
            select.appendChild(option);
        }
        select.value = settings.ollama_model;
    }
```

This seeds the saved model into the `<select>` even before "Modelle laden" is clicked, so the picker reflects the saved choice on page load (matching how `ftp_host`/`ftp_user` are seeded above it).

- [ ] **Step 5: Static syntax check**

Run: `node --check src/web/app.js` (or `venv/bin/python -c "import subprocess"` isn't relevant — use `node` if available, otherwise open the file in a browser console via Step 6 and check for parse errors)
Expected: no output (syntax OK). If `node` isn't installed on this machine, skip straight to Step 6 — a JS syntax error will show up immediately as a browser console error and no tabs will respond to clicks.

- [ ] **Step 6: Manual browser verification**

Run: `venv/bin/uvicorn src.main:app --port 8001 --reload` and open `http://127.0.0.1:8001/` in a browser.
Expected, checked by hand:
- "Konfiguration" tab shows 5 sub-tab buttons (Verbindung/Pfade/IP-Filter/KI/Wartung); clicking each switches the visible panel and only one panel is visible at a time.
- Verbindung/Pfade/IP-Filter panels show the exact same fields/behavior as before (save buttons still work, "Verbindung Testen" still works, IP list still populates).
- KI panel: enter a Base-URL (e.g. `https://llmproxy.internal.familie-frischkorn.de:11435`), click "Modelle laden" → dropdown populates with real model names, result text turns green. Select a model, click "Ollama-Einstellungen Speichern" → alert fires, token field clears (if one was typed).
- Reload the page → KI panel's Base-URL and selected model are pre-filled from the saved settings; token field shows the "gespeichert" placeholder instead of the raw value.
- "Deep Insights" tab → clicking "Security Audit"/"Traffic Analyse"/"Longterm Audit" still returns an analysis (or a graceful `Fehler: ...` message if the configured host is unreachable from this machine), confirming `analyze_with_llm` still works end-to-end through the new `OllamaClient` path.

- [ ] **Step 7: Commit**

```bash
git add src/web/app.js
git commit -m "Wire sub-tab switching and Ollama settings/model-loading UI"
```

---

## Task 7: Final full-page regression pass

**Files:** none (verification only)

**Interfaces:** none

- [ ] **Step 1: Full tab sweep**

With the dev server still running (`venv/bin/uvicorn src.main:app --port 8001 --reload`), click through every top-level tab (Übersicht, Fehler-Analyse, Security Audit, Validator, Integrität, Deep Insights, Monitoring, YADS Releases, Docker Registry, Konfiguration) and confirm none of them throw console errors (open browser dev tools console, watch while clicking).
Expected: no red console errors on any tab, including the ones untouched by this plan (regression check that the sub-tab JS/CSS additions didn't leak into unrelated tabs).

- [ ] **Step 2: Settings persistence spot-check via sqlite3**

Run: `sqlite3 data/logs.db "SELECT key, value FROM settings WHERE key LIKE 'ollama_%';"`
Expected: rows for `ollama_url` and `ollama_model` with the values saved during Task 6 Step 6's manual test; `ollama_token` present only if a token was actually typed and saved (and never displayed back in plaintext by the UI).

- [ ] **Step 3: Stop the dev server**

Run: `kill %1` (or Ctrl+C in the terminal running uvicorn).

- [ ] **Step 4: Final commit if anything was left uncommitted**

Run: `git status`
Expected: clean tree (all prior task commits already cover everything). If anything is unstaged (e.g. a fix made during manual verification), commit it with a message describing what regression it fixed.
