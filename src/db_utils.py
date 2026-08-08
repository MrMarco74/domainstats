import os
import sqlite3

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "logs.db")

def get_db():
    # Ensure data directory exists
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    
    conn = sqlite3.connect(DB_PATH, timeout=30.0) # Longer timeout for busy DB
    conn.row_factory = sqlite3.Row
    
    # Performance Tuning
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA cache_size=-2000") # ~2MB cache
    
    cursor = conn.cursor()
    # Ensure all tables exist
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS ignored_errors (
            domain TEXT,
            path TEXT,
            status INTEGER,
            PRIMARY KEY (domain, path, status)
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS domain_configs (
            domain TEXT PRIMARY KEY,
            path TEXT
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS ignored_ips (
            ip TEXT PRIMARY KEY
        )
    ''')
    
    # Tables for Integrity Monitoring
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS file_integrity (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            domain TEXT,
            path TEXT,
            checksum TEXT,
            mtime REAL,
            last_checked TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(domain, path)
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS integrity_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            domain TEXT,
            path TEXT,
            change_type TEXT, -- 'added', 'modified', 'deleted'
            old_checksum TEXT,
            new_checksum TEXT,
            old_mtime REAL,
            new_mtime REAL,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS llm_cache (
            prompt_hash TEXT PRIMARY KEY,
            response     TEXT NOT NULL,
            analyzed_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS security_false_positives (
            domain TEXT,
            path TEXT,
            PRIMARY KEY (domain, path)
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS daily_stats (
            day TEXT, -- YYYY-MM-DD
            domain TEXT,
            requests INTEGER,
            visitors INTEGER,
            bandwidth INTEGER,
            PRIMARY KEY (day, domain)
        )
    ''')
    cursor.execute("PRAGMA table_info(daily_stats)")
    if 'downloads' not in [col[1] for col in cursor.fetchall()]:
        cursor.execute("ALTER TABLE daily_stats ADD COLUMN downloads INTEGER DEFAULT 0")

    # Performance Indexes for large log tables
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_logs_timestamp ON logs(timestamp)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_logs_path ON logs(path)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_logs_domain ON logs(domain)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_logs_ip ON logs(ip)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_logs_status ON logs(status)")
    # Initialize default settings
    defaults = {
        'base_path': '/public_html',
        'remote_log_path': '/public_html/logs',
        'conn_type': 'ftp' # Default to FTP
    }
    for k, v in defaults.items():
        cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (k, v))

    conn.commit()
    return conn
