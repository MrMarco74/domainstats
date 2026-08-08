import os
import re
import sqlite3
import json
import time
from datetime import datetime
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from typing import List, Dict, Optional
import stat
import asyncio
from src.ftp_utils import get_ftp_client, close_ftp_client
from src.db_utils import get_db
from src.integrity import IntegrityScanner
from src.llm_utils import analyze_with_llm, prepare_security_prompt, prepare_traffic_prompt, prepare_longterm_prompt, reject_unsafe_ollama_host, normalize_ollama_base_url
import requests
from urllib.parse import urlparse

from pylibs.ollama import OllamaClient, OllamaEndpoints
from pylibs.ollama.models import list_models
from src.monitor import monitoring_loop
from src.parser import REGISTRY_DOMAIN
import threading
import shutil
from fastapi.responses import HTMLResponse

app = FastAPI(title="DomainStats API")

# Security: CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.on_event("startup")
async def startup_event():
    # Start monitoring thread
    monitor_thread = threading.Thread(target=monitoring_loop, daemon=True)
    monitor_thread.start()

# Configuration for local web roots to check file existence
# Format: { "domain.com": "/path/to/web/root" }
SITE_ROOTS_FALLBACK = {
    "yads-security.de": "/var/www/vhosts/yads-security.de/httpdocs/",
    "yads-security.com": "/var/www/vhosts/yads-security.com/httpdocs/",
}
# Default base path for unknown domains (using the newly discovered root)
DEFAULT_BASE_PATH = "/public_html/{domain}/"

YADS_DOMAINS = ["yads-security.de", "yads-security.com"]

# Domains with a configured FTP path are hosted on external webspace (Hetzner).
# Domains without one (incl. those never added to domain_configs) are served
# directly via the home reverse-proxy, i.e. under our own internet IP.
EXTERNAL_DOMAINS_SUBQUERY = "SELECT domain FROM domain_configs WHERE TRIM(COALESCE(path, '')) != ''"

def get_combined_filter(domain: str, existing_where: bool = False, alias: str = None):
    keyword = "AND" if existing_where else "WHERE"
    prefix = f"{alias}." if alias else ""

    # 1. Domain part
    if not domain:
        domain_sql = ""
        params = []
    elif domain == "yads-security-combined":
        domain_sql = f" {prefix}domain IN ({','.join(['?' for _ in YADS_DOMAINS])})"
        params = list(YADS_DOMAINS)
    elif domain == "__external__":
        domain_sql = f" {prefix}domain IN ({EXTERNAL_DOMAINS_SUBQUERY})"
        params = []
    elif domain == "__selfhosted__":
        domain_sql = f" {prefix}domain NOT IN ({EXTERNAL_DOMAINS_SUBQUERY})"
        params = []
    else:
        domain_sql = f" {prefix}domain = ?"
        params = [domain]
        
    # 2. IP part
    ip_sql = f" {prefix}ip NOT IN (SELECT ip FROM ignored_ips)"
    
    if not domain_sql:
        return f" {keyword} {ip_sql}", params
    else:
        return f" {keyword} {domain_sql} AND {ip_sql}", params

def anonymize_ip(ip: str) -> str:
    if not ip or ip == '-': return ip
    if ':' in ip: # IPv6
        parts = ip.split(':')
        if len(parts) >= 3:
            return ":".join(parts[:3]) + ":xxxx:xxxx:xxxx:xxxx"
        return "xxxx:xxxx:xxxx:xxxx"
    else: # IPv4
        parts = ip.split('.')
        if len(parts) == 4:
            return ".".join(parts[:3]) + ".x"
        return "xxx.xxx.xxx.x"

@app.get("/stats/system/disk")
async def get_disk_usage():
    target = "/mnt/storage" if os.path.exists("/mnt/storage") else "."
    usage = shutil.disk_usage(target)
    return {
        "total": usage.total,
        "used": usage.used,
        "free": usage.free,
        "percent": (usage.used / usage.total) * 100,
        "path": target
    }

@app.get("/stats/overview")
async def get_overview(domain: str = None):
    conn = get_db()
    cursor = conn.cursor()
    
    # 1. Historical data from daily_stats
    ds_clause = ""
    ds_params = []
    if domain == "yads-security-combined":
        ds_clause = f" WHERE domain IN ({','.join(['?' for _ in YADS_DOMAINS])})"
        ds_params = list(YADS_DOMAINS)
    elif domain == "__external__":
        ds_clause = f" WHERE domain IN ({EXTERNAL_DOMAINS_SUBQUERY})"
        ds_params = []
    elif domain == "__selfhosted__":
        ds_clause = f" WHERE domain NOT IN ({EXTERNAL_DOMAINS_SUBQUERY})"
        ds_params = []
    elif domain:
        ds_clause = " WHERE domain = ?"
        ds_params = [domain]
        
    cursor.execute(f"SELECT SUM(requests), SUM(bandwidth) FROM daily_stats{ds_clause}", ds_params)
    hist_reqs, hist_bw = cursor.fetchone()
    hist_reqs = hist_reqs or 0
    hist_bw = hist_bw or 0
    
    # 2. Today's data from logs
    log_clause, log_params = get_combined_filter(domain)
    # We use a simple date check to get today's data
    cursor.execute(f"SELECT COUNT(*), SUM(CASE WHEN size = '-' THEN 0 ELSE CAST(size AS INTEGER) END) FROM logs{log_clause} AND strftime('%Y-%m-%d', timestamp) >= DATE('now')", log_params)
    today_reqs, today_bw = cursor.fetchone()
    today_reqs = today_reqs or 0
    today_bw = today_bw or 0
    
    total_reqs = hist_reqs + today_reqs
    total_mb = round((hist_bw + today_bw) / (1024 * 1024), 2)

    # 3. Unique IPs (approximate from logs window)
    query_suffix, params = get_combined_filter(domain)
    cursor.execute(f"SELECT COUNT(DISTINCT ip) FROM logs{query_suffix}", params)
    unique_ips = cursor.fetchone()[0]
    
    # 4. Error rate (4xx/5xx) from logs window
    clause, params_err = get_combined_filter(domain, existing_where=True)
    cursor.execute(f"SELECT COUNT(*) FROM logs WHERE status >= 400" + clause, params_err)
    error_reqs = cursor.fetchone()[0]
    error_rate = round((error_reqs / total_reqs * 100), 2) if total_reqs > 0 else 0
    
    conn.close()
    return {
        "total_requests": total_reqs,
        "unique_ips": unique_ips,
        "total_traffic_mb": total_mb,
        "error_rate": error_rate
    }

@app.get("/stats/domains")
async def get_domains():
    conn = get_db()
    cursor = conn.cursor()
    query = "SELECT domain, COUNT(*) as count FROM logs"
    clause, params = get_combined_filter(None)
    query += clause
    query += " GROUP BY domain ORDER BY count DESC"
    cursor.execute(query, params)
    data = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return data

@app.get("/stats/geo")
async def get_geo_stats(domain: str = None):
    conn = get_db()
    cursor = conn.cursor()
    query = "SELECT country_code, COUNT(*) as count FROM logs"
    clause, params = get_combined_filter(domain)
    query += clause
    query += " GROUP BY country_code ORDER BY count DESC LIMIT 10"
    
    cursor.execute(query, params)
    data = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return data

