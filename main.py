"""
============================================================
🔧 TOOL DETECTION & TRACKING SYSTEM
------------------------------------------------------------
- Uses YOLO model for tool classification
- Uses ArUco markers for tray identification
- Logs all tool issue/return events to file
- Supports technician login/logout & auto timeout
- Syncs logs with external API/database (via event_sync)
============================================================
"""

import os
import cv2
import json
import time
import yaml
import queue
import threading
import numpy as np
from datetime import datetime
from ultralytics import YOLO
import cv2.aruco as aruco
from event_sync import start_event_sync_loop

# ============================================================
# 🔧 GLOBAL CONFIGURATION & CONSTANTS
# ============================================================

SYSTEM_UNLOCK_MARKER = 33  # ArUco marker to toggle system lock/unlock
STATUS_FILE = "utils/tool_status.json"
LOG_DIR = "logs"
LOG_FILE = os.path.join(LOG_DIR, "event.log")

# Ensure directory & log file exist
os.makedirs(LOG_DIR, exist_ok=True)
if not os.path.exists(LOG_FILE):
    open(LOG_FILE, "w").close()

# ============================================================
# ⚙️ LOAD CONFIGURATION FILES
# ============================================================

with open("utils/config_files/config.yaml", "r") as f:
    cfg = yaml.safe_load(f)

with open("utils/config_files/tools_config.yaml", "r") as f:
    t_cfg = yaml.safe_load(f)

with open("utils/config_files/marker_mapped.yaml", "r") as f:
    tray_map_data = yaml.safe_load(f)

with open("utils/config_files/technician_data.yaml", "r") as f:
    tech_data = yaml.safe_load(f)

# ------------------------------------------------------------
# 🧰 TOOL & TRAY CONFIGURATION
# ------------------------------------------------------------
tools_details = {
    tray_id: {t["tool_name"]: t["tool_id"] for t in tools} if tools else {}
    for tray_id, tools in t_cfg["tools_details"].items()
}

tools_class_details = {
    tray_id: {int(k): v for k, v in tools.items()} if tools else {}
    for tray_id, tools in cfg["tools_class_details"].items()
}

TRAY_MARKERS = {
    tray_id: int(marker_id)
    for tray_id, marker_id in tray_map_data["TRAY_MAPPED"].items()
}

techni_details = {
    entry["user_id"]: entry["username"]
    for entry in tech_data["technician_data"]
}

# ------------------------------------------------------------
# 🔧 SYSTEM PARAMETERS
# ------------------------------------------------------------
AUTO_LOGOUT_TIMEOUT = cfg.get("AUTO_LOGOUT_TIMEOUT", 60)
TOOL_UNIT_NAME = cfg.get("Tool_Unit_name", "Unknown")
TOOL_UNIT_ID = cfg.get("Tool_Unit_id", "Unknown")
STATION_NAME = cfg.get("Service_station", "Unknown")
MODEL_PATH = cfg.get(
    "model_path",
)

print(f"\n✅ Loaded config for {STATION_NAME}")
print(f"Tray Markers: {TRAY_MARKERS}")
print(f"Technicians: {techni_details}")
print(f"Tool Unit: {TOOL_UNIT_NAME} (ID: {TOOL_UNIT_ID})\n")

# ============================================================
# 🧠 RUNTIME VARIABLES
# ============================================================

system_online = False
current_emp_id = None
last_event_time = None
auto_logout_thread = None
stop_threads = False

# Initialize tool status
if os.path.exists(STATUS_FILE):
    with open(STATUS_FILE, "r") as f:
        tool_status = json.load(f)
        print("Tool Status file loaded successfully.")
else:
    tool_status = {
        tray_id: {name: "Returned" for name in tray_tools.keys()}
        for tray_id, tray_tools in tools_details.items()
    }
    print("New status file initialized.")

# Technician-tool mapping
techni_tool_map = {str(emp_id): [] for emp_id in techni_details.keys()}

# ============================================================
# 🧾 LOGGING & STATE MANAGEMENT
# ============================================================

def save_status():
    """Persist tool status to file."""
    with open(STATUS_FILE, "w") as f:
        json.dump(tool_status, f, indent=2)


