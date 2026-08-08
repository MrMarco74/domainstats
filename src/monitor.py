import socket
import ssl
import requests
import datetime
import time
from src.db_utils import get_db

def check_uptime(domain):
    try:
        url = f"https://{domain}"
        start = time.time()
        res = requests.get(url, timeout=10, verify=False) # We check SSL separately
        latency = (time.time() - start) * 1000
        return True, int(latency), res.status_code
    except Exception as e:
        try:
            # Fallback to http
            url = f"http://{domain}"
            start = time.time()
            res = requests.get(url, timeout=10)
            latency = (time.time() - start) * 1000
            return True, int(latency), res.status_code
        except:
            return False, 0, 0

def check_ssl_expiry(domain):
    try:
        context = ssl.create_default_context()
        with socket.create_connection((domain, 443), timeout=5) as sock:
            with context.wrap_socket(sock, server_hostname=domain) as ssock:
                cert = ssock.getpeercert()
                # cert['notAfter'] format: 'May 20 12:00:00 2026 GMT'
                expiry_str = cert['notAfter']
                expiry_date = datetime.datetime.strptime(expiry_str, '%b %d %H:%M:%S %Y %Z')
                days_left = (expiry_date - datetime.datetime.now()).days
                return days_left
    except:
        return -1

def run_monitoring_cycle():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT domain FROM domain_configs")
    domains = [row['domain'] for row in cursor.fetchall()]
    conn.close()
    
    if not domains: return
    
    conn = get_db()
    cursor = conn.cursor()
    # Create table if not exists
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS monitoring (
            domain TEXT PRIMARY KEY,
            is_up INTEGER,
            latency INTEGER,
            status_code INTEGER,
            ssl_days INTEGER,
            last_check TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    for d in domains:
        up, latency, status = check_uptime(d)
        ssl_days = check_ssl_expiry(d)
        
        cursor.execute("""
            INSERT OR REPLACE INTO monitoring (domain, is_up, latency, status_code, ssl_days, last_check)
            VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        """, (d, 1 if up else 0, latency, status, ssl_days))
    
    conn.commit()
    conn.close()

def monitoring_loop():
    while True:
        try:
            run_monitoring_cycle()
        except Exception as e:
            print(f"Monitoring error: {str(e)}")
        time.sleep(1800) # Every 30 minutes