@app.get("/stats/pages")
async def get_top_pages(domain: str = None):
    conn = get_db()
    cursor = conn.cursor()
    query = "SELECT path, COUNT(*) as count FROM logs"
    clause, params = get_combined_filter(domain)
    query += clause
    query += " GROUP BY path ORDER BY count DESC LIMIT 15"
    
    cursor.execute(query, params)
    data = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return data

@app.get("/stats/timeseries")
async def get_timeseries(domain: str = None):
    conn = get_db()
    cursor = conn.cursor()
    
    # Use aggregation table for history and logs for today
    # This provides lightning-fast charts even with millions of records
    ds_clause = ""
    ds_params = []
    if domain == "yads-security-combined":
        ds_clause = f" WHERE domain IN ({','.join(['?' for _ in YADS_DOMAINS])})"
        ds_params = list(YADS_DOMAINS)
    elif domain == "__external__":
        ds_clause = f" WHERE domain IN ({EXTERNAL_DOMAINS_SUBQUERY})"
        ds_params = []
    elif domain == "__selfhosted__":
        ds_clause = f" WHERE domain NOT IN ({EXTERNAL_DOMAINS_SUBQUERY})"
        ds_params = []
    elif domain:
        ds_clause = " WHERE domain = ?"
        ds_params = [domain]

    log_clause, log_params = get_combined_filter(domain, existing_where=True)
    
    query = f"""
        SELECT day as date, SUM(requests) as count, SUM(downloads) as downloads FROM daily_stats
        {ds_clause}
        GROUP BY day
        UNION ALL
        SELECT strftime('%Y-%m-%d', timestamp) as date, COUNT(*) as count,
               SUM(CASE WHEN (path LIKE '/releases/%.zip' OR path LIKE '%yads_v%_customer_pkg.zip') THEN 1 ELSE 0 END) as downloads
        FROM logs
        WHERE strftime('%Y-%m-%d', timestamp) >= DATE('now')
        {log_clause}
        GROUP BY date
        ORDER BY date ASC
    """
    
    cursor.execute(query, ds_params + log_params)
    data = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return data

@app.get("/stats/browsers")
async def get_browsers(domain: str = None):
    conn = get_db()
    cursor = conn.cursor()
    query = "SELECT browser, COUNT(*) as count FROM logs"
    clause, params = get_combined_filter(domain)
    query += clause
    query += " GROUP BY browser ORDER BY count DESC LIMIT 10"
    
    cursor.execute(query, params)
    data = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return data

@app.get("/stats/errors_detail")
async def get_errors_detail(domain: str = None):
    conn = get_db()
    cursor = conn.cursor()
    query = "SELECT path, status, COUNT(*) as count FROM logs WHERE status >= 400"
    clause, params = get_combined_filter(domain, existing_where=True)
    query += clause
    query += " GROUP BY path, status ORDER BY count DESC LIMIT 20"
    
    cursor.execute(query, params)
    data = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return data

@app.get("/stats/error_ips")
async def get_error_ips(domain: str = None):
    conn = get_db()
    cursor = conn.cursor()
    query = "SELECT ip, country_code, COUNT(*) as count FROM logs WHERE status >= 400"
    clause, params = get_combined_filter(domain, existing_where=True)
    query += clause
    query += " GROUP BY ip ORDER BY count DESC LIMIT 20"
    
    cursor.execute(query, params)
    data = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return data

@app.get("/stats/validate_errors")
async def validate_errors(domain: str = None):
    conn = get_db()
    cursor = conn.cursor()
    
    # Get all errors that are NOT ignored
    query = """
        SELECT l.domain, l.path, l.status, COUNT(*) as count 
        FROM logs l
        LEFT JOIN ignored_errors i ON l.domain = i.domain AND l.path = i.path AND l.status = i.status
        WHERE l.status >= 400 AND i.domain IS NULL
        AND NOT EXISTS (SELECT 1 FROM security_false_positives sfp WHERE sfp.domain = l.domain AND sfp.path = l.path)
    """
    clause, params = get_combined_filter(domain, existing_where=True, alias="l")
    query += clause
    
    query += " GROUP BY l.domain, l.path, l.status ORDER BY count DESC LIMIT 100"
    
    cursor.execute(query, params)
    errors = [dict(row) for row in cursor.fetchall()]
    
    # Get all domain configs
    cursor.execute("SELECT domain, path FROM domain_configs")
    configs = {row['domain']: row['path'] for row in cursor.fetchall()}
    
    results = []
    for err in errors:
        # Determine actual file path: 
        # 1. DB Config
        # 2. Hardcoded fallback
        # 3. Default pattern
        root = configs.get(err['domain']) or SITE_ROOTS_FALLBACK.get(err['domain'], DEFAULT_BASE_PATH.format(domain=err['domain']))
        
        # Strip query parameters from path for file check
        clean_path = err['path'].split('?')[0].lstrip('/')
        full_path = os.path.join(root, clean_path)
        
        exists = os.path.exists(full_path)
        is_directory = os.path.isdir(full_path) if exists else False
        
        results.append({
            **err,
            "full_path": full_path,
            "exists": exists,
            "is_directory": is_directory,
            "check_status": "exists" if exists else "missing"
        })
        
    conn.close()
    return results

@app.get("/stats/settings")
async def get_settings():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT key, value FROM settings")
    settings = {row['key']: row['value'] for row in cursor.fetchall()}
    conn.close()
    if settings.get('ollama_token'):
        settings['ollama_token'] = True
    return settings

from fastapi import Request

@app.post("/stats/settings")
async def update_settings(data: Dict):
    conn = get_db()
    cursor = conn.cursor()
    try:
        for key, value in data.items():
            # Don't overwrite passwords if they are empty (user just saved other settings)
            if key in ('ftp_pass', 'key_pass', 'ollama_token') and not value:
                continue
                
            cursor.execute(
                "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                (key, value)
            )
        conn.commit()
    except Exception as e:
        conn.close()
        raise HTTPException(status_code=400, detail=str(e))
    conn.close()
    return {"status": "success"}

@app.post("/stats/ollama/models")
async def get_ollama_models(data: Dict):
    base_url = data.get('base_url')
    token = data.get('token')

    conn = get_db()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT key, value FROM settings WHERE key IN ('ollama_url', 'ollama_token')")
        saved = {row['key']: row['value'] for row in cursor.fetchall()}
    finally:
        conn.close()

    resolved_url = base_url or saved.get('ollama_url')

    if token:
        resolved_token = token
    elif not base_url or (saved.get('ollama_url') and urlparse(base_url).hostname == urlparse(saved.get('ollama_url')).hostname):
        resolved_token = saved.get('ollama_token')
    else:
        resolved_token = ""  # explicit empty string suppresses OllamaClient's own env-var fallback (LLMPROXY_TOKEN)

    if not resolved_url:
        raise HTTPException(status_code=400, detail="Keine Ollama Base-URL angegeben oder gespeichert.")

    resolved_url = normalize_ollama_base_url(resolved_url)

    try:
        reject_unsafe_ollama_host(resolved_url)
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

