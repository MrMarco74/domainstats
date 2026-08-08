import os
import ftplib
import ssl
from src.db_utils import get_db

# Singleton for FTP connection
_ftp_client = None

def get_ftp_client():
    global _ftp_client
    
    if _ftp_client is not None:
        try:
            _ftp_client.pwd()
            return _ftp_client
        except:
            _ftp_client = None

    # Get credentials from DB
    host = os.getenv("FTP_HOST", "ftp.example.com")
    user = os.getenv("FTP_USER", "changeme")
    password = os.getenv("FTP_PASS", "")
    
    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("SELECT key, value FROM settings WHERE key IN ('ftp_host', 'ftp_user', 'ftp_pass')")
        rows = cursor.fetchall()
        settings = {row['key']: row['value'] for row in rows}
        conn.close()
        
        if settings.get('ftp_host'): host = settings['ftp_host']
        if settings.get('ftp_user'): user = settings['ftp_user']
        if settings.get('ftp_pass'): password = settings['ftp_pass']
    except:
        pass

    print(f"DEBUG: Attempting FTP connection to {host} as {user}...")
    
    try:
        # Enforce FTPS (Explicit TLS)
        print(f"DEBUG: Connecting to {host} via FTPS...")
        client = ftplib.FTP_TLS()
        client.connect(host, 21, timeout=10)
        client.auth() # Explicitly request TLS
        client.login(user, password)
        client.prot_p() # Switch to secure data connection
        _ftp_client = client
        print("DEBUG: FTPS connection established.")
        return _ftp_client
    except Exception as e:
        print(f"FTPS Connection Error: {e}")
        # If FTPS fails, we don't fallback to plain FTP as the server requires TLS
        raise e

def close_ftp_client():
    global _ftp_client
    if _ftp_client:
        try:
            _ftp_client.quit()
        except:
            try:
                _ftp_client.close()
            except:
                pass
        _ftp_client = None
