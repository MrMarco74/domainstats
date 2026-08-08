import requests
import json
import os
import hashlib
import ipaddress
import socket
from urllib.parse import urlparse
from datetime import datetime, timedelta
from src.db_utils import get_db

# Configurable via environment variables — no redeploy needed, just restart the service.
LLM_URL     = os.getenv("LLM_URL", "http://localhost:11434/api/generate")
DEFAULT_MODEL = os.getenv("LLM_MODEL", "deepseek-r1:14b")
LLM_TIMEOUT   = int(os.getenv("LLM_TIMEOUT_SECONDS", "300"))   # Default: 5 Minuten
LLM_CACHE_TTL = int(os.getenv("LLM_CACHE_TTL_HOURS", "24"))    # Default: 24 Stunden
LLMPROXY_TOKEN = os.environ.get("LLMPROXY_TOKEN", "")          # Bearer token for llmproxy client identity (optional — IP fallback if unset)

from pylibs.ollama import OllamaClient, OllamaEndpoints


def reject_unsafe_ollama_host(base_url: str):
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
        if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
            ip = ip.ipv4_mapped  # normalize ::ffff:127.0.0.1-style addresses before checking
        if ip.is_loopback or ip.is_link_local or ip.is_unspecified or ip.is_multicast or ip.is_reserved:
            raise ValueError(f"Host '{hostname}' löst zu einer nicht erlaubten Adresse auf ({ip}).")


def normalize_ollama_base_url(url: str) -> str:
    """Strip a trailing /api/generate (legacy LLM_URL format) and any trailing slash."""
    if url.endswith('/api/generate'):
        url = url[: -len('/api/generate')]
    return url.rstrip('/')


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


def analyze_with_llm(prompt, model=None):
    settings = _get_ollama_settings()
    resolved_model = model or settings['model']
    base_url = normalize_ollama_base_url(settings['url'])

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
            reject_unsafe_ollama_host(base_url)
            session = requests.Session()
            session.max_redirects = 0  # a redirect could point at a loopback/link-local address the check above already rejected
            client = OllamaClient(
                OllamaEndpoints(base_url=base_url),
                token=settings['token'] or None,
                timeout=LLM_TIMEOUT,
                session=session,
            )
            result = client.generate(resolved_model, prompt)
            if not result:
                return "Fehler: Keine Antwort vom Modell."
        except requests.exceptions.Timeout:
            return f"Fehler: LLM-Anfrage timed out nach {LLM_TIMEOUT}s"
        except ValueError as e:
            return f"Fehler: Ungültige Ollama-URL ({str(e)})"
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


def prepare_security_prompt(logs, integrity_logs):
    log_str = "\n".join([f"{l['timestamp']} - {l['ip']} - {l['method']} {l['path']} - {l['status']}" for l in logs])
    integrity_str = "\n".join([f"{l['timestamp']} - {l['domain']} - {l['change_type']}: {l['path']}" for l in integrity_logs])
    
    return f"""
Du bist ein Security-Experte für Webserver. Analysiere die folgenden Server-Logs und Datei-Integritäts-Meldungen auf verdächtige Muster, Angriffsversuche oder Anomalien.
Hinweis: Bekannte Bot-Scanner und harmlose 404-Zugriffe (False-Positives) wurden bereits vorab gefiltert.
Gib eine strukturierte Zusammenfassung in Markdown aus.

### SERVER LOGS (Auszug):
{log_str}

### INTEGRITY LOGS (Auszug):
{integrity_str}

### AUFGABE:
1. Identifiziere auffällige IPs oder User-Agents.
2. Prüfe, ob Dateiänderungen zeitlich mit verdächtigen Anfragen korrelieren.
3. Gib Empfehlungen für Firewall-Regeln (IP-Sperren) oder Sicherheitsmaßnahmen.
4. Falls alles normal aussieht, bestätige dies kurz.

Antworte auf Deutsch.
"""

def prepare_traffic_prompt(stats):
    stats_str = json.dumps(stats, indent=2)
    return f"""
Du bist ein Data Analyst. Analysiere die folgenden Webserver-Statistiken und erstelle einen kurzen, prägnanten Bericht über die Traffic-Highlights.
Hinweis: Technischer Hintergrundrauschen (Scans nach .env, wp-login etc.) wurde bereits ausgefiltert.
Gib den Bericht in Markdown aus.

### STATISTIKEN:
{stats_str}

### AUFGABE:
1. Was sind die meistbesuchten Domains und Pfade?
2. Gibt es ungewöhnliche Referrer oder Traffic-Quellen?
3. Was lässt sich über die Browser/OS-Verteilung sagen?

Antworte auf Deutsch.
"""

def prepare_longterm_prompt(suspicious_ips):
    ips_str = json.dumps(suspicious_ips, indent=2)
    return f"""
Du bist ein Experte für Intrusion Detection. Analysiere die folgenden Langzeit-Daten auf "Slow Attacks" (langsame, unauffällige Angriffe über mehrere Tage).
Gib deine Analyse in Markdown aus.

### VERDÄCHTIGE IP-MUSTER (über die letzten 7-14 Tage):
{ips_str}

### AUFGABE:
1. Welche IPs zeigen ein "Low and Slow" Muster (z.B. konstant wenige 404s pro Tag, aber über einen langen Zeitraum)?
2. Gibt es IPs, die über Tage hinweg systematisch verschiedene Domains scannen?
3. Welche dieser Muster sind am gefährlichsten?
4. Schlage präventive Maßnahmen vor.

Antworte auf Deutsch.
"""