@app.get("/stats/my_ip")
async def get_my_ip(request: Request):
    # Handle proxy headers if necessary
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return {"ip": forwarded.split(",")[0]}
    return {"ip": request.client.host}

@app.get("/stats/ignored_ips")
async def get_ignored_ips():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT ip FROM ignored_ips")
    ips = [row['ip'] for row in cursor.fetchall()]
    conn.close()
    return ips

@app.post("/stats/ignore_ip")
async def ignore_ip(data: Dict):
    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute("INSERT OR IGNORE INTO ignored_ips (ip) VALUES (?)", (data['ip'],))
        conn.commit()
    except Exception as e:
        conn.close()
        raise HTTPException(status_code=400, detail=str(e))
    conn.close()
    return {"status": "success"}

@app.delete("/stats/ignore_ip")
async def unignore_ip(ip: str):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM ignored_ips WHERE ip = ?", (ip,))
    conn.commit()
    conn.close()
    return {"status": "success"}

@app.post("/stats/refresh_daily")
async def refresh_daily_stats():
    from src.aggregator import run_aggregation
    try:
        # Run in a thread to avoid blocking the API
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, run_aggregation)
        return {"status": "success", "message": "Aggregation abgeschlossen."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# Integrity Monitoring Endpoints

@app.get("/stats/integrity/logs")
async def get_integrity_logs(domain: Optional[str] = None, limit: int = 50):
    conn = get_db()
    cursor = conn.cursor()
    query = "SELECT * FROM integrity_logs"
    params = []
    if domain:
        query += " WHERE domain = ?"
        params.append(domain)
    query += " ORDER BY timestamp DESC LIMIT ?"
    params.append(limit)
    
    cursor.execute(query, params)
    rows = cursor.fetchall()
    logs = []
    # Use same cache resolution as IntegrityScanner
    cache_base = "/mnt/storage/caching" if os.path.isdir("/mnt/storage/caching") else "data/integrity_cache"
    for row in rows:
        d = dict(row)
        # Check if a cached .gz exists for this file (only cacheable types get one)
        gz_path = os.path.join(cache_base, d['domain'], d['path'].replace('/', '_') + ".gz")
        d['has_cache'] = os.path.exists(gz_path)
        logs.append(d)
        
    conn.close()
    return logs

active_scans = {}

@app.post("/stats/integrity/scan")
async def trigger_integrity_scan(domain: Optional[str] = None, auto_accept: bool = False):
    scan_id = str(int(time.time()))
    active_scans[scan_id] = {"status": "starting", "logs": ["Scan gestartet..."]}
    
    def progress_callback(msg):
        print(f"DEBUG: Progress callback: {msg}")
        active_scans[scan_id]["status"] = "running"
        active_scans[scan_id]["logs"].append(msg)
        if len(active_scans[scan_id]["logs"]) > 100:
            active_scans[scan_id]["logs"].pop(0)

    async def run_scan():
        scanner = IntegrityScanner(progress_callback)
        active_scans[scan_id]["scanner"] = scanner
        loop = asyncio.get_event_loop()
        
        if domain:
            # Need path for domain
            conn = get_db()
            cursor = conn.cursor()
            cursor.execute("SELECT path FROM domain_configs WHERE domain = ?", (domain,))
            row = cursor.fetchone()
            path = row['path'] if row else None
            if not path:
                # Fallback
                path = SITE_ROOTS_FALLBACK.get(domain, DEFAULT_BASE_PATH.format(domain=domain))
            conn.close()
            
            try:
                await loop.run_in_executor(None, scanner.scan_domain, domain, path, auto_accept)
            except Exception as e:
                progress_callback(f"Error: {str(e)}")
        else:
            await loop.run_in_executor(None, scanner.scan_all, auto_accept)
            
        active_scans[scan_id]["status"] = "finished"

    asyncio.create_task(run_scan())
    return {"scan_id": scan_id}

@app.get("/stats/integrity/status/{scan_id}")
async def get_scan_status(scan_id: str):
    data = active_scans.get(scan_id, {"status": "not_found", "logs": []})
    # Remove scanner object from response to avoid JSON errors
    if "scanner" in data:
        data = data.copy()
        del data["scanner"]
    return data

@app.post("/stats/integrity/stop/{scan_id}")
async def stop_integrity_scan(scan_id: str):
    if scan_id in active_scans and "scanner" in active_scans[scan_id]:
        active_scans[scan_id]["scanner"].stop()
        return {"status": "success"}
    return {"status": "not_found"}

@app.get("/stats/insights/security")
async def get_security_insights(domain: Optional[str] = None):
    conn = get_db()
    cursor = conn.cursor()
    
    # Fetch recent logs (last 500) — excluding false positives for errors
    query = """
        SELECT timestamp, ip, method, path, status 
        FROM logs l 
        WHERE (l.status < 400 OR NOT EXISTS (SELECT 1 FROM security_false_positives sfp WHERE sfp.domain = l.domain AND sfp.path = l.path))
    """
    clause, params = get_combined_filter(domain, existing_where=True, alias="l")
    query += clause
    query += " ORDER BY l.timestamp DESC LIMIT 500"
    cursor.execute(query, params)
    logs = [dict(row) for row in cursor.fetchall()]
    
    # Fetch recent integrity logs (last 50)
    query = "SELECT timestamp, domain, path, change_type FROM integrity_logs"
    params = []
    if domain:
        query += " WHERE domain = ?"
        params.append(domain)
    query += " ORDER BY timestamp DESC LIMIT 50"
    cursor.execute(query, params)
    integrity_logs = [dict(row) for row in cursor.fetchall()]
    conn.close()
    
    prompt = prepare_security_prompt(logs, integrity_logs)
    analysis = analyze_with_llm(prompt)
    return {"analysis": analysis}

@app.get("/stats/insights/traffic")
async def get_traffic_insights(domain: Optional[str] = None):
    # Fetch some summary stats
    # We'll use existing endpoints' logic but internally
    conn = get_db()
    cursor = conn.cursor()
    
    # Top paths (excluding false positives for errors and ignored IPs)
    query = """
        SELECT path, COUNT(*) as count 
        FROM logs l
        WHERE (l.status < 400 OR NOT EXISTS (SELECT 1 FROM security_false_positives sfp WHERE sfp.domain = l.domain AND sfp.path = l.path))
    """
    clause, params = get_combined_filter(domain, existing_where=True, alias="l")
    query += clause
    query += " GROUP BY path ORDER BY count DESC LIMIT 10"
    cursor.execute(query, params)
    top_paths = [dict(row) for row in cursor.fetchall()]
    
    # Browser dist
    query = "SELECT browser, COUNT(*) as count FROM logs l"
    clause, params_b = get_combined_filter(domain, alias="l")
    query += clause
    query += " GROUP BY browser ORDER BY count DESC LIMIT 5"
    cursor.execute(query, params_b)
    browsers = [dict(row) for row in cursor.fetchall()]
    
    conn.close()
    
    stats = {
        "top_paths": top_paths,
        "browsers": browsers
    }
    
    prompt = prepare_traffic_prompt(stats)
    analysis = analyze_with_llm(prompt)
    return {"analysis": analysis}

@app.get("/stats/insights/longterm")
async def get_longterm_insights(domain: Optional[str] = None):
    conn = get_db()
    cursor = conn.cursor()

    # 1. IPs scanning multiple domains over time (excluding false positives for errors and ignored IPs)
    query = """
        SELECT l.ip, COUNT(DISTINCT l.domain) as domain_count, COUNT(*) as total_requests
        FROM logs l
        WHERE (l.status < 400 OR NOT EXISTS (SELECT 1 FROM security_false_positives sfp WHERE sfp.domain = l.domain AND sfp.path = l.path))
    """
    clause, params = get_combined_filter(domain, existing_where=True, alias="l")
    query += clause
    query += " GROUP BY l.ip HAVING domain_count > 2 ORDER BY domain_count DESC LIMIT 20"
    cursor.execute(query, params)
    multi_domain_scanners = [dict(row) for row in cursor.fetchall()]

    # 2. IPs with persistent 404 activity (Slow scan) - excluding false positives and ignored IPs
    query = """
        SELECT l.ip,
               COUNT(*) as total_requests,
               SUM(CASE WHEN l.status = '404' THEN 1 ELSE 0 END) as count_404,
               COUNT(DISTINCT date(l.timestamp)) as active_days
        FROM logs l
        WHERE l.timestamp > datetime('now', '-14 days')
        AND (l.status < 400 OR NOT EXISTS (SELECT 1 FROM security_false_positives sfp WHERE sfp.domain = l.domain AND sfp.path = l.path))
    """
    clause, params_slow = get_combined_filter(domain, existing_where=True, alias="l")
    query += clause
    query += " GROUP BY l.ip HAVING count_404 > 10 AND active_days > 2 ORDER BY count_404 DESC LIMIT 20"
    cursor.execute(query, params_slow)
    slow_scanners = [dict(row) for row in cursor.fetchall()]
    
    conn.close()
    
    suspicious_data = {
        "multi_domain_scanners": multi_domain_scanners,
        "slow_scanners": slow_scanners
    }
    
    prompt = prepare_longterm_prompt(suspicious_data)
    analysis = analyze_with_llm(prompt)
    return {"analysis": analysis}

@app.post("/stats/integrity/preload")
async def preload_integrity_cache(domain: Optional[str] = None):
    scan_id = str(int(time.time()))
    active_scans[scan_id] = {"status": "starting", "logs": ["Preload gestartet..."]}
    
    def progress_callback(msg):
        active_scans[scan_id]["status"] = "running"
        active_scans[scan_id]["logs"].append(msg)
        
    async def run_preload():
        scanner = IntegrityScanner(progress_callback)
        active_scans[scan_id]["scanner"] = scanner
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, scanner.preload_all_cache, domain)
        active_scans[scan_id]["status"] = "finished"
        
    asyncio.create_task(run_preload())
    return {"scan_id": scan_id}

