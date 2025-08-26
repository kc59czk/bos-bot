import sys
import queue
import threading
import socket
import struct
import winreg
import xml.etree.ElementTree as ET
import time
from datetime import datetime
from enum import Enum
import re

from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
                             QTabWidget, QLabel, QLineEdit, QPushButton, QTextEdit, QTreeWidget,
                             QTreeWidgetItem, QComboBox, QGroupBox, QScrollArea, QFrame, QSizePolicy,
                             QStatusBar, QMessageBox)
from PyQt6.QtCore import Qt, QTimer, pyqtSignal, QObject, QThread
from PyQt6.QtGui import QFont, QColor, QPalette

# (Enum BotState remains unchanged)
class BotState(Enum):
    STOPPED = 0
    IDLE = 1
    WAITING_FOR_ENTRY_FILL = 2
    IN_LONG_POSITION = 3
    IN_SHORT_POSITION = 4

# Bossa Client that runs in a separate thread
class BossaClient(QObject):
    login_success = pyqtSignal()
    login_failed = pyqtSignal(str)
    message_received = pyqtSignal(str)
    heartbeat_update = pyqtSignal(str)
    latency_update = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self.username = "BOS"
        self.password = "BOS"
        self.is_logged_in = False
        self.request_id = 0
        self.socket = None
        self.running = False
        self.ports = {}
        self.sync_port = None
        self.async_port = None

    def _get_ports_from_registry(self):
        try:
            key_path = r"SOFTWARE\COMARCH S.A.\NOL3\7\Settings"
            print("Odczyt portów z rejestru...")
            registry_key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_READ)
            print("Otworzono klucz rejestru.")
            self.sync_port, _ = winreg.QueryValueEx(registry_key, "nca_psync")
            self.async_port, _ = winreg.QueryValueEx(registry_key, "nca_pasync")
            print(f"Odczytane wartości: Sync={self.sync_port}, Async={self.async_port}")
            print(f"Typy wartości: Sync={type(self.sync_port)}, Async={type(self.async_port)}")
            self.sync_port = int(self.sync_port)
            self.async_port = int(self.async_port)
            self.ports['sync'] = self.sync_port
            self.ports['async'] = self.async_port
            winreg.CloseKey(registry_key)
            print(f"Odczytane porty z rejestru: Sync={self.sync_port}, Async={self.async_port}")

            return True
        except Exception as e:
            self.login_failed.emit(f"Błąd odczytu portów z rejestru: {e}")
            return False

    def _send_and_receive_sync(self, message):
        sync_socket = None
        try:
            sync_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sync_socket.connect(('127.0.0.1', self.sync_port))
            self._send_message(sync_socket, message)
            response = self._receive_message(sync_socket)
            return response
        except ConnectionAbortedError as e:
            self.message_received.emit(f"BŁAD polaczenie zostalo zerwane przez serwer NOL3: {e}")
            return None
        except Exception as e:
            self.message_received.emit(f"BŁAD komunikacji synchronicznej: {e}")
            return None
        finally:
            if sync_socket:
                sync_socket.close()


    def _send_and_receive_sync(self, message):
        try:
            self.message_received.emit(f"Łączenie z localhost:{self.sync_port}...")
            self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        
        # Set timeout to avoid hanging
            self.socket.settimeout(10.0)
        
            self.socket.connect(('localhost', self.sync_port))
            self.message_received.emit("Połączenie nawiązane, wysyłanie wiadomości...")
        
            self.socket.send(message.encode('utf-8'))
            self.message_received.emit("Wiadomość wysłana, oczekiwanie na odpowiedź...")
        
        # Read response length (first 4 bytes)
            length_data = self.socket.recv(4)
            self.message_received.emit(f"Otrzymano dane długości: {len(length_data)} bajtów")
        
            if len(length_data) < 4:
                self.message_received.emit("BŁAD: Za mało danych dla długości wiadomości")
                return None
            
            msg_length = struct.unpack('!I', length_data)[0]
            self.message_received.emit(f"Długość wiadomości: {msg_length} bajtów")
        
        # Read the actual message
            response = b''
            bytes_received = 0
            while len(response) < msg_length:
                chunk = self.socket.recv(min(4096, msg_length - len(response)))
                if not chunk:
                    self.message_received.emit("BŁAD: Połączenie zamknięte przed odebraniem pełnej wiadomości")
                    break
                response += chunk
                bytes_received += len(chunk)
                self.message_received.emit(f"Odebrano {bytes_received}/{msg_length} bajtów")
        
            self.socket.close()
            self.message_received.emit("Połączenie zamknięte")
        
            if response:
                decoded_response = response.decode('utf-8')
                self.message_received.emit(f"Odebrana wiadomość: {decoded_response}")
                return decoded_response
            else:
                self.message_received.emit("BŁAD: Brak treści wiadomości")
                return None
            
        except socket.timeout:
            error_msg = "Timeout połączenia (10 sekund)"
            self.message_received.emit(f"BŁAD: {error_msg}")
            return None
        except ConnectionRefusedError:
            error_msg = "Połączenie odrzucone. Sprawdź czy serwer BossaAPI jest uruchomiony."
            self.message_received.emit(f"BŁAD: {error_msg}")
            return None
        except Exception as e:
            error_msg = f"BŁAD komunikacji synchronicznej: {e}"
            self.message_received.emit(error_msg)
            return None


    def login(self):
        self.message_received.emit("Rozpoczynanie procesu logowania...")
    
    # Debug: Check if ports can be read from registry
        self.message_received.emit("Próba odczytu portów z rejestru...")
        if not self._get_ports_from_registry():
            self.message_received.emit("BŁAD: Nie udało się odczytać portów z rejestru")
            return

        self.message_received.emit(f"Odczytane porty: Sync={self.ports.get('sync')}, Async={self.ports.get('async')}")
    
        self.request_id += 1
        login_request = f'<FIXML v="5.0" r="20080317" s="20080314"><UserReq UserReqID="{self.request_id}" UserReqTyp="1" Username="{self.username}" Password="{self.password}"/></FIXML>'
    
        self.message_received.emit("Wysyłanie żądania logowania...")
        self.message_received.emit(f"Request XML: {login_request}")
    
        response = self._send_and_receive_sync(login_request)
    
        self.message_received.emit(f"Otrzymana odpowiedź: {response}")
    
        if response:
            self.message_received.emit(f"Długość odpowiedzi: {len(response)} znaków")
        
            if '<UserRsp' in response:
                self.message_received.emit("Znaleziono tag UserRsp w odpowiedzi")
                try:
                    root = ET.fromstring(response)
                    self.message_received.emit("XML został pomyślnie sparsowany")
                
                    user_rsp = root.find('UserRsp')
                    if user_rsp is not None:
                        self.message_received.emit(f"Znaleziono UserRsp: {ET.tostring(user_rsp, encoding='unicode')}")
                        user_stat = user_rsp.get('UserStat')
                        self.message_received.emit(f"UserStat: {user_stat}")
                    
                        if user_stat == '1':
                            self.is_logged_in = True
                            self.message_received.emit("Logowanie udane! UserStat = 1")
                            self.login_success.emit()
                            self.message_received.emit("Logowanie udane!")
                        else:
                            status = user_stat if user_stat is not None else 'brak'
                            self.message_received.emit(f"Logowanie nieudane. Status: {status}")
                            self.login_failed.emit(f"Logowanie nieudane. Status: {status}")
                    else:
                        self.message_received.emit("BŁAD: Nie znaleziono tagu UserRsp w odpowiedzi XML")
                        self.login_failed.emit("BŁAD: Nie znaleziono tagu UserRsp w odpowiedzi")
                    
                except ET.ParseError as e:
                    self.message_received.emit(f"BŁAD parsowania XML: {e}")
                    self.message_received.emit(f"Pełna odpowiedź: {response}")
                    self.login_failed.emit(f"Błąd parsowania odpowiedzi: {e}")
            else:
                self.message_received.emit("BŁAD: W odpowiedzi nie znaleziono tagu UserRsp")
                self.message_received.emit(f"Sprawdzam czy odpowiedź zawiera błąd: {'<Err' in response}")
                self.login_failed.emit(f"Nieoczekiwana odpowiedź (brak UserRsp): {response[:200]}...")  # Show first 200 chars
        else:
            self.message_received.emit("BŁAD: Brak odpowiedzi lub odpowiedź pusta")
            self.login_failed.emit("Brak odpowiedzi z serwera")

    def disconnect(self):
        self.running = False
        if self.socket:
            try:
                self.socket.close()
            except:
                pass
        self.is_logged_in = False

