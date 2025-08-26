import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox
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

class BotState(Enum):
    STOPPED = 0
    IDLE = 1
    WAITING_FOR_ENTRY_FILL = 2
    IN_LONG_POSITION = 3
    IN_SHORT_POSITION = 4

class BossaApp:
    def __init__(self, root):
        self.root = root
        self.root.title("BossaAPI - Menedżer Transakcji")
        self.root.geometry("1100x900")
        self.client = None
        self.queue = queue.Queue()
        self.TARGET_ISIN = "PL0GF0031252"
        self.orders = {}
        self.STATUS_MAP = {'0': 'Nowe', '1': 'Aktywne', '2': 'Wykonane', '4': 'Anulowane', '5': 'Zastąpione', '6': 'Oczekuje na anul.', '8': 'Odrzucone', 'E': 'Oczekuje na mod.'}
        self.SIDE_MAP = {'1': 'Kupno', '2': 'Sprzedaż'}
        self.create_widgets()
        self.process_queue()

    def create_widgets(self):
        main_frame = tk.Frame(self.root)
        main_frame.pack(fill='both', expand=True, padx=10, pady=10)
        
        self.notebook = ttk.Notebook(main_frame)
        self.notebook.pack(fill='both', expand=True)

        self.tab_login = tk.Frame(self.notebook, padx=10, pady=10)
        self.tab_orders = tk.Frame(self.notebook, padx=10, pady=10)
        self.tab_monitor = tk.Frame(self.notebook, padx=10, pady=10)
        self.tab_bot = tk.Frame(self.notebook, padx=10, pady=10)
        self.tab_portfolio = tk.Frame(self.notebook, padx=10, pady=10)

        self.notebook.add(self.tab_login, text="Połączenie i Filtr")
        self.notebook.add(self.tab_orders, text="Zlecenie Ręczne")
        self.notebook.add(self.tab_monitor, text="Monitor Zleceń")
        self.notebook.add(self.tab_bot, text="Menedżer Transakcji")
        self.notebook.add(self.tab_portfolio, text="Portfel")

        tk.Label(self.tab_portfolio, text="Dane portfela:").pack(anchor='w')
        self.portfolio_display = scrolledtext.ScrolledText(self.tab_portfolio, state='disabled')
        self.portfolio_display.pack(fill='both', expand=True)

        monitor_main_frame = tk.Frame(self.tab_monitor)
        monitor_main_frame.pack(fill='both', expand=True)
        
        cols = ('id_dm', 'id_klienta', 'status', 'symbol', 'k_s', 'ilosc', 'pozostalo', 'wykonano', 'limit', 'cena_ost', 'czas')
        self.order_tree = ttk.Treeview(monitor_main_frame, columns=cols, show='headings', height=10)
        col_map = {'id_dm': ('ID (DM)', 100), 'id_klienta': ('ID (Klient)', 80), 'status': ('Status', 120), 'symbol': ('Symbol', 80), 'k_s': ('K/S', 60), 'ilosc': ('Ilość', 60), 'pozostalo': ('Pozostało', 70), 'wykonano': ('Wykonano', 70), 'limit': ('Limit', 70), 'cena_ost': ('Cena ost.', 70), 'czas': ('Czas', 140)}
        for col, (text, width) in col_map.items():
            self.order_tree.heading(col, text=text)
            self.order_tree.column(col, width=width, anchor='center')
        
        vsb = ttk.Scrollbar(monitor_main_frame, orient="vertical", command=self.order_tree.yview)
        hsb = ttk.Scrollbar(monitor_main_frame, orient="horizontal", command=self.order_tree.xview)
        self.order_tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        
        vsb.pack(side='right', fill='y')
        self.order_tree.pack(fill='both', expand=True)
        hsb.pack(side='bottom', fill='x')
        
        self.cancel_order_button = tk.Button(monitor_main_frame, text="Anuluj Zaznaczone Zlecenie", command=self.cancel_selected_order, state='disabled')
        self.cancel_order_button.pack(pady=5)
        self.order_tree.bind('<<TreeviewSelect>>', self.on_treeview_select)
        
        tile_frame = tk.Frame(self.tab_bot, pady=10)
        tile_frame.pack(fill='x')
        def create_tile(parent, title):
            frame = tk.Frame(parent, relief='sunken', borderwidth=2)
            frame.pack(side='left', fill='x', expand=True, padx=5)
            tk.Label(frame, text=title, font=('Helvetica', 10, 'bold')).pack(pady=(5,0))
            value_label = tk.Label(frame, text="---", font=('Helvetica', 18, 'bold'), fg='blue')
            value_label.pack(pady=(0,5), padx=10)
            return value_label
        
        # Updated price tiles with quantity labels
        self.bid_label = create_tile(tile_frame, "BID")
        self.bid_qty_label = create_tile(tile_frame, "BID QTY") # NEW
        self.ask_label = create_tile(tile_frame, "ASK")
        self.ask_qty_label = create_tile(tile_frame, "ASK QTY") # NEW
        self.last_label = create_tile(tile_frame, "LAST")
        self.lop_label = create_tile(tile_frame, "LOP")
        self.be_label = create_tile(tile_frame, "BREAK-EVEN")
        self.pos_label = create_tile(tile_frame, "OTWARTE POZYCJE")
        
        bot_params_frame = tk.Frame(self.tab_bot, relief='groove', borderwidth=2, padx=5, pady=5)
        bot_params_frame.pack(fill='x')
        tk.Label(bot_params_frame, text="Parametry Menedżera:", font=('Helvetica', 10, 'bold')).pack(side='left', padx=(0,10))
        tk.Label(bot_params_frame, text="Trailing Stop:").pack(side='left')
        self.stoploss_entry = tk.Entry(bot_params_frame, width=5)
        self.stoploss_entry.pack(side='left', padx=(0,10))
        self.stoploss_entry.insert(0, "10")
        tk.Label(bot_params_frame, text="Cel dzienny (pkt):").pack(side='left')
        self.daily_goal_entry = tk.Entry(bot_params_frame, width=5)
        self.daily_goal_entry.pack(side='left', padx=(0,10))
        self.daily_goal_entry.insert(0, "20")

        bot_action_frame = tk.Frame(self.tab_bot, pady=10)
        bot_action_frame.pack(fill='x')
        self.start_long_button = tk.Button(bot_action_frame, text="OTWÓRZ LONG", command=lambda: self.start_trade("Kupno"), state='disabled', bg='lightgreen')
        self.start_long_button.pack(side='left', expand=True, fill='x', padx=5)
        self.start_short_button = tk.Button(bot_action_frame, text="OTWÓRZ SHORT", command=lambda: self.start_trade("Sprzedaż"), state='disabled', bg='salmon')
        self.start_short_button.pack(side='left', expand=True, fill='x', padx=5)
        self.close_pos_button = tk.Button(bot_action_frame, text="ZAMKNIJ POZYCJĘ (PANIC)", command=self.close_trade_manually, state='disabled', bg='orange')
        self.close_pos_button.pack(side='left', expand=True, fill='x', padx=5)
        
        self.start_bot_existing_pos_button = tk.Button(bot_action_frame, text="START BOT Z ISTNIEJĄCĄ POZYCJĄ", command=self.start_bot_with_existing_position, state='disabled', bg='lightblue')
        self.start_bot_existing_pos_button.pack(side='left', expand=True, fill='x', padx=5)
        
        tk.Label(self.tab_bot, text="Log Menedżera:").pack(anchor='w', pady=(10,0))
        self.bot_log = scrolledtext.ScrolledText(self.tab_bot, height=10, state='disabled')
        self.bot_log.pack(fill='both', expand=True)

        login_frame = tk.Frame(self.tab_login)
        login_frame.pack(fill='x', pady=(0, 10))
        tk.Label(login_frame, text="Użytkownik:").pack(side='left', padx=(0, 5))
        self.username_entry = tk.Entry(login_frame, width=20)
        self.username_entry.pack(side='left', padx=5)
        tk.Label(login_frame, text="Hasło:").pack(side='left', padx=5)
        self.password_entry = tk.Entry(login_frame, show="*", width=20)
        self.password_entry.pack(side='left', padx=5)
        self.username_entry.insert(0, "BOS")
        self.password_entry.insert(0, "BOS")
        self.login_button = tk.Button(login_frame, text="Połącz i zaloguj", command=self.start_login_thread)
        self.login_button.pack(side='left', padx=10)
        self.disconnect_button = tk.Button(login_frame, text="Rozłącz", command=self.disconnect, state='disabled')
        self.disconnect_button.pack(side='left', padx=10)

        filter_frame = tk.Frame(self.tab_login, relief='groove', borderwidth=2, padx=5, pady=5)
        filter_frame.pack(fill='x', pady=5)
        tk.Label(filter_frame, text=f"Filtr notowań dla {self.TARGET_ISIN}:", font=('Helvetica', 10, 'bold')).pack(side='left', padx=(0, 10))
        self.add_filter_button = tk.Button(filter_frame, text=f"Dodaj {self.TARGET_ISIN} do filtra", command=self.add_to_filter, state='disabled')
        self.add_filter_button.pack(side='left', padx=5)
        self.clear_filter_button = tk.Button(filter_frame, text="Wyczyść filtr", command=self.clear_filter, state='disabled')
        self.clear_filter_button.pack(side='left', padx=5)

        order_frame = tk.Frame(self.tab_orders, relief='groove', borderwidth=2, padx=5, pady=5)
        order_frame.pack(fill='x', pady=5)
        tk.Label(order_frame, text=f"Nowe zlecenie (Limit, Dzień) dla {self.TARGET_ISIN}:", font=('Helvetica', 10, 'bold')).pack(anchor='w')
        controls_frame = tk.Frame(order_frame)
        controls_frame.pack(fill='x', pady=5)
        tk.Label(controls_frame, text="Rachunek:").pack(side='left', padx=(5, 5))
        self.account_entry = tk.Entry(controls_frame, width=15)
        self.account_entry.pack(side='left', padx=5)
        tk.Label(controls_frame, text="Kierunek:").pack(side='left', padx=(10, 5))
        self.direction_combo = ttk.Combobox(controls_frame, values=["Kupno", "Sprzedaż"], width=8)
        self.direction_combo.pack(side='left', padx=5)
        self.direction_combo.set("Kupno")
        tk.Label(controls_frame, text="Ilość:").pack(side='left', padx=(10, 5))
        self.quantity_entry = tk.Entry(controls_frame, width=8)
        self.quantity_entry.pack(side='left', padx=5)
        self.quantity_entry.insert(0, "1")
        tk.Label(controls_frame, text="Cena (Limit):").pack(side='left', padx=(10, 5))
        self.price_entry = tk.Entry(controls_frame, width=10)
        self.price_entry.pack(side='left', padx=5)
        self.send_order_button = tk.Button(controls_frame, text="Złóż zlecenie", command=self.send_order, state='disabled')
        self.send_order_button.pack(side='left', padx=10)
        
        bottom_logs_frame = tk.Frame(main_frame)
        bottom_logs_frame.pack(fill='both', expand=True)
        paned_window = tk.PanedWindow(bottom_logs_frame, orient='vertical', sashrelief='raised')
        paned_window.pack(fill='both', expand=True, pady=5)
        top_panel = tk.Frame(paned_window)
        tk.Label(top_panel, text="Log statusu:").pack(anchor='w')
        self.status_log = scrolledtext.ScrolledText(top_panel, height=8, state='disabled')
        self.status_log.pack(fill='both', expand=True, pady=(0, 5))
        tk.Label(top_panel, text="Surowe komunikaty (kanał asynchroniczny):").pack(anchor='w')
        self.async_messages = scrolledtext.ScrolledText(top_panel, height=12, state='disabled', bg='lightgrey')
        self.async_messages.pack(fill='both', expand=True)
        paned_window.add(top_panel)