@app.post("/stats/integrity/clear_cache")
async def clear_integrity_cache():
    # Stop any active scans first
    for scan_id, scan in active_scans.items():
        scanner = scan.get("scanner")
        if scanner:
            scanner.stop()
    active_scans.clear()
    
    # Clear cache files + DB tables
    scanner = IntegrityScanner()
    scanner.clear_cache()
    return {"message": "Cache und Baseline vollständig geleert. Bereit für neuen Preload."}

@app.get("/stats/monitoring/status")
async def get_monitoring_status():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM monitoring")
    status = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return status

@app.get("/stats/integrity/view")
async def view_remote_file(domain: str, path: str):
    # Determine full path
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT path FROM domain_configs WHERE domain = ?", (domain,))
    row = cursor.fetchone()
    root = row['path'] if row else SITE_ROOTS_FALLBACK.get(domain, DEFAULT_BASE_PATH.format(domain=domain))
    
    conn.close()
    
    full_path = os.path.join(root, path)
    content = ""
    
    try:
        ftp = get_ftp_client()
        lines = []
        ftp.retrlines(f"RETR {full_path}", lines.append)
        content = "\n".join(lines)
        import html
        return {"content": html.escape(content), "path": path, "domain": domain}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/stats/integrity/accept")
async def accept_integrity_change(data: Dict):
    domain = data.get('domain')
    path = data.get('path')
    new_checksum = data.get('new_checksum')
    new_mtime = data.get('new_mtime')
    
    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "UPDATE file_integrity SET checksum = ?, mtime = ?, last_checked = CURRENT_TIMESTAMP WHERE domain = ? AND path = ?",
            (new_checksum, new_mtime, domain, path)
        )
        # For ADDED files not yet in baseline, insert them
        cursor.execute(
            "INSERT OR IGNORE INTO file_integrity (domain, path, checksum, mtime) VALUES (?, ?, ?, ?)",
            (domain, path, new_checksum, new_mtime)
        )
        cursor.execute(
            "DELETE FROM integrity_logs WHERE domain = ? AND path = ?",
            (domain, path)
        )
        conn.commit()
    except Exception as e:
        conn.close()
        raise HTTPException(status_code=400, detail=str(e))
    conn.close()

    # Refresh local cache in background (non-fatal if it fails)
    try:
        conn2 = get_db()
        cursor2 = conn2.cursor()
        cursor2.execute("SELECT path FROM domain_configs WHERE domain = ?", (domain,))
        row = cursor2.fetchone()
        conn2.close()
        if row:
            root = row['path'] if row['path'].startswith('/') else '/' + row['path']
            full_path = os.path.join(root, path)
            scanner = IntegrityScanner()
            ftp = get_ftp_client()
            scanner.cache_file(domain, path, ftp, 'ftp', full_path)
    except Exception as e:
        print(f"[Accept] Cache-Update fehlgeschlagen (nicht kritisch): {e}")

    return {"status": "success"}


@app.get("/stats/config")
async def get_config():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT domain, path FROM domain_configs")
    configs = {row['domain']: row['path'] for row in cursor.fetchall()}
    conn.close()
    return configs

@app.post("/stats/config")
async def update_config(data: Dict):
    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "INSERT OR REPLACE INTO domain_configs (domain, path) VALUES (?, ?)",
            (data['domain'], data['path'])
        )
        conn.commit()
    except Exception as e:
        conn.close()
        raise HTTPException(status_code=400, detail=str(e))
    conn.close()
    return {"status": "success"}

@app.get("/stats/remote_ls")
async def remote_ls(path: str = "/"):
    try:
        entries = []
        ftp = get_ftp_client()
        # Try MLSD first (modern FTP)
        try:
            for name, facts in ftp.mlsd(path):
                if name in ('.', '..'): continue
                entries.append({
                    "name": name,
                    "type": "dir" if facts.get('type') == 'dir' else "file",
                    "path": os.path.join(path, name)
                })
        except:
            # Fallback to NLST + CWD checks
            for name in ftp.nlst(path):
                basename = os.path.basename(name)
                if basename in ('.', '..'): continue
                is_dir = False
                try:
                    ftp.cwd(os.path.join(path, basename))
                    is_dir = True
                    ftp.cwd('..')
                except:
                    pass
                entries.append({
                    "name": basename,
                    "type": "dir" if is_dir else "file",
                    "path": os.path.join(path, basename)
                })
        return sorted(entries, key=lambda x: (x['type'] != 'dir', x['name'].lower()))
    except Exception as e:
        print(f"Remote LS failed: {e}")
        raise HTTPException(status_code=400, detail=str(e))

