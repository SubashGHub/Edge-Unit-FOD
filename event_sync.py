import os
import json
import time
import socket
from datetime import datetime
import requests
import yaml

# ======================================================
# CONFIGURATION
# ======================================================
BAD_LOG_FILE = "logs/invalid_event_lines.log"

# --- Load Sync Config ---
with open("utils/config_files/sync_config.yaml", "r") as f:
    SYNC_CFG = yaml.safe_load(f)

LOG_FILE = SYNC_CFG["log_settings"]["event_log"]
SYNC_LOG = SYNC_CFG["log_settings"]["sync_log"]
CHECKPOINT_FILE = SYNC_CFG["log_settings"]["checkpoint_file"]

BATCH_SIZE = SYNC_CFG["sync_settings"]["batch_size"]
SYNC_INTERVAL = SYNC_CFG["sync_settings"]["sync_interval_sec"]

ROTATION_ENABLED = SYNC_CFG["rotation_settings"]["enable_rotation"]
MAX_LOG_SIZE_MB = SYNC_CFG["rotation_settings"]["max_log_size_mb"]
MAX_LOG_BACKUPS = SYNC_CFG["rotation_settings"]["max_log_backups"]
LOG_DIR = SYNC_CFG["rotation_settings"]["log_dir"]

MASTER_IP = "192.168.0.113"
# API_URL = os.getenv("API_URL", "http://192.168.0.113:8000/api/events/")
API_URL = f"http://{MASTER_IP}:8000/api/detections/"

# ======================================================
# NETWORK CHECK
# ======================================================
def is_server_reachable(url=API_URL, timeout=3):
    """
    Quickly checks if the server is reachable.
    Returns True if reachable, False otherwise.
    """
    try:
        host = url.split("/")[2].split(":")[0]
        socket.create_connection((host, 5432), timeout=timeout)
        return True
    except Exception:
        return False


# ======================================================
# EVENT SEND FUNCTION
# ======================================================
def send_event_log(event_data: dict):
    headers = {"Content-Type": "application/json"}
    event_data["device_id"] = socket.gethostname()
    # if "timestamp" not in event_data:
    #     event_data["timestamp"] = time.strftime("%Y-%m-%d %H:%M:%S")

    try:
        r = requests.post(API_URL, headers=headers, data=json.dumps(event_data), timeout=5)
        print(f"→ Sent to {API_URL} | Status {r.status_code}")
        return r.status_code == 200
    except Exception as e:
        print("Send failed:", e)
        return False


# ======================================================
# CHECKPOINT UTILITIES
# ======================================================
def read_checkpoint():
    """Read checkpoint JSON safely."""
    if not os.path.exists(CHECKPOINT_FILE):
        return 0, None
    try:
        with open(CHECKPOINT_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data.get("offset", 0), data.get("last_sent")
    except Exception:
        return 0, None


def write_checkpoint(offset, last_event=None):
    """Write checkpoint as JSON atomically with metadata."""
    os.makedirs(os.path.dirname(CHECKPOINT_FILE), exist_ok=True)
    checkpoint_data = {
        "offset": offset,
        "last_sent": {
            "timestamp": last_event.get("timestamp"),
            "event": last_event.get("event"),
            "tray_id": last_event.get("tray_id"),
            "tool_id": last_event.get("tool_id"),
            "tool_name": last_event.get("tool_name")
        } if last_event else None
    }

    tmp_file = CHECKPOINT_FILE + ".tmp"
    with open(tmp_file, "w", encoding="utf-8") as f:
        json.dump(checkpoint_data, f, indent=2)
    os.replace(tmp_file, CHECKPOINT_FILE)

def write_sync_log(message):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] {message}"
    print(line)
    with open(SYNC_LOG, "a") as f:
        f.write(line + "\n")

import glob

def rotate_logs_if_needed():
    """Rotate event log if it exceeds MAX_LOG_SIZE_MB and all data is synced."""
    if not ROTATION_ENABLED or not os.path.exists(LOG_FILE):
        return

    size_mb = os.path.getsize(LOG_FILE) / (1024 * 1024)
    if size_mb < MAX_LOG_SIZE_MB:
        return  # no rotation needed

    offset, _ = read_checkpoint()
    file_size = os.path.getsize(LOG_FILE)
    if offset < file_size:
        write_sync_log(f"⚠️ Log too large ({size_mb:.2f} MB) but unsynced data remains — skipping rotation.")
        return

    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    rotated_name = os.path.join(LOG_DIR, f"event_{timestamp}.log")
    os.rename(LOG_FILE, rotated_name)

    # Reset checkpoint
    write_checkpoint(0, None)
    write_sync_log(f"🔄 Log rotated: {rotated_name} (size: {size_mb:.2f} MB)")

    # Cleanup old logs
    log_files = sorted(glob.glob(os.path.join(LOG_DIR, "event_*.log")), reverse=True)
    for old_file in log_files[MAX_LOG_BACKUPS:]:
        os.remove(old_file)
        write_sync_log(f"🧹 Deleted old rotated log: {old_file}")


# ======================================================
# MAIN SYNC FUNCTION
# ======================================================
def sync_events_from_log():
    """
    Reads unsent events from the log and pushes them to the server
    if reachable. Supports resuming via checkpoint and tracks last sent event.
    """
    if not os.path.exists(LOG_FILE):
        print("No log file found, skipping sync.")
        return

    if not is_server_reachable():
        print("🌐 Server unreachable — skipping sync for now.")
        return

    offset, last_sent = read_checkpoint()

    if last_sent:
        print(f"🔁 Resuming from offset {offset} (last event: {last_sent.get('event')} @ {last_sent.get('timestamp')})")
    else:
        print(f"🔁 Starting sync from offset {offset}")

    sent_count = 0
    with open(LOG_FILE, "r", encoding="utf-8") as f:
        f.seek(offset)

        while True:
            current_pos = f.tell()
            line = f.readline()
            if not line:
                break  # EOF

            line = line.strip()
            if not line:
                continue

            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                with open(BAD_LOG_FILE, "a", encoding="utf-8") as badf:
                    badf.write(line + "\n")
                continue

            # Send event
            if send_event_log(event):
                sent_count += 1
                write_checkpoint(f.tell(), event)
                print(f"✅ Sent event #{sent_count}: {event.get('event')} ({event.get('timestamp')})")
            else:
                print("⚠️ Send failed — pausing sync.")
                break

            # Optional pacing
            if sent_count % BATCH_SIZE == 0:
                time.sleep(1)

    if sent_count > 0:
        print(f"✅ Sync complete — {sent_count} event(s) sent.")
        rotate_logs_if_needed()
    else:
        print("No new events to sync.")

# ======================================================
# BACKGROUND LOOP
# ======================================================
def start_event_sync_loop():
    """
    Runs continuous sync every SYNC_INTERVAL seconds.
    """
    print(f"🚀 Starting event sync loop (every {SYNC_INTERVAL}s)")
    while True:
        try:
            sync_events_from_log()
        except Exception as e:
            print("Error during sync:", e)
        time.sleep(SYNC_INTERVAL)

# start_event_sync_loop()