# --- Status bar at the bottom ---
        self.status_frame = tk.Frame(self.root, relief="sunken", bd=1)
        self.status_frame.pack(side="bottom", fill="x")

# Container for right-aligned widgets
        self.right_status_frame = tk.Frame(self.status_frame)
        self.right_status_frame.pack(side="right", padx=5, pady=2)

# Heartbeat icon (first, on the far right)
        self.heartbeat_var = tk.StringVar(value="♡")
        self.status_label = tk.Label(
            self.right_status_frame, 
            textvariable=self.heartbeat_var, 
            width=2, 
            fg="red", 
            font=("Arial", 12, "bold")
        )
        self.status_label.pack(side="right", padx=(2, 5))

# Time (to the left of heartbeat)
        self.status_time_var = tk.StringVar()
        self.status_time_label = tk.Label(
            self.right_status_frame, 
            textvariable=self.status_time_var, 
            font=("Arial", 10)
        )
        self.status_time_label.pack(side="right", padx=5)
        self._update_status_time()

# Latency (furthest left in this group)
        self.status_latency_var = tk.StringVar()
        self.status_latency_label = tk.Label(
            self.right_status_frame, 
            textvariable=self.status_latency_var, 
            font=("Arial", 10)
        )
        self.status_latency_label.pack(side="right", padx=5)
        self.status_latency_var.set("Latency: --- ")


    def _flash_heartbeat(self):
        # Flash the heartbeat icon
        self.heartbeat_var.set("❤")
        self.status_label.after(300, lambda: self.heartbeat_var.set("♡"))


    def _update_status_time(self):
        now = datetime.now().strftime("%H:%M:%S")
        self.status_time_var.set(f"Czas: {now}")
        self.root.after(1000, self._update_status_time)


    def on_treeview_select(self, event):
        """Aktywuje/deaktywuje przycisk anulowania w zależności od statusu zlecenia."""
        selected_items = self.order_tree.selection()
        if not selected_items:
            self.cancel_order_button.config(state='disabled')
            return
        
        selected_item = selected_items[0]
        item_values = self.order_tree.item(selected_item, 'values')
        status = item_values[2]
        
        if status in ['Nowe', 'Aktywne']:
            self.cancel_order_button.config(state='normal')
        else:
            self.cancel_order_button.config(state='disabled')

    def cancel_selected_order(self):
        """Pobiera wszystkie dane zaznaczonego zlecenia i wysyła prośbę o anulowanie."""
        selected_items = self.order_tree.selection()
        if not selected_items:
            messagebox.showwarning("Brak zaznaczenia", "Proszę zaznaczyć zlecenie do anulowania.")
            return
            
        item_values = self.order_tree.item(selected_items[0], 'values')
        
        order_details = {
            'id_dm': item_values[0],
            'k_s_text': item_values[4],
            'ilosc': item_values[5],
            'rachunek': self.account_entry.get()
        }
        
        if not order_details['rachunek']:
            messagebox.showerror("Błąd", "Nie można anulować zlecenia bez podanego numeru rachunku.")
            return

        if self.client:
            self.log_message(self.status_log, f"Wysyłanie prośby o anulowanie zlecenia {order_details['id_dm']}...")
            threading.Thread(target=self.client.cancel_order, args=(order_details,), daemon=True).start()

    def process_queue(self):
        try:
            message_type, data = self.queue.get_nowait()
            if message_type == "PORTFOLIO_UPDATE":
                self.display_portfolio(data['portfolio_data'])
                self.pos_label.config(text=str(data.get('open_position_qty', '---')))
                if data.get('portfolio_data') and not self.account_entry.get():
                    first_account = next(iter(data['portfolio_data']))
                    self.account_entry.insert(0, first_account)
                
                if data.get('existing_position_found'):
                    self.start_bot_existing_pos_button.config(state='normal')
                    self.log_message(self.bot_log, f"Znaleziono istniejącą pozycję: {data['existing_position_details']['quantity']} szt. {data['existing_position_details']['symbol']} ({data['existing_position_details']['position_type']}). Możesz uruchomić bota z tą pozycją.")
                else:
                    self.start_bot_existing_pos_button.config(state='disabled')

            elif message_type == "MARKET_DATA_UPDATE":
                if data.get('isin') == self.TARGET_ISIN:
                    self.bid_label.config(text=f"{data.get('bid', '---'):.2f}")
                    self.bid_qty_label.config(text=f"{data.get('bid_qty', '---')}") # NEW
                    self.ask_label.config(text=f"{data.get('ask', '---'):.2f}")
                    self.ask_qty_label.config(text=f"{data.get('ask_qty', '---')}") # NEW
                    self.last_label.config(text=f"{data.get('last_price', '---'):.2f}")
                    self.lop_label.config(text=f"{data.get('lop', '---')}")
                    if self.root.focus_get() != self.price_entry:
                        price = data.get('last_price')
                        if price:
                            self.price_entry.delete(0, tk.END)
                            self.price_entry.insert(0, f"{price:.2f}")
            elif message_type == "BOT_STATE_UPDATE":
                entry_price = data.get('entry_price')
                self.close_pos_button.config(state='normal' if entry_price else 'disabled')
                if entry_price:
                    be_price = entry_price + 2 * data.get('commission', 1) if data.get('position_type') == 'LONG' else entry_price - 2 * data.get('commission', 1)
                    self.be_label.config(text=f"{be_price:.2f}")
                else:
                    self.be_label.config(text="---")
                    self.start_long_button.config(state='normal')
                    self.start_short_button.config(state='normal')
                    self.start_bot_existing_pos_button.config(state='disabled')
            elif message_type == "BOT_LOG":
                self.log_message(self.bot_log, data)
            elif message_type == "EXEC_REPORT":
                self.update_order_monitor(data)
            elif message_type == "LOG":
                self.log_message(self.status_log, data)
            elif message_type == "LOGIN_SUCCESS":
                self.log_message(self.status_log, f"Logowanie udane! Dodaj {self.TARGET_ISIN} do filtra, aby otrzymywać ceny.")
                self.disconnect_button.config(state='normal')
                self.add_filter_button.config(state='normal')
                self.clear_filter_button.config(state='normal')
                self.send_order_button.config(state='normal')
                self.start_long_button.config(state='normal')
                self.start_short_button.config(state='normal')
            elif message_type == "ASYNC_MSG":
           # Heartbeat detection (assuming heartbeat contains <HrtBt or similar)
                if "<Heartbeat" in data:
                    self._flash_heartbeat()
                elif "<ApplMsgRpt" in data:
                    try:
                        root = ET.fromstring(data)  # parse string into XML
                        appl_msg = root.find("ApplMsgRpt")  # find <ApplMsgRpt> element
                        txt_value = appl_msg.get("Txt") if appl_msg is not None else None
                        if txt_value:
                            self.status_latency_var.set(f"Latency: {txt_value.strip()} ")
