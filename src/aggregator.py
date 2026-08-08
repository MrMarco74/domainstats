import sqlite3
import os
import sys
# Add parent dir to path to allow importing src
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.db_utils import get_db
from datetime import datetime, timedelta

def run_aggregation():
    conn = get_db()
    cursor = conn.cursor()
    
    print("Starte Daten-Aggregation...")
    
    # Aggregate data from logs into daily_stats
    # We aggregate all data up to yesterday to ensure the day is complete
    yesterday = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')
    
    # 1. Update daily_stats
    cursor.execute("""
        INSERT OR REPLACE INTO daily_stats (day, domain, requests, visitors, bandwidth, downloads)
        SELECT 
            strftime('%Y-%m-%d', timestamp) as day,
            domain,
            COUNT(*) as requests,
            COUNT(DISTINCT ip) as visitors,
            SUM(CASE WHEN size = '-' THEN 0 ELSE CAST(size AS INTEGER) END) as bandwidth,
            SUM(CASE WHEN (path LIKE '/releases/%.zip' OR path LIKE '%yads_v%_customer_pkg.zip') THEN 1 ELSE 0 END) as downloads
        FROM logs
        WHERE strftime('%Y-%m-%d', timestamp) <= ?
        AND ip NOT IN (SELECT ip FROM ignored_ips)
        GROUP BY day, domain
    """, (yesterday,))
    
    added = cursor.rowcount
    conn.commit()
    print(f"Aggregation beendet. {added} Tage/Domains aktualisiert.")
    
    # 2. Cleanup old logs (keep 180 days of raw logs)
    # This keeps the raw 'logs' table lean for heavy queries (like Security Audit)
    # while historical trends are preserved in 'daily_stats'.
    retention_days = 180
    cutoff = (datetime.now() - timedelta(days=retention_days)).strftime('%Y-%m-%d')
    
    print(f"Lösche alte Logs (älter als {retention_days} Tage: {cutoff})...")
    cursor.execute("DELETE FROM logs WHERE timestamp < ?", (cutoff,))
    deleted = cursor.rowcount
    conn.commit()
    
    if deleted > 0:
        print(f"Vakuumierung der Datenbank ({deleted} Zeilen gelöscht)...")
        # VACUUM can be slow, but it's okay for a daily task
        conn.execute("VACUUM")
    
    conn.close()
    print("Bereinigung und Aggregation abgeschlossen.")

if __name__ == "__main__":
    run_aggregation()
