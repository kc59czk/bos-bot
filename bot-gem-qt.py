import sys
import threading
import queue
import socket
import struct
import winreg
import xml.etree.ElementTree as ET
import time
from datetime import datetime
from enum import Enum
import re
import os
import random

# PyQt6 imports
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QLineEdit, QPushButton, QTabWidget, QTreeWidget, QTreeWidgetItem, QTextEdit,
    QComboBox, QFrame, QMessageBox, QHeaderView, QSplitter, QPlainTextEdit,QGroupBox,QCheckBox,QFileDialog
)
from PyQt6.QtCore import QTimer, Qt
from PyQt6.QtGui import QFont

class BotState(Enum):
    STOPPED = 0
    IDLE = 1
    WAITING_FOR_ENTRY_FILL = 2
    IN_LONG_POSITION = 3
    IN_SHORT_POSITION = 4

class BossaAppPyQt(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("BossaAPI - Menedżer Transakcji (PyQt6)")
        self.setGeometry(100, 30, 1100, 800)

        self.client = None
        self.queue = queue.Queue()
#        self.TARGET_ISIN = "PL0GF0031252"
        self.TARGET_ISIN = "PL0GF0031880"  # fw20z2520
        self.orders = {}
        # NEW: Flag to prevent multiple bot confirmation dialogs
        self.is_bot_confirmation_pending = False
        
        self.STATUS_MAP = {'0': 'Nowe', '1': 'Aktywne', '2': 'Wykonane', '4': 'Anulowane', '5': 'Zastąpione', '6': 'Oczekuje na anul.', '8': 'Odrzucone', 'E': 'Oczekuje na mod.'}
        self.SIDE_MAP = {'1': 'Kupno', '2': 'Sprzedaż'}
        self.TREE_COLS = ('id_dm', 'id_klienta', 'status', 'symbol', 'k_s', 'ilosc', 'pozostalo', 'wykonano', 'limit', 'cena_ost', 'czas')

        self.create_widgets()
        
        self.queue_timer = QTimer(self)
        self.queue_timer.timeout.connect(self.process_queue)
        self.queue_timer.start(100)

    # --- UI Creation methods (unchanged from previous version) ---
    def create_widgets(self):
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QHBoxLayout(central_widget)  # can chage to QVBoxLayout
        self.tabs = QTabWidget()
        main_layout.addWidget(self.tabs)
        self.create_login_tab()
        self.create_orders_tab()
        self.create_monitor_tab()
        self.create_bot_tab()
        self.create_portfolio_tab()
        self.create_charts_tab()  # New Charts tab
        bottom_logs_splitter = QSplitter(Qt.Orientation.Vertical)
        top_panel_widget = QWidget()
        top_panel_layout = QVBoxLayout(top_panel_widget)
        top_panel_layout.setContentsMargins(0,0,0,0)
        top_panel_layout.addWidget(QLabel("Log statusu:"))
        self.status_log = QPlainTextEdit()
        self.status_log.setReadOnly(True)
        top_panel_layout.addWidget(self.status_log)
        top_panel_layout.addWidget(QLabel("Surowe komunikaty (kanał asynchroniczny):"))
        self.async_messages = QPlainTextEdit()
        self.async_messages.setReadOnly(True)
        self.async_messages.setStyleSheet("background-color: #f0f0f0;")
        top_panel_layout.addWidget(self.async_messages)
        bottom_logs_splitter.addWidget(top_panel_widget)
        main_layout.addWidget(bottom_logs_splitter, stretch=1)
        self.statusBar = self.statusBar()
        self.status_latency_label = QLabel("Latency: --- ")
        self.status_time_label = QLabel("Czas: --:--:--")
        self.heartbeat_label = QLabel("♡")
        self.heartbeat_label.setFont(QFont("Arial", 12, QFont.Weight.Bold))
        self.heartbeat_label.setStyleSheet("color: red;")
        self.statusBar.addPermanentWidget(self.status_latency_label)
        self.statusBar.addPermanentWidget(self.status_time_label)
        self.statusBar.addPermanentWidget(self.heartbeat_label)
        self.time_timer = QTimer(self)
        self.time_timer.timeout.connect(self._update_status_time)
        self.time_timer.start(1000)

    def _update_status_time(self):
        now = datetime.now().strftime("%H:%M:%S")
        self.status_time_label.setText(f"Czas: {now}")

    def _create_tile(self, title):
        frame = QFrame()
        frame.setFrameShape(QFrame.Shape.StyledPanel)
        frame.setFrameShadow(QFrame.Shadow.Sunken)
        layout = QVBoxLayout(frame)
        title_label = QLabel(title)
        title_label.setFont(QFont("Helvetica", 10, QFont.Weight.Bold))
        title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title_label)
        value_label = QLabel("---")
        value_label.setFont(QFont("Helvetica", 18, QFont.Weight.Bold))
        value_label.setStyleSheet("color: blue;")
        value_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(value_label)
        return frame, value_label

    def create_bot_tab(self):
        tab_bot = QWidget()
        layout = QVBoxLayout(tab_bot)

 # --- Single row with all tiles ---
        tile_widget = QWidget()
        tile_layout = QHBoxLayout(tile_widget)

# Create all tile frames and labels
        bid_frame, self.bid_label = self._create_tile("BID")
        bid_size_frame, self.bid_size_label = self._create_tile("BID Size")
        ask_frame, self.ask_label = self._create_tile("ASK")
        ask_size_frame, self.ask_size_label = self._create_tile("ASK Size")
        last_frame, self.last_label = self._create_tile("LAST")
        lop_frame, self.lop_label = self._create_tile("LOP")
        be_frame, self.be_label = self._create_tile("BREAK-EVEN")
        pos_frame, self.pos_label = self._create_tile("OTWARTE POZYCJE")

# Add all widgets to the single horizontal layout
        tile_layout.addWidget(bid_frame)
        tile_layout.addWidget(bid_size_frame)
        tile_layout.addWidget(ask_frame)
        tile_layout.addWidget(ask_size_frame)
        tile_layout.addWidget(last_frame)
        tile_layout.addWidget(lop_frame)
        tile_layout.addWidget(be_frame)
        tile_layout.addWidget(pos_frame)