#                            self.log_message(self.async_messages, f"Otrzymano ApplMsgRpt: {txt_value.strip()}")
                        else:
                            self.log_message(self.async_messages, "Otrzymano ApplMsgRpt (brak Txt)")
                    except Exception as e:
                        self.log_message(self.async_messages, f"Błąd parsowania ApplMsgRpt: {e}")
                else:
                    self.log_message(self.async_messages, data.strip())
            elif message_type == "DISCONNECTED":
                self.log_message(self.status_log, "Rozłączono.")
                self.login_button.config(state='normal')
                self.disconnect_button.config(state='disabled')
                self.add_filter_button.config(state='disabled')
                self.clear_filter_button.config(state='disabled')
                self.send_order_button.config(state='disabled')
                self.start_long_button.config(state='disabled')
                self.start_short_button.config(state='disabled')
                self.close_pos_button.config(state='disabled')
                self.start_bot_existing_pos_button.config(state='disabled')
                self.client = None
                self.orders = {}
                for i in self.order_tree.get_children(): self.order_tree.delete(i)
            elif message_type == "LOGIN_FAIL":
                self.log_message(self.status_log, f"Logowanie nie powiodło się: {data}")
                self.login_button.config(state='normal')
        except queue.Empty:
            pass
        finally:
            self.root.after(100, self.process_queue)
    
    def start_trade(self, direction):
        if not self.client:
            self.log_message(self.bot_log, "Błąd: Klient nie jest połączony.")
            return
        try:
            params = { 'account': self.account_entry.get(), 'trailing_stop': int(self.stoploss_entry.get()), 'daily_goal': int(self.daily_goal_entry.get()), 'commission': 1 }
            if not params['account']:
                self.log_message(self.bot_log, "Błąd: Numer rachunku jest wymagany.")
                return
        except ValueError:
            self.log_message(self.bot_log, "Błąd: Parametry menedżera muszą być liczbami.")
            return
        self.start_long_button.config(state='disabled')
        self.start_short_button.config(state='disabled')
        self.start_bot_existing_pos_button.config(state='disabled')
        self.log_message(self.bot_log, f"Inicjowanie pozycji {direction}...")
        threading.Thread(target=self.client.start_trade_manager, args=(params, direction), daemon=True).start()

    def start_bot_with_existing_position(self):
        if not self.client:
            self.log_message(self.bot_log, "Błąd: Klient nie jest połączony.")
            return
        try:
            params = { 'account': self.account_entry.get(), 'trailing_stop': int(self.stoploss_entry.get()), 'daily_goal': int(self.daily_goal_entry.get()), 'commission': 1 }
            if not params['account']:
                self.log_message(self.bot_log, "Błąd: Numer rachunku jest wymagany.")
                return
        except ValueError:
            self.log_message(self.bot_log, "Błąd: Parametry menedżera muszą być liczbami.")
            return
        
        self.start_long_button.config(state='disabled')
        self.start_short_button.config(state='disabled')
        self.start_bot_existing_pos_button.config(state='disabled')
        self.log_message(self.bot_log, "Uruchamiam bota z istniejącą pozycją...")
        threading.Thread(target=self.client.start_trade_manager_with_existing_position, args=(params,), daemon=True).start()

    def close_trade_manually(self):
        if self.client:
            self.log_message(self.bot_log, "Ręczne zamykanie pozycji...")
            self.client.close_trade_manually()

    def update_order_monitor(self, data):
        order_id = data.get('id_dm')
        if not order_id: return
        data['status'] = self.STATUS_MAP.get(data['status'], data['status'])
        data['k_s'] = self.SIDE_MAP.get(data['k_s'], data['k_s'])
        values = [data.get(col, '') for col in ('id_dm', 'id_klienta', 'status', 'symbol', 'k_s', 'ilosc', 'pozostalo', 'wykonano', 'limit', 'cena_ost', 'czas')]
        if order_id in self.orders:
            item_id = self.orders[order_id]
            self.order_tree.item(item_id, values=values)
        else:
            item_id = self.order_tree.insert('', 'end', values=values)
            self.orders[order_id] = item_id

    def send_order(self):
        account = self.account_entry.get()
        direction = self.direction_combo.get()
        quantity_str = self.quantity_entry.get()
        price_str = self.price_entry.get()
        if not all([account, direction, quantity_str, price_str]):
            self.log_message(self.status_log, "BŁĄD: Wszystkie pola zlecenia muszą być wypełnione.")
            return
        try:
            quantity = int(quantity_str)
            if quantity <= 0: raise ValueError
        except ValueError:
            self.log_message(self.status_log, "BŁĄD: Ilość musi być dodatnią liczbą całkowitą.")
            return
        try:
            price = float(price_str)
            if price <= 0: raise ValueError
        except ValueError:
            self.log_message(self.status_log, "BŁĄD: Cena musi być dodatnią liczbą.")
            return
        if self.client:
            self.log_message(self.status_log, f"Przygotowywanie zlecenia {direction} {quantity} szt. {self.TARGET_ISIN} z limitem {price}...")
            params = (account, direction, quantity, price)
            threading.Thread(target=self.client.send_limit_order, args=params, daemon=True).start()

    def log_message(self, widget, message):
        if isinstance(message, str) and message.startswith("<FIXML"):
            message = re.sub(r'^<FIXML[^>]*>', '', message, flags=re.DOTALL)
            message = re.sub(r'</FIXML>$', '', message, flags=re.DOTALL)
            message = message.strip()
        widget.config(state='normal')
        widget.insert(tk.END, f"{time.strftime('%H:%M:%S')} - {message}\n")
        widget.yview(tk.END)
        widget.config(state='disabled')

    def start_login_thread(self):
        self.login_button.config(state='disabled')
        self.disconnect_button.config(state='disabled')
        username = self.username_entry.get()
        password = self.password_entry.get()
        if username == "TWOJA_NAZWA_UŻYTKOWNIKA" or password == "TWOJE_HASŁO":
            self.log_message(self.status_log, "BŁĄD: Wprowadź swoje dane logowania.")
            self.login_button.config(state='normal')
            return
        self.client = BossaAPIClient(username, password, self.queue)
        threading.Thread(target=self.client.run, daemon=True).start()

    def display_portfolio(self, portfolio_data):
        self.portfolio_display.config(state='normal')
        self.portfolio_display.delete('1.0', tk.END)
        formatted_text = ""
        for account, data in portfolio_data.items():
            formatted_text += f"[ RACHUNEK: {account} ]\n"
            formatted_text += "  Środki:\n"
            for fund, value in data.get('funds', {}).items():
                formatted_text += f"    - {fund}: {value}\n"
            formatted_text += "\n  Pozycje:\n"
            positions = data.get('positions', [])
            if positions:
                for pos in positions:
                    formatted_text += f"    - Symbol: {pos['symbol']}, Ilość: {pos['quantity']}, ISIN: {pos['isin']}\n"
            else:
                formatted_text += "    - Brak otwartych pozycji.\n"
            formatted_text += "-"*40 + "\n"
        self.portfolio_display.insert(tk.END, formatted_text)
        self.portfolio_display.config(state='disabled')


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
            self.disconnect_button.config(state='disabled')
            self.client.disconnect()
    
    def _get_ports_from_registry(self):
        try:
            key_path = r"Software\COMARCH S.A.\NOL3\7\Settings"
            registry_key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_READ)
            self.sync_port, _ = winreg.QueryValueEx(registry_key, "nca_psync")
            self.async_port, _ = winreg.QueryValueEx(registry_key, "nca_pasync")
            self.sync_port = int(self.sync_port)
            self.async_port = int(self.async_port)
            winreg.CloseKey(registry_key)
            self._log(f"Odczytano porty: Sync={self.sync_port}, Async={self.async_port}")
            return True
        except FileNotFoundError:
            self._log("BŁĄD: Nie znaleziono klucza rejestru bossaNOL3.")
            return False
        except Exception as e:
            self._log(f"BŁĄD podczas odczytu rejestru: {e}")
            return False

    def _send_message(self, sock, message):
        encoded_message = message.encode('utf-8')
        header = struct.pack('<I', len(encoded_message))
        sock.sendall(header)
        sock.sendall(encoded_message)

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

