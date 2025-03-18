import sys
import json
import os
import webbrowser
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QListWidget,
    QTableWidget, QTableWidgetItem, QMessageBox, QFileDialog, QInputDialog, QCompleter, QComboBox
)
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QColor
from pythonosc import udp_client, dispatcher, osc_server
import mido
import threading

# Generate a list of OSC commands:
# 32 Chanel: /ch/XX/mix/XX/level
channel_commands = [f"/ch/{i:02d}/mix/{i:02d}/level" for i in range(1, 33)]
# 16 BUS : /bus/X/mix/fader
bus_commands = [f"/bus/{i}/mix/fader" for i in range(1, 17)]
OSC_COMMANDS = channel_commands + bus_commands

class MIDItoOSCConverter(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("MIDI to OSC Converter")
        self.setGeometry(100, 100, 950, 700)

        self.osc_client = None
        self.osc_server = None
        self.osc_server_thread = None
        self.midi_input = None
        self.midi_ports = mido.get_input_names()
        # mapping_table: list of dictionaries with mapping parameters
        # {'cc': int, 'osc': str, 'min': float, 'max': float, 'name': str, 'control_type': str}
        self.mapping_table = []
        self.capture_mode = False

        self.init_ui()

    def init_ui(self):
        main_widget = QWidget()
        main_layout = QVBoxLayout()

        # Host settings
        host_layout = QHBoxLayout()
        self.host_ip_input = QLineEdit("127.0.0.1")
        self.host_port_input = QLineEdit("10024")
        self.connect_button = QPushButton("Connect to Mixer")
        self.connect_button.clicked.connect(self.connect_to_mixer)
        self.connection_status = QLabel("Not Connected")
        self.connection_status.setStyleSheet("color: red")
        host_layout.addWidget(QLabel("Host IP:"))
        host_layout.addWidget(self.host_ip_input)
        host_layout.addWidget(QLabel("Port:"))
        host_layout.addWidget(self.host_port_input)
        host_layout.addWidget(self.connect_button)
        host_layout.addWidget(self.connection_status)

        # OSC server settings (receiving messages from the mixer)
        osc_layout = QHBoxLayout()
        self.listen_port_input = QLineEdit("10023")
        self.start_osc_server_button = QPushButton("Start OSC Server")
        self.start_osc_server_button.clicked.connect(self.start_osc_server)
        osc_layout.addWidget(QLabel("OSC Listen Port:"))
        osc_layout.addWidget(self.listen_port_input)
        osc_layout.addWidget(self.start_osc_server_button)

        # List of MIDI controllers with Refresh button
        midi_layout = QHBoxLayout()
        self.midi_list = QListWidget()
        self.midi_list.addItems(self.midi_ports)
        self.midi_list.itemDoubleClicked.connect(self.select_midi_device)
        self.refresh_button = QPushButton("Refresh")
        self.refresh_button.clicked.connect(self.refresh_midi_ports)
        midi_layout.addWidget(QLabel("MIDI Controllers:"))
        midi_layout.addWidget(self.midi_list)
        midi_layout.addWidget(self.refresh_button)

        # MIDI Monitor
        self.midi_monitor = QListWidget()
        self.clear_monitor_button = QPushButton("Clear MIDI Monitor")
        self.clear_monitor_button.clicked.connect(self.midi_monitor.clear)

        # OSC Monitor
        self.osc_monitor = QListWidget()
        self.clear_osc_monitor_button = QPushButton("Clear OSC Monitor")
        self.clear_osc_monitor_button.clicked.connect(self.osc_monitor.clear)

        # Mapping table (6 columns: Name, MIDI CC, OSC Address, Min, Max, Type)
        self.mapping_table_widget = QTableWidget(0, 6)
        self.mapping_table_widget.setHorizontalHeaderLabels(["Name", "MIDI CC", "OSC Address", "Min", "Max", "Type"])
        self.mapping_table_widget.setSelectionMode(QTableWidget.SelectionMode.ExtendedSelection)
        self.mapping_table_widget.keyPressEvent = self.delete_selected_rows

        # Control buttons
        button_layout = QHBoxLayout()
        self.capture_button = QPushButton("Capture")
        self.capture_button.clicked.connect(self.toggle_capture_mode)
        self.save_button = QPushButton("Save Preset")
        self.save_button.clicked.connect(self.save_preset)
        self.load_button = QPushButton("Load Preset")
        self.load_button.clicked.connect(self.load_preset)
        self.help_button = QPushButton("?")
        self.help_button.clicked.connect(self.show_help)
        button_layout.addWidget(self.capture_button)
        button_layout.addWidget(self.save_button)
        button_layout.addWidget(self.load_button)
        button_layout.addWidget(self.help_button)

        # Copywriting with a link to the website
        copyright_label = QLabel("Made by MARKI NA LUNU")
        copyright_label.setStyleSheet("color: white; background-color: black; padding: 5px;")
        copyright_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        copyright_label.mousePressEvent = self.open_website

        # Layout assembly
        main_layout.addLayout(host_layout)
        main_layout.addLayout(osc_layout)
        main_layout.addLayout(midi_layout)
        main_layout.addWidget(QLabel("MIDI Monitor:"))
        main_layout.addWidget(self.midi_monitor)
        main_layout.addWidget(self.clear_monitor_button)
        main_layout.addWidget(QLabel("OSC Monitor:"))
        main_layout.addWidget(self.osc_monitor)
        main_layout.addWidget(self.clear_osc_monitor_button)
        main_layout.addWidget(QLabel("Mapping Table:"))
        main_layout.addWidget(self.mapping_table_widget)
        main_layout.addLayout(button_layout)
        main_layout.addWidget(copyright_label)

        main_widget.setLayout(main_layout)
        self.setCentralWidget(main_widget)

        # Timer for checking MIDI messages
        self.midi_timer = QTimer()
        self.midi_timer.timeout.connect(self.check_midi_messages)

    def refresh_midi_ports(self):
        self.midi_ports = mido.get_input_names()
        self.midi_list.clear()
        self.midi_list.addItems(self.midi_ports)

    def start_osc_server(self):
        try:
            port = int(self.listen_port_input.text())
        except ValueError:
            QMessageBox.warning(self, "Input Error", "Listen Port must be a number.")
            return

        disp = dispatcher.Dispatcher()
        disp.set_default_handler(self.process_osc_message)
        self.osc_server = osc_server.ThreadingOSCUDPServer(("0.0.0.0", port), disp)
        self.osc_server_thread = threading.Thread(target=self.osc_server.serve_forever, daemon=True)
        self.osc_server_thread.start()
        self.osc_monitor.addItem(f"OSC Server started on port {port}")

    def process_osc_message(self, address, *args):
        self.osc_monitor.addItem(f"{address}: {args}")

    def select_midi_device(self, item):
        try:
            if self.midi_input:
                self.midi_input.close()
            self.midi_input = mido.open_input(item.text())
            self.midi_timer.start(10)
        except Exception as e:
            QMessageBox.critical(self, "MIDI Error", str(e))

    def delete_selected_rows(self, event):
        if event.key() == Qt.Key.Key_Delete:
            selected_rows = sorted(set(index.row() for index in self.mapping_table_widget.selectedIndexes()), reverse=True)
            for row in selected_rows:
                self.mapping_table_widget.removeRow(row)
                del self.mapping_table[row]
        else:
            super(QTableWidget, self.mapping_table_widget).keyPressEvent(event)

    def show_help(self):
        try:
            os.startfile("commands.pdf")
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Unable to open help file: {e}")

    def open_website(self, event):
        webbrowser.open("https://band.link/markinalunu")

    def toggle_capture_mode(self):
        self.capture_mode = not self.capture_mode
        self.capture_button.setText("Capture (ON)" if self.capture_mode else "Capture")

    def add_mapping(self, cc):
        # Dialog for selecting an OSC command with predictive input, offering a list for 32 channels and 16 BUS buses.
        osc_cmd, ok = QInputDialog.getItem(
            self, "Select OSC Command",
            f"Assign OSC command for MIDI CC {cc}:",
            OSC_COMMANDS, 0, True
        )
        if ok and osc_cmd:
            # Request for control type: fader или button.
            control_type, ok_type = QInputDialog.getItem(
                self, "Select Control Type",
                "Select control type:",
                ["fader", "button"], 0, False
            )
            if not ok_type:
                control_type = "fader"
            # If the type is fader, set the range 0.0 - 1.0, otherwise for the button the range is ignored (will be 0/1)
            if control_type == "fader":
                min_val, max_val = 0.0, 127
            else:
                min_val, max_val = 0, 1
            mapping = {'cc': cc, 'osc': osc_cmd, 'min': min_val, 'max': max_val, 'name': "New Parameter", 'control_type': control_type}
            self.mapping_table.append(mapping)
            self.add_mapping_row(mapping)

    def add_mapping_row(self, mapping):
        row_position = self.mapping_table_widget.rowCount()
        self.mapping_table_widget.insertRow(row_position)
        # Parameter Name
        self.mapping_table_widget.setItem(row_position, 0, QTableWidgetItem(mapping.get('name', 'New Parameter')))
        # MIDI CC
        self.mapping_table_widget.setItem(row_position, 1, QTableWidgetItem(str(mapping['cc'])))
        # OSC command with autocomplete
        osc_line = QLineEdit(mapping['osc'])
        completer = QCompleter(OSC_COMMANDS)
        osc_line.setCompleter(completer)
        self.mapping_table_widget.setCellWidget(row_position, 2, osc_line)
        # Minimum value
        self.mapping_table_widget.setItem(row_position, 3, QTableWidgetItem(str(mapping['min'])))
        # Maximum value
        self.mapping_table_widget.setItem(row_position, 4, QTableWidgetItem(str(mapping['max'])))
        # Control type - use QComboBox
        type_combo = QComboBox()
        type_combo.addItems(["fader", "button"])
        index = type_combo.findText(mapping.get('control_type', 'fader'))
        if index >= 0:
            type_combo.setCurrentIndex(index)
        self.mapping_table_widget.setCellWidget(row_position, 5, type_combo)

    def update_mapping_from_table(self):
        new_mapping = []
        rows = self.mapping_table_widget.rowCount()
        for row in range(rows):
            name_item = self.mapping_table_widget.item(row, 0)
            cc_item = self.mapping_table_widget.item(row, 1)
            osc_widget = self.mapping_table_widget.cellWidget(row, 2)
            min_item = self.mapping_table_widget.item(row, 3)
            max_item = self.mapping_table_widget.item(row, 4)
            type_widget = self.mapping_table_widget.cellWidget(row, 5)
            name = name_item.text() if name_item else "New Parameter"
            cc = int(cc_item.text()) if cc_item else 0
            osc = osc_widget.text() if osc_widget else ""
            try:
                min_val = float(min_item.text()) if min_item else 0
                max_val = float(max_item.text()) if max_item else 1
            except ValueError:
                min_val, max_val = 0, 1
            control_type = type_widget.currentText() if type_widget else "fader"
            new_mapping.append({'name': name, 'cc': cc, 'osc': osc, 'min': min_val, 'max': max_val, 'control_type': control_type})
        self.mapping_table = new_mapping

    def connect_to_mixer(self):
        ip = self.host_ip_input.text()
        try:
            port = int(self.host_port_input.text())
            self.osc_client = udp_client.SimpleUDPClient(ip, port)
            self.connection_status.setText("Connected")
            self.connection_status.setStyleSheet("color: green")
        except ValueError:
            QMessageBox.warning(self, "Input Error", "Port must be a number.")
        except Exception as e:
            QMessageBox.critical(self, "Connection Error", str(e))
            self.osc_client = None

    def save_preset(self):
        self.update_mapping_from_table()
        file_name, _ = QFileDialog.getSaveFileName(self, "Save Preset", "", "JSON Files (*.json)")
        if file_name:
            preset = {
                "host_ip": self.host_ip_input.text(),
                "host_port": self.host_port_input.text(),
                "mappings": self.mapping_table
            }
            with open(file_name, 'w') as file:
                json.dump(preset, file)

    def load_preset(self):
        file_name, _ = QFileDialog.getOpenFileName(self, "Load Preset", "", "JSON Files (*.json)")
        if file_name:
            with open(file_name, 'r') as file:
                preset = json.load(file)
                self.host_ip_input.setText(preset.get("host_ip", "127.0.0.1"))
                self.host_port_input.setText(preset.get("host_port", "10024"))
                self.mapping_table = preset.get("mappings", [])
                self.refresh_mapping_table()
                self.connect_to_mixer()

    def refresh_mapping_table(self):
        self.mapping_table_widget.setRowCount(0)
        for mapping in self.mapping_table:
            self.add_mapping_row(mapping)

    def check_midi_messages(self):
        if self.midi_input:
            for message in self.midi_input.iter_pending():
                if message.type == 'control_change':
                    self.midi_monitor.addItem(f"CC: {message.control} Value: {message.value}")
                    if self.capture_mode:
                        self.add_mapping(message.control)
                        self.capture_mode = False
                        self.capture_button.setText("Capture")
                    self.send_osc_message(message.control, message.value)
                    self.indicate_midi_activity()

    def indicate_midi_activity(self):
        if self.midi_input:
            active_port = self.midi_input.name
            for index in range(self.midi_list.count()):
                item = self.midi_list.item(index)
                if item.text() == active_port:
                    original_color = item.background().color()
                    item.setBackground(QColor("green"))
                    QTimer.singleShot(200, lambda: item.setBackground(original_color))

    def send_osc_message(self, cc, value):
        if self.osc_client:
            for mapping in self.mapping_table:
                if mapping['cc'] == cc:
                    if mapping.get('control_type', 'fader') == 'button':
                        osc_value = 1 if value > 0 else 0
                    else:
                        min_val = mapping['min']
                        max_val = mapping['max']
                        osc_value = min_val + (value / 127.0) * (max_val - min_val)
                    self.osc_client.send_message(mapping['osc'], osc_value)

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MIDItoOSCConverter()
    window.show()
    sys.exit(app.exec())