# Add the single widget with all tiles to the main layout
        layout.addWidget(tile_widget)

        # --- Bot Parameters ---
        params_frame = QFrame()
        params_frame.setFrameShape(QFrame.Shape.StyledPanel)
        params_layout = QHBoxLayout(params_frame)
        params_layout.addWidget(QLabel("<b>Parametry Menedżera:</b>"))
        params_layout.addWidget(QLabel("Trailing Stop:"))
        self.stoploss_entry = QLineEdit("10")
        self.stoploss_entry.setFixedWidth(50)
        params_layout.addWidget(self.stoploss_entry)
        params_layout.addWidget(QLabel("Cel dzienny (pkt):"))
        self.daily_goal_entry = QLineEdit("40")
        self.daily_goal_entry.setFixedWidth(50)
        params_layout.addWidget(self.daily_goal_entry)
        params_layout.addStretch()
        layout.addWidget(params_frame)

        # --- Bot Actions ---
        action_widget = QWidget()
        action_layout = QHBoxLayout(action_widget)
        self.start_long_button = QPushButton("OTWÓRZ LONG")
        self.start_long_button.setStyleSheet("background-color: lightgreen;")
        self.start_long_button.setEnabled(False)
        self.start_long_button.clicked.connect(lambda: self.start_trade("Kupno"))
        action_layout.addWidget(self.start_long_button)

        self.start_short_button = QPushButton("OTWÓRZ SHORT")
        self.start_short_button.setStyleSheet("background-color: salmon;")
        self.start_short_button.setEnabled(False)
        self.start_short_button.clicked.connect(lambda: self.start_trade("Sprzedaż"))
        action_layout.addWidget(self.start_short_button)
        
        self.close_pos_button = QPushButton("ZAMKNIJ POZYCJĘ (PANIC)")
        self.close_pos_button.setStyleSheet("background-color: orange;")
        self.close_pos_button.setEnabled(False)
        self.close_pos_button.clicked.connect(self.close_trade_manually)
        action_layout.addWidget(self.close_pos_button)
        
        self.start_bot_existing_pos_button = QPushButton("START BOT Z ISTNIEJĄCĄ POZYCJĄ")
        self.start_bot_existing_pos_button.setStyleSheet("background-color: lightblue;")
        self.start_bot_existing_pos_button.setEnabled(False)
        self.start_bot_existing_pos_button.clicked.connect(self.start_bot_with_existing_position)
        action_layout.addWidget(self.start_bot_existing_pos_button)
        
        layout.addWidget(action_widget)
        layout.addWidget(QLabel("Log Menedżera:"))
        self.bot_log = QPlainTextEdit()
        self.bot_log.setReadOnly(True)
        layout.addWidget(self.bot_log, stretch=1)
        self.tabs.addTab(tab_bot, "Menedżer Transakcji")

    def create_login_tab(self):
        tab_login = QWidget()
        layout = QVBoxLayout(tab_login)
        login_frame = QFrame()
        login_layout = QHBoxLayout(login_frame)
        login_layout.addWidget(QLabel("Użytkownik:"))
        self.username_entry = QLineEdit("BOS")
        login_layout.addWidget(self.username_entry)
        login_layout.addWidget(QLabel("Hasło:"))
        self.password_entry = QLineEdit("BOS")
        self.password_entry.setEchoMode(QLineEdit.EchoMode.Password)
        login_layout.addWidget(self.password_entry)
        self.login_button = QPushButton("Połącz i zaloguj")
        self.login_button.clicked.connect(self.start_login_thread)
        login_layout.addWidget(self.login_button)
        self.disconnect_button = QPushButton("Rozłącz")
        self.disconnect_button.setEnabled(False)
        self.disconnect_button.clicked.connect(self.disconnect)
        login_layout.addWidget(self.disconnect_button)
        login_layout.addStretch()
        layout.addWidget(login_frame)
        filter_frame = QFrame()
        filter_frame.setFrameShape(QFrame.Shape.StyledPanel)
        filter_layout = QHBoxLayout(filter_frame)
        filter_layout.addWidget(QLabel(f"<b>Filtr notowań dla {self.TARGET_ISIN}:</b>"))
        self.add_filter_button = QPushButton(f"Dodaj {self.TARGET_ISIN} do filtra")
        self.add_filter_button.setEnabled(False)
        self.add_filter_button.clicked.connect(self.add_to_filter)
        filter_layout.addWidget(self.add_filter_button)
        self.clear_filter_button = QPushButton("Wyczyść filtr")
        self.clear_filter_button.setEnabled(False)
        self.clear_filter_button.clicked.connect(self.clear_filter)
        filter_layout.addWidget(self.clear_filter_button)
        filter_layout.addStretch()
        layout.addWidget(filter_frame)
        layout.addStretch()
        self.tabs.addTab(tab_login, "Połączenie i Filtr")

    def create_orders_tab(self):
        tab_orders = QWidget()
        layout = QVBoxLayout(tab_orders)
        order_frame = QFrame()
        order_frame.setFrameShape(QFrame.Shape.StyledPanel)
        order_frame_layout = QVBoxLayout(order_frame)
        order_frame_layout.addWidget(QLabel(f"<b>Nowe zlecenie (Limit, Dzień) dla {self.TARGET_ISIN}:</b>"))
        controls_widget = QWidget()
        controls_layout = QHBoxLayout(controls_widget)
        controls_layout.addWidget(QLabel("Rachunek:"))
        self.account_entry = QLineEdit()
        controls_layout.addWidget(self.account_entry)
        controls_layout.addWidget(QLabel("Kierunek:"))
        self.direction_combo = QComboBox()
        self.direction_combo.addItems(["Kupno", "Sprzedaż"])
        controls_layout.addWidget(self.direction_combo)
        controls_layout.addWidget(QLabel("Ilość:"))
        self.quantity_entry = QLineEdit("1")
        self.quantity_entry.setFixedWidth(80)
        controls_layout.addWidget(self.quantity_entry)
        controls_layout.addWidget(QLabel("Cena (Limit):"))
        self.price_entry = QLineEdit()
        self.price_entry.setFixedWidth(100)
        controls_layout.addWidget(self.price_entry)
        self.send_order_button = QPushButton("Złóż zlecenie")
        self.send_order_button.setEnabled(False)
        self.send_order_button.clicked.connect(self.send_order)
        controls_layout.addWidget(self.send_order_button)
        controls_layout.addStretch()
        order_frame_layout.addWidget(controls_widget)
        layout.addWidget(order_frame)
        layout.addStretch()
        self.tabs.addTab(tab_orders, "Zlecenie Ręczne")
    
    def create_monitor_tab(self):
        tab_monitor = QWidget()
        layout = QVBoxLayout(tab_monitor)
        col_map = {'id_dm': 'ID (DM)', 'id_klienta': 'ID (Klient)', 'status': 'Status', 'symbol': 'Symbol', 'k_s': 'K/S', 'ilosc': 'Ilość', 'pozostalo': 'Pozostało', 'wykonano': 'Wykonano', 'limit': 'Limit', 'cena_ost': 'Cena ost.', 'czas': 'Czas'}
        self.order_tree = QTreeWidget()
        self.order_tree.setColumnCount(len(self.TREE_COLS))
        self.order_tree.setHeaderLabels(list(col_map.values()))
        self.order_tree.header().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.order_tree.header().setStretchLastSection(False)
        layout.addWidget(self.order_tree)
        self.cancel_order_button = QPushButton("Anuluj Zaznaczone Zlecenie")
        self.cancel_order_button.setEnabled(False)
        self.cancel_order_button.clicked.connect(self.cancel_selected_order)
        layout.addWidget(self.cancel_order_button)
        self.order_tree.itemSelectionChanged.connect(self.on_treeview_select)
        self.tabs.addTab(tab_monitor, "Monitor Zleceń")

    def create_portfolio_tab(self):
        tab_portfolio = QWidget()
        layout = QVBoxLayout(tab_portfolio)
        layout.addWidget(QLabel("Dane portfela:"))
        self.portfolio_display = QTextEdit()
        self.portfolio_display.setReadOnly(True)
        layout.addWidget(self.portfolio_display)
        self.tabs.addTab(tab_portfolio, "Portfel")


