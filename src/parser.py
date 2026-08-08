import os
import re
import gzip
import sqlite3
import json
import geoip2.database
from user_agents import parse
from datetime import datetime
from dateutil import parser as date_parser

# Configuration
DB_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "logs.db")
GEOIP_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "geoip", "GeoLite2-Country.mmdb")

# Domain suffix used to identify internal-only admin/tool hosts (hwmon, doku,
# llmproxy, ...) that should be excluded from traffic evaluation entirely.
# Empty by default (no domains excluded) — set to your own internal suffix,
# e.g. "INTERNAL_DOMAIN_SUFFIX=.internal.example.com".
INTERNAL_DOMAIN_SUFFIX = os.getenv("INTERNAL_DOMAIN_SUFFIX", "")

# Regex for Combined Log Format
# Example: 127.0.0.1 - frank [10/Oct/2000:13:55:36 -0700] "GET /apache_pb.gif HTTP/1.0" 200 2326 "http://www.example.com/start.html" "Mozilla/4.08 [en] (Win98; I ;Nav)"
LOG_PATTERN = re.compile(
    r'(?P<ip>\S+) \S+ \S+ \[(?P<timestamp>.*?)\] "(?P<method>\S+) (?P<path>.*?) \S+" (?P<status>\d+) (?P<size>\S+) "(?P<referrer>.*?)" "(?P<user_agent>.*?)"'
)

# Regex for the registry_stats log_format (anonymized pull stats from registry.yads-security.com)
# Example: 1.2.3.0 [09/Jun/2026:13:02:33 +0000] "GET /v2/yads/manifests/latest HTTP/1.1" 200 "Docker-Client/24.0.0" 1234
REGISTRY_PATTERN = re.compile(
    r'(?P<ip>\S+) \[(?P<timestamp>.*?)\] "(?P<method>\S+) (?P<path>.*?) \S+" (?P<status>\d+) "(?P<user_agent>.*?)" (?P<size>\S+)'
)
REGISTRY_DOMAIN = "registry.yads-security.com"

def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Logs table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            domain TEXT,
            ip TEXT,
            country_code TEXT,
            timestamp DATETIME,
            method TEXT,
            path TEXT,
            status INTEGER,
            size INTEGER,
            referrer TEXT,
            user_agent TEXT,
            browser TEXT,
            os TEXT,
            is_bot BOOLEAN,
            file_source TEXT
        )
    ''')
    
    # Processed files table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS processed_files (
            domain TEXT,
            filename TEXT,
            processed_at DATETIME,
            PRIMARY KEY (domain, filename)
        )
    ''')
    
    # Remove un-rotated active log files from processed_files so active log files are tracked properly by line counts
    cursor.execute("""
        DELETE FROM processed_files 
        WHERE filename IN ('access.log', 'error.log', 'registry_stats.log')
           OR (filename NOT LIKE '%.gz' AND filename NOT GLOB '*[0-9][0-9][0-9][0-9]*')
    """)

    conn.commit()
    return conn

def get_country(reader, ip):
    try:
        response = reader.country(ip)
        return response.country.iso_code
    except:
        return "Unknown"

def is_active_log_file(filename: str) -> bool:
    """
    Returns True if the file is an active, continuously growing un-rotated log file
    (e.g., access.log, error.log, registry_stats.log).
    Returns False if it is a completed, rotated archive file (e.g. ending in .gz or containing a date pattern).
    """
    if filename.endswith('.gz'):
        return False
    if re.search(r'\d{8}|\d{4}[-_]\d{2}[-_]\d{2}', filename):
        return False
    return True