@app.get("/stats/suggest_paths")
async def suggest_paths():
    """Attempts to find matching folders for all domains on the remote server via FTP."""
    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM settings WHERE key = 'base_path'")
        row = cursor.fetchone()
        custom_base = row['value'] if row else None
        
        cursor.execute("SELECT DISTINCT domain FROM logs")
        domains = [row['domain'] for row in cursor.fetchall()]
        conn.close()
        
        suggestions = {}
        search_roots = [custom_base] if custom_base else ["/public_html", "/var/www/vhosts"]
        
        ftp = get_ftp_client()
        for root in search_roots:
            if not root: continue
            try:
                ftp.cwd(root)
                raw_dirs = ftp.nlst()
                available_dirs = [os.path.basename(d.strip('./').strip('/')) for d in raw_dirs]
                for i, dir_name in enumerate(available_dirs):
                    raw_name = raw_dirs[i]
                    for domain in domains:
                        if domain.lower() in dir_name.lower():
                            domain_path = os.path.join(root, raw_name)
                            try:
                                ftp.cwd(domain_path)
                                subdirs = [os.path.basename(s.strip('./').strip('/')) for s in ftp.nlst()]
                                if "httpdocs" in subdirs:
                                    suggestions[domain] = os.path.join(domain_path, "httpdocs")
                                elif "www" in subdirs:
                                    suggestions[domain] = os.path.join(domain_path, "www")
                                else:
                                    suggestions[domain] = domain_path
                            except:
                                suggestions[domain] = domain_path
            except Exception as e:
                print(f"FTP Suggest error for {root}: {e}")
                continue
                
        return suggestions
    except Exception as e:
        print(f"Path suggestion failed: {e}")
        raise HTTPException(status_code=400, detail=str(e))

@app.get("/stats/test_connection")
async def test_connection():
    try:
        close_ftp_client()
        client = get_ftp_client()
        client.pwd()
        return {"status": "success", "message": "FTP/FTPS Verbindung erfolgreich hergestellt!"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@app.post("/stats/ignore_error")
async def ignore_error(data: Dict):
    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "INSERT OR IGNORE INTO ignored_errors (domain, path, status) VALUES (?, ?, ?)",
            (data['domain'], data['path'], data['status'])
        )
        conn.commit()
    except Exception as e:
        conn.close()
        raise HTTPException(status_code=400, detail=str(e))
    conn.close()
    return {"status": "success"}

@app.get("/stats/ignored")
async def get_ignored():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM ignored_errors")
    data = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return data

@app.delete("/stats/ignore_error")
async def unignore_error(domain: str, path: str, status: int):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        "DELETE FROM ignored_errors WHERE domain = ? AND path = ? AND status = ?",
        (domain, path, status)
    )
    conn.commit()
    conn.close()
    return {"status": "success"}

@app.get("/stats/security_audit")
async def get_security_audit(domain: str = None):
    conn = get_db()
    cursor = conn.cursor()
    
    # Common attack patterns
    patterns = [
        ('%../%', 'Path Traversal'),
        ('%.env%', 'Config Access'),
        ('%union select%', 'SQL Injection'),
        ('%<script%', 'XSS Attempt'),
        ('%.git%', 'Git Repo Access'),
        ('%wp-config%', 'WP Config Access'),
        ('%.php%', 'PHP Script Scan (Non-PHP site)'),
        ('%id_rsa%', 'SSH Key Access')
    ]
    
    audit_results = []
    for pattern, label in patterns:
        query = f"SELECT ip, country_code, path, COUNT(*) as count FROM logs WHERE path LIKE ? AND NOT EXISTS (SELECT 1 FROM security_false_positives sfp WHERE sfp.domain = logs.domain AND sfp.path = logs.path)"
        clause, ip_params = get_combined_filter(domain, existing_where=True)
        query += clause
        params = [pattern] + ip_params
        query += " GROUP BY ip, path ORDER BY count DESC LIMIT 10"
        
        cursor.execute(query, params)
        results = cursor.fetchall()
        for r in results:
            # If a specific domain was requested, use it; otherwise, we'd need to fetch it from the log row
            # But the query already groups by ip, path. Let's add domain to the query.
            audit_results.append({
                "domain": domain or "Global", 
                "ip": r["ip"],
                "country": r["country_code"],
                "path": r["path"],
                "count": r["count"],
                "threat_type": label
            })
            
    conn.close()
    return sorted(audit_results, key=lambda x: x['count'], reverse=True)

@app.get("/stats/security/false_positives")
async def get_false_positives():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT domain, path FROM security_false_positives ORDER BY domain, path")
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return rows

@app.post("/stats/security/false_positives/toggle")
async def toggle_false_positive(data: Dict):
    domain = data.get('domain')
    path = data.get('path')
    if not domain or not path:
        raise HTTPException(status_code=400, detail="Domain and path required")
        
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT 1 FROM security_false_positives WHERE domain = ? AND path = ?", (domain, path))
    if cursor.fetchone():
        cursor.execute("DELETE FROM security_false_positives WHERE domain = ? AND path = ?", (domain, path))
        action = "removed"
    else:
        cursor.execute("INSERT INTO security_false_positives (domain, path) VALUES (?, ?)", (domain, path))
        action = "added"
    conn.commit()
    conn.close()
    return {"status": "success", "action": action}

@app.post("/stats/security/identify")
async def identify_false_positives():
    scanner = IntegrityScanner()
    scanner.identify_security_false_positives()
    return {"status": "success", "message": "Audit-Abgleich mit Baseline gestartet."}

@app.get("/stats/peak_hours")
async def get_peak_hours(domain: str = None):
    conn = get_db()
    cursor = conn.cursor()
    query = "SELECT STRFTIME('%H', timestamp) as hour, COUNT(*) as count FROM logs"
    clause, params = get_combined_filter(domain)
    query += clause
    query += " GROUP BY hour ORDER BY hour ASC"
    
    cursor.execute(query, params)
    data = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return data

@app.get("/stats/bandwidth")
async def get_bandwidth(domain: str = None):
    conn = get_db()
    cursor = conn.cursor()
    query = "SELECT path, SUM(size) as total_size FROM logs"
    clause, params = get_combined_filter(domain)
    query += clause
    query += " GROUP BY path ORDER BY total_size DESC LIMIT 10"
    
    cursor.execute(query, params)
    data = []
    for row in cursor.fetchall():
        data.append({
            "path": row["path"],
            "size_mb": round(row["total_size"] / (1024 * 1024), 2)
        })
    conn.close()
    return data

