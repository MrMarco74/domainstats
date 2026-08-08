import os
import hashlib
import sqlite3
import time
from datetime import datetime
from src.db_utils import get_db
from src.ftp_utils import get_ftp_client
import stat
import gzip
import shutil
import hashlib

DEFAULT_BASE_PATH = "/var/www/vhosts/{domain}/httpdocs/"
SITE_ROOTS_FALLBACK = {
    "yads-security.de": "/var/www/vhosts/yads-security.de/httpdocs/",
    "domainstats.de": "/var/www/vhosts/domainstats.de/httpdocs/"
}

class IntegrityScanner:
    def __init__(self, progress_callback=None):
        self.progress_callback = progress_callback
        self.is_scanning = False
        self.stop_requested = False
        self.cache_dir = "/mnt/storage/caching"
        try:
            os.makedirs(self.cache_dir, exist_ok=True)
        except Exception as e:
            # Fallback to local if /mnt/storage is not writable
            self.cache_dir = "data/integrity_cache"
            os.makedirs(self.cache_dir, exist_ok=True)

    def is_cacheable(self, rel_path):
        """
        Entscheidet, ob eine Datei in den lokalen Offline-Cache aufgenommen wird.

        GECACHT (Whitelist):
          .php, .html, .htm  – Serverside-Code & Templates; primäres Angriffsziel für
                               Code-Injection und Webshell-Platzierung
          .js, .css          – Frontend-Assets; häufig für Supply-Chain-Angriffe und
                               Crypto-Miner-Injection missbraucht
          .htaccess          – Apache-Konfiguration; Manipulation ermöglicht Redirects,
                               Backdoors und Zugriffskontrolle auszuhebeln
          .txt, .md          – Textdokumente & READMEs; selten kritisch, aber
                               einfach zu cachen und wiederherstellbar
          .json, .yaml, .yml – Konfigurationsdateien; können Credentials oder
                               Deployment-Parameter enthalten
          .xml, .env, .conf  – (Kandidaten für Erweiterung, falls benötigt)

        NICHT GECACHT (Ausschlüsse):
          Bilder (.jpg, .png, .gif, .svg, .webp, .ico)
            → Großes Volumen, kaum Angriffsfläche; Wiederherstellung aus Backup
          Videos, Binärdateien (.mp4, .avi, .exe, .bin, .so)
            → Zu groß, nicht sinnvoll komprimierbar, kein Code-Injektionsrisiko
          Archive (.zip, .gz, .tar, .rar)
            → Bereits komprimiert; Gzip-Overhead ohne Nutzen
          Fonts (.woff, .woff2, .ttf, .otf, .eot)
            → Statisch, binär, kaum änderbar
          PDFs, Office-Dokumente (.pdf, .doc, .xls)
            → Binär, groß; Integritätsprüfung via SHA256-Baseline trotzdem möglich
          Dateien > 1 MB
            → Caching würde zu viel Speicher auf /mnt/storage/caching verbrauchen
               und den FTP-Download pro Datei zu langsam machen.
               Großdateien werden nur in der SHA256-Baseline geführt (kein lokaler Cache).

        Hinweis: Der SHA256-Hash wird für ALLE Dateien in der Baseline gespeichert
        (file_integrity-Tabelle), unabhängig von is_cacheable(). Der Cache dient nur
        zur lokalen Kopie für die Wiederherstellungsfunktion.
        """
        ext = os.path.splitext(rel_path)[1].lower()
        CACHEABLE_EXTENSIONS = {
            '.php', '.html', '.htm',   # Server-Code & Templates
            '.js', '.css',             # Frontend-Assets
            '.htaccess',               # Apache-Konfiguration
            '.txt', '.md',             # Textdokumente
            '.json', '.yaml', '.yml',  # Konfigurationsdateien
        }
        return ext in CACHEABLE_EXTENSIONS

    def clear_cache(self):
        if os.path.exists(self.cache_dir):
            shutil.rmtree(self.cache_dir)
        os.makedirs(self.cache_dir, exist_ok=True)
        
        # Also clear the database tables
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM integrity_logs")
        cursor.execute("DELETE FROM file_integrity")
        conn.commit()
        conn.close()
        
        self.log_progress("Cache und Datenbank-Baseline vollständig geleert.")

    def stop(self):
        self.stop_requested = True

    def log_progress(self, message):
        if self.progress_callback:
            self.progress_callback(message)
        print(f"[Integrity] {message}")

    def scan_all(self, auto_accept=False):
        self.is_scanning = True
        conn = get_db()
        cursor = conn.cursor()
        
        # Get all domains that have a config
        cursor.execute("SELECT domain, path FROM domain_configs")
        configs = cursor.fetchall()
        
        # Also check hardcoded defaults for YADS if not in config
        yads_domains = ["yads-security.de", "yads-security.com"]
        known_domains = [c['domain'] for c in configs]
        
        scan_list = []
        for c in configs:
            if not c['path']:
                continue
            scan_list.append((c['domain'], c['path']))
        
        for d in yads_domains:
            if d not in known_domains:
                # Fallback path logic
                from src.main import SITE_ROOTS_FALLBACK, DEFAULT_BASE_PATH
                path = SITE_ROOTS_FALLBACK.get(d, DEFAULT_BASE_PATH.format(domain=d))
                scan_list.append((d, path))

        self.log_progress(f"Starting integrity scan for {len(scan_list)} domains...")
        
        try:
            for domain, path in scan_list:
                if self.stop_requested:
                    self.log_progress("Scan by user cancelled.")
                    break
                try:
                    self.scan_domain(domain, path, auto_accept=auto_accept)
                except Exception as e:
                    error_msg = str(e)
                    self.log_progress(f"Error scanning {domain}: {error_msg}")
                    if "530" in error_msg or "denied" in error_msg.lower():
                        self.log_progress("CRITICAL: Authentication failed or Access Denied. ABORTING SCAN.")
                        self.is_scanning = False
                        return # Stop all further scans
        finally:
            self.identify_security_false_positives()
            self.is_scanning = False
            self.log_progress("Integrity scan finished.")
            conn.close()

    def identify_security_false_positives(self):
        """
        Cross-references 404 logs with the file_integrity database.
        If a 404 path is NOT in the integrity baseline, it's a false positive (scanner noise).
        """
        self.log_progress("Identifiziere Security False-Positives (404 Abgleich)...")
        conn = get_db()
        cursor = conn.cursor()
        
        # Find all paths that produced 404s but are NOT in our baseline
        # We look at recent logs to identify common scanner targets
        # Optimized: Use timestamp first (indexed), and EXISTS instead of IN
        cursor.execute("""
            INSERT OR IGNORE INTO security_false_positives (domain, path)
            SELECT DISTINCT domain, path FROM logs 
            WHERE timestamp > datetime('now', '-30 days')
            AND (
                status = 404 
                OR path LIKE '%wp-includes%'
                OR path LIKE '%.env%'
                OR path LIKE '%.git/config%'
            )
            AND NOT EXISTS (
                SELECT 1 FROM file_integrity fi 
                WHERE fi.domain = logs.domain AND fi.path = logs.path
            )
        """)
        added = cursor.rowcount
        conn.commit()
        conn.close()
        self.log_progress(f"Audit: {added} neue False-Positives identifiziert und ausgeblendet.")

    def scan_domain(self, domain, path, auto_accept=False):
        # Normalize: FTP paths must be absolute
        if not path.startswith('/'):
            path = '/' + path
        self.log_progress(f"Scanning {domain} at {path} via FTP...")
        client = get_ftp_client()
        current_files = self.remote_scan_ftp(domain, path, client)
        self.compare_and_log(domain, current_files, client, 'ftp', auto_accept=auto_accept)

    def remote_scan_ftp(self, domain, root_path, ftp):
        current_files = {}
        processed_count = 0
        
        def walk_ftp(path):
            if self.stop_requested: return
            nonlocal processed_count
            try:
                # Try MLSD for better metadata
                for name, facts in ftp.mlsd(path):
                    if name in ('.', '..'): continue
                    full_path = os.path.join(path, name)
                    if facts.get('type') == 'dir':
                        walk_ftp(full_path)
                    else:
                        processed_count += 1
                        if processed_count % 50 == 0:
                            self.log_progress(f"Processing FTP files... ({processed_count} found)")
                        mtime_str = facts.get('modify')
                        if mtime_str:
                            mtime = datetime.strptime(mtime_str, "%Y%m%d%H%M%S").timestamp()
                        else:
                            mtime = 0
                        rel_path = os.path.relpath(full_path, root_path)
                        current_files[rel_path] = {'mtime': mtime, 'full_path': full_path}
            except:
                for name in ftp.nlst(path):
                    if name in ('.', '..'): continue
                    full_path = os.path.join(path, name)
                    try:
                        mtime_str = ftp.sendcmd(f"MDTM {full_path}").split(' ')[1]
                        mtime = datetime.strptime(mtime_str, "%Y%m%d%H%M%S").timestamp()
                    except:
                        mtime = 0
                    
                    is_dir = False
                    try:
                        ftp.cwd(full_path)
                        is_dir = True
                        ftp.cwd('..')
                    except: pass
                    
                    if is_dir:
                        walk_ftp(full_path)
                    else:
                        rel_path = os.path.relpath(full_path, root_path)
                        current_files[rel_path] = {'mtime': mtime, 'full_path': full_path}

        walk_ftp(root_path)
        return current_files

    def compare_and_log(self, domain, current_files, client, conn_type, auto_accept=False):
        conn = get_db()
        cursor = conn.cursor()
        
        # Get stored files
        cursor.execute("SELECT path, checksum, mtime FROM file_integrity WHERE domain = ?", (domain,))
        stored_files = {row['path']: {'checksum': row['checksum'], 'mtime': row['mtime']} for row in cursor.fetchall()}
        
        self.log_progress(f"Comparing {len(current_files)} files for {domain}...")
        processed = 0
        
        new_records = []
        updates = []
        deletions = []
        
        for rel_path, info in current_files.items():
            if self.stop_requested: break
            processed += 1
            mtime = info['mtime']
            full_path = info['full_path']
            
            if rel_path not in stored_files:
                # New file!
                self.log_progress(f"New file detected: {rel_path}")
                checksum = self.get_remote_checksum(full_path, client, conn_type)
                cursor.execute(
                    "INSERT INTO file_integrity (domain, path, checksum, mtime) VALUES (?, ?, ?, ?)",
                    (domain, rel_path, checksum, mtime)
                )
                if not auto_accept:
                    cursor.execute(
                        "INSERT INTO integrity_logs (domain, path, change_type, new_checksum, new_mtime) VALUES (?, ?, 'added', ?, ?)",
                        (domain, rel_path, checksum, mtime)
                    )
                else:
                    # Auto-accept or baseline: cache the file
                    self.cache_file(domain, rel_path, client, conn_type, full_path)
            else:
                stored = stored_files[rel_path]
                if abs(stored['mtime'] - mtime) > 1: # 1s tolerance for float comparison
                    # Time changed, check checksum
                    self.log_progress(f"Verify: {rel_path}...")
                    new_checksum = self.get_remote_checksum(full_path, client, conn_type)
                    if new_checksum != stored['checksum']:
                        self.log_progress(f"ALERT: Integrity breach in {rel_path}!")
                        if not auto_accept:
                            cursor.execute(
                                "INSERT INTO integrity_logs (domain, path, change_type, old_checksum, new_checksum, old_mtime, new_mtime) VALUES (?, ?, 'modified', ?, ?, ?, ?)",
                                (domain, rel_path, stored['checksum'], new_checksum, stored['mtime'], mtime)
                            )
                        else:
                            # Auto-accept: update cache
                            self.cache_file(domain, rel_path, client, conn_type, full_path)
                        # Update the main record to current state
                        cursor.execute(
                            "UPDATE file_integrity SET checksum = ?, mtime = ?, last_checked = CURRENT_TIMESTAMP WHERE domain = ? AND path = ?",
                            (new_checksum, mtime, domain, rel_path)
                        )
                    else:
                        # Only time changed (e.g. touch or deploy without changes)
                        cursor.execute(
                            "UPDATE file_integrity SET mtime = ?, last_checked = CURRENT_TIMESTAMP WHERE domain = ? AND path = ?",
                            (mtime, domain, rel_path)
                        )
                else:
                    # Update last_checked
                    cursor.execute(
                        "UPDATE file_integrity SET last_checked = CURRENT_TIMESTAMP WHERE domain = ? AND path = ?",
                        (domain, rel_path)
                    )
        
        # Check for deleted files
        for rel_path in stored_files:
            if self.stop_requested: break
            if rel_path not in current_files:
                self.log_progress(f"File deleted: {rel_path}")
                cursor.execute(
                    "INSERT INTO integrity_logs (domain, path, change_type, old_checksum, old_mtime) VALUES (?, ?, 'deleted', ?, ?)",
                    (domain, rel_path, stored_files[rel_path]['checksum'], stored_files[rel_path]['mtime'])
                )
                cursor.execute(
                    "DELETE FROM file_integrity WHERE domain = ? AND path = ?",
                    (domain, rel_path)
                )
        
        conn.commit()
        conn.close()

    def get_remote_checksum(self, full_path, client, conn_type):
        if conn_type == 'ftp':
            # Download and hash
            local_tmp = f"/tmp/integrity_{hash(full_path)}_{int(time.time())}"
            try:
                with open(local_tmp, 'wb') as f:
                    client.retrbinary(f"RETR {full_path}", f.write)
                
                sha256_hash = hashlib.sha256()
                with open(local_tmp, "rb") as f:
                    for byte_block in iter(lambda: f.read(4096), b""):
                        sha256_hash.update(byte_block)
                return sha256_hash.hexdigest()
            except Exception as e:
                self.log_progress(f"Checksum error for {full_path}: {str(e)}")
                return "error"
            finally:
                if os.path.exists(local_tmp): os.remove(local_tmp)
        else:
            # Run sha256sum remotely
            stdin, stdout, stderr = client.exec_command(f"sha256sum \"{full_path}\"")
            output = stdout.read().decode().strip()
            if output:
                return output.split(' ')[0]
            return "error"

    def cache_file(self, domain, rel_path, client, conn_type, full_path):
        if not self.is_cacheable(rel_path):
            return False
            
        domain_cache = os.path.join(self.cache_dir, domain)
        os.makedirs(domain_cache, exist_ok=True)
        
        safe_name = rel_path.replace('/', '_')
        local_tmp = f"/tmp/cache_{hash(full_path)}_{int(time.time())}"
        gz_path = os.path.join(domain_cache, safe_name + ".gz")
        sha_path = os.path.join(domain_cache, safe_name + ".sha256")
        
        try:
            # 1. Download to tmp
            if conn_type == 'ftp':
                with open(local_tmp, 'wb') as f:
                    client.retrbinary(f"RETR {full_path}", f.write)
            else:
                sftp = client.open_sftp()
                sftp.get(full_path, local_tmp)
                sftp.close()
                
            # 2. Calculate SHA256 of raw file
            sha256_hash = hashlib.sha256()
            with open(local_tmp, "rb") as f:
                content = f.read()
                sha256_hash.update(content)
            checksum = sha256_hash.hexdigest()
            
            # 3. Gzip and save
            with open(local_tmp, 'rb') as f_in:
                with gzip.open(gz_path, 'wb') as f_out:
                    shutil.copyfileobj(f_in, f_out)
                    
            # 4. Save checksum
            with open(sha_path, 'w') as f:
                f.write(checksum)
                
            return True
        except Exception as e:
            self.log_progress(f"Cache error for {rel_path}: {str(e)}")
            return False
        finally:
            if os.path.exists(local_tmp): os.remove(local_tmp)

    def restore_from_cache(self, domain, rel_path, client, conn_type, remote_root):
        safe_name = rel_path.replace('/', '_')
        domain_cache = os.path.join(self.cache_dir, domain)
        gz_path = os.path.join(domain_cache, safe_name + ".gz")
        sha_path = os.path.join(domain_cache, safe_name + ".sha256")
        
        if not os.path.exists(gz_path) or not os.path.exists(sha_path):
            return False, "Datei nicht im Cache vorhanden."
            
        local_tmp = f"/tmp/restore_{hash(rel_path)}_{int(time.time())}"
        try:
            # 1. Gunzip to tmp
            with gzip.open(gz_path, 'rb') as f_in:
                with open(local_tmp, 'wb') as f_out:
                    shutil.copyfileobj(f_in, f_out)
            
            # 2. Verify checksum
            with open(sha_path, 'r') as f:
                stored_sha = f.read().strip()
                
            sha256_hash = hashlib.sha256()
            with open(local_tmp, "rb") as f:
                sha256_hash.update(f.read())
            
            if sha256_hash.hexdigest() != stored_sha:
                return False, "Cache-Integritätsfehler: Checksumme stimmt nicht überein!"
            
            # 3. Upload
            remote_path = os.path.join(remote_root, rel_path)
            if conn_type == 'ftp':
                with open(local_tmp, 'rb') as f:
                    client.storbinary(f"STOR {remote_path}", f)
            else:
                sftp = client.open_sftp()
                sftp.put(local_tmp, remote_path)
                sftp.close()
            return True, "Wiederherstellung erfolgreich."
        except Exception as e:
            return False, f"Wiederherstellungsfehler: {str(e)}"
        finally:
            if os.path.exists(local_tmp): os.remove(local_tmp)

    def preload_all_cache(self, domain=None):
        self.is_scanning = True
        conn = get_db()
        cursor = conn.cursor()
        
        # 1. Group domains by their unique remote root
        cursor.execute("SELECT domain, path FROM domain_configs")
        configs = {row['domain']: row['path'] for row in cursor.fetchall()}
        
        cursor.execute("SELECT value FROM settings WHERE key = 'conn_type'")
        row = cursor.fetchone()
        conn_type = row['value'] if row else 'ftp'
        
        # The paths stored in domain_configs are always FTP-relative paths.
        # SSH would need absolute paths, which we don't store.
        # So we always use FTP for preload, regardless of global conn_type setting.
        conn_type = 'ftp'
        
        # Group domains that share the same path
        path_to_domains = {}
        target_domains = [domain] if domain else list(configs.keys())
        
        for d in target_domains:
            root = configs.get(d, DEFAULT_BASE_PATH.format(domain=d))
            if root not in path_to_domains:
                path_to_domains[root] = []
            path_to_domains[root].append(d)
            
        self.log_progress(f"Verbinde via FTP für Preload ({len(path_to_domains)} unique Pfade)...")
        client = get_ftp_client()
        if not client:
            self.log_progress("FEHLER: FTP-Verbindung fehlgeschlagen. Preload abgebrochen.")
            self.is_scanning = False
            conn.close()
            return
            
        try:
            for root, domains in path_to_domains.items():
                if self.stop_requested: break
                self.log_progress(f"[{', '.join(domains)}] Scanne Verzeichnis: {root} ...")
                
                # Normalize path: FTP paths must be absolute (start with /)
                if not root.startswith('/'):
                    root = '/' + root
                
                # Full recursive scan — report progress every 250 files
                remote_files = {}
                
                class CountingProxy:
                    """Wraps result dict to emit progress every N inserts."""
                    def __init__(self, d, log_fn, interval=250):
                        self._d = d
                        self._log = log_fn
                        self._interval = interval
                    def __setitem__(self, k, v):
                        self._d[k] = v
                        if len(self._d) % self._interval == 0:
                            self._log(f"  Scanning... {len(self._d)} Dateien bisher gefunden")
                    def items(self): return self._d.items()
                    def __len__(self): return len(self._d)

                proxy = CountingProxy(remote_files, self.log_progress)
                self.scan_ftp_recursive(client, root, "", proxy)

                total = len(remote_files)
                self.log_progress(f"Scan abgeschlossen: {total} Dateien gefunden. Starte Caching...")
                
                cached_ok = 0
                cached_skip = 0
                for i, (rel_path, info) in enumerate(remote_files.items(), 1):
                    if self.stop_requested: break
                    full_path = os.path.join(root, rel_path)
                    
                    primary_domain = domains[0]
                    success = self.cache_file(primary_domain, rel_path, client, conn_type, full_path)
                    
                    if success:
                        cached_ok += 1
                        sha_path = os.path.join(self.cache_dir, primary_domain, rel_path.replace('/', '_') + ".sha256")
                        with open(sha_path, 'r') as f:
                            checksum = f.read().strip()
                        for d in domains:
                            cursor.execute("""
                                INSERT OR REPLACE INTO file_integrity (domain, path, checksum, mtime, last_checked)
                                VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
                            """, (d, rel_path, checksum, info['mtime']))
                        conn.commit()
                    else:
                        cached_skip += 1
                    
                    # Progress every 25 files
                    if i % 25 == 0:
                        pct = int(i / total * 100)
                        self.log_progress(f"  Caching... {i}/{total} ({pct}%) ✓{cached_ok} ⏭{cached_skip}")
                        
                self.log_progress(f"✅ Preload {root} abgeschlossen: {cached_ok} gecacht, {cached_skip} übersprungen.")
                
        finally:
            if conn_type == 'ftp': close_ftp_client(client)
            else: client.close()
            conn.close()
            self.is_scanning = False

    def scan_ftp_recursive(self, ftp, base_path, rel_path, result):
        if self.stop_requested: return
        current_path = os.path.join(base_path, rel_path) if rel_path else base_path
        try:
            # Prefer MLSD — gives type, size, mtime without extra roundtrips
            for name, facts in ftp.mlsd(current_path):
                if name in ('.', '..'): continue
                if name.startswith('.'): continue
                item_path = os.path.join(current_path, name)
                new_rel = os.path.join(rel_path, name) if rel_path else name
                if facts.get('type') == 'dir':
                    self.scan_ftp_recursive(ftp, base_path, new_rel, result)
                else:
                    size = int(facts.get('size', 0))
                    if size < 1000000:
                        mtime_str = facts.get('modify', '')
                        try:
                            mtime = datetime.strptime(mtime_str, "%Y%m%d%H%M%S").timestamp()
                        except:
                            mtime = time.time()
                        result[new_rel] = {'mtime': mtime}
        except Exception:
            # MLSD not supported — fall back to NLST
            try:
                items = ftp.nlst(current_path)
                for item in items:
                    name = os.path.basename(item)
                    if not name or name.startswith('.'): continue
                    new_rel = os.path.join(rel_path, name) if rel_path else name
                    try:
                        ftp.cwd(item)
                        ftp.cwd('..')
                        self.scan_ftp_recursive(ftp, base_path, new_rel, result)
                    except:
                        # It's a file — switch to binary mode before SIZE
                        try:
                            ftp.voidcmd('TYPE I')
                            size = ftp.size(item) or 0
                        except:
                            size = 0
                        if size < 1000000:
                            try:
                                mtime_str = ftp.sendcmd(f"MDTM {item}").split(' ')[1]
                                mtime = time.mktime(time.strptime(mtime_str, "%Y%m%d%H%M%S"))
                            except:
                                mtime = time.time()
                            result[new_rel] = {'mtime': mtime}
            except:
                pass
