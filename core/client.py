import threading
import socket
import struct
import time
import winreg
import xml.etree.ElementTree as ET
from datetime import datetime
from enum import Enum

class BotState(Enum):
    STOPPED = 0
    IDLE = 1
    WAITING_FOR_ENTRY_FILL = 2
    IN_LONG_POSITION = 3
    IN_SHORT_POSITION = 4

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