@app.get("/stats/user_agents_detail")
async def get_user_agents_detail(domain: str = None):
    conn = get_db()
    cursor = conn.cursor()
    
    # OS distribution
    query_os = "SELECT os, COUNT(*) as count FROM logs"
    # Bot vs Human
    query_bot = "SELECT is_bot, COUNT(*) as count FROM logs"
    
    clause, params = get_combined_filter(domain)
    query_os += clause
    query_bot += clause
        
    query_os += " GROUP BY os ORDER BY count DESC LIMIT 5"
    query_bot += " GROUP BY is_bot"
    
    cursor.execute(query_os, params)
    os_data = [dict(row) for row in cursor.fetchall()]
    
    cursor.execute(query_bot, params)
    bot_data = [dict(row) for row in cursor.fetchall()]
    
    conn.close()
    return {
        "os": os_data,
        "bot_vs_human": bot_data
    }

@app.get("/stats/referrers")
async def get_referrers(domain: str = None):
    conn = get_db()
    cursor = conn.cursor()
    # Filter out internal referrers and empty ones
    query = "SELECT referrer, COUNT(*) as count FROM logs WHERE referrer != '-' AND referrer NOT LIKE '%yads-security%'"
    clause, params = get_combined_filter(domain, existing_where=True)
    query += clause
    query += " GROUP BY referrer ORDER BY count DESC LIMIT 10"
    
    cursor.execute(query, params)
    data = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return data

@app.get("/stats/file_types")
async def get_file_types(domain: str = None):
    conn = get_db()
    cursor = conn.cursor()
    # Extract extension from path
    query = """
        SELECT 
            CASE 
                WHEN path LIKE '%.html' THEN 'HTML'
                WHEN path LIKE '%.js' THEN 'JS'
                WHEN path LIKE '%.css' THEN 'CSS'
                WHEN path LIKE '%.png' OR path LIKE '%.jpg' OR path LIKE '%.gif' OR path LIKE '%.svg' THEN 'Images'
                WHEN path LIKE '%.php' THEN 'PHP'
                WHEN path LIKE '%.json' THEN 'JSON/API'
                WHEN path LIKE '%.zip' OR path LIKE '%.sig' THEN 'Releases'
                ELSE 'Other'
            END as type,
            COUNT(*) as count 
        FROM logs
    """
    clause, params = get_combined_filter(domain)
    query += clause
    query += " GROUP BY type ORDER BY count DESC"
    
    cursor.execute(query, params)
    data = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return data

@app.get("/stats/validator")
async def get_validator_results(domain: Optional[str] = None):
    conn = get_db()
    cursor = conn.cursor()
    
    # Get errors (404/500) from logs — excluding security false-positives
    query = """
        SELECT domain, path, status, COUNT(*) as count 
        FROM logs 
        WHERE status IN (404, 500)
        AND NOT EXISTS (SELECT 1 FROM security_false_positives sfp WHERE sfp.domain = logs.domain AND sfp.path = logs.path)
    """
    params = []
    if domain:
        query += " AND domain = ?"
        params.append(domain)
    
    query += " GROUP BY domain, path, status ORDER BY count DESC LIMIT 100"
    
    cursor.execute(query, params)
    errors = [dict(row) for row in cursor.fetchall()]
    
    results = []
    for err in errors:
        # Check if file is known in integrity DB
        rel_path = err['path'].lstrip('/')
        cursor.execute(
            "SELECT 1 FROM file_integrity WHERE domain = ? AND path = ?",
            (err['domain'], rel_path)
        )
        exists_in_integrity = cursor.fetchone() is not None
        
        # Check if ignored
        cursor.execute(
            "SELECT 1 FROM ignored_errors WHERE domain = ? AND path = ? AND status = ?",
            (err['domain'], err['path'], err['status'])
        )
        is_ignored = cursor.fetchone() is not None
        
        results.append({
            **err,
            "exists_on_server": exists_in_integrity,
            "is_ignored": is_ignored
        })
        
    conn.close()
    return results

@app.get("/stats/releases")
async def get_releases(domain: str = None):
    releases_path = os.path.join(os.path.dirname(__file__), "..", "data", "releases.json")
    if not os.path.exists(releases_path):
        return []
    
    with open(releases_path, 'r') as f:
        data = json.load(f)
    
    if domain == "yads-security-combined":
        # Return releases for the first domain in the group (they are likely identical)
        return data.get(YADS_DOMAINS[0], [])
    elif domain and domain in data:
        return data[domain]
    elif not domain:
        # Flatten all releases if no domain specified
        all_releases = []
        for d in data:
            all_releases.extend(data[d])
        return all_releases
    return []


@app.get("/stats/releases/status")
async def get_releases_status():
    """Latest YADS release + how long ago that was, for a stalled-cadence indicator."""
    releases_path = os.path.join(os.path.dirname(__file__), "..", "data", "releases.json")
    if not os.path.exists(releases_path):
        return {"latest_version": None, "latest_date": None, "days_since_last_release": None}

    with open(releases_path, 'r') as f:
        data = json.load(f)

    releases = data.get(YADS_DOMAINS[0], [])
    if not releases:
        return {"latest_version": None, "latest_date": None, "days_since_last_release": None}

    latest = max(releases, key=lambda r: r["date"])
    days_since = (datetime.now().date() - datetime.strptime(latest["date"], "%Y-%m-%d").date()).days

    return {
        "latest_version": latest["version"],
        "latest_date": latest["date"],
        "days_since_last_release": days_since,
    }

@app.get("/stats/release_downloads")
async def get_release_downloads(domain: str = None):
    conn = get_db()
    cursor = conn.cursor()
    
    # We filter by /releases/ and .zip
    clause, params = get_combined_filter(domain, existing_where=True)
    
    query = f"""
        SELECT path, country_code, COUNT(*) as count 
        FROM logs 
        WHERE (path LIKE '/releases/%.zip' OR path LIKE '%yads_v%_customer_pkg.zip')
        {clause}
        GROUP BY path, country_code 
        ORDER BY path ASC, count DESC
    """
    
    cursor.execute(query, params)
    rows = cursor.fetchall()
    
    # Structure the data by path
    results = {}
    for row in rows:
        path = row['path']
        if path not in results:
            results[path] = []
        results[path].append({
            "country": row['country_code'],
            "count": row['count']
        })
        
    conn.close()
    return results


_VERSION_IN_PATH_RE = re.compile(r'v(\d+\.\d+(?:\.\d+)?)')


def _load_release_dates() -> dict:
    """version -> release date (YYYY-MM-DD), from data/releases.json."""
    releases_path = os.path.join(os.path.dirname(__file__), "..", "data", "releases.json")
    if not os.path.exists(releases_path):
        return {}
    with open(releases_path, 'r') as f:
        data = json.load(f)
    return {r["version"]: r["date"] for r in data.get(YADS_DOMAINS[0], [])}