def log_event(event_type, tray_id=None, details=None):
    """Record system or tool events."""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    details = details or {}
    technician_name = techni_details.get(current_emp_id, "Unknown")

    entry = {
        "timestamp": timestamp,
        "service_station": STATION_NAME,
        "unit": TOOL_UNIT_NAME,
        "unit_id": TOOL_UNIT_ID,
        "user_id": current_emp_id or "N/A",
        "user_name": technician_name,
        "event": event_type,
        "tray_id": tray_id,
        "tool_id": details.get("tool_id"),
        "tool_name": details.get("tool_name")
    }

    print(f"[LOG] {json.dumps(entry)}")
    with open(LOG_FILE, "a") as f:
        f.write(json.dumps(entry) + "\n")


# ============================================================
# 👤 TECHNICIAN SESSION HANDLING
# ============================================================

def auto_logout_monitor():
    """Automatically logout if no activity for AUTO_LOGOUT_TIMEOUT."""
    global current_emp_id, system_online, last_event_time
    while system_online and current_emp_id:
        if last_event_time:
            idle_time = (datetime.now() - last_event_time).total_seconds()
            if idle_time > AUTO_LOGOUT_TIMEOUT:
                print(f"\n⏳ Auto-logout: Technician {current_emp_id}\n")
                log_event("auto_logout", details={"technician_id": current_emp_id})
                current_emp_id = None
                system_online = False
                log_event("system_offline")
                break
        time.sleep(1)


def toggle_system_state():
    """Toggle lock/unlock state using SYSTEM_UNLOCK_MARKER."""
    global system_online, current_emp_id, last_event_time, auto_logout_thread

    system_online = not system_online
    if system_online:
        print("\n🔓 System ONLINE - Technician login required\n")
        log_event("system_online")

        # Technician login
        while current_emp_id not in techni_details:
            try:
                current_emp_id = int(input(f"Enter Technician ID {list(techni_details.keys())}: "))
            except ValueError:
                print("Invalid ID. Try again.")

        print(f"\n👤 Technician {current_emp_id} ({techni_details[current_emp_id]}) logged in.\n")
        log_event("login", details={"technician_id": current_emp_id})
        last_event_time = datetime.now()

        # Start inactivity monitor
        if not auto_logout_thread or not auto_logout_thread.is_alive():
            auto_logout_thread = threading.Thread(target=auto_logout_monitor, daemon=True)
            auto_logout_thread.start()
    else:
        print(f"\n🔒 Logging out technician {current_emp_id}\n")
        log_event("logout", details={"technician_id": current_emp_id})
        current_emp_id = None
        system_online = False
        log_event("system_offline")


# ============================================================
# 🧩 MARKER & TOOL DETECTION
# ============================================================

def check_tray_marker(frame):
    """Detect ArUco marker and return tray_id or toggle system."""
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    corners, ids, _ = detector.detectMarkers(gray)
    if ids is not None:
        for i in ids.flatten():
            if i == SYSTEM_UNLOCK_MARKER:
                toggle_system_state()
                cv2.waitKey(1000)
                return None
            for tray_id, marker_id in TRAY_MARKERS.items():
                if i == marker_id:
                    return str(tray_id)
    return None


def detect_tools(frame):
    """Run YOLO inference to detect tools."""
    results = model(frame, imgsz=960)
    detected_ids = {int(det.cls[0]) for det in results[0].boxes}
    annotated_frame = results[0].plot()
    return detected_ids, annotated_frame


def annotate_status(tray_id):
    """Show tray tool status in sidebar window."""
    statuses = tool_status.get(str(tray_id), {})
    num_tools = len(statuses)

    base_height = 120
    per_tool_height = 35
    height = max(base_height + num_tools * per_tool_height, 200)
    width = 550

    sidebar = np.zeros((height, width, 3), dtype=np.uint8)
    sidebar[:] = (40, 40, 40)
    cv2.putText(sidebar, f"Tray ID: {tray_id or 'N/A'}", (10, 40),
                cv2.FONT_HERSHEY_SIMPLEX, 0.9, (255, 255, 255), 2)

    y0, dy = 80, 30
    for i, (name, status) in enumerate(statuses.items()):
        color = (0, 255, 0) if status == "Returned" else (0, 0, 255)
        cv2.putText(sidebar, f"{name}: {status}", (10, y0 + i * dy),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.8, color, 2)

    if statuses:
        total = len(statuses)
        taken = sum(1 for s in statuses.values() if s == "Taken")
        cv2.putText(sidebar, f"Taken: {taken}/{total}",
                    (10, y0 + len(statuses) * dy + 20),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.9, (255, 255, 0), 2)
    else:
        cv2.putText(sidebar, "No Tray Open", (10, y0 + 20),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 255), 2)

    cv2.imshow("Tool Status", sidebar)
    cv2.resizeWindow("Tool Status", width, height)


