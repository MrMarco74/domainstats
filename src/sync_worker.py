import os
import subprocess

LOCAL_RAW_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "raw", "worker_proxy")
REMOTE_ARCHIVE_DIR = "/var/log/nginx/archive/"

def sync_worker_logs():
    if not os.path.exists(LOCAL_RAW_DIR):
        os.makedirs(LOCAL_RAW_DIR)

    print(f"Syncing worker logs via SCP from root@worker:{REMOTE_ARCHIVE_DIR}")

    try:
        cmd = ["scp", "-q", f"root@worker:{REMOTE_ARCHIVE_DIR}*.json", f"{LOCAL_RAW_DIR}/"]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode == 0:
            print("Sync complete.")
        else:
            if "No such file or directory" in result.stderr:
                print("No files to sync.")
            else:
                print(f"Sync completed with warnings/errors: {result.stderr}")
    except Exception as e:
        print(f"Exception during sync: {e}")

    print("Syncing registry pull stats via SCP from root@worker:/var/log/nginx/registry_stats.log")
    try:
        cmd = ["scp", "-q", "root@worker:/var/log/nginx/registry_stats.log", f"{LOCAL_RAW_DIR}/"]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode == 0:
            print("Registry stats sync complete.")
        else:
            print(f"Registry stats sync completed with warnings/errors: {result.stderr}")
    except Exception as e:
        print(f"Exception during registry stats sync: {e}")

if __name__ == "__main__":
    sync_worker_logs()