#============================================
    def create_charts_tab(self):
        """Create the lightweight charts tab"""
        charts_tab = QWidget()
        charts_layout = QVBoxLayout(charts_tab)
        
        # Chart selection controls
        control_widget = QWidget()
        control_layout = QHBoxLayout(control_widget)
        
        # Symbol selection
        control_layout.addWidget(QLabel("Symbol:"))
        self.chart_symbol_input = QLineEdit("FW20Z2520")
        control_layout.addWidget(self.chart_symbol_input)
        
        # Timeframe selection
        control_layout.addWidget(QLabel("Timeframe:"))
        self.timeframe_combo = QComboBox()
        self.timeframe_combo.addItems(["Tick", "1m", "5m", "15m", "30m", "1h", "4h", "1d"])
        control_layout.addWidget(self.timeframe_combo)
        
        # Data file selection
        control_layout.addWidget(QLabel("Data File:"))
        self.data_file_input = QLineEdit("historical_data.csv")
        control_layout.addWidget(self.data_file_input)
        self.browse_file_button = QPushButton("Browse...")
        self.browse_file_button.clicked.connect(self.browse_data_file)
        control_layout.addWidget(self.browse_file_button)
        
        # Load chart button
        self.load_chart_button = QPushButton("Load Historical Data")
        self.load_chart_button.clicked.connect(self.load_historical_data)
        control_layout.addWidget(self.load_chart_button)
        
        # Start live update button
        self.start_live_button = QPushButton("Start Live Updates")
        self.start_live_button.clicked.connect(self.start_live_updates)
        self.start_live_button.setEnabled(False)
        control_layout.addWidget(self.start_live_button)
        
        control_layout.addStretch()
        charts_layout.addWidget(control_widget)
        
        # Chart container
        chart_container = QWidget()
        chart_layout = QVBoxLayout(chart_container)
        
        # Placeholder for the chart - you'll integrate lightweight-charts here
        self.chart_widget = QWidget()
        self.chart_widget.setMinimumSize(800, 500)
        self.chart_widget.setStyleSheet("background-color: #f8f8f8; border: 1px solid #ccc;")
        
        # Placeholder label
        placeholder_label = QLabel("Lightweight Charts will be displayed here\nHistorical data will be loaded from file")
        placeholder_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        placeholder_layout = QVBoxLayout(self.chart_widget)
        placeholder_layout.addWidget(placeholder_label)
        
        chart_layout.addWidget(self.chart_widget)
        charts_layout.addWidget(chart_container)
        
        # Status label for chart
        self.chart_status_label = QLabel("Ready to load historical data")
        charts_layout.addWidget(self.chart_status_label)
        
        # Add indicators/studies section
        indicators_group = QGroupBox("Indicators")
        indicators_layout = QHBoxLayout(indicators_group)
        
        self.sma_checkbox = QCheckBox("SMA")
        self.ema_checkbox = QCheckBox("EMA")
        self.rsi_checkbox = QCheckBox("RSI")
        self.macd_checkbox = QCheckBox("MACD")
        
        indicators_layout.addWidget(self.sma_checkbox)
        indicators_layout.addWidget(self.ema_checkbox)
        indicators_layout.addWidget(self.rsi_checkbox)
        indicators_layout.addWidget(self.macd_checkbox)
        indicators_layout.addStretch()
        
        charts_layout.addWidget(indicators_group)
        
        self.tabs.addTab(charts_tab, "Charts")
        
        # Initialize chart data storage
        self.chart_data = []
        self.current_symbol = ""
        self.live_update_timer = QTimer(self)
        self.live_update_timer.timeout.connect(self.update_live_data)

      #  self.tabs.addTab(charts_tab, "Wykres")

    def browse_data_file(self):
        """Open file dialog to select data file"""
        file_path, _ = QFileDialog.getOpenFileName(
            self, "Select Historical Data File", "", "CSV Files (*.csv);;All Files (*)"
        )
        if file_path:
            self.data_file_input.setText(file_path)

    def load_historical_data(self):
        """Load historical data from file and initialize chart"""
        try:
            file_path = self.data_file_input.text()
            symbol = self.chart_symbol_input.text()
            timeframe = self.timeframe_combo.currentText()
            
            if not os.path.exists(file_path):
                self.chart_status_label.setText(f"Error: File not found - {file_path}")
                return
            
            # Read and parse historical data
            self.chart_data = self.parse_historical_data(file_path, symbol)
            
            if not self.chart_data:
                self.chart_status_label.setText("No data found for the specified symbol")
                return
            
            self.current_symbol = symbol
            self.chart_status_label.setText(f"Loaded {len(self.chart_data)} records for {symbol}")
            
            # Initialize chart with historical data
            self.initialize_chart(self.chart_data, timeframe)
            
            # Enable live updates
            self.start_live_button.setEnabled(True)
            self.load_chart_button.setEnabled(False)
            
        except Exception as e:
            self.chart_status_label.setText(f"Error loading data: {str(e)}")

    def parse_historical_data(self, file_path, symbol):
        """Parse historical data from CSV file"""
        data = []
        try:
            with open(file_path, 'r') as file:
                # Skip header
                next(file)
                
                for line in file:
                    parts = line.strip().split(',')
                    if len(parts) >= 11 and parts[0] == symbol:
                        try:
                            # Parse the data according to your format
                            bar_data = {
                                'time': int(float(parts[3])),  # _quote_date_tms as timestamp
                                'open': float(parts[4]),       # _quote_open
                                'high': float(parts[5]),       # _quote_max
                                'low': float(parts[6]),        # _quote_min
                                'close': float(parts[7]),      # _quote
                                'volume': float(parts[8])      # _volume
                            }
                            data.append(bar_data)
                        except ValueError:
                            continue  # Skip invalid lines
            
            # Sort by timestamp
            data.sort(key=lambda x: x['time'])
            
        except Exception as e:
            self.status_log.appendPlainText(f"Error parsing historical data: {str(e)}")
        
        return data

    def initialize_chart(self, data, timeframe):
        """Initialize the chart with historical data"""
        # This is where you'll integrate with lightweight-charts-python
        # For now, just display the data summary
        self.status_log.appendPlainText(
            f"Chart initialized with {len(data)} bars for {self.current_symbol} "
            f"({timeframe}) from {datetime.fromtimestamp(data[0]['time'])} to "
            f"{datetime.fromtimestamp(data[-1]['time'])}"
        )
        
        # TODO: Replace with actual lightweight-charts integration
        # Example: self.chart.set(data)
        
        # Update placeholder with data summary
        if hasattr(self, 'chart_widget'):
            for i in reversed(range(self.chart_widget.layout().count())): 
                self.chart_widget.layout().itemAt(i).widget().setParent(None)
            
            summary_label = QLabel(
                f"Historical Data Loaded:\n"
                f"Symbol: {self.current_symbol}\n"
                f"Timeframe: {timeframe}\n"
                f"Bars: {len(data)}\n"
                f"Date Range: {datetime.fromtimestamp(data[0]['time'])} - {datetime.fromtimestamp(data[-1]['time'])}\n"
                f"Price Range: {min(d['low'] for d in data):.2f} - {max(d['high'] for d in data):.2f}"
            )
            summary_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.chart_widget.layout().addWidget(summary_label)

    def start_live_updates(self):
        """Start receiving live market data updates"""
        self.chart_status_label.setText("Live updates started - waiting for market data...")
        self.start_live_button.setEnabled(False)
        
        # Simulate live updates (replace with your actual market data connection)
        self.live_update_timer.start(1000)  # Update every second

    def update_live_data(self):
        """Update chart with new live market data"""
        # This would be called when new market data arrives
        # For demonstration, we'll simulate some data
        if self.chart_data:
            last_bar = self.chart_data[-1].copy()
            last_bar['time'] = int(time.time())  # Current timestamp
            last_bar['close'] = last_bar['close'] * (1 + (random.random() - 0.5) * 0.01)  # Random price change
            last_bar['high'] = max(last_bar['high'], last_bar['close'])
            last_bar['low'] = min(last_bar['low'], last_bar['close'])
            last_bar['volume'] = random.randint(1, 10)
            
            self.chart_data.append(last_bar)
            
            # Update chart with new data
            # TODO: Replace with actual lightweight-charts update
            self.status_log.appendPlainText(
                f"Live update: {datetime.fromtimestamp(last_bar['time'])} - "
                f"Price: {last_bar['close']:.2f}, Volume: {last_bar['volume']}"
            )