# ============================================================
# 🧠 YOLO & ARUCO INITIALIZATION
# ============================================================

model = YOLO(MODEL_PATH)
aruco_dict = aruco.getPredefinedDictionary(aruco.DICT_4X4_50)
parameters = aruco.DetectorParameters()
detector = aruco.ArucoDetector(aruco_dict, parameters)

# ============================================================
# ⚡ MULTITHREADING PIPELINES
# ============================================================

frame_queue = queue.Queue(maxsize=1)
result_queue = queue.Queue(maxsize=1)

def yolo_worker():
    """Background YOLO inference thread."""
    while not stop_threads:
        if not frame_queue.empty():
            frame = frame_queue.get()
            detected_ids, annotated_frame = detect_tools(frame)
            result_queue.put((detected_ids, annotated_frame))


# ============================================================
# 🚀 MAIN LOOP
# ============================================================

def main():
    global stop_threads, last_event_time
    print("\n🔒 System locked (show ArUco 33 to unlock)\n")

    cap = cv2.VideoCapture(0)
    if not cap.isOpened():
        print("❌ Cannot open camera.")
        return

    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 960)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 540)

    threading.Thread(target=yolo_worker, daemon=True).start()
    threading.Thread(target=start_event_sync_loop, daemon=True).start()

    tray_open = False
    current_tray = None
    last_detected_ids = set()
    marker_present_count = 0
    marker_absent_count = 0
    STABLE_COUNT = 3

    while True:
        ret, frame = cap.read()
        if not ret:
            break

        tray_id = check_tray_marker(frame)

        if system_online and current_emp_id:
            if tray_id or tray_open:
                last_event_time = datetime.now()

            if tray_id:
                marker_present_count += 1
                marker_absent_count = 0

                # Tray opened
                if not tray_open and marker_present_count >= STABLE_COUNT:
                    tray_open = True
                    current_tray = tray_id
                    log_event("tray_open", tray_id=tray_id)

                # Process detection
                if tray_open and frame_queue.empty():
                    frame_queue.put(frame.copy())

                if not result_queue.empty():
                    detected_ids, annotated_frame = result_queue.get()
                    last_detected_ids = detected_ids
                    annotate_status(current_tray)
                    cv2.imshow("Tray Monitor", annotated_frame)

            else:
                marker_absent_count += 1
                marker_present_count = 0

                # Tray closed
                if tray_open and marker_absent_count >= STABLE_COUNT:
                    log_event("tray_close", tray_id=current_tray)

                    prev_status = tool_status[str(current_tray)]
                    new_status = {}
                    for class_id, tool_name in tools_class_details[str(current_tray)].items():
                        new_status[tool_name] = "Returned" if class_id in last_detected_ids else "Taken"

                    # Detect tool status changes
                    for class_id, tool_name in tools_class_details[str(current_tray)].items():
                        old_status = prev_status.get(tool_name)
                        new_stat = new_status[tool_name]
                        if old_status != new_stat:
                            tool_id = tools_details[str(current_tray)].get(tool_name, "UNKNOWN_ID")
                            event_type = "tool_Issued" if new_stat == "Taken" else "tool_Returned"
                            log_event(event_type, tray_id=current_tray, details={
                                "tool_id": tool_id,
                                "tool_name": tool_name,
                                "unit_id": TOOL_UNIT_ID
                            })
                            # Update technician-tool map
                            if new_stat == "Taken":
                                if tool_name not in techni_tool_map[str(current_emp_id)]:
                                    techni_tool_map[str(current_emp_id)].append(tool_name)
                            else:
                                for tools in techni_tool_map.values():
                                    if tool_name in tools:
                                        tools.remove(tool_name)

                    tool_status[str(current_tray)] = new_status
                    save_status()

                    tray_open = False
                    current_tray = None
                    marker_absent_count = 0

                annotate_status(current_tray)
                cv2.imshow("Tray Monitor", frame)

        else:
            cv2.putText(frame, "SYSTEM LOCKED - Show ArUco 33 to Unlock", (20, 40),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 255), 2)
            cv2.imshow("Tray Monitor", frame)

        if cv2.waitKey(1) == 27:  # ESC key
            stop_threads = True
            break

    cap.release()
    cv2.destroyAllWindows()


# ============================================================
# 🏁 ENTRY POINT
# ============================================================
if __name__ == "__main__":
    main()