# A helper QObject to handle cross-thread communication via signals
class Communicate(QObject):
    log_message = pyqtSignal(str)
    update_heartbeat = pyqtSignal(str)
    update_latency = pyqtSignal(str)

class BossaApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.client_thread = None
        self.bossa_client = None
        self.TARGET_ISIN = "PL0GF0031252"
        self.orders = {}
        self.STATUS_MAP = {'0': 'Nowe', '1': 'Aktywne', '2': 'Wykonane', '4': 'Anulowane', '5': 'Zastąpione', '6': 'Oczekuje na anul.', '8': 'Odrzucone', 'E': 'Oczekuje na mod.'}
        self.SIDE_MAP = {'1': 'Kupno', '2': 'Sprzedaż'}

        # Create a communication object
        self.comm = Communicate()
        self.comm.log_message.connect(self.log_to_status)
        self.comm.update_heartbeat.connect(self.update_heartbeat_icon)
        self.comm.update_latency.connect(self.update_latency_display)

        self.init_ui()

    def init_ui(self):
        self.setWindowTitle("BossaAPI - Menedżer Transakcji (PyQt6)")
        self.setGeometry(100, 100, 1100, 900)

        # Central Widget and Main Layout
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)

        # Create the Tab Widget
        self.tab_widget = QTabWidget()
        main_layout.addWidget(self.tab_widget)

        # Create the tabs
        self.tab_login = QWidget()
        self.tab_orders = QWidget()
        self.tab_monitor = QWidget()
        self.tab_bot = QWidget()
        self.tab_portfolio = QWidget()

        self.tab_widget.addTab(self.tab_login, "Połączenie i Filtr")
        self.tab_widget.addTab(self.tab_orders, "Zlecenie Ręczne")
        self.tab_widget.addTab(self.tab_monitor, "Monitor Zleceń")
        self.tab_widget.addTab(self.tab_bot, "Menedżer Transakcji")
        self.tab_widget.addTab(self.tab_portfolio, "Portfel")

        # Initialize the UI for each tab
        self.setup_login_tab()
        self.setup_orders_tab()
        self.setup_monitor_tab()
        self.setup_bot_tab()
        self.setup_portfolio_tab()

        # Setup the status bar (QStatusBar is built-in)
        self.statusBar().showMessage("Gotowy")
        
        # Add custom widgets to the status bar for time and latency
        self.status_heartbeat_label = QLabel("♡")
        self.status_heartbeat_label.setStyleSheet("color: red; font-weight: bold; font-size: 14px;")
        self.status_time_label = QLabel()
        self.status_latency_label = QLabel("Latency: ---")

        self.statusBar().addPermanentWidget(self.status_latency_label)
        self.statusBar().addPermanentWidget(self.status_time_label)
        self.statusBar().addPermanentWidget(self.status_heartbeat_label)

        # Timer to update the time
        self.timer = QTimer()
        self.timer.timeout.connect(self.update_status_time)
        self.timer.start(1000) # Update every second
        self.update_status_time()

    def setup_login_tab(self):
        layout = QVBoxLayout(self.tab_login)
        
        # Login Frame
        login_frame = QWidget()
        login_layout = QHBoxLayout(login_frame)
        

        self.login_button = QPushButton("Połącz i zaloguj")
        self.login_button.clicked.connect(self.start_login)
        login_layout.addWidget(self.login_button)
        
        self.disconnect_button = QPushButton("Rozłącz")
        self.disconnect_button.setEnabled(False)
        self.disconnect_button.clicked.connect(self.disconnect)
        login_layout.addWidget(self.disconnect_button)
        
        login_layout.addStretch() # Pushes widgets to the left
        layout.addWidget(login_frame)

        # Filter Frame
        filter_group = QGroupBox(f"Filtr notowań dla {self.TARGET_ISIN}:")
        filter_layout = QHBoxLayout(filter_group)
        
        self.add_filter_button = QPushButton(f"Dodaj {self.TARGET_ISIN} do filtra")
        self.add_filter_button.setEnabled(False)
        filter_layout.addWidget(self.add_filter_button)
        
        self.clear_filter_button = QPushButton("Wyczyść filtr")
        self.clear_filter_button.setEnabled(False)
        filter_layout.addWidget(self.clear_filter_button)
        
        filter_layout.addStretch()
        layout.addWidget(filter_group)
        layout.addStretch() # Pushes everything to the top

    def setup_orders_tab(self):
        layout = QVBoxLayout(self.tab_orders)
        order_group = QGroupBox(f"Nowe zlecenie (Limit, Dzień) dla {self.TARGET_ISIN}:")
        order_layout = QGridLayout(order_group)
        
        row = 0
        order_layout.addWidget(QLabel("Rachunek:"), row, 0)
        self.account_entry = QLineEdit()
        order_layout.addWidget(self.account_entry, row, 1)
        
        order_layout.addWidget(QLabel("Kierunek:"), row, 2)
        self.direction_combo = QComboBox()
        self.direction_combo.addItems(["Kupno", "Sprzedaż"])
        order_layout.addWidget(self.direction_combo, row, 3)
        
        row += 1
        order_layout.addWidget(QLabel("Ilość:"), row, 0)
        self.quantity_entry = QLineEdit("1")
        order_layout.addWidget(self.quantity_entry, row, 1)
        
        order_layout.addWidget(QLabel("Cena (Limit):"), row, 2)
        self.price_entry = QLineEdit()
        order_layout.addWidget(self.price_entry, row, 3)
        
        self.send_order_button = QPushButton("Złóż zlecenie")
        self.send_order_button.setEnabled(False)
        order_layout.addWidget(self.send_order_button, row, 4)
        
        layout.addWidget(order_group)
        layout.addStretch()

    def setup_monitor_tab(self):
        layout = QVBoxLayout(self.tab_monitor)
        
        self.order_tree = QTreeWidget()
        self.order_tree.setColumnCount(11)
        headers = ["ID (DM)", "ID (Klient)", "Status", "Symbol", "K/S", "Ilość", "Pozostało", "Wykonano", "Limit", "Cena ost.", "Czas"]
        self.order_tree.setHeaderLabels(headers)
        col_widths = [100, 80, 120, 80, 60, 60, 70, 70, 70, 70, 140]
        for i, width in enumerate(col_widths):
            self.order_tree.setColumnWidth(i, width)
        
        layout.addWidget(self.order_tree)
        
        self.cancel_order_button = QPushButton("Anuluj Zaznaczone Zlecenie")
        self.cancel_order_button.setEnabled(False)
        self.order_tree.itemSelectionChanged.connect(self.on_treeview_select)
        layout.addWidget(self.cancel_order_button)

    def setup_bot_tab(self):
        layout = QVBoxLayout(self.tab_bot)
        
        tile_frame = QWidget()
        tile_layout = QHBoxLayout(tile_frame)
        tile_layout.setContentsMargins(0, 0, 0, 0)
        
        def create_tile(title):
            frame = QFrame()
            frame.setFrameStyle(QFrame.Shape.Box | QFrame.Shadow.Sunken)
            layout_v = QVBoxLayout(frame)
            label_title = QLabel(title)
            label_title.setFont(QFont('Helvetica', 10, QFont.Weight.Bold))
            layout_v.addWidget(label_title, alignment=Qt.AlignmentFlag.AlignCenter)
            label_value = QLabel("---")
            label_value.setFont(QFont('Helvetica', 18, QFont.Weight.Bold))
            label_value.setStyleSheet("color: blue;")
            layout_v.addWidget(label_value, alignment=Qt.AlignmentFlag.AlignCenter)
            return frame, label_value
        
        bid_frame, self.bid_label = create_tile("BID")
        ask_frame, self.ask_label = create_tile("ASK")
        last_frame, self.last_label = create_tile("LAST")
        lop_frame, self.lop_label = create_tile("LOP")
        be_frame, self.be_label = create_tile("BREAK-EVEN")
        pos_frame, self.pos_label = create_tile("OTWARTE POZYCJE")
        
        tile_layout.addWidget(bid_frame)
        tile_layout.addWidget(ask_frame)
        tile_layout.addWidget(last_frame)
        tile_layout.addWidget(lop_frame)
        tile_layout.addWidget(be_frame)
        tile_layout.addWidget(pos_frame)
        
        layout.addWidget(tile_frame)
        
        params_group = QGroupBox("Parametry Menedżera:")
        params_layout = QHBoxLayout(params_group)
        
        params_layout.addWidget(QLabel("Trailing Stop:"))
        self.stoploss_entry = QLineEdit("10")
        self.stoploss_entry.setMaximumWidth(50)
        params_layout.addWidget(self.stoploss_entry)
        
        params_layout.addWidget(QLabel("Cel dzienny (pkt):"))
        self.daily_goal_entry = QLineEdit("40")
        self.daily_goal_entry.setMaximumWidth(50)
        params_layout.addWidget(self.daily_goal_entry)
        
        params_layout.addStretch()
        layout.addWidget(params_group)
        
        action_frame = QWidget()
        action_layout = QHBoxLayout(action_frame)
        
        self.start_long_button = QPushButton("OTWÓRZ LONG")
        self.start_long_button.setEnabled(False)
        self.start_long_button.setStyleSheet("background-color: lightgreen;")
        action_layout.addWidget(self.start_long_button)
        
        self.start_short_button = QPushButton("OTWÓRZ SHORT")
        self.start_short_button.setEnabled(False)
        self.start_short_button.setStyleSheet("background-color: salmon;")
        action_layout.addWidget(self.start_short_button)
        
        self.close_pos_button = QPushButton("ZAMKNIJ POZYCJĘ (PANIC)")
        self.close_pos_button.setEnabled(False)
        self.close_pos_button.setStyleSheet("background-color: orange;")
        action_layout.addWidget(self.close_pos_button)
        
        self.start_bot_existing_pos_button = QPushButton("START BOT Z ISTNIEJĄCĄ POZYCJĄ")
        self.start_bot_existing_pos_button.setEnabled(False)
        self.start_bot_existing_pos_button.setStyleSheet("background-color: lightblue;")
        action_layout.addWidget(self.start_bot_existing_pos_button)
        
        layout.addWidget(action_frame)
        
        layout.addWidget(QLabel("Log Menedżera:"))
        self.bot_log = QTextEdit()
        self.bot_log.setReadOnly(True)
        layout.addWidget(self.bot_log)

    def setup_portfolio_tab(self):
        layout = QVBoxLayout(self.tab_portfolio)
        layout.addWidget(QLabel("Dane portfela:"))
        self.portfolio_display = QTextEdit()
        self.portfolio_display.setReadOnly(True)
        layout.addWidget(self.portfolio_display)

    def start_login(self):
        """Start the login process in a separate thread"""
        self.log_to_status("Rozpoczynanie logowania...")
        self.login_button.setEnabled(False)
        
        # Create client and thread
        self.bossa_client = BossaClient()
        self.client_thread = QThread()
        
        # Move client to thread
        self.bossa_client.moveToThread(self.client_thread)
        
        # Connect signals
        self.bossa_client.login_success.connect(self.on_login_success)
        self.bossa_client.login_failed.connect(self.on_login_failed)
        self.bossa_client.message_received.connect(self.log_to_status)
        
        # Start the thread and trigger login
        self.client_thread.started.connect(self.bossa_client.login)
        self.client_thread.start()

    def on_login_success(self):
        """Handle successful login"""
        self.log_to_status("Logowanie udane!")
        self.login_button.setEnabled(False)
        self.disconnect_button.setEnabled(True)
        self.send_order_button.setEnabled(True)
        self.add_filter_button.setEnabled(True)
        self.clear_filter_button.setEnabled(True)
        self.start_long_button.setEnabled(True)
        self.start_short_button.setEnabled(True)
        self.close_pos_button.setEnabled(True)
        self.start_bot_existing_pos_button.setEnabled(True)
        
        QMessageBox.information(self, "Sukces", "Logowanie udane!")

    def on_login_failed(self, error_message):
        """Handle login failure"""
        self.log_to_status(f"Błąd logowania: {error_message}")
        self.login_button.setEnabled(True)
        self.disconnect_button.setEnabled(False)
        
        QMessageBox.critical(self, "Błąd logowania", error_message)
        
        # Clean up thread
        if self.client_thread and self.client_thread.isRunning():
            self.client_thread.quit()
            self.client_thread.wait()

    def disconnect(self):
        """Disconnect from the server"""
        self.log_to_status("Rozłączanie...")
        
        if self.bossa_client:
            self.bossa_client.disconnect()
        
        if self.client_thread and self.client_thread.isRunning():
            self.client_thread.quit()
            self.client_thread.wait()
        
        self.login_button.setEnabled(True)
        self.disconnect_button.setEnabled(False)
        self.send_order_button.setEnabled(False)
        self.add_filter_button.setEnabled(False)
        self.clear_filter_button.setEnabled(False)
        self.start_long_button.setEnabled(False)
        self.start_short_button.setEnabled(False)
        self.close_pos_button.setEnabled(False)
        self.start_bot_existing_pos_button.setEnabled(False)
        
        self.log_to_status("Rozłączono")

    def update_status_time(self):
        now = datetime.now().strftime("%H:%M:%S")
        self.status_time_label.setText(f"Czas: {now}")

    def update_heartbeat_icon(self, icon_char):
        self.status_heartbeat_label.setText(icon_char)

    def update_latency_display(self, text):
        self.status_latency_label.setText(text)

    def log_to_status(self, message):
        """Append message to the bot log with timestamp"""
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.bot_log.append(f"[{timestamp}] {message}")
        self.bot_log.verticalScrollBar().setValue(
            self.bot_log.verticalScrollBar().maximum()
        )

    def on_treeview_select(self):
        """Slot for treeview selection change"""
        selected = self.order_tree.selectedItems()
        self.cancel_order_button.setEnabled(len(selected) > 0)

# Main execution
if __name__ == '__main__':
    app = QApplication(sys.argv)
    window = BossaApp()
    window.show()
    sys.exit(app.exec())