#============================================
    # --- Confirmation and Action Handlers ---

    def confirm_dialog(self, title, message):
        """NEW: Centralized confirmation dialog."""
        reply = QMessageBox.question(self, title, message,
                                     QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                     QMessageBox.StandardButton.No)
        return reply == QMessageBox.StandardButton.Yes

    def on_treeview_select(self):
        selected_items = self.order_tree.selectedItems()
        if not selected_items:
            self.cancel_order_button.setEnabled(False)
            return
        item = selected_items[0]
        status = item.text(self.TREE_COLS.index('status'))
        self.cancel_order_button.setEnabled(status in ['Nowe', 'Aktywne'])

    def cancel_selected_order(self):
        selected_items = self.order_tree.selectedItems()
        if not selected_items:
            QMessageBox.warning(self, "Brak zaznaczenia", "Proszę zaznaczyć zlecenie do anulowania.")
            return
            
        item = selected_items[0]
        order_details = {
            'id_dm': item.text(self.TREE_COLS.index('id_dm')),
            'k_s_text': item.text(self.TREE_COLS.index('k_s')),
            'ilosc': item.text(self.TREE_COLS.index('ilosc')),
            'rachunek': self.account_entry.text()
        }
        
        if not order_details['rachunek']:
            QMessageBox.critical(self, "Błąd", "Nie można anulować zlecenia bez podanego numeru rachunku.")
            return

        # NEW: Confirmation step
        msg = f"Czy na pewno chcesz ANULOWAĆ zlecenie ID {order_details['id_dm']} ({order_details['k_s_text']} {order_details['ilosc']} szt.)?"
        if self.confirm_dialog("Potwierdzenie Anulowania", msg):
            if self.client:
                self.log_message(self.status_log, f"Wysyłanie prośby o anulowanie zlecenia {order_details['id_dm']}...")
                threading.Thread(target=self.client.cancel_order, args=(order_details,), daemon=True).start()

    def _flash_heartbeat(self):
        self.heartbeat_label.setText("❤")
        QTimer.singleShot(300, lambda: self.heartbeat_label.setText("♡"))
    
    def process_queue(self):
        try:
            while not self.queue.empty():
                message_type, data = self.queue.get_nowait()
                # ... (message handling for PORTFOLIO_UPDATE, MARKET_DATA_UPDATE, etc. is unchanged)
                if message_type == "PORTFOLIO_UPDATE":
                    self.display_portfolio(data['portfolio_data'])
                    self.pos_label.setText(str(data.get('open_position_qty', '---')))
                    if data.get('portfolio_data') and not self.account_entry.text():
                        first_account = next(iter(data['portfolio_data']))
                        self.account_entry.setText(first_account)
                    
                    if data.get('existing_position_found'):
                        self.start_bot_existing_pos_button.setEnabled(True)
                        pos_details = data['existing_position_details']
                        self.log_message(self.bot_log, f"Znaleziono istniejącą pozycję: {pos_details['quantity']} szt. {pos_details['symbol']} ({pos_details['position_type']}). Możesz uruchomić bota z tą pozycją.")
                    else:
                        self.start_bot_existing_pos_button.setEnabled(False)

                elif message_type == "MARKET_DATA_UPDATE":
                    if data.get('isin') == self.TARGET_ISIN:
                        self.bid_label.setText(f"{data.get('bid', '---'):.2f}")
                        self.ask_label.setText(f"{data.get('ask', '---'):.2f}")
                        self.last_label.setText(f"{data.get('last_price', '---'):.2f}")
                        self.lop_label.setText(f"{data.get('lop', '---')}")
                        self.bid_size_label.setText(str(data.get('bid_size', '---')))
                        self.ask_size_label.setText(str(data.get('ask_size', '---')))

                        if not self.price_entry.hasFocus():
                            price = data.get('last_price')
                            if price:
                                self.price_entry.setText(f"{price:.2f}")

                # NEW: Handle confirmation requests from the bot
                elif message_type == "CONFIRM_BOT_ACTION":
                    if self.is_bot_confirmation_pending:
                        continue # Skip if a dialog is already open

                    self.is_bot_confirmation_pending = True
                    details = data['details']
                    action_type = data['action_type']
                    
                    if action_type == "MOVE_STOP":
                        msg = (f"BOT SUGERUJE AKCJĘ: Przesunięcie Stop-Loss.\n\n"
                               f"ANULUJ Zlecenie ID: {details['old_stop_id']}\n"
                               f"ZŁÓŻ NOWE Zlecenie: {details['direction']} {details['quantity']} szt. @ {details['new_price']:.2f}\n\n"
                               "Czy potwierdzasz tę operację?")
                        
                        if self.confirm_dialog("Potwierdzenie Akcji Bota", msg):
                            self.log_message(self.bot_log, "Użytkownik potwierdził przesunięcie stop-lossa.")
                            # Execute confirmed actions
                            self.client.execute_bot_action(data)
                        else:
                            self.log_message(self.bot_log, "Użytkownik odrzucił przesunięcie stop-lossa.")
                            # Inform client that action was rejected
                            self.client.bot_action_rejected()
                    
                    self.is_bot_confirmation_pending = False
                
                # ... other message types from previous version
                elif message_type == "BOT_STATE_UPDATE":
                    entry_price = data.get('entry_price')
                    self.close_pos_button.setEnabled(True if entry_price else False)
                    if entry_price:
                        be_price = entry_price + 2 * data.get('commission', 1) if data.get('position_type') == 'LONG' else entry_price - 2 * data.get('commission', 1)
                        self.be_label.setText(f"{be_price:.2f}")
                    else:
                        self.be_label.setText("---")
                        self.start_long_button.setEnabled(True)
                        self.start_short_button.setEnabled(True)
                        self.start_bot_existing_pos_button.setEnabled(False)

                elif message_type == "BOT_LOG": self.log_message(self.bot_log, data)
                elif message_type == "EXEC_REPORT": self.update_order_monitor(data)
                elif message_type == "LOG": self.log_message(self.status_log, data)
                elif message_type == "LOGIN_SUCCESS":
                    self.log_message(self.status_log, f"Logowanie udane! Dodaj {self.TARGET_ISIN} do filtra, aby otrzymywać ceny.")
                    self.disconnect_button.setEnabled(True); self.add_filter_button.setEnabled(True)
                    self.clear_filter_button.setEnabled(True); self.send_order_button.setEnabled(True)
                    self.start_long_button.setEnabled(True); self.start_short_button.setEnabled(True)
                elif message_type == "ASYNC_MSG":
                    if "<Heartbeat" in data: self._flash_heartbeat()
                    elif "<ApplMsgRpt" in data:
                        try:
                            root = ET.fromstring(data); appl_msg = root.find("ApplMsgRpt")
                            txt_value = appl_msg.get("Txt") if appl_msg is not None else None
                            if txt_value: self.status_latency_label.setText(f"Latency: {txt_value.strip()} ")
                            else: self.log_message(self.async_messages, "Otrzymano ApplMsgRpt (brak Txt)")
                        except Exception as e: self.log_message(self.async_messages, f"Błąd parsowania ApplMsgRpt: {e}")
                    else: self.log_message(self.async_messages, data.strip())
                elif message_type == "DISCONNECTED":
                    self.log_message(self.status_log, "Rozłączono.")
                    self.login_button.setEnabled(True); self.disconnect_button.setEnabled(False)
                    self.add_filter_button.setEnabled(False); self.clear_filter_button.setEnabled(False)
                    self.send_order_button.setEnabled(False); self.start_long_button.setEnabled(False)
                    self.start_short_button.setEnabled(False); self.close_pos_button.setEnabled(False)
                    self.start_bot_existing_pos_button.setEnabled(False); self.client = None
                    self.orders.clear(); self.order_tree.clear()
                elif message_type == "LOGIN_FAIL":
                    self.log_message(self.status_log, f"Logowanie nie powiodło się: {data}")
                    self.login_button.setEnabled(True)

        except queue.Empty:
            pass
            
    def start_trade(self, direction):
        if not self.client:
            self.log_message(self.bot_log, "Błąd: Klient nie jest połączony.")
            return
        try:
            params = {'account': self.account_entry.text(), 'trailing_stop': int(self.stoploss_entry.text()), 'daily_goal': int(self.daily_goal_entry.text()), 'commission': 1}
            if not params['account']:
                self.log_message(self.bot_log, "Błąd: Numer rachunku jest wymagany.")
                return
        except ValueError:
            self.log_message(self.bot_log, "Błąd: Parametry menedżera muszą być liczbami.")
            return
        
        # NEW: Confirmation step
        trade_type = "LONG" if direction == "Kupno" else "SHORT"
        msg = f"Czy na pewno chcesz uruchomić bota i otworzyć pozycję {trade_type}?"
        if self.confirm_dialog("Potwierdzenie Uruchomienia Bota", msg):
            self.start_long_button.setEnabled(False)
            self.start_short_button.setEnabled(False)
            self.start_bot_existing_pos_button.setEnabled(False)
            self.log_message(self.bot_log, f"Inicjowanie pozycji {direction}...")
            threading.Thread(target=self.client.start_trade_manager, args=(params, direction), daemon=True).start()

    def start_bot_with_existing_position(self):
        if not self.client:
            self.log_message(self.bot_log, "Błąd: Klient nie jest połączony.")
            return
        try:
            params = {'account': self.account_entry.text(), 'trailing_stop': int(self.stoploss_entry.text()), 'daily_goal': int(self.daily_goal_entry.text()), 'commission': 1}
            if not params['account']:
                self.log_message(self.bot_log, "Błąd: Numer rachunku jest wymagany.")
                return
        except ValueError:
            self.log_message(self.bot_log, "Błąd: Parametry menedżera muszą być liczbami.")
            return
        
        # NEW: Confirmation step
        msg = "Czy na pewno chcesz uruchomić bota, aby zarządzał już ISTNIEJĄCĄ pozycją?"
        if self.confirm_dialog("Potwierdzenie Uruchomienia Bota", msg):
            self.start_long_button.setEnabled(False)
            self.start_short_button.setEnabled(False)
            self.start_bot_existing_pos_button.setEnabled(False)
            self.log_message(self.bot_log, "Uruchamiam bota z istniejącą pozycją...")
            threading.Thread(target=self.client.start_trade_manager_with_existing_position, args=(params,), daemon=True).start()

    def close_trade_manually(self):
        if self.client and self.client.manager_state in [BotState.IN_LONG_POSITION, BotState.IN_SHORT_POSITION]:
             # NEW: Confirmation step
            msg = "Czy na pewno chcesz natychmiast zamknąć pozycję po cenie rynkowej (PANIC)?"
            if self.confirm_dialog("Potwierdzenie Zamknięcia Pozycji", msg):
                self.log_message(self.bot_log, "Ręczne zamykanie pozycji...")
                self.client.close_trade_manually()

    def send_order(self):
        account = self.account_entry.text()
        direction = self.direction_combo.currentText()
        quantity_str = self.quantity_entry.text()
        price_str = self.price_entry.text()
        if not all([account, direction, quantity_str, price_str]):
            self.log_message(self.status_log, "BŁĄD: Wszystkie pola zlecenia muszą być wypełnione."); return
        try:
            quantity = int(quantity_str)
            if quantity <= 0: raise ValueError
        except ValueError:
            self.log_message(self.status_log, "BŁĄD: Ilość musi być dodatnią liczbą całkowitą."); return
        try:
            price = float(price_str.replace(',', '.'))
            if price <= 0: raise ValueError
        except ValueError:
            self.log_message(self.status_log, "BŁĄD: Cena musi być dodatnią liczbą."); return

        # NEW: Confirmation step
        msg = (f"Czy na pewno chcesz złożyć zlecenie:\n\n"
               f"Kierunek: {direction}\n"
               f"Ilość: {quantity} szt.\n"
               f"Instrument: {self.TARGET_ISIN}\n"
               f"Cena limit: {price:.2f} PLN\n"
               f"Rachunek: {account}")
        
        if self.confirm_dialog("Potwierdzenie Zlecenia", msg):
            if self.client:
                self.log_message(self.status_log, f"Przygotowywanie zlecenia {direction} {quantity} szt. {self.TARGET_ISIN} z limitem {price}...")
                params = (account, direction, quantity, price)
                threading.Thread(target=self.client.send_limit_order, args=params, daemon=True).start()

    def update_order_monitor(self, data):
        order_id = data.get('id_dm');
        if not order_id: return
        data['status'] = self.STATUS_MAP.get(data['status'], data['status'])
        data['k_s'] = self.SIDE_MAP.get(data['k_s'], data['k_s'])
        values = [data.get(col, '') for col in self.TREE_COLS]
        if order_id in self.orders:
            item = self.orders[order_id]
            for i, value in enumerate(values): item.setText(i, str(value))
        else:
            item = QTreeWidgetItem(values); self.order_tree.addTopLevelItem(item)
            self.orders[order_id] = item
    
    def log_message(self, widget, message):
        if isinstance(message, str) and message.startswith("<FIXML"):
            message = re.sub(r'^<FIXML[^>]*>', '', message, flags=re.DOTALL)
            message = re.sub(r'</FIXML>$', '', message, flags=re.DOTALL)
            message = message.strip()
        timestamp = time.strftime('%H:%M:%S')
        widget.appendPlainText(f"{timestamp} - {message}")

    # --- Other methods (unchanged) ---
    def start_login_thread(self):
        self.login_button.setEnabled(False); self.disconnect_button.setEnabled(False)
        username = self.username_entry.text(); password = self.password_entry.text()
        if username == "TWOJA_NAZWA_UŻYTKOWNIKA" or password == "TWOJE_HASŁO":
            self.log_message(self.status_log, "BŁĄD: Wprowadź swoje dane logowania.")
            self.login_button.setEnabled(True); return
        self.client = BossaAPIClient(username, password, self.queue)
        threading.Thread(target=self.client.run, daemon=True).start()

    def add_to_filter(self):
        if self.client:
            self.log_message(self.status_log, f"Wysyłanie żądania dodania {self.TARGET_ISIN} do filtra...")
            threading.Thread(target=self.client.add_to_filter, args=(self.TARGET_ISIN,), daemon=True).start()

    def clear_filter(self):
        if self.client:
            self.log_message(self.status_log, "Wysyłanie żądania wyczyszczenia filtra...")
            threading.Thread(target=self.client.clear_filter, daemon=True).start()

    def disconnect(self):
        if self.client:
            self.log_message(self.status_log, "Rozłączanie...")
            self.disconnect_button.setEnabled(False)
            self.client.disconnect()
            
    def display_portfolio(self, portfolio_data):
        self.portfolio_display.clear()
        formatted_text = ""
        for account, data in portfolio_data.items():
            formatted_text += f"[ RACHUNEK: {account} ]\n"
            formatted_text += "  Środki:\n"
            for fund, value in data.get('funds', {}).items():
                formatted_text += f"    - {fund}: {value}\n"
            formatted_text += "\n  Pozycje:\n"
            positions = data.get('positions', [])
            if positions:
                for pos in positions: formatted_text += f"    - Symbol: {pos['symbol']}, Ilość: {pos['quantity']}, ISIN: {pos['isin']}\n"
            else: formatted_text += "    - Brak otwartych pozycji.\n"
            formatted_text += "-"*40 + "\n"
        self.portfolio_display.setPlainText(formatted_text)
        
    def closeEvent(self, event):
        if self.client: self.disconnect()
        event.accept()