class BossaAPIClient:
    def __init__(self, username, password, gui_queue):
        self.username = username
        self.password = password
        self.gui_queue = gui_queue
        self.sync_port = None
        self.async_port = None
        self.is_logged_in = False
        self.portfolio = {}
        self.stop_event = threading.Event()
        self.request_id = 1
        self.async_socket = None
        self.market_data = {}
        self.TARGET_ISIN = "PL0GF0031252"
        self.manager_thread = None
        self.manager_stop_event = threading.Event()
        self.manager_state = BotState.STOPPED
        self.manager_params = {}
        self.entry_order_id = None
        self.stop_order_id = None
        self.position_entry_price = 0
        self.active_stop_price = 0
        self.position_type = None
        self.daily_profit = 0
        self.existing_position_details = None

    def cancel_order(self, order_details):
        """Wysyła w pełni sformatowane żądanie anulowania zlecenia."""
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
        if response and '<ExecRpt' in response:
            self._parse_execution_report(response)
        else:
            self._log(f"Odpowiedź na anulatę zlecenia {order_details['id_dm']}: {response}")

    def _parse_portfolio(self, xml_data):
        root = ET.fromstring(xml_data)
        open_position_qty = 0
        parsed_portfolio = {}
        self.existing_position_details = None

        for statement in root.findall('Statement'):
            account_id = statement.get('Acct')
            parsed_portfolio[account_id] = {'funds': {}, 'positions': []}
            for fund in statement.findall('Fund'):
                parsed_portfolio[account_id]['funds'][fund.get('name')] = fund.get('value')
            for position in statement.findall('.//Position'):
                instrument = position.find('Instrmt')
                pos_data = { 'symbol': instrument.get('Sym'), 'isin': instrument.get('ID'), 'quantity': int(position.get('Acc110')), 'blocked_quantity': position.get('Acc120') }
                parsed_portfolio[account_id]['positions'].append(pos_data)
                
                if pos_data['isin'] == self.TARGET_ISIN:
                    open_position_qty += int(pos_data['quantity'])
                    if account_id == "00-22-172137" and pos_data['quantity'] != 0:
                        self.existing_position_details = {
                            'account': account_id,
                            'symbol': pos_data['symbol'],
                            'isin': pos_data['isin'],
                            'quantity': pos_data['quantity'],
                            'position_type': "LONG" if pos_data['quantity'] > 0 else "SHORT"
                        }

        self.portfolio = parsed_portfolio
        self.gui_queue.put(("PORTFOLIO_UPDATE", {
            'portfolio_data': self.portfolio,
            'open_position_qty': open_position_qty,
            'existing_position_found': self.existing_position_details is not None,
            'existing_position_details': self.existing_position_details
        }))
    
    def _parse_execution_report(self, xml_data):
        try:
            root = ET.fromstring(xml_data)
            exec_rpt = root.find('ExecRpt')
            if exec_rpt is None: return
            instrument = exec_rpt.find('Instrmt')
            symbol = instrument.get('Sym', 'N/A') if instrument is not None else 'N/A'
            order_data = {'id_dm': exec_rpt.get('OrdID', ''), 'id_klienta': exec_rpt.get('ID', ''),'status': exec_rpt.get('Stat', ''), 'symbol': symbol, 'k_s': exec_rpt.get('Side', ''), 'ilosc': exec_rpt.find('.//OrdQty').get('Qty', '') if exec_rpt.find('.//OrdQty') is not None else '', 'pozostalo': exec_rpt.get('LeavesQty', ''), 'wykonano': exec_rpt.get('CumQty', ''), 'limit': exec_rpt.get('Px', ''), 'cena_ost': exec_rpt.get('LastPx', ''), 'czas': exec_rpt.get('TxnTm', '')}
            self.gui_queue.put(("EXEC_REPORT", order_data))
            client_id = exec_rpt.get('ID')
            status = exec_rpt.get('Stat')
            dm_id = exec_rpt.get('OrdID')
            self._bot_log(f"DEBUG: Parsing ExecRpt - ID Klienta: {client_id}, Status: {status}, ID DM: {dm_id} ")
            if status == '2':
                last_px_str = exec_rpt.get('LastPx')
                if not last_px_str: return

                client_id = exec_rpt.get('ID')
                dm_id = exec_rpt.get('OrdID')

                if self.manager_state == BotState.WAITING_FOR_ENTRY_FILL and client_id == self.entry_order_id:
                    self._bot_log(f"DEBUG: Entry order filled. ID Klienta: {client_id}, ID DM: {dm_id}, Cena: {last_px_str}. Preparing to place stop order.")
                    self.position_entry_price = float(last_px_str)
                    self.entry_order_id = dm_id
                    if self.position_type == "LONG":
                        self.manager_state = BotState.IN_LONG_POSITION
                        stop_price = self.position_entry_price - self.manager_params['trailing_stop']
                        self.active_stop_price = stop_price
                        self._bot_log(f"Pozycja LONG otwarta @ {self.position_entry_price:.2f}. Ustawiam Stop-Loss na {stop_price:.2f}")
                        self.send_limit_order(self.manager_params['account'], "Sprzedaż", 1, stop_price, is_managed=True)
                    elif self.position_type == "SHORT":
                        self.manager_state = BotState.IN_SHORT_POSITION
                        stop_price = self.position_entry_price + self.manager_params['trailing_stop']
                        self.active_stop_price = stop_price
                        self._bot_log(f"Pozycja SHORT otwarta @ {self.position_entry_price:.2f}. Ustawiam Stop-Loss na {stop_price:.2f}")
                        self.send_limit_order(self.manager_params['account'], "Kupno", 1, stop_price, is_managed=True)
                    self.gui_queue.put(("BOT_STATE_UPDATE", {'entry_price': self.position_entry_price, 'commission': self.manager_params['commission'], 'position_type': self.position_type}))
                elif client_id == self.stop_order_id:
                    exit_price = float(last_px_str)
                    profit = (exit_price - self.position_entry_price) if self.position_type == "LONG" else (self.position_entry_price - exit_price)
                    profit -= 2 * self.manager_params['commission']
                    self.daily_profit += profit
                    self._bot_log(f"Pozycja ZAMKNIĘTA @ {exit_price:.2f}. Zysk/Strata: {profit:.2f}. Zysk dzienny: {self.daily_profit:.2f}")
                    self.manager_stop_event.set()
                    self.manager_state = BotState.IDLE
                    self.gui_queue.put(("BOT_STATE_UPDATE", {'entry_price': None}))
                elif self.manager_state in [BotState.IN_LONG_POSITION, BotState.IN_SHORT_POSITION] and client_id == self.stop_order_id:
                    self._bot_log(f"DEBUG: Stop-loss order acknowledged by the server. ID: {dm_id}")
                    self.stop_order_id = dm_id
            elif status in ['4', '8']:
                client_id = exec_rpt.get('ID')
                dm_id = exec_rpt.get('OrdID')
                if self.manager_state == BotState.WAITING_FOR_ENTRY_FILL and client_id == self.stop_order_id:
                    self._bot_log(f"DEBUG: Stop-loss order updated. Status: {status}, Server ID: {dm_id}")
                    self.stop_order_id = dm_id
                if self.manager_state in [BotState.IN_LONG_POSITION, BotState.IN_SHORT_POSITION] and dm_id == self.stop_order_id:
                    self._bot_log(f"DEBUG: Active stop order (ID: {self.stop_order_id}) was canceled/rejected. Clearing stop_order_id.")
                    self.stop_order_id = None
        except Exception as e:
            self._log(f"Błąd podczas parsowania ExecutionReport: {e}")

    def _parse_market_data(self, xml_data):
        try:
            root = ET.fromstring(xml_data)
            for inc_element in root.findall('.//Inc'):
                entry_type = inc_element.get('Typ')
                instrument = inc_element.find('Instrmt')
                if instrument is not None:
                    isin = instrument.get('ID')
                    if isin not in self.market_data: self.market_data[isin] = {}
                    price_str = inc_element.get('Px')
                    size_str = inc_element.get('Sz') # Extract size
                    if price_str:
                        price = float(price_str)
                        if entry_type == '0': # BID
                            self.market_data[isin]['bid'] = price
                            if size_str: self.market_data[isin]['bid_qty'] = int(float(size_str)) # Store bid quantity
                        elif entry_type == '1': # ASK
                            self.market_data[isin]['ask'] = price
                            if size_str: self.market_data[isin]['ask_qty'] = int(float(size_str)) # Store ask quantity
                        elif entry_type == '2': self.market_data[isin]['last_price'] = price
                    if size_str and entry_type == 'C':
                        self.market_data[isin]['lop'] = int(float(size_str))
            if self.TARGET_ISIN in self.market_data:
                data_to_send = self.market_data[self.TARGET_ISIN]
                data_to_send['isin'] = self.TARGET_ISIN
                self.gui_queue.put(("MARKET_DATA_UPDATE", data_to_send))
        except Exception as e:
            self._log(f"Błąd podczas parsowania danych rynkowych: {e}")

    def _bot_log(self, message):
        self.gui_queue.put(("BOT_LOG", message))

    def start_trade_manager(self, params, direction):
        if self.manager_state not in [BotState.STOPPED, BotState.IDLE]:
            self._bot_log("Błąd: Menedżer jest już aktywny w innej pozycji.")
            return
        self.manager_params = params
        self.manager_stop_event.clear()
        market_info = self.market_data.get(self.TARGET_ISIN)
        if not market_info:
            self._bot_log("Błąd: Brak danych rynkowych. Dodaj instrument do filtra.")
            return
        if direction == "Kupno":
            entry_price = market_info.get('ask')
            if not entry_price:
                self._bot_log("Błąd: Brak ceny ASK do otwarcia pozycji LONG.")
                return
            self.position_type = "LONG"
        else:
            entry_price = market_info.get('bid')
            if not entry_price:
                self._bot_log("Błąd: Brak ceny BID do otwarcia pozycji SHORT.")
                return
            self.position_type = "SHORT"
        self._bot_log(f"Otwieram pozycję {self.position_type} zleceniem LIMIT po cenie {entry_price}...")
        self.manager_state = BotState.WAITING_FOR_ENTRY_FILL
        self.send_limit_order(params['account'], direction, 1, entry_price, is_managed=True)
        self.manager_thread = threading.Thread(target=self._trailing_stop_loop, daemon=True)
        self.manager_thread.start()

    def start_trade_manager_with_existing_position(self, params):
        if not self.existing_position_details:
            self._bot_log("Błąd: Brak istniejącej pozycji do zarządzania.")
            return
        if self.manager_state not in [BotState.STOPPED, BotState.IDLE]:
            self._bot_log("Błąd: Menedżer jest już aktywny w innej pozycji.")
            return

        self.manager_params = params
        self.manager_stop_event.clear()

        current_market_price = self.market_data.get(self.TARGET_ISIN, {}).get('last_price')
        current_bid = self.market_data.get(self.TARGET_ISIN, {}).get('bid')
        current_ask = self.market_data.get(self.TARGET_ISIN, {}).get('ask')

        if not all([current_market_price, current_bid, current_ask]):
            self._bot_log("Błąd: Brak aktualnych danych rynkowych (LAST, BID, ASK) dla istniejącej pozycji. Nie można ustawić stop-lossa.")
            return

        self.position_type = self.existing_position_details['position_type']
        self.position_entry_price = current_market_price # Reference for trailing stop

        # Determine smallest tick size (assuming 0.01 for now, adjust if needed for your instrument)
        TICK_SIZE = 1

        if self.position_type == "LONG":
            self.manager_state = BotState.IN_LONG_POSITION
            target_stop_from_last = current_market_price - self.manager_params['trailing_stop']
            
            initial_stop_price = target_stop_from_last
            if current_bid is not None and initial_stop_price <= current_bid:
                # Adjust stop price to be slightly above the current BID to avoid immediate execution
                initial_stop_price = current_bid + TICK_SIZE
                self._bot_log(f"UWAGA: Początkowy stop-loss dla LONG ({target_stop_from_last:.2f}) byłby natychmiast wykonany (BID: {current_bid:.2f}). Dostosowano do: {initial_stop_price:.2f}")
            
            self.active_stop_price = initial_stop_price
            self._bot_log(f"Zarządzam istniejącą pozycją LONG. Cena referencyjna (LAST): {self.position_entry_price:.2f}. Ustawiam początkowy Stop-Loss na {self.active_stop_price:.2f}")
            self.send_limit_order(self.manager_params['account'], "Sprzedaż", self.existing_position_details['quantity'], self.active_stop_price, is_managed=True)
        
        elif self.position_type == "SHORT":
            self.manager_state = BotState.IN_SHORT_POSITION
            target_stop_from_last = current_market_price + self.manager_params['trailing_stop']
            
            initial_stop_price = target_stop_from_last
            if current_ask is not None and initial_stop_price >= current_ask:
                # Adjust stop price to be slightly below the current ASK to avoid immediate execution
                initial_stop_price = current_ask - TICK_SIZE
                self._bot_log(f"UWAGA: Początkowy stop-loss dla SHORT ({target_stop_from_last:.2f}) byłby natychmiast wykonany (ASK: {current_ask:.2f}). Dostosowano do: {initial_stop_price:.2f}")
            
            self.active_stop_price = initial_stop_price
            self._bot_log(f"Zarządzam istniejącą pozycją SHORT. Cena referencyjna (LAST): {self.position_entry_price:.2f}. Ustawiam początkowy Stop-Loss na {self.active_stop_price:.2f}")
            self.send_limit_order(self.manager_params['account'], "Kupno", self.existing_position_details['quantity'], self.active_stop_price, is_managed=True)
        
        self.gui_queue.put(("BOT_STATE_UPDATE", {'entry_price': self.position_entry_price, 'commission': self.manager_params['commission'], 'position_type': self.position_type}))
        
        self.manager_thread = threading.Thread(target=self._trailing_stop_loop, daemon=True)
        self.manager_thread.start()

    def close_trade_manually(self):
        if self.manager_state not in [BotState.IN_LONG_POSITION, BotState.IN_SHORT_POSITION]:
            self._bot_log("Brak otwartej pozycji do zamknięcia.")
            return
        if self.stop_order_id:
            self._bot_log(f"Anulowanie aktywnego stop-lossa (ID: {self.stop_order_id})...")
            qty_to_cancel = self.existing_position_details['quantity'] if self.existing_position_details else 1
            self.cancel_order({'id_dm': self.stop_order_id, 'k_s_text': 'Sprzedaż' if self.position_type == 'LONG' else 'Kupno', 'ilosc': qty_to_cancel, 'rachunek': self.manager_params['account']})
            self.stop_order_id = None
        market_info = self.market_data.get(self.TARGET_ISIN)
        if self.position_type == "LONG":
            exit_price = market_info.get('bid')
            self._bot_log(f"Ręczne zamykanie LONG po cenie rynkowej (BID): {exit_price}")
            qty_to_close = self.existing_position_details['quantity'] if self.existing_position_details else 1
            self.send_limit_order(self.manager_params['account'], "Sprzedaż", qty_to_close, exit_price, is_managed=True)
        elif self.position_type == "SHORT":
            exit_price = market_info.get('ask')
            self._bot_log(f"Ręczne zamykanie SHORT po cenie rynkowej (ASK): {exit_price}")
            qty_to_close = self.existing_position_details['quantity'] if self.existing_position_details else 1
            self.send_limit_order(self.manager_params['account'], "Kupno", qty_to_close, exit_price, is_managed=True)
        self.manager_stop_event.set()

    def _trailing_stop_loop(self):
        self._bot_log("Pętla Trailing Stop rozpoczęta.")
        while not self.manager_stop_event.is_set():
            time.sleep(1.5)
            if self.manager_state not in [BotState.IN_LONG_POSITION, BotState.IN_SHORT_POSITION]:
                self._bot_log("DEBUG: Menedżer nie jest w aktywnej pozycji, kontynuuję pętlę.")
                continue
            last_price = self.market_data.get(self.TARGET_ISIN, {}).get('last_price')
            if not last_price:
                self._bot_log("DEBUG: Brak aktualnej ceny LAST, pomijam ruch stop-lossa.")
                continue
            
            new_stop_price = self.active_stop_price
            should_move_stop = False
            
            qty_for_stop = self.existing_position_details['quantity'] if self.existing_position_details else 1

            if self.position_type == BotState.IN_LONG_POSITION:
                potential_stop = last_price - self.manager_params['trailing_stop']
                if potential_stop > self.active_stop_price:
                    new_stop_price = potential_stop
                    should_move_stop = True
            elif self.position_type == BotState.IN_SHORT_POSITION:
                potential_stop = last_price + self.manager_params['trailing_stop']
                if potential_stop < self.active_stop_price:
                    new_stop_price = potential_stop
                    should_move_stop = True
            
            if should_move_stop:
                self._bot_log(f"Cena przesunęła się. Przesuwam stop-loss z {self.active_stop_price:.2f} na {new_stop_price:.2f}")
                
                if self.stop_order_id:
                    self._bot_log(f"DEBUG: Próba anulowania starego zlecenia stop-loss ID: {self.stop_order_id}")
                    self.cancel_order({'id_dm': self.stop_order_id, 'k_s_text': 'Sprzedaż' if self.position_type == "LONG" else 'Kupno', 'ilosc': qty_for_stop, 'rachunek': self.manager_params['account']})
                    self.stop_order_id = None
                else:
                    self._bot_log("DEBUG: Brak aktywnego ID zlecenia stop-loss do anulowania, składam nowe.")

                self.active_stop_price = new_stop_price
                if self.position_type == "LONG":
                    self._bot_log(f"DEBUG: Składam nowe zlecenie stop-loss LONG na cenę {new_stop_price:.2f}")
                    self.send_limit_order(self.manager_params['account'], "Sprzedaż", qty_for_stop, new_stop_price, is_managed=True)
                else:
                    self._bot_log(f"DEBUG: Składam nowe zlecenie stop-loss SHORT na cenę {new_stop_price:.2f}")
                    self.send_limit_order(self.manager_params['account'], "Kupno", qty_for_stop, new_stop_price, is_managed=True)
            else:
                self._bot_log(f"DEBUG: Stop-loss nie wymaga przesunięcia. Aktywny stop: {self.active_stop_price:.2f}, Ostatnia cena: {last_price:.2f}")
        self._bot_log("Pętla Trailing Stop zakończona.")

    def send_limit_order(self, account, direction, quantity, price, is_managed=False):
        self.request_id += 1
        client_order_id = self.request_id
        if is_managed:
            if self.manager_state == BotState.WAITING_FOR_ENTRY_FILL and not self.entry_order_id:
                self.entry_order_id = str(client_order_id)
            elif self.manager_state in [BotState.IN_LONG_POSITION, BotState.IN_SHORT_POSITION]:
                self.stop_order_id = str(client_order_id)
                self._bot_log(f"DEBUG: Ustawiono self.stop_order_id na {self.stop_order_id} dla nowego zlecenia stop-loss.")
        side = '1' if direction == "Kupno" else '2'
        trade_date = datetime.now().strftime('%Y%m%d')
        transact_time = datetime.now().strftime('%Y%m%d-%H:%M:%S')
        order_type = 'L'
        time_in_force = '0'
        fixml_request = f"""<FIXML v="5.0" r="20080317" s="20080314"><Order ID="{client_order_id}" TrdDt="{trade_date}" Acct="{account}" Side="{side}" TxnTm="{transact_time}" OrdTyp="{order_type}" Px="{price:.2f}" Ccy="PLN" TmInForce="{time_in_force}"><Instrmt ID="{self.TARGET_ISIN}" Src="4"/><OrdQty Qty="{quantity}"/></Order></FIXML>"""
        response = self._send_and_receive_sync(fixml_request)
        if response and '<ExecRpt' in response:
            self._parse_execution_report(response)
        elif response:
             self._log(f"Odrzucenie zlecenia. Odpowiedź: {response}")
        else:
            self._log("Brak odpowiedzi serwera na zlecenie.")

    def _log(self, message):
        self.gui_queue.put(("LOG", message))

    def _send_and_receive_sync(self, message):
        sync_socket = None
        try:
            sync_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sync_socket.connect(('127.0.0.1', self.sync_port))
            self._send_message(sync_socket, message)
            response = self._receive_message(sync_socket)
            return response
        except ConnectionAbortedError as e:
            self._log(f"BŁĄD: Połączenie zostało zerwane przez serwer (NOL3). {e}")
            return None
        except Exception as e:
            self._log(f"BŁĄD komunikacji synchronicznej: {e}")
            return None
        finally:
            if sync_socket:
                sync_socket.close()

    def add_to_filter(self, isin):
        self.request_id += 1
        fixml_request = f'<FIXML v="5.0" r="20080317" s="20080314"><MktDataReq ReqID="{self.request_id}" SubReqTyp="1" MktDepth="0"><req Typ="0"/><req Typ="1"/><req Typ="2"/><req Typ="B"/><req Typ="C"/><req Typ="3"/><req Typ="4"/><req Typ="5"/><req Typ="7"/><req Typ="r"/><req Typ="8"/><InstReq><Instrmt ID="{isin}" Src="4"/></InstReq></MktDataReq></FIXML>'
        response = self._send_and_receive_sync(fixml_request)
        if response and '<MktDataFull' in response:
            self._log(f"Pomyślnie dodano {isin} do filtra.")
        else:
            self._log(f"Błąd podczas dodawania do filtra. Odpowiedź: {response}")

    def clear_filter(self):
        self.request_id += 1
        fixml_request = f'<FIXML v="5.0" r="20080317" s="20080314"><MktDataReq ReqID="{self.request_id}" SubReqTyp="2"></MktDataReq></FIXML>'
        response = self._send_and_receive_sync(fixml_request)
        if response and '<MktDataFull' in response:
            self._log("Pomyślnie wyczyszczono filtr.")
        else:
            self._log(f"Błąd podczas czyszczenia filtra. Odpowiedź: {response}")

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
                elif '<Statement' in message:
                    self._parse_portfolio(message)
        except Exception as e:
            if not self.stop_event.is_set(): self._log(f"Błąd w wątku asynchronicznym: {e}")
        finally:
            if self.async_socket: self.async_socket.close()
            
    def run(self):
        if not self._get_ports_from_registry():
            self.gui_queue.put(("LOGIN_FAIL", "Błąd odczytu portów z rejestru."))
            return
        self.request_id += 1
        login_request = f'<FIXML v="5.0" r="20080317" s="20080314"><UserReq UserReqID="{self.request_id}" UserReqTyp="1" Username="{self.username}" Password="{self.password}"/></FIXML>'
        self._log("Wysyłanie żądania logowania...")
        response = self._send_and_receive_sync(login_request)
        if response and '<UserRsp' in response:
            root = ET.fromstring(response)
            user_rsp = root.find('UserRsp')
            if user_rsp is not None and user_rsp.get('UserStat') == '1':
                self.is_logged_in = True
                self.gui_queue.put(("LOGIN_SUCCESS", None))
                self.manager_state = BotState.IDLE
                self._async_listener()
            else:
                status = user_rsp.get('UserStat') if user_rsp is not None else 'brak'
                self.gui_queue.put(("LOGIN_FAIL", f"Status: {status}"))
        else:
            self.gui_queue.put(("LOGIN_FAIL", f"Nieoczekiwana odpowiedź: {response}"))

    def disconnect(self):
        self.manager_stop_event.set()
        self.stop_event.set()
        if self.async_socket:
            try:
                self.async_socket.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            finally:
                self.async_socket.close()
        self.gui_queue.put(("DISCONNECTED", None))

    def _get_ports_from_registry(self):
        try:
            key_path = r"Software\COMARCH S.A.\NOL3\7\Settings"
            registry_key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_READ)
            self.sync_port, reg_type = winreg.QueryValueEx(registry_key, "nca_psync")
            self.async_port, _ = winreg.QueryValueEx(registry_key, "nca_pasync")
            self.sync_port = int(self.sync_port)
            self.async_port = int(self.async_port)
            print(self.sync_port, reg_type)
            winreg.CloseKey(registry_key)
            self._log(f"Odczytano porty: Sync={self.sync_port}, Async={self.async_port}")
            return True
        except FileNotFoundError:
            self._log("BŁĄD: Nie znaleziono klucza rejestru bossaNOL3.")
            return False
        except Exception as e:
            self._log(f"BŁĄD podczas odczytu rejestru: {e}")
            return False

    def _send_message(self, sock, message):
        encoded_message = message.encode('utf-8')
        header = struct.pack('<I', len(encoded_message))
        sock.sendall(header)
        sock.sendall(encoded_message)

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
    root = tk.Tk()
    app = BossaApp(root)
    root.mainloop()