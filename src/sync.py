import os
import re
from datetime import datetime, timedelta
from src.ftp_utils import get_ftp_client
from src.db_utils import get_db

LOCAL_RAW_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "raw")

def sync_logs():
    if not os.path.exists(LOCAL_RAW_DIR):
        os.makedirs(LOCAL_RAW_DIR)

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT value FROM settings WHERE key = 'remote_log_path'")
    row = cursor.fetchone()
    remote_log_path = row['value'] if row else os.getenv("SFTP_REMOTE_DIR", "/www_logs")
    conn.close()

    try:
        ftp = get_ftp_client()
        print(f"Syncing logs via FTP from: {remote_log_path}")
        ftp.cwd(remote_log_path)

        for name, facts in ftp.mlsd():
            if name in ('.', '..'): continue
            if facts.get('type') == 'dir':
                domain = name
                local_domain_dir = os.path.join(LOCAL_RAW_DIR, domain)
                os.makedirs(local_domain_dir, exist_ok=True)
                _sync_domain_dir(ftp, f"{remote_log_path}/{domain}", domain, local_domain_dir)
            elif facts.get('type') == 'file':
                _sync_file_ftp(ftp, name, int(facts.get('size', -1)), LOCAL_RAW_DIR)

        print("Sync complete.")

    except Exception as e:
        error_msg = str(e)
        print(f"!!! CRITICAL SYNC ERROR: {error_msg} !!!")
        if "530" in error_msg or "denied" in error_msg.lower():
            print("CRITICAL: Authentication failed or Access Denied. Aborting to avoid lockout.")
            exit(99)
        exit(1)

def _sync_domain_dir(ftp, remote_dir, domain, local_dir):
    try:
        for name, facts in ftp.mlsd(remote_dir):
            if name in ('.', '..'): continue
            remote_path = f"{remote_dir}/{name}"
            local_path = os.path.join(local_dir, name)
            if facts.get('type') == 'dir':
                os.makedirs(local_path, exist_ok=True)
                _sync_domain_dir(ftp, remote_path, domain, local_path)
            else:
                _sync_file_ftp(ftp, remote_path, int(facts.get('size', -1)), local_dir, name)
    except Exception as e:
        print(f"Error syncing {remote_dir}: {e}")

def _sync_file_ftp(ftp, remote_path, remote_size, local_dir, filename=None):
    if filename is None:
        filename = os.path.basename(remote_path)

    # Skip historical log files older than retention window (180 days)
    match = re.search(r'(\d{4})[-_]?(\d{2})[-_]?(\d{2})', filename)
    if match:
        try:
            file_date = datetime(int(match.group(1)), int(match.group(2)), int(match.group(3)))
            cutoff = datetime.now() - timedelta(days=180)
            if file_date < cutoff:
                return
        except ValueError:
            pass

    local_file = os.path.join(local_dir, filename)
    if os.path.exists(local_file):
        if remote_size != -1 and os.path.getsize(local_file) == remote_size:
            return

    print(f"Downloading {remote_path} via FTP...")
    with open(local_file, 'wb') as f:
        ftp.retrbinary(f"RETR {remote_path}", f.write)

if __name__ == "__main__":
    sync_logs()