# ==============================================================================
# BossaAPIClient Class (Backend Logic)
# MODIFIED to request confirmation for bot actions instead of executing directly.
# ==============================================================================
class BossaAPIClient:
    def __init__(self, username, password, gui_queue):
        self.username = username; self.password = password
        self.gui_queue = gui_queue; self.sync_port = None
        self.async_port = None; self.is_logged_in = False
        self.portfolio = {}; self.stop_event = threading.Event()
        self.request_id = 1; self.async_socket = None
        self.market_data = {}; self.TARGET_ISIN = "PL0GF0031880" #fw20z2520
        self.manager_thread = None; self.manager_stop_event = threading.Event()
        self.manager_state = BotState.STOPPED; self.manager_params = {}
        self.entry_order_id = None; self.stop_order_id = None
        self.position_entry_price = 0; self.active_stop_price = 0
        self.position_type = None; self.daily_profit = 0
        self.existing_position_details = None
        # NEW: Flag to prevent bot from re-calculating while waiting for user
        self.waiting_for_confirmation = False

    # NEW: Executes a bot action after GUI confirmation
    def execute_bot_action(self, action_data):
        action_type = action_data['action_type']
        details = action_data['details']

        if action_type == "MOVE_STOP":
            self._bot_log(f"Wykonywanie przesunięcia stop-loss na {details['new_price']:.2f}...")
            
            # 1. Cancel the old stop order
            if details['old_stop_id']:
                cancel_details = {
                    'id_dm': details['old_stop_id'],
                    'k_s_text': 'Sprzedaż' if self.position_type == "LONG" else 'Kupno',
                    'ilosc': details['quantity'],
                    'rachunek': self.manager_params['account']
                }
                self.cancel_order(cancel_details)
                # Wait for cancellation to be acknowledged before placing new order
                time.sleep(0.5) 
            
            # 2. Place the new stop order
            self.send_limit_order(self.manager_params['account'], details['direction'],
                                  details['quantity'], details['new_price'], is_managed=True)
            
            # 3. Update internal state
            self.active_stop_price = details['new_price']
        
        self.waiting_for_confirmation = False

    # NEW: Resets the confirmation flag if user rejects the action
    def bot_action_rejected(self):
        self.waiting_for_confirmation = False
        self._bot_log("Akcja odrzucona. Bot wznawia monitorowanie.")

    # MODIFIED: Trailing stop loop now sends a confirmation request instead of acting directly
    def _trailing_stop_loop(self):
        self._bot_log("Pętla Trailing Stop rozpoczęta.")
        while not self.manager_stop_event.is_set():
            time.sleep(15) # 1.5 seconds is too frequent for real trading
            # Do nothing if we are in the wrong state or waiting for user input
            if self.manager_state not in [BotState.IN_LONG_POSITION, BotState.IN_SHORT_POSITION] or self.waiting_for_confirmation:
                continue
                
            last_price = self.market_data.get(self.TARGET_ISIN, {}).get('last_price')
            if not last_price: continue
            
            new_stop_price = self.active_stop_price
            should_move_stop = False
            qty_for_stop = abs(self.existing_position_details['quantity']) if self.existing_position_details else 1

            if self.position_type == "LONG":
                potential_stop = last_price - self.manager_params['trailing_stop']
