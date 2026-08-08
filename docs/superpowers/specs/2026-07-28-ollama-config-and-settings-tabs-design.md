# Ollama-Konfiguration & Reiter für die Settings-Seite

Datum: 2026-07-28

## Ziel

Die Konfigurationsseite (`#config-tab`) erlaubt es aktuell schon, SSH/FTP-Verbindung,
Web-Root/Log-Pfade und IP-Filter einzustellen, alles auf einer langen Seite. Zwei Dinge
sollen sich ändern:

1. Eine neue Sektion "KI" kommt dazu: Ollama Base-URL, API-Token und eine Auswahl der auf
   dem Ollama-Host tatsächlich installierten Modelle (statt eines hartkodierten
   Modellnamens).
2. Die Konfigurationsseite bekommt Reiter (Sub-Tabs), damit die wachsende Zahl an
   Abschnitten nicht mehr eine einzige lange Seite ist.

Für Ollama-Zugriff und Modell-Listing wird `pylibs.ollama` (Sibling-Projekt
`/home/mrmarco/Documents/gitlab/pylibs`) verwendet statt eigener `requests`-Aufrufe.

## Architektur

### Speicherung der Einstellungen

Kein neues Schema. Die bestehende generische `settings`-Tabelle (Key/Value, siehe
`GET/POST /stats/settings` in `src/main.py`) bekommt drei neue Keys:

- `ollama_url` — Base-URL des Ollama/Proxy-Endpunkts
- `ollama_token` — Bearer-Token (optional)
- `ollama_model` — ausgewähltes Default-Modell für Deep Insights

`ollama_token` wird in `update_settings()` der bestehenden Skip-if-empty-Regel
hinzugefügt (aktuell nur `ftp_pass`, `key_pass`), damit ein leeres Feld beim Speichern
nicht das gespeicherte Token löscht.

### Abhängigkeit auf pylibs

`requirements.txt` bekommt:

```
pylibs[ollama] @ git+ssh://git@gitlab.internal.familie-frischkorn.de/apps/pylibs.git
```

Verwendet werden `pylibs.ollama.OllamaClient`, `OllamaEndpoints` und
`pylibs.ollama.models.list_models`.

### Neuer Endpunkt: Modelle auflisten

`GET /stats/ollama/models?base_url=...&token=...`

- `base_url`/`token` sind optionale Query-Parameter. Wenn nicht gesetzt, werden die
  gespeicherten Settings-Werte verwendet — das erlaubt "Modelle laden" schon vor dem
  Speichern (Test-vor-Save, analog zum bestehenden "Verbindung Testen"-Button bei
  SSH/FTP).
- Baut einen kurzlebigen `OllamaClient(OllamaEndpoints(base_url=...), token=...)` und
  ruft `list_models()` auf.
- Erfolg: `{"models": ["llama3:latest", "deepseek-r1:14b", ...]}` (Namen aus
  `ModelInfo.name`, alphabetisch sortiert).
- Fehler (Timeout, Connection-Error, HTTP != 200): `HTTPException(502, detail=<kurze
  Fehlermeldung>)`.

### `src/llm_utils.py` Umstellung

`analyze_with_llm()` nutzt aktuell einen eigenen `requests.post` gegen `LLM_URL` mit
`LLMPROXY_TOKEN` aus der Umgebungsvariable. Das wird ersetzt durch:

- Zur Laufzeit werden `ollama_url`/`ollama_token`/`ollama_model` aus der `settings`-
  Tabelle gelesen (via `get_db()`, gleiche Connection wie die anderen Reads in dieser
  Datei).
- Fallback auf die bestehenden ENV-Vars (`LLM_URL`, `LLMPROXY_TOKEN`, `LLM_MODEL`), falls
  in der DB noch nichts gesetzt ist — kein Breaking Change für bestehende Deployments,
  bis jemand die neue UI einmal benutzt.
- `OllamaClient(...).generate(model, prompt)` ersetzt den rohen `requests.post`-Call.
  Das Cache-Verhalten (sha256-Hash über Model+Prompt, `llm_cache`-Tabelle, TTL) bleibt
  unverändert.
- Fehlerpfade bleiben textuell gleich (`"Fehler: ..."`-Strings), nur dass sie jetzt aus
  `requests.exceptions`/`OllamaClient`-Exceptions statt der bisherigen manuellen
  Statuscode-Prüfung kommen.

## Frontend

### Sub-Tabs innerhalb der Konfigurationsseite

Fünf Reiter, alle als Kinder von `#config-tab`, mit demselben `active`-Klassen-Muster wie
die bestehenden Top-Level-Tabs, aber über eine neue, generische Hilfsfunktion
`initSubTabs(navSelector, contentSelector)` in `app.js` (statt den bestehenden
Klick-Handler in `initDashboard()` zu kopieren):