def parse_log_file(file_path, domain, conn, geo_reader):
    filename = os.path.basename(file_path)
    cursor = conn.cursor()

    is_active = is_active_log_file(filename)
    lines_processed = 0

    if is_active:
        setting_key = f"lines_processed:{domain}:{filename}"
        if filename == "registry_stats.log":
            cursor.execute("SELECT value FROM settings WHERE key IN ('registry_stats_lines_processed', ?)", (setting_key,))
        else:
            cursor.execute("SELECT value FROM settings WHERE key = ?", (setting_key,))
        row = cursor.fetchone()
        lines_processed = int(row[0]) if row else 0
    else:
        # Check if already processed (using domain + filename for uniqueness)
        cursor.execute("SELECT 1 FROM processed_files WHERE domain = ? AND filename = ?", (domain, filename))
        if cursor.fetchone():
            return

    print(f"Processing {domain} - {filename}...")

    # Open file (handle .gz)
    if file_path.endswith('.gz'):
        f = gzip.open(file_path, 'rt', errors='ignore')
    else:
        f = open(file_path, 'r', errors='ignore')

    entries = []
    line_count = 0
    for line in f:
        line_count += 1
        if is_active and line_count <= lines_processed:
            continue
        data = None
        current_domain = domain
        is_traefik = False

        line = line.strip()
        if not line:
            continue

        if line.startswith('{'):
            try:
                jdata = json.loads(line)
                if 'RequestHost' in jdata:
                    # Traefik JSON access log (pvedmz)
                    is_traefik = True
                    data = {
                        'ip': jdata.get('ClientHost', '-'),
                        'timestamp': jdata.get('time', ''),
                        'method': jdata.get('RequestMethod', '-'),
                        'path': jdata.get('RequestPath', '-'),
                        'status': str(jdata.get('DownstreamStatus', '0')),
                        'size': str(jdata.get('DownstreamContentSize', '0')),
                        'referrer': jdata.get('request_Referer', '-'),
                        'user_agent': jdata.get('request_User-Agent', '-')
                    }
                    current_domain = jdata['RequestHost']
                elif 'request' in jdata:
                    parts = jdata['request'].split(' ')
                    method = parts[0] if len(parts) > 0 else '-'
                    path = parts[1] if len(parts) > 1 else '-'

                    data = {
                        'ip': jdata.get('remote_addr', '-'),
                        'timestamp': jdata.get('time_local', ''),
                        'method': method,
                        'path': path,
                        'status': str(jdata.get('status', '0')),
                        'size': str(jdata.get('body_bytes_sent', '0')),
                        'referrer': jdata.get('http_referrer', '-'),
                        'user_agent': jdata.get('http_user_agent', '-')
                    }
                    if jdata.get('host'):
                        current_domain = jdata['host']
            except json.JSONDecodeError:
                pass
                
        if not data and filename == "registry_stats.log":
            match = REGISTRY_PATTERN.match(line)
            if match:
                data = match.groupdict()
                data['referrer'] = '-'
                current_domain = REGISTRY_DOMAIN

        if not data:
            match = LOG_PATTERN.match(line)
            if match:
                data = match.groupdict()

        if data and INTERNAL_DOMAIN_SUFFIX and current_domain.endswith(INTERNAL_DOMAIN_SUFFIX):
            # Internal-only admin/tool hosts (hwmon, doku, llmproxy, ...) — not public
            # domains, exclude from traffic evaluation entirely.
            continue

        if data:
            # Basic parsing
            try:
                if is_traefik:
                    # Format: 2026-06-15T11:52:13Z (RFC3339)
                    dt = date_parser.parse(data['timestamp'])
                else:
                    # Format: 05/May/2026:07:31:06 +0200
                    dt = date_parser.parse(data['timestamp'].replace(':', ' ', 1))
            except:
                dt = datetime.now()
                
            status = int(data['status'])
            size = int(data['size']) if str(data['size']).isdigit() else 0
            
            # User Agent parsing
            ua = parse(data['user_agent'])
            
            # GeoIP
            country = get_country(geo_reader, data['ip'])
            
            entries.append((
                current_domain,
                data['ip'],
                country,
                dt,
                data['method'],
                data['path'],
                status,
                size,
                data['referrer'],
                data['user_agent'],
                ua.browser.family,
                ua.os.family,
                ua.is_bot,
                filename
            ))
            
            if len(entries) >= 500:
                cursor.executemany('''
                    INSERT INTO logs (domain, ip, country_code, timestamp, method, path, status, size, referrer, user_agent, browser, os, is_bot, file_source)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', entries)
                entries = []
                
    if entries:
        cursor.executemany('''
            INSERT INTO logs (domain, ip, country_code, timestamp, method, path, status, size, referrer, user_agent, browser, os, is_bot, file_source)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', entries)

    if is_active:
        setting_key = f"lines_processed:{domain}:{filename}"
        cursor.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
            (setting_key, str(line_count))
        )
        if filename == "registry_stats.log":
            cursor.execute(
                "INSERT OR REPLACE INTO settings (key, value) VALUES ('registry_stats_lines_processed', ?)",
                (str(line_count),)
            )
    else:
        cursor.execute("INSERT OR REPLACE INTO processed_files (domain, filename, processed_at) VALUES (?, ?, ?)", (domain, filename, datetime.now()))
    conn.commit()
    f.close()

def main():
    conn = init_db()
    
    try:
        geo_reader = geoip2.database.Reader(GEOIP_PATH)
    except Exception as e:
        print(f"Warning: Could not open GeoIP DB: {e}")
        geo_reader = None

    # Paths to look for logs
    log_dirs = [
        os.path.join(os.path.dirname(__file__), "..", "data", "initial_logs"),
        os.path.join(os.path.dirname(__file__), "..", "data", "raw"),
        "/var/log/nginx/archive",
        "/var/log/nginx"
    ]
    
    for log_dir in log_dirs:
        if not os.path.exists(log_dir):
            continue

        for root, dirs, files in os.walk(log_dir):
            # Extract domain from path
            rel_path = os.path.relpath(root, log_dir)
            domain = rel_path if rel_path != "." else "Unknown"

            for file in sorted(files):
                if ".log" in file or ".json" in file:
                    parse_log_file(os.path.join(root, file), domain, conn, geo_reader)

    # Registry pull stats (single, continuously growing file, lives directly in
    # /var/log/nginx/ rather than under a per-domain directory). Read directly when
    # this runs on the same host as the reverse-proxy (no scp needed).
    registry_stats_path = "/var/log/nginx/registry_stats.log"
    if os.path.exists(registry_stats_path):
        parse_log_file(registry_stats_path, "registry", conn, geo_reader)

    if geo_reader:
        geo_reader.close()
    conn.close()

if __name__ == "__main__":
    main()