#                if potential_stop <= self.market_data.get(self.TARGET_ISIN, {}).get('bid', 0):
                if self.active_stop_price <= self.market_data.get(self.TARGET_ISIN, {}).get('bid', 0):
                    self._bot_log(f"Active stop:  {self.active_stop_price:.2f} <= {self.market_data.get(self.TARGET_ISIN, {}).get('bid', 0)}. Potential stop: {potential_stop} skipping...")
                elif potential_stop > self.active_stop_price:
                    new_stop_price = potential_stop
                    should_move_stop = True
            elif self.position_type == "SHORT":
                potential_stop = last_price + self.manager_params['trailing_stop']
                if self.active_stop_price >= self.market_data.get(self.TARGET_ISIN, {}).get('ask', 0):
                    self._bot_log(f"Active stop:  {self.active_stop_price:.2f} >= {self.market_data.get(self.TARGET_ISIN, {}).get('ask', 0)}. Potential stop: {potential_stop} skipping...")
                elif potential_stop < self.active_stop_price:
                    new_stop_price = potential_stop
                    should_move_stop = True
            
            if should_move_stop:
                self._bot_log(f"Wykryto potrzebę przesunięcia stop-loss z {self.active_stop_price:.2f} na {new_stop_price:.2f}. Oczekiwanie na potwierdzenie...")
                self.waiting_for_confirmation = True

                direction = "Sprzedaż" if self.position_type == "LONG" else "Kupno"
                
                # Prepare all details needed by the GUI to confirm and execute the action
                action_details = {
                    "old_stop_id": self.stop_order_id,
                    "new_price": new_stop_price,
                    "quantity": qty_for_stop,
                    "direction": direction
                }
                
                # Send request to GUI
                self.gui_queue.put(("CONFIRM_BOT_ACTION", {"action_type": "MOVE_STOP", "details": action_details}))

        self._bot_log("Pętla Trailing Stop zakończona.")
        
    # --- Other BossaAPIClient methods are mostly unchanged ---
    # They are now invoked by the GUI after confirmation.
    def cancel_order(self, order_details):
        self.request_id += 1
        client_cancel_id = self.request_id
        side = '1' if order_details['k_s_text'] == "Kupno" else '2'
        txn_time = datetime.now().strftime('%Y%m%d-%H:%M:%S')
        fixml_request = f"""<FIXML v="5.0" r="20080317" s="20080314">
<OrdCxlReq ID="{client_cancel_id}" OrdID="{order_details['id_dm']}" Acct="{order_details['rachunek']}" Side="{side}"  TxnTm="{txn_time}">
<Instrmt ID="{self.TARGET_ISIN}" Src="4"/>
<OrdQty Qty="{order_details['ilosc']}"/>
</OrdCxlReq></FIXML>"""
        response = self._send_and_receive_sync(fixml_request)
        if response and '<ExecRpt' in response: self._parse_execution_report(response)
        else: self._log(f"Odpowiedź na anulatę zlecenia {order_details['id_dm']}: {response}")

    def _parse_portfolio(self, xml_data):
        root = ET.fromstring(xml_data); open_position_qty = 0
        parsed_portfolio = {}; self.existing_position_details = None
        for statement in root.findall('Statement'):
            account_id = statement.get('Acct')
            parsed_portfolio[account_id] = {'funds': {}, 'positions': []}
            for fund in statement.findall('Fund'): parsed_portfolio[account_id]['funds'][fund.get('name')] = fund.get('value')
            for position in statement.findall('.//Position'):
                instrument = position.find('Instrmt'); raw_qty = position.get('Acc110', '0')
                pos_data = {'symbol': instrument.get('Sym'), 'isin': instrument.get('ID'), 'quantity': int(raw_qty), 'blocked_quantity': position.get('Acc120')}
                parsed_portfolio[account_id]['positions'].append(pos_data)
                if pos_data['isin'] == self.TARGET_ISIN:
                    qty = pos_data['quantity']; open_position_qty += qty
                    if qty != 0: self.existing_position_details = {'account': account_id, 'symbol': pos_data['symbol'], 'isin': pos_data['isin'], 'quantity': qty, 'position_type': "LONG" if qty > 0 else "SHORT"}
        self.portfolio = parsed_portfolio
        self.gui_queue.put(("PORTFOLIO_UPDATE", {'portfolio_data': self.portfolio, 'open_position_qty': open_position_qty, 'existing_position_found': self.existing_position_details is not None, 'existing_position_details': self.existing_position_details}))
    
    def _parse_execution_report(self, xml_data):
        try:
            root = ET.fromstring(xml_data)
            exec_rpt = root.find('ExecRpt')
            if exec_rpt is None: return
            instrument = exec_rpt.find('Instrmt')
            symbol = instrument.get('Sym', 'N/A') if instrument is not None else 'N/A'
            order_data = {'id_dm': exec_rpt.get('OrdID', ''), 'id_klienta': exec_rpt.get('ID', ''),'status': exec_rpt.get('Stat', ''), 'symbol': symbol, 'k_s': exec_rpt.get('Side', ''), 'ilosc': exec_rpt.find('.//OrdQty').get('Qty', '') if exec_rpt.find('.//OrdQty') is not None else '', 'pozostalo': exec_rpt.get('LeavesQty', ''), 'wykonano': exec_rpt.get('CumQty', ''), 'limit': exec_rpt.get('Px', ''), 'cena_ost': exec_rpt.get('LastPx', ''), 'czas': exec_rpt.get('TxnTm', '')}
            self.gui_queue.put(("EXEC_REPORT", order_data))
            client_id = exec_rpt.get('ID'); status = exec_rpt.get('Stat'); dm_id = exec_rpt.get('OrdID')
            self._bot_log(f"DEBUG: Parsing ExecRpt - ID Klienta: {client_id}, Status: {status}, ID DM: {dm_id} ")
            if status == '2':
                last_px_str = exec_rpt.get('LastPx')
                if not last_px_str: return
                if self.manager_state == BotState.WAITING_FOR_ENTRY_FILL and client_id == self.entry_order_id:
                    self._bot_log(f"DEBUG: Entry order filled. ID Klienta: {client_id}, ID DM: {dm_id}, Cena: {last_px_str}.")
                    self.position_entry_price = float(last_px_str); self.entry_order_id = dm_id
                    qty_for_stop = abs(self.existing_position_details['quantity']) if self.existing_position_details else 1
                    if self.position_type == "LONG":
                        self.manager_state = BotState.IN_LONG_POSITION; stop_price = self.position_entry_price - self.manager_params['trailing_stop']
                        self.active_stop_price = stop_price; self._bot_log(f"Pozycja LONG otwarta @ {self.position_entry_price:.2f}. Ustawiam Stop-Loss na {stop_price:.2f}")
                        self.send_limit_order(self.manager_params['account'], "Sprzedaż", qty_for_stop, stop_price, is_managed=True)
                    elif self.position_type == "SHORT":
                        self.manager_state = BotState.IN_SHORT_POSITION; stop_price = self.position_entry_price + self.manager_params['trailing_stop']
                        self.active_stop_price = stop_price; self._bot_log(f"Pozycja SHORT otwarta @ {self.position_entry_price:.2f}. Ustawiam Stop-Loss na {stop_price:.2f}")
                        self.send_limit_order(self.manager_params['account'], "Kupno", qty_for_stop, stop_price, is_managed=True)
                    self.gui_queue.put(("BOT_STATE_UPDATE", {'entry_price': self.position_entry_price, 'commission': self.manager_params['commission'], 'position_type': self.position_type}))
                elif self.manager_state in [BotState.IN_LONG_POSITION, BotState.IN_SHORT_POSITION] and client_id == self.stop_order_id:
                    exit_price = float(last_px_str); profit = (exit_price - self.position_entry_price) if self.position_type == "LONG" else (self.position_entry_price - exit_price)
                    profit -= 2 * self.manager_params['commission']; self.daily_profit += profit
                    self._bot_log(f"Pozycja ZAMKNIĘTA @ {exit_price:.2f}. Zysk/Strata: {profit:.2f}. Zysk dzienny: {self.daily_profit:.2f}")
                    self.manager_stop_event.set(); self.manager_state = BotState.IDLE; self.gui_queue.put(("BOT_STATE_UPDATE", {'entry_price': None}))
            elif status in ['0', '1', '5']:
                 if self.manager_state in [BotState.IN_LONG_POSITION, BotState.IN_SHORT_POSITION] and client_id == self.stop_order_id:
                    self._bot_log(f"Stop-loss order acknowledged/updated. Client ID: {client_id}, Server ID: {dm_id}")
                    self.stop_order_id = dm_id
            elif status in ['4', '8']:
                if self.manager_state in [BotState.IN_LONG_POSITION, BotState.IN_SHORT_POSITION] and (client_id == self.stop_order_id or dm_id == self.stop_order_id):
                    self._bot_log(f"Active stop order (ID: {self.stop_order_id}) was canceled/rejected. Clearing ID.")
                    self.stop_order_id = None
        except Exception as e: self._log(f"Błąd podczas parsowania ExecutionReport: {e}")
    
    def _parse_market_data(self, xml_data):
        try:
            root = ET.fromstring(xml_data); data_changed = False
            for inc_element in root.findall('.//Inc'):
                entry_type = inc_element.get('Typ'); instrument = inc_element.find('Instrmt')
                if instrument is not None:
                    isin = instrument.get('ID')
                    if isin not in self.market_data: self.market_data[isin] = {}
                    price_str = inc_element.get('Px'); size_str = inc_element.get('Sz')
                    if entry_type == '0':
                        if price_str: self.market_data[isin]['bid'] = float(price_str)
                        if size_str: self.market_data[isin]['bid_size'] = int(float(size_str))
                        data_changed = True
                    elif entry_type == '1':
                        if price_str: self.market_data[isin]['ask'] = float(price_str)
                        if size_str: self.market_data[isin]['ask_size'] = int(float(size_str))
                        data_changed = True
                    elif entry_type == '2' and price_str: self.market_data[isin]['last_price'] = float(price_str); data_changed = True
                    elif entry_type == 'C' and size_str: self.market_data[isin]['lop'] = int(float(size_str)); data_changed = True
            if self.TARGET_ISIN in self.market_data and data_changed:
                data_to_send = self.market_data[self.TARGET_ISIN].copy()
                data_to_send['isin'] = self.TARGET_ISIN
                self.gui_queue.put(("MARKET_DATA_UPDATE", data_to_send))
        except Exception as e: self._log(f"Błąd podczas parsowania danych rynkowych: {e}")

    def _bot_log(self, message): self.gui_queue.put(("BOT_LOG", message))

    def start_trade_manager(self, params, direction):
        if self.manager_state not in [BotState.STOPPED, BotState.IDLE]: self._bot_log("Błąd: Menedżer jest już aktywny."); return
        self.manager_params = params; self.manager_stop_event.clear()
        self.entry_order_id = None; self.stop_order_id = None
        market_info = self.market_data.get(self.TARGET_ISIN)
        if not market_info: self._bot_log("Błąd: Brak danych rynkowych."); return
        if direction == "Kupno":
            entry_price = market_info.get('bid') # zamienilem na bid
            if not entry_price: self._bot_log("Błąd: Brak ceny BID."); return
            self.position_type = "LONG"
        else:
            entry_price = market_info.get('ask') # zamienilem na ask
            if not entry_price: self._bot_log("Błąd: Brak ceny ASK."); return
            self.position_type = "SHORT"
        self._bot_log(f"Otwieram pozycję {self.position_type} zleceniem LIMIT po cenie {entry_price}...")
        self.manager_state = BotState.WAITING_FOR_ENTRY_FILL
        self.send_limit_order(params['account'], direction, 1, entry_price, is_managed=True)
        if self.manager_thread is None or not self.manager_thread.is_alive():
            self.manager_thread = threading.Thread(target=self._trailing_stop_loop, daemon=True)
            self.manager_thread.start()

    def start_trade_manager_with_existing_position(self, params):
        if not self.existing_position_details: self._bot_log("Błąd: Brak istniejącej pozycji."); return
        if self.manager_state not in [BotState.STOPPED, BotState.IDLE]: self._bot_log("Błąd: Menedżer jest już aktywny."); return
        self.manager_params = params; self.manager_stop_event.clear()
        self.entry_order_id = None; self.stop_order_id = None
        self.position_type = self.existing_position_details['position_type']
        self.position_entry_price = self.market_data.get(self.TARGET_ISIN, {}).get('last_price', 0)
        order_quantity = abs(self.existing_position_details['quantity'])

        if self.position_type == "LONG":
            self.manager_state = BotState.IN_LONG_POSITION
            self.active_stop_price = self.position_entry_price - self.manager_params['trailing_stop']
            self._bot_log(f"Zarządzam ist. poz. LONG. Cena wejścia (szac.): {self.position_entry_price:.2f}. Ustawiam SL na {self.active_stop_price:.2f}")
            if self.active_stop_price <= self.market_data.get(self.TARGET_ISIN, {}).get('bid', 0):
                self._bot_log(f"SL {self.active_stop_price:.2f} <= {self.market_data.get(self.TARGET_ISIN, {}).get('bid', 0)}  . Nie ustawiam zlecenia SL.")
            else:
                self.send_limit_order(self.manager_params['account'], "Sprzedaż", order_quantity, self.active_stop_price, is_managed=True)

        elif self.position_type == "SHORT":
            self.manager_state = BotState.IN_SHORT_POSITION
            self.active_stop_price = self.position_entry_price + self.manager_params['trailing_stop']
            self._bot_log(f"Zarządzam ist. poz. SHORT. Cena wejścia (szac.): {self.position_entry_price:.2f}. Ustawiam SL na {self.active_stop_price:.2f}")
            if self.active_stop_price >= self.market_data.get(self.TARGET_ISIN, {}).get('ask', 0):
                self._bot_log(f"SL {self.active_stop_price:.2f} >=  {self.market_data.get(self.TARGET_ISIN, {}).get('ask', 0)}  . Nie ustawiam zlecenia SL.")
            else:
                self.send_limit_order(self.manager_params['account'], "Kupno", order_quantity, self.active_stop_price, is_managed=True)

        self.gui_queue.put(("BOT_STATE_UPDATE", {'entry_price': self.position_entry_price, 'commission': self.manager_params['commission'], 'position_type': self.position_type}))
        if self.manager_thread is None or not self.manager_thread.is_alive():
            self.manager_thread = threading.Thread(target=self._trailing_stop_loop, daemon=True)
            self.manager_thread.start()

    def close_trade_manually(self):
        if self.manager_state not in [BotState.IN_LONG_POSITION, BotState.IN_SHORT_POSITION]: self._bot_log("Brak otwartej pozycji do zamknięcia."); return
        qty_to_manage = abs(self.existing_position_details['quantity']) if self.existing_position_details else 1
        if self.stop_order_id:
            self._bot_log(f"Anulowanie aktywnego SL (ID: {self.stop_order_id})...")
            self.cancel_order({'id_dm': self.stop_order_id, 'k_s_text': 'Sprzedaż' if self.position_type == 'LONG' else 'Kupno', 'ilosc': qty_to_manage, 'rachunek': self.manager_params['account']})
            self.stop_order_id = None; time.sleep(0.5)
        market_info = self.market_data.get(self.TARGET_ISIN)
        if self.position_type == "LONG":
            exit_price = market_info.get('bid'); self._bot_log(f"Ręczne zamykanie LONG po cenie rynkowej (BID): {exit_price}")
            self.send_limit_order(self.manager_params['account'], "Sprzedaż", qty_to_manage, exit_price, is_managed=True)
        elif self.position_type == "SHORT":
            exit_price = market_info.get('ask'); self._bot_log(f"Ręczne zamykanie SHORT po cenie rynkowej (ASK): {exit_price}")
            self.send_limit_order(self.manager_params['account'], "Kupno", qty_to_manage, exit_price, is_managed=True)
        self.manager_stop_event.set()

    def send_limit_order(self, account, direction, quantity, price, is_managed=False):
        self.request_id += 1; client_order_id = str(self.request_id)
        if is_managed:
            if self.manager_state == BotState.WAITING_FOR_ENTRY_FILL: self.entry_order_id = client_order_id
            elif self.manager_state in [BotState.IN_LONG_POSITION, BotState.IN_SHORT_POSITION]: self.stop_order_id = client_order_id
        side = '1' if direction == "Kupno" else '2'; trade_date = datetime.now().strftime('%Y%m%d')
        transact_time = datetime.now().strftime('%Y%m%d-%H:%M:%S'); order_type = 'L'; time_in_force = '0'
        fixml_request = f"""<FIXML v="5.0" r="20080317" s="20080314"><Order ID="{client_order_id}" TrdDt="{trade_date}" Acct="{account}" Side="{side}" TxnTm="{transact_time}" OrdTyp="{order_type}" Px="{price:.2f}" Ccy="PLN" TmInForce="{time_in_force}"><Instrmt ID="{self.TARGET_ISIN}" Src="4"/><OrdQty Qty="{quantity}"/></Order></FIXML>"""
        response = self._send_and_receive_sync(fixml_request)
        if response and '<ExecRpt' in response: self._parse_execution_report(response)
        elif response: self._log(f"Odrzucenie zlecenia. Odpowiedź: {response}")
        else: self._log("Brak odpowiedzi serwera na zlecenie.")

    def _log(self, message): self.gui_queue.put(("LOG", message))

    def _send_and_receive_sync(self, message):
        sync_socket = None
        try:
            sync_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sync_socket.connect(('127.0.0.1', self.sync_port))
            self._send_message(sync_socket, message)
            return self._receive_message(sync_socket)
        except ConnectionAbortedError as e: self._log(f"BŁĄD: Połączenie zerwane (NOL3). {e}"); return None
        except Exception as e: self._log(f"BŁĄD komunikacji synchronicznej: {e}"); return None
        finally:
            if sync_socket: sync_socket.close()

    def add_to_filter(self, isin):
        self.request_id += 1
        fixml_request = f'<FIXML v="5.0" r="20080317" s="20080314"><MktDataReq ReqID="{self.request_id}" SubReqTyp="1" MktDepth="0"><req Typ="0"/><req Typ="1"/><req Typ="2"/><req Typ="B"/><req Typ="C"/><req Typ="3"/><req Typ="4"/><req Typ="5"/><req Typ="7"/><req Typ="r"/><req Typ="8"/><InstReq><Instrmt ID="{isin}" Src="4"/></InstReq></MktDataReq></FIXML>'
        response = self._send_and_receive_sync(fixml_request)
        if response and '<MktDataFull' in response: self._log(f"Pomyślnie dodano {isin} do filtra.")
        else: self._log(f"Błąd podczas dodawania do filtra. Odpowiedź: {response}")

    def clear_filter(self):
        self.request_id += 1
        fixml_request = f'<FIXML v="5.0" r="20080317" s="20080314"><MktDataReq ReqID="{self.request_id}" SubReqTyp="2"></MktDataReq></FIXML>'
        response = self._send_and_receive_sync(fixml_request)
        if response and '<MktDataFull' in response: self._log("Pomyślnie wyczyszczono filtr.")
        else: self._log(f"Błąd podczas czyszczenia filtra. Odpowiedź: {response}")

    def _async_listener(self):
        try:
            self.async_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.async_socket.connect(('127.0.0.1', self.async_port))
            self._log("Połączono z portem asynchronicznym.")
            while not self.stop_event.is_set():
                message = self._receive_message(self.async_socket)
                if message is None: break
                self.gui_queue.put(("ASYNC_MSG", message))
                if '<ExecRpt' in message: self._parse_execution_report(message)
                elif '<MktDataInc' in message: self._parse_market_data(message)
                elif '<Statement' in message: self._parse_portfolio(message)
        except Exception as e:
            if not self.stop_event.is_set(): self._log(f"Błąd w wątku asynchronicznym: {e}")
        finally:
            if self.async_socket: self.async_socket.close()
            
    def run(self):
        if not self._get_ports_from_registry(): self.gui_queue.put(("LOGIN_FAIL", "Błąd odczytu portów.")); return
        self.request_id += 1
        login_request = f'<FIXML v="5.0" r="20080317" s="20080314"><UserReq UserReqID="{self.request_id}" UserReqTyp="1" Username="{self.username}" Password="{self.password}"/></FIXML>'
        self._log("Wysyłanie żądania logowania...")
        response = self._send_and_receive_sync(login_request)
        if response and '<UserRsp' in response:
            root = ET.fromstring(response); user_rsp = root.find('UserRsp')
            if user_rsp is not None and user_rsp.get('UserStat') == '1':
                self.is_logged_in = True; self.gui_queue.put(("LOGIN_SUCCESS", None))
                self.manager_state = BotState.IDLE; self._async_listener()
            else:
                status = user_rsp.get('UserStat') if user_rsp is not None else 'brak'
                self.gui_queue.put(("LOGIN_FAIL", f"Status: {status}"))
        else: self.gui_queue.put(("LOGIN_FAIL", f"Nieoczekiwana odpowiedź: {response}"))

    def disconnect(self):
        self.manager_stop_event.set(); self.stop_event.set()
        if self.async_socket:
            try: self.async_socket.shutdown(socket.SHUT_RDWR)
            except OSError: pass
            finally: self.async_socket.close()
        self.gui_queue.put(("DISCONNECTED", None))

    def _get_ports_from_registry(self):
        try:
            key_path = r"Software\COMARCH S.A.\NOL3\7\Settings"
            registry_key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_READ)
            self.sync_port, _ = winreg.QueryValueEx(registry_key, "nca_psync")
            self.async_port, _ = winreg.QueryValueEx(registry_key, "nca_pasync")
            self.sync_port = int(self.sync_port); self.async_port = int(self.async_port)
            winreg.CloseKey(registry_key)
            self._log(f"Odczytano porty: Sync={self.sync_port}, Async={self.async_port}"); return True
        except FileNotFoundError: self._log("BŁĄD: Nie znaleziono klucza rejestru bossaNOL3."); return False
        except Exception as e: self._log(f"BŁĄD podczas odczytu rejestru: {e}"); return False

    def _send_message(self, sock, message):
        encoded_message = message.encode('utf-8')
        header = struct.pack('<I', len(encoded_message))
        sock.sendall(header); sock.sendall(encoded_message)

    def _receive_message(self, sock):
        header_data = sock.recv(4)
        if not header_data: return None
        message_length = struct.unpack('<I', header_data)[0]
        if message_length == 0: return ""
        message_data = b''
        while len(message_data) < message_length:
            chunk = sock.recv(message_length - len(message_data))
            if not chunk: raise ConnectionError("Przerwano połączenie.")
            message_data += chunk
        return message_data.decode('utf-8','replace').strip().rstrip('\x00')


if __name__ == '__main__':
    app = QApplication(sys.argv)
    window = BossaAppPyQt()
    window.show()
    sys.exit(app.exec())