@app.get("/stats/release_adoption")
async def get_release_adoption(domain: str = None):
    """Per-release adoption: total downloads, first-download lag after release, top countries."""
    conn = get_db()
    cursor = conn.cursor()
    clause, params = get_combined_filter(domain, existing_where=True)

    query = f"""
        SELECT path, country_code, timestamp
        FROM logs
        WHERE (path LIKE '/releases/%.zip' OR path LIKE '%yads_v%_customer_pkg.zip')
        {clause}
    """
    cursor.execute(query, params)
    rows = cursor.fetchall()
    conn.close()

    per_version = {}
    for row in rows:
        m = _VERSION_IN_PATH_RE.search(row['path'])
        if not m:
            continue
        version = f"v{m.group(1)}"
        entry = per_version.setdefault(version, {"count": 0, "first_download": None, "countries": {}})
        entry["count"] += 1
        cc = row['country_code'] or "Unknown"
        entry["countries"][cc] = entry["countries"].get(cc, 0) + 1
        ts = row['timestamp']
        if ts and (entry["first_download"] is None or ts < entry["first_download"]):
            entry["first_download"] = ts

    release_dates = _load_release_dates()

    result = []
    for version, entry in per_version.items():
        release_date = release_dates.get(version)
        days_to_first = None
        if release_date and entry["first_download"]:
            try:
                rd = datetime.strptime(release_date, "%Y-%m-%d").date()
                fd = datetime.strptime(entry["first_download"][:10], "%Y-%m-%d").date()
                days_to_first = (fd - rd).days
            except ValueError:
                pass
        top_countries = dict(sorted(entry["countries"].items(), key=lambda kv: -kv[1])[:10])
        result.append({
            "version": version,
            "release_date": release_date,
            "total_downloads": entry["count"],
            "first_download": entry["first_download"],
            "days_to_first_download": days_to_first,
            "countries": top_countries,
        })

    result.sort(key=lambda r: r["release_date"] or "", reverse=True)
    return result


@app.get("/stats/release_version_churn")
async def get_release_version_churn(domain: str = None):
    """Monthly download counts per version - shows whether people move off old versions."""
    conn = get_db()
    cursor = conn.cursor()
    clause, params = get_combined_filter(domain, existing_where=True)

    query = f"""
        SELECT path, timestamp
        FROM logs
        WHERE (path LIKE '/releases/%.zip' OR path LIKE '%yads_v%_customer_pkg.zip')
        {clause}
    """
    cursor.execute(query, params)
    rows = cursor.fetchall()
    conn.close()

    # month -> version -> count
    by_month = {}
    for row in rows:
        m = _VERSION_IN_PATH_RE.search(row['path'])
        ts = row['timestamp']
        if not m or not ts:
            continue
        version = f"v{m.group(1)}"
        month = ts[:7]  # YYYY-MM
        by_month.setdefault(month, {}).setdefault(version, 0)
        by_month[month][version] += 1

    return dict(sorted(by_month.items()))


_REGISTRY_MANIFEST_RE = re.compile(r'^/v2/(?P<image>.+)/manifests/(?P<tag>[^/]+)$')


@app.get("/stats/registry/images")
async def get_registry_images():
    """
    Docker pull breakdown by image + tag, parsed from registry manifest requests
    (GET /v2/<image>/manifests/<tag>) - this is the request Docker issues per `docker pull`
    to resolve a tag, so it's the right signal for "who pulled what version", unlike blob
    requests which are supporting data (layers) and often digest-addressed, not tag-addressed.
    """
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT path, country_code FROM logs WHERE domain = ? AND path LIKE '/v2/%/manifests/%'",
        (REGISTRY_DOMAIN,)
    )
    rows = cursor.fetchall()
    conn.close()

    per_image = {}
    for row in rows:
        m = _REGISTRY_MANIFEST_RE.match(row['path'])
        if not m:
            continue
        tag = m.group('tag')
        if tag.startswith('sha256:'):
            continue  # digest-pinned pull, not a human-meaningful tag
        image = m.group('image')
        entry = per_image.setdefault(image, {})
        tag_entry = entry.setdefault(tag, {"count": 0, "countries": {}})
        tag_entry["count"] += 1
        cc = row['country_code'] or "Unknown"
        tag_entry["countries"][cc] = tag_entry["countries"].get(cc, 0) + 1

    result = []
    for image, tags in per_image.items():
        tag_list = [
            {"tag": tag, "count": t["count"],
             "countries": dict(sorted(t["countries"].items(), key=lambda kv: -kv[1])[:5])}
            for tag, t in tags.items()
        ]
        tag_list.sort(key=lambda t: -t["count"])
        result.append({"image": image, "total_pulls": sum(t["count"] for t in tag_list), "tags": tag_list})

    result.sort(key=lambda r: -r["total_pulls"])
    return result


