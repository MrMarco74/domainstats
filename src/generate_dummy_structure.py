import os
import sqlite3
import shutil

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "logs.db")
SECURITY_TXT_SRC = os.path.join(os.path.dirname(__file__), "..", "data", "security_txt")
DUMMY_DIR = os.path.join(os.path.dirname(__file__), "..", "dummy_deploy")

def generate():
    if not os.path.exists(DB_PATH):
        print("Fehler: logs.db nicht gefunden.")
        return

    if os.path.exists(DUMMY_DIR):
        shutil.rmtree(DUMMY_DIR)
    
    os.makedirs(DUMMY_DIR)
    
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT domain, path FROM domain_configs")
    configs = cursor.fetchall()
    conn.close()

    print(f"Erzeuge Dummy-Struktur in: {DUMMY_DIR}")
    
    for row in configs:
        domain = row['domain']
        path = row['path'].lstrip('/')
        
        # Source file (already generated previously in data/security_txt/[domain]/security.txt)
        src_file = os.path.join(SECURITY_TXT_SRC, domain, "security.txt")
        if not os.path.exists(src_file):
            print(f"Warnung: Quell-Datei für {domain} nicht gefunden.")
            continue
            
        # Target path: dummy_deploy/[path]/.well-known/security.txt
        target_dir = os.path.join(DUMMY_DIR, path, ".well-known")
        os.makedirs(target_dir, exist_ok=True)
        
        target_file = os.path.join(target_dir, "security.txt")
        shutil.copy2(src_file, target_file)
        print(f"  -> {path}/.well-known/security.txt")

    print("\nDummy-Struktur erfolgreich erstellt.")

if __name__ == "__main__":
    generate()
