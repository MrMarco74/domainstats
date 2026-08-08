import os
import sqlite3
from datetime import datetime, timedelta

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "logs.db")
OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "security_txt")

YADS_DOMAINS = ["yads-security.de", "yads-security.com"]
DEFAULT_EMAIL = "webmaster@highantdev.de"
YADS_EMAIL = "support@yads-security.com"

def generate():
    if not os.path.exists(DB_PATH):
        print("Fehler: logs.db nicht gefunden.")
        return

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    # Get domains from both configs and logs to be sure
    cursor.execute("SELECT domain FROM domain_configs")
    domains = set(row[0] for row in cursor.fetchall() if row[0])
    
    cursor.execute("SELECT DISTINCT domain FROM logs")
    for row in cursor.fetchall():
        if row[0]: domains.add(row[0])
    conn.close()

    # Ensure YADS domains are included even if not in logs yet
    for d in YADS_DOMAINS:
        if d not in domains:
            domains.append(d)

    expires = (datetime.now() + timedelta(days=365)).strftime('%Y-%m-%dT%H:%M:%S.000Z')
    
    print(f"Generiere security.txt für {len(domains)} Domains...")
    
    for domain in domains:
        email = YADS_EMAIL if domain in YADS_DOMAINS else DEFAULT_EMAIL
        
        content = f"""Contact: mailto:{email}
Expires: {expires}
Preferred-Languages: de, en
Canonical: https://{domain}/.well-known/security.txt
"""
        domain_dir = os.path.join(OUTPUT_DIR, domain)
        os.makedirs(domain_dir, exist_ok=True)
        
        with open(os.path.join(domain_dir, "security.txt"), "w") as f:
            f.write(content)
            
    print(f"Fertig! Dateien liegen in: {OUTPUT_DIR}")

if __name__ == "__main__":
    generate()