1. **Verbindung** — bestehendes `connection-settings-form` (SSH/FTP)
2. **Pfade** — bestehendes `path-settings-form`
3. **IP-Filter** — bestehendes `ip-filter-section`
4. **KI** (neu) — Ollama-Konfiguration
5. **Wartung** — Domain-Pfad-Tabelle (`config-table`) + Speicherplatz/Cache-Karte

Die bestehenden Markup-Blöcke werden nur umsortiert (in die jeweilige Sub-Tab-`<div>`
verschoben), ihr Verhalten/ihre IDs bleiben unverändert — reines Re-Nesting, kein
Rewrite.

### Neuer Reiter "KI"

Formular mit:

- Base-URL-Input (`#ollama-url`), vorbefüllt aus `GET /stats/settings`
- API-Token-Passwort-Input (`#ollama-token`), bleibt beim Laden leer (wie `ftp-pass`)
- Button "Modelle laden" → ruft `/stats/ollama/models` mit den aktuell eingetippten
  (noch ungespeicherten) Werten aus den beiden Feldern oben auf, befüllt ein
  `<select id="ollama-model">` mit den zurückgegebenen Namen
- Inline-Fehlertext neben dem Button bei Fehlschlag (gleiches visuelles Muster wie
  `#test-conn-result`)
- Save-Button → `saveOllamaSettings()`, POST an `/stats/settings` mit `ollama_url`,
  `ollama_token` (leer → wird von der Skip-if-empty-Regel serverseitig ignoriert),
  `ollama_model`

Das ausgewählte Modell wird beim Aufruf der Deep-Insights-Buttons
(`runLLMAnalysis('security'|'longterm'|'traffic')`) nicht mehr clientseitig übergeben —
das Backend liest `ollama_model` direkt aus den Settings (siehe oben), analog dazu, wie
FTP-Zugangsdaten heute schon serverseitig statt clientseitig verwaltet werden.

## Datenfluss

1. Seitenaufruf → bestehendes `GET /stats/settings` befüllt zusätzlich die KI-Felder
   (`ollama_url`, `ollama_model`); `ollama_token` bleibt leer.
2. User trägt URL/Token ein → "Modelle laden" → `GET /stats/ollama/models?base_url=...
   &token=...` → Dropdown wird befüllt.
3. User wählt Modell, klickt Speichern → `POST /stats/settings`.
4. Deep-Insights-Klick → Backend liest `ollama_url`/`ollama_token`/`ollama_model` aus der
   DB → `OllamaClient(...).generate(...)`.

## Fehlerbehandlung

- `/stats/ollama/models`: Netzwerk-/Timeout-/HTTP-Fehler → 502 mit kurzer Nachricht,
  Frontend zeigt sie inline an; kein Absturz der Seite.
- Deep Insights ohne konfiguriertes Modell/URL: fällt auf die bisherigen ENV-Var-Defaults
  zurück (siehe oben), verhält sich also wie heute, bis die neue UI einmal benutzt wurde.

## Testing

Kein bestehendes Test-Framework in domainstats. Manuelle Verifikation:

- `pip install` der neuen `pylibs[ollama]`-Abhängigkeit funktioniert lokal
- `/stats/ollama/models` liefert echte Modellnamen vom
  `llmproxy.internal.familie-frischkorn.de:11435`
- Settings-Roundtrip: Speichern → `sqlite3 data/logs.db "select * from settings"` zeigt
  die neuen Keys, Token wird bei leerem Save-Feld nicht überschrieben
- Deep Insights funktioniert weiterhin mit dem gewählten Modell
- Sub-Tab-Navigation und Layout im Browser (alle 5 Reiter erreichbar, bestehende
  Funktionen in ihrem neuen Reiter weiterhin funktionsfähig)

## Deployment

`deploy.sh`/`install.sh` werden für dieses Feature **nicht** angefasst oder ausgeführt.
labcontrol verwaltet domainstats auf `worker` bereits über eine Ansible-Rolle
(`svc_domainstats_api`), die per `update`-Task einen `git pull` + Venv-Rebuild + Neustart
des systemd-Service durchführt. Ausrollen bedeutet also:

1. Push nach `origin` (`git@gitlab.fritz.box:apps/domainstats.git`)
2. `domainstats-api`-Update-Playbook über labcontrol (MCP `run_playbook`) laufen lassen

Der Venv-Rebuild im Playbook zieht dabei automatisch die neue `pylibs[ollama]`-
Abhängigkeit.
