import os
import paramiko
import logging
import struct
from src.db_utils import get_db

# Configuration
SFTP_HOST = os.getenv("SFTP_HOST", "ftp.example.com")
SFTP_USER = os.getenv("SFTP_USER", "changeme")
SFTP_PASS = os.getenv("SFTP_PASS", "")
KEY_PATH = os.getenv("SFTP_KEY_PATH", "/home/domainstats/.ssh/id_rsa")
KEY_PASS = os.getenv("SFTP_KEY_PASS", None)

# Singleton for SSH connection to avoid multiple auth attempts
_ssh_client = None

def get_ssh_client():
    global _ssh_client
    
    if _ssh_client is not None:
        try:
            # Check if connection is still alive
            _ssh_client.listdir('.')
            return _ssh_client
        except (paramiko.SSHException, struct.error, Exception):
            # Connection died or is in a weird state (like "unpack requires 4 bytes")
            print("DEBUG: SSH connection stale, clearing singleton.")
            try:
                _ssh_client.close()
            except:
                pass
            _ssh_client = None

    # Try to get passphrase from DB if not in Env
    current_key_pass = KEY_PASS
    if not current_key_pass:
        try:
            conn = get_db()
            cursor = conn.cursor()
            cursor.execute("SELECT value FROM settings WHERE key = 'key_pass'")
            row = cursor.fetchone()
            if row:
                current_key_pass = row['value']
            conn.close()
        except:
            pass

    print(f"DEBUG: Using SSH Key: {KEY_PATH} (Exists: {os.path.exists(KEY_PATH)})")
    if current_key_pass:
        print(f"DEBUG: Passphrase provided (Length: {len(current_key_pass)})")
    else:
        print("DEBUG: No passphrase provided.")

    print(f"DEBUG: Attempting SSH connection to {SFTP_HOST} as {SFTP_USER}...")
    
    try:
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        
        client.connect(
            hostname=SFTP_HOST, 
            port=22, 
            username=SFTP_USER, 
            password=SFTP_PASS,
            key_filename=KEY_PATH if os.path.exists(KEY_PATH) else None,
            passphrase=current_key_pass,
            timeout=10,
            banner_timeout=20,
            look_for_keys=False,
            allow_agent=False
        )
            
        _ssh_client = client.open_sftp()
        return _ssh_client
    except paramiko.AuthenticationException:
        print("CRITICAL: SSH Authentication failed!")
        raise Exception("SSH Authentication failed. Check user/pass/key.")
    except Exception as e:
        print(f"SSH Connection Error: {e}")
        raise e

def close_ssh_client():
    global _ssh_client
    if _ssh_client:
        try:
            _ssh_client.close()
        except:
            pass
        _ssh_client = None
