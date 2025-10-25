from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QComboBox, QGroupBox, QCheckBox, QFileDialog
)
from PyQt6.QtCore import QTimer, Qt, QSize
from PyQt6.QtGui import QFont
from PyQt6.QtWebEngineWidgets import QWebEngineView
import os, time, random, json
from datetime import datetime

class ChartsTab(QWidget):
    """
    Charts tab implemented using an embedded QWebEngineView + lightweight-charts JS.
    Requires: pip install PyQt6-WebEngine
    """
    def __init__(self, parent):
        super().__init__()
        self.parent = parent
        self.chart_data = []
        self.current_symbol = ""
        self.live_update_timer = QTimer(self)
        self.live_update_timer.timeout.connect(self.update_live_data)
        self.web_ready = False
        self._pending_payload = None
        self._build_ui()
        self._init_webview()

    def _build_ui(self):
        layout = QVBoxLayout(self)

        control_widget = QWidget()
        control_layout = QHBoxLayout(control_widget)

        control_layout.addWidget(QLabel("Symbol:"))
        self.chart_symbol_input = QLineEdit("FW20Z2520")
        control_layout.addWidget(self.chart_symbol_input)

        control_layout.addWidget(QLabel("Timeframe:"))
        self.timeframe_combo = QComboBox()
        self.timeframe_combo.addItems(["Tick", "1m", "5m", "15m", "30m", "1h", "4h", "1d"])
        control_layout.addWidget(self.timeframe_combo)

        control_layout.addWidget(QLabel("Data File:"))
        self.data_file_input = QLineEdit("historical_data.csv")
        control_layout.addWidget(self.data_file_input)
        self.browse_file_button = QPushButton("Browse...")
        self.browse_file_button.clicked.connect(self.browse_data_file)
        control_layout.addWidget(self.browse_file_button)

        self.load_chart_button = QPushButton("Load Historical Data")
        self.load_chart_button.clicked.connect(self.load_historical_data)
        control_layout.addWidget(self.load_chart_button)

        self.start_live_button = QPushButton("Start Live Updates")
        self.start_live_button.clicked.connect(self.start_live_updates)
        self.start_live_button.setEnabled(False)
        control_layout.addWidget(self.start_live_button)

        control_layout.addStretch()
        layout.addWidget(control_widget)

        # Web view container (lightweight-charts JS runs here)
        self.webview = QWebEngineView()
        self.webview.setMinimumSize(QSize(800, 500))
        layout.addWidget(self.webview, stretch=1)

        self.chart_status_label = QLabel("Ready to load historical data")
        layout.addWidget(self.chart_status_label)

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
        layout.addWidget(indicators_group)

    def _init_webview(self):
        """Load the HTML container with lightweight-charts and helper functions."""
        html = """
        <!doctype html>
        <html>
        <head>
          <meta charset="utf-8">
          <style>
            html,body,#chart { height: 100%; margin: 0; padding: 0; }
            body { background: #fff; font-family: Arial, Helvetica, sans-serif; }
          </style>
          <!-- use a specific stable version of lightweight-charts -->
          <script src="https://unpkg.com/lightweight-charts@3.8.0/dist/lightweight-charts.standalone.production.js"></script>
        </head>
        <body>
          <div id="chart"></div>
          <script>
            const chart = LightweightCharts.createChart(document.getElementById('chart'), {
              layout: { backgroundColor: '#ffffff', textColor: '#333' },
              rightPriceScale: { visible: true },
              timeScale: { timeVisible: true, secondsVisible: true }
            });

            // prefer candlestick series, fallback to line series if not available
            let candleSeries;
            let useCandles = true;
            if (typeof chart.addCandlestickSeries === 'function') {
              candleSeries = chart.addCandlestickSeries();
            } else {
              // some bundles might not expose candlesticks; use line as fallback
              candleSeries = chart.addLineSeries();
              useCandles = false;
            }

            window.setData = function(data) {
              try {
                if (!useCandles) {
                  // line series expects {time, value}
                  const lineData = data.map(d => ({ time: d.time, value: d.close }));
                  candleSeries.setData(lineData);
                } else {
                  candleSeries.setData(data);
                }
                if (data && data.length) chart.timeScale().fitContent();
              } catch(e) { console.error(e); }
            }

            window.updatePoint = function(point) {
              try {
                if (!useCandles) {
                  candleSeries.update({ time: point.time, value: point.close });
                } else {
                  candleSeries.update(point);
                }
              } catch(e) { console.error(e); }
            }

            window.resizeObserver = new ResizeObserver(() => {
              chart.applyOptions({ width: document.getElementById('chart').clientWidth, height: document.getElementById('chart').clientHeight });
            });
            window.resizeObserver.observe(document.getElementById('chart'));
          </script>
        </body>
        </html>
        """
        self.webview.setHtml(html)
        # wait for the page to be fully initialized before running JS
        self.webview.loadFinished.connect(self._on_webview_ready)

    def _on_webview_ready(self, ok: bool):
        self.web_ready = bool(ok)
        if self._pending_payload:
            # push pending data to JS now that webview is ready
            js = f"setData({self._pending_payload});"
            try:
                self.webview.page().runJavaScript(js)
            except Exception as e:
                if hasattr(self.parent, "status_log"):
                    self.parent.status_log.appendPlainText(f"JS run error: {e}")
            self._pending_payload = None

    def browse_data_file(self):
        file_path, _ = QFileDialog.getOpenFileName(self, "Select Historical Data File", "", "CSV Files (*.csv);;All Files (*)")
        if file_path:
            self.data_file_input.setText(file_path)

    def load_historical_data(self):
        try:
            file_path = self.data_file_input.text()
            symbol = self.chart_symbol_input.text()
            timeframe = self.timeframe_combo.currentText()
            if not os.path.exists(file_path):
                self.chart_status_label.setText(f"Error: File not found - {file_path}")
                return
            self.chart_data = self.parse_historical_data(file_path, symbol)
            if not self.chart_data:
                self.chart_status_label.setText("No data found for the specified symbol")
                return
            self.current_symbol = symbol
            self.chart_status_label.setText(f"Loaded {len(self.chart_data)} records for {symbol}")
            # Prepare data for lightweight-charts JS (time in unix seconds)
            candles = []
            for bar in self.chart_data:
                candles.append({
                    "time": int(bar["time"]),
                    "open": float(bar["open"]),
                    "high": float(bar["high"]),
                    "low": float(bar["low"]),
                    "close": float(bar["close"])
                })
            payload = json.dumps(candles)
            # If webview is not yet ready, store payload and it will be pushed on loadFinished.
            if self.web_ready:
                js = f"setData({payload});"
                self.webview.page().runJavaScript(js)
            else:
                self._pending_payload = payload
            self.start_live_button.setEnabled(True)
            self.load_chart_button.setEnabled(False)
        except Exception as e:
            self.chart_status_label.setText(f"Error loading data: {e}")
            self.parent.status_log.appendPlainText(f"ChartsTab load error: {e}")

    def parse_historical_data(self, file_path, symbol):
        data = []
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                next(f)
                for line in f:
                    parts = line.strip().split(',')
                    # adjust indices to your CSV layout; expecting: symbol,...,timestamp,open,high,low,close,volume
                    if len(parts) >= 8 and parts[0] == symbol:
                        try:
                            bar = {
                                'time': int(float(parts[3])),   # timestamp in seconds
                                'open': float(parts[4]),
                                'high': float(parts[5]),
                                'low': float(parts[6]),
                                'close': float(parts[7]),
                                'volume': float(parts[8]) if len(parts) > 8 else 0
                            }
                            data.append(bar)
                        except Exception:
                            continue
            data.sort(key=lambda x: x['time'])
        except Exception as e:
            self.parent.status_log.appendPlainText(f"Error parsing historical data: {e}")
        return data

    def start_live_updates(self):
        self.chart_status_label.setText("Live updates started - simulating market data...")
        self.start_live_button.setEnabled(False)
        self.live_update_timer.start(1000)

    def update_live_data(self):
        if not self.chart_data:
            return
        last = self.chart_data[-1].copy()
        last['time'] = int(time.time())
        last['close'] = last['close'] * (1 + (random.random() - 0.5) * 0.01)
        last['high'] = max(last['high'], last['close'])
        last['low'] = min(last['low'], last['close'])
        self.chart_data.append(last)
        point = {
            "time": int(last['time']),
            "open": float(last['open']),
            "high": float(last['high']),
            "low": float(last['low']),
            "close": float(last['close'])
        }
        # Only push updates if webview is ready
        if self.web_ready:
            js = f"updatePoint({json.dumps(point)});"
            self.webview.page().runJavaScript(js)

        # log (parent.status_log exists in main app)
        if hasattr(self.parent, "status_log"):
            self.parent.status_log.appendPlainText(f"{datetime.fromtimestamp(last['time'])} - {last['close']:.2f}")