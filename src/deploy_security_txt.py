import os
import ftplib
import sys
# Add parent dir to path to allow importing src
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.ftp_utils import get_ftp_client

# Use settings from DB which are already handled in get_ftp_client
# but we need to know the base path
from src.db_utils import get_db

SECURITY_TXT_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "security_txt")

def deploy():
    client = get_ftp_client()
    if not client:
        print("Fehler: FTP-Verbindung fehlgeschlagen.")
        return

    # Get exact domain mappings from DB
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT domain, path FROM domain_configs")
    configs = {row['domain']: row['path'] for row in cursor.fetchall()}
    conn.close()

    if not os.path.exists(SECURITY_TXT_DIR):
        print(f"Fehler: Verzeichnis {SECURITY_TXT_DIR} nicht gefunden.")
        return

    domains = [d for d in os.listdir(SECURITY_TXT_DIR) if os.path.isdir(os.path.join(SECURITY_TXT_DIR, d))]
    
    for domain in domains:
        print(f"\n--- Domain: {domain} ---")
        
        target_base = configs.get(domain)
        if not target_base:
            print(f"Überspringe {domain}: Kein Pfad-Mapping in domain_configs gefunden.")
            continue
            
        # Standardize target_base (remove leading slash if present, as it's relative to FTP root)
        target_base = target_base.lstrip('/')
        well_known_dir = f"{target_base}/.well-known"
        
        # Safe directory check/creation
        try:
            # Check if .well-known already exists by trying to CWD into it
            client.cwd('/')
            client.cwd(well_known_dir)
        except:
            # If CWD fails, try to create it level by level but only if necessary
            client.cwd('/')
            parts = well_known_dir.split('/')
            current = ""
            for part in parts:
                current += part + "/"
                try:
                    client.mkd(current)
                    print(f"Verzeichnis erstellt: {current}")
                except:
                    pass # Already exists
        
        local_file = os.path.join(SECURITY_TXT_DIR, domain, "security.txt")
        target_file = f"{well_known_dir}/security.txt"
        
        try:
            with open(local_file, "rb") as f:
                client.storbinary(f"STOR {target_file}", f)
            print(f"Erfolgreich hochgeladen: {target_file}")
        except Exception as e:
            print(f"Fehler beim Upload für {domain}: {e}")

    client.quit()
    print("\nAlle Deployments abgeschlossen (Fix-Version).")

if __name__ == "__main__":
    deploy()
