import sys
import os
import cv2
import yaml
from ultralytics import YOLO
from PyQt5.QtWidgets import (
    QApplication, QWidget, QLabel, QVBoxLayout, QHBoxLayout,
    QPushButton, QComboBox, QTableWidget, QTableWidgetItem, QHeaderView
)
from PyQt5.QtGui import QPixmap, QImage
from PyQt5.QtCore import QTimer, Qt

# Lazy import for detection logic
try:
    import main  # Your detection logic (YOLO or other)
except ImportError as e:
    print("⚠️ Warning: Could not import detection logic:", e)
    main = None


class ToolTrackingUI(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Smart Tool Tracking System")
        self.setGeometry(100, 100, 1280, 720)
        self.setStyleSheet("background-color: #1e293b; color: white; font-size: 16px;")

        # --- File paths ---
        base_path = os.path.join(os.getcwd(), "utils", "config_files")
        self.tool_config_path = os.path.join(base_path, "tools_config.yaml")
        self.tech_config_path = os.path.join(base_path, "technician_data.yaml")

        # --- Load YAMLs ---
        self.tray_map = self.load_yaml(self.tool_config_path) or {}
        self.tech_data = self.load_yaml(self.tech_config_path) or {}

        # --- Layouts ---
        main_layout = QVBoxLayout()
        top_layout = QHBoxLayout()
        mid_layout = QHBoxLayout()
        bottom_layout = QHBoxLayout()

        # --- Header ---
        self.station_label = QLabel("Service Station: ABC Service Center")
        self.unit_label = QLabel("Unit: Tool Unit 1")
        self.station_label.setStyleSheet("font-size: 20px; font-weight: bold; color: #38bdf8;")
        self.unit_label.setStyleSheet("font-size: 18px; color: #facc15;")
        top_layout.addWidget(self.station_label)
        top_layout.addStretch()
        top_layout.addWidget(self.unit_label)

        # --- Dropdowns ---
        self.tray_combo = QComboBox()
        self.tray_combo.addItem("Select Tray")
        self.tray_combo.addItems(list(self.tray_map.keys()))
        self.tray_combo.currentIndexChanged.connect(self.load_tool_list)

        self.user_combo = QComboBox()
        self.user_combo.addItem("Select Technician")
        if "technicians" in self.tech_data:
            for tech in self.tech_data["technicians"]:
                self.user_combo.addItem(f"{tech['id']} - {tech['name']}")
        self.user_combo.currentIndexChanged.connect(self.select_technician)

        # --- Buttons ---
        self.start_button = QPushButton("Start System")
        self.start_button.setStyleSheet("background-color: #22c55e; font-weight: bold;")
        self.start_button.clicked.connect(self.toggle_system)

        self.stop_button = QPushButton("Stop System")
        self.stop_button.setStyleSheet("background-color: #ef4444; font-weight: bold;")
        self.stop_button.clicked.connect(self.stop_system)

        mid_layout.addWidget(QLabel("Tray:"))
        mid_layout.addWidget(self.tray_combo)
        mid_layout.addWidget(QLabel("Technician:"))
        mid_layout.addWidget(self.user_combo)
        mid_layout.addWidget(self.start_button)
        mid_layout.addWidget(self.stop_button)

        # --- Camera Feed ---
        self.camera_label = QLabel("Camera Feed")
        self.camera_label.setAlignment(Qt.AlignCenter)
        self.camera_label.setStyleSheet("border: 2px solid white; background-color: black;")
        self.camera_label.setFixedSize(640, 360)

        # --- Tool Table ---
        self.tool_table = QTableWidget(0, 2)
        self.tool_table.setHorizontalHeaderLabels(["Tool Name", "Status"])
        self.tool_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.tool_table.setStyleSheet("QTableWidget {background-color: #334155;}")

        bottom_layout.addWidget(self.tool_table)
        bottom_layout.addWidget(self.camera_label)

        # --- Combine Layouts ---
        main_layout.addLayout(top_layout)
        main_layout.addLayout(mid_layout)
        main_layout.addLayout(bottom_layout)
        self.setLayout(main_layout)

        # --- Timer for camera ---
        self.cap = None
        self.timer = QTimer()
        self.timer.timeout.connect(self.update_frame)

    # ---------------- Helper Methods ----------------
    def load_yaml(self, filepath):
        try:
            with open(filepath, "r") as f:
                return yaml.safe_load(f)
        except Exception as e:
            print(f"⚠️ Error loading {filepath}: {e}")
            return {}

    # ---------------- UI Logic ----------------
    def load_tool_list(self):
        tray_name = self.tray_combo.currentText()
        if tray_name == "Select Tray" or tray_name not in self.tray_map:
            self.tool_table.setRowCount(0)
            return
        tools = self.tray_map[tray_name]
        self.tool_table.setRowCount(len(tools))
        for i, tool in enumerate(tools):
            self.tool_table.setItem(i, 0, QTableWidgetItem(tool))
            self.tool_table.setItem(i, 1, QTableWidgetItem("Available"))

    def select_technician(self):
        user = self.user_combo.currentText()
        print(f"Technician selected: {user}")

    def toggle_system(self):
        print("🟢 Starting System...")

        # Lazy import YOLO logic AFTER UI starts
        try:
            import main  # import only now
            self.main = main
            self.main.toggle_system_state()
            print("✅ YOLO Detection module loaded successfully.")
        except Exception as e:
            print(f"❌ Failed to load YOLO logic: {e}")
            return

        # Start camera
        self.cap = cv2.VideoCapture(0)
        if not self.cap.isOpened():
            print("❌ Could not open camera.")
            return
        self.timer.start(30)

    def stop_system(self):
        print("⛔ System Stopped")
        if main and hasattr(main, "toggle_system_state"):
            main.toggle_system_state(False)

        self.timer.stop()
        if self.cap:
            self.cap.release()
        self.camera_label.clear()
        self.camera_label.setText("Camera Feed")

    def update_frame(self):
        if not self.cap:
            return
        ret, frame = self.cap.read()
        if not ret:
            return
        frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

        # Optional: integrate YOLO detection safely
        if main and hasattr(main, "detect_tools"):
            try:
                frame = main.detect_tools(frame)
            except Exception as e:
                print("⚠️ Detection failed:", e)

        image = QImage(frame, frame.shape[1], frame.shape[0], QImage.Format_RGB888)
        self.camera_label.setPixmap(QPixmap.fromImage(image))


if __name__ == "__main__":
    app = QApplication(sys.argv)
    ui = ToolTrackingUI()
    ui.show()
    sys.exit(app.exec_())