@app.get("/stats/export-static")
async def export_static(domain: Optional[str] = None, tab: Optional[str] = None):
    # 1. Gather all data for the requested domain
    from src.main import get_overview, get_geo_stats, get_top_pages, get_timeseries, \
                         get_browsers, get_errors_detail, get_error_ips, get_security_audit, \
                         get_releases, get_release_downloads, get_peak_hours, get_bandwidth, \
                         get_user_agents_detail, get_referrers, get_file_types, get_monitoring_status, \
                         get_domains

    d = domain # shortcut
    
    # Map of URL -> Data
    static_data = {}
    
    async def add_to_mock(endpoint, func, *args, **kwargs):
        base_url = f"/stats/{endpoint}"
        try:
            res = await func(*args, **kwargs)
            # Anonymize if it's security or error IPs
            if endpoint in ("security_audit", "error_ips"):
                for row in res:
                    if "ip" in row: row["ip"] = anonymize_ip(row["ip"])
            
            # Store under every URL key that app.js might request
            # 1. No domain param (fallback)
            static_data[base_url] = res
            # 2. With the real domain param
            if d:
                static_data[f"{base_url}?domain={d}"] = res
            # 3. With the 'yads-security-combined' alias (app.js auto-selects this)
            static_data[f"{base_url}?domain=yads-security-combined"] = res
        except Exception as e:
            print(f"Export Error ({endpoint}): {e}")
            static_data[base_url] = []

    await add_to_mock("overview", get_overview, d)
    await add_to_mock("geo", get_geo_stats, d)
    await add_to_mock("pages", get_top_pages, d)
    await add_to_mock("timeseries", get_timeseries, d)
    await add_to_mock("browsers", get_browsers, d)
    await add_to_mock("errors_detail", get_errors_detail, d)
    await add_to_mock("error_ips", get_error_ips, d)
    await add_to_mock("security_audit", get_security_audit, d)
    await add_to_mock("releases", get_releases, d)
    await add_to_mock("release_downloads", get_release_downloads, d)
    await add_to_mock("peak_hours", get_peak_hours, d)
    await add_to_mock("bandwidth", get_bandwidth, d)
    await add_to_mock("user_agents_detail", get_user_agents_detail, d)
    await add_to_mock("referrers", get_referrers, d)
    await add_to_mock("file_types", get_file_types, d)

    static_data["/stats/monitoring/status"] = await get_monitoring_status()
    static_data["/stats/domains"] = await get_domains()
    static_data["/stats/config"] = {}
    static_data["/stats/settings"] = {}

    # 2. Read assets
    ui_dir = os.path.join(os.path.dirname(__file__), "web")
    index_path = os.path.join(ui_dir, "index.html")
    style_path = os.path.join(ui_dir, "style.css")
    app_path = os.path.join(ui_dir, "app.js")
    logo_path = os.path.join(ui_dir, "logo.png")
    favicon_path = os.path.join(ui_dir, "favicon.png")
    
    with open(index_path, 'r') as f: index_html = f.read()
    with open(style_path, 'r') as f: style_css = f.read()
    with open(app_path, 'r') as f: app_js = f.read()
    
    import base64
    logo_base64 = ""
    if os.path.exists(logo_path):
        with open(logo_path, "rb") as f:
            logo_base64 = f"data:image/png;base64,{base64.b64encode(f.read()).decode()}"
            
    favicon_base64 = ""
    if os.path.exists(favicon_path):
        with open(favicon_path, "rb") as f:
            favicon_base64 = f"data:image/png;base64,{base64.b64encode(f.read()).decode()}"

    # 3. Assemble Static Export
    import re
    index_html = index_html.replace('<link rel="stylesheet" href="style.css">', f'<style>{style_css}</style>')
    
    # Inline Chart.js for offline compatibility
    chart_js_content = ""
    try:
        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        chart_js_tmp_path = os.path.join(repo_root, "scratch", "chart.js.tmp")
        with open(chart_js_tmp_path, "r") as f:
            chart_js_content = f.read()
    except:
        pass
        
    if chart_js_content:
        index_html = index_html.replace('<script src="lib/chart.js"></script>', f'<script>{chart_js_content}</script>')
    else:
        index_html = index_html.replace('<script src="lib/chart.js"></script>', '<script src="https://cdn.jsdelivr.net/npm/chart.js" crossorigin="anonymous"></script>')
    
    # Robust asset inlining
    if logo_base64:
        index_html = re.sub(r'src=["\']logo\.png["\']', f'src="{logo_base64}"', index_html)
    if favicon_base64:
        index_html = re.sub(r'href=["\']favicon\.png["\']', f'href="{favicon_base64}"', index_html)

    target_tab = tab or "overview"
    tab_labels = {
        "overview": "Übersicht", "errors": "Fehler-Analyse", "security": "Security Audit",
        "releases": "YADS Releases", "insights": "Deep Insights", "monitoring": "Monitoring",
        "validator": "Validator", "integrity": "Integrität", "config": "Konfiguration"
    }
    target_tab_label = tab_labels.get(target_tab, target_tab.capitalize())

    mock_script = f"""
    <script>
    window.staticData = {json.dumps(static_data)};
    window.isStaticExport = true;
    window.targetTab = "{target_tab}";
    
    // Polyfill fetch BEFORE any other scripts load
    (function() {{
        const originalFetch = window.fetch;
        window.fetch = async (url, options) => {{
            let mockKey = url;
            if (typeof url === 'string') {{
                if (url.includes('://')) {{
                    try {{
                        const u = new URL(url);
                        mockKey = u.pathname + u.search;
                    }} catch(e) {{}}
                }} else {{
                    // Handle relative paths or paths starting with /
                    mockKey = url.split('#')[0];
                }}
            }}
            
            // Normalize leading slash
            if (!mockKey.startsWith('/')) mockKey = '/' + mockKey;
            
            // Try different key combinations to be robust
            const keysToTry = [
                mockKey,
                mockKey.replace('/stats/', '/stats'), // maybe missing slash
                mockKey.startsWith('/stats') ? mockKey : '/stats' + mockKey
            ];
            
            for (const key of keysToTry) {{
                if (window.staticData[key]) {{
                    return new Response(JSON.stringify(window.staticData[key]), {{
                        status: 200,
                        headers: {{ 'Content-Type': 'application/json' }}
                    }});
                }}
            }}
            
            console.warn("Static Export: Missing mock for", url, "(Key: " + mockKey + ")");
            return new Response(JSON.stringify({{ error: "Not found in export" }}), {{
                status: 404,
                headers: {{ 'Content-Type': 'application/json' }}
            }});
        }};
    }})();
    
    // UI fixing function
    function applyStaticFixes() {{
        // Hide domain selector instead of removing it (to avoid JS errors in app.js)
        const domainSelectEl = document.getElementById('domain-select');
        if (domainSelectEl) {{
            domainSelectEl.style.display = 'none';
            // Also hide the label if it's in a container
            if (domainSelectEl.parentElement && domainSelectEl.parentElement.classList.contains('select-wrapper')) {{
                domainSelectEl.parentElement.style.display = 'none';
            }}
        }}
        
        // Navigation and Tab visibility
        const nav = document.querySelector('nav.tabs');
        if (nav) nav.style.display = 'none';
        
        document.querySelectorAll('.tab-content').forEach(t => {{
            t.classList.remove('active');
            t.style.display = 'none';
        }});
        
        const targetTabEl = document.getElementById(window.targetTab + '-tab');
        if (targetTabEl) {{
            targetTabEl.classList.add('active');
            targetTabEl.style.display = 'block';
        }}

        // Also update the active button so app.js knows which data to load
        document.querySelectorAll('.tab-btn').forEach(btn => {{
            if (btn.getAttribute('data-tab') === window.targetTab) {{
                btn.classList.add('active');
            }} else {{
                btn.classList.remove('active');
            }}
        }});

        // Force app.js to reload data for this specific tab now that the DOM is patched
        if (typeof refreshTabData === 'function') {{
            refreshTabData(window.targetTab, window.exportDomain || 'yads-security-combined');
        }}

        // Disable server-only features
        const serverOnly = [
            '#run-validator', '#suggest-all-btn', '#connection-settings-form',
            '#path-settings-form', '#ip-filter-section', '.save-btn',
            '.ignore-btn', '#start-scan-btn', '#preload-btn', '#baseline-scan-btn'
        ];
        serverOnly.forEach(sel => {{
            document.querySelectorAll(sel).forEach(el => {{
                el.style.opacity = '0.5';
                el.style.pointerEvents = 'none';
            }});
        }});

        const expBtn = document.getElementById('export-btn');
        if (expBtn) expBtn.style.display = 'none';

        const h1 = document.querySelector('header h1');
        if (h1 && !h1.dataset.fixed) {{
            h1.innerHTML += ` <span style="font-size: 0.8rem; vertical-align: middle; opacity: 0.5;">{domain or 'Alle Domains'} &mdash; {target_tab_label}</span>`;
            h1.dataset.fixed = "true";
        }}
    }}

    // Run fixes after everything is loaded to ensure we override app.js behavior
    window.addEventListener('load', applyStaticFixes);
    // Also run on DOMContentLoaded with a small delay just in case
    document.addEventListener('DOMContentLoaded', () => setTimeout(applyStaticFixes, 100));
    </script>
    """
    
    index_html = index_html.replace('</head>', f'{mock_script}</head>')
    index_html = index_html.replace('<script src="app.js"></script>', f'<script>{app_js}</script>')
    
    filename = f"DomainStats_{target_tab.capitalize()}_{domain or 'Global'}_{int(time.time())}.html"
    return HTMLResponse(content=index_html, headers={"Content-Disposition": f"attachment; filename={filename}"})

# Serve static files for the UI
UI_DIR = os.path.join(os.path.dirname(__file__), "web")
if os.path.exists(UI_DIR):
    app.mount("/", StaticFiles(directory=UI_DIR, html=True), name="web")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
