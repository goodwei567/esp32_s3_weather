"""串口采集：读取 ESP32 上报的 DATA / DATA2 行写入 SQLite，
并定时从心知天气取数，通过串口下发给屏幕显示。

ESP32 每行格式：
    DATA,<温度>,<湿度>,<气压hPa>,<海拔m>                        （旧版固件）
    DATA2,<温度>,<湿度>,<气压hPa>,<海拔m>,<城市>,<天气>,<最高温>,
          <最低温>,<紫外线>,<明日天气>                          （新版固件）

两种格式都支持，天气字段缺失时存 NULL。
断线（拔线、板子复位、串口号变化）会自动重连。
"""

import json
import os
import re
import sqlite3
import threading
import time
from datetime import datetime
from urllib.parse import urlencode
from urllib.request import urlopen

import serial
from serial.tools import list_ports

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "weather.db")
BAUDRATE = 115200
WEATHER_SEC = 1800            # 天气刷新间隔（秒），1800 = 30 分钟

# 心知天气配置：优先读同目录 secrets.py（真实密钥，不进 git）
SENV = {"key": "", "city": "kunming"}
_sec_path = os.path.join(BASE_DIR, "..", "firmware", "secrets.py")
if os.path.exists(_sec_path):
    with open(_sec_path, encoding="utf-8") as f:
        for line in f:
            m = re.match(r"(SENV_KEY|SENV_CITY)\s*=\s*['\"]([^'\"]+)", line.strip())
            if m:
                SENV[{"SENV_KEY": "key", "SENV_CITY": "city"}[m.group(1)]] = m.group(2)

LINE_RE = re.compile(
    r"DATA,\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*,"
    r"\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)"
)

LINE2_RE = re.compile(
    r"DATA2,\s*(-?[\d.]+)\s*,\s*(-?[\d.]+)\s*,\s*(-?[\d.]+)\s*,\s*(-?[\d.]+)"
    r"(?:,\s*([^,]*),\s*([^,]*),\s*([^,]*),\s*([^,]*),\s*([^,]*)"
    r",\s*([^,]*))?"
)

WEATHER_COLS = ("city", "cond", "hi", "lo", "uv", "cond2")


def _num_or_none(s):
    """'24.5' -> 24.5；'--'/空/非数值 -> None"""
    try:
        return float(s)
    except (TypeError, ValueError):
        return None

# 用于自动识别开发板的串口特征（CH343/CH340/CP210x/板载 USB 串口）
PORT_HINTS = ("ch343", "ch340", "cp210", "usb-serial",
              "usb single serial", "1a86", "10c4", "esp32")


def init_db(path=DB_PATH):
    conn = sqlite3.connect(path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS readings (
            id    INTEGER PRIMARY KEY AUTOINCREMENT,
            epoch REAL NOT NULL,
            ts    TEXT NOT NULL,
            t     REAL,
            h     REAL,
            p     REAL,
            alt   REAL,
            city  TEXT,
            cond  TEXT,
            hi    REAL,
            lo    REAL,
            uv    TEXT,
            rain  REAL,
            rain2 REAL
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_readings_epoch ON readings(epoch)")
    # 旧库自动补列（SQLite 没有 IF NOT EXISTS 的 ALTER TABLE）
    existing = {r[1] for r in conn.execute("PRAGMA table_info(readings)")}
    for col, decl in (("city", "TEXT"), ("cond", "TEXT"), ("hi", "REAL"),
                      ("lo", "REAL"), ("uv", "TEXT"), ("cond2", "TEXT")):
        if col not in existing:
            conn.execute("ALTER TABLE readings ADD COLUMN %s %s" % (col, decl))
    conn.commit()
    conn.close()


class Collector:
    """后台线程：持续读串口入库，并定时把天气下发给屏幕。"""

    def __init__(self, port=None, db_path=DB_PATH, reset_on_open=True,
                 weather=True):
        self.port = port
        self.db_path = db_path
        self.reset_on_open = reset_on_open
        self.weather = weather
        self.connected = False
        self.active_port = None
        self.samples = 0
        self.last_epoch = None
        self.last_line = ""
        self.last_error = None
        self._hinted = False
        self._last_wx_sent = 0
        self._wants_weather = False
        self._stop = threading.Event()
        self._lock = threading.Lock()

    # ---------- 串口探测 ----------
    @staticmethod
    def find_port():
        ports = list(list_ports.comports())
        for p in ports:
            text = " ".join(str(x) for x in
                            (p.description, p.manufacturer, p.product, p.hwid))
            if any(h in text.lower() for h in PORT_HINTS):
                return p.device
        # 次选：任何 USB 串口；蓝牙虚拟串口(COM3/COM4)不自动选
        for p in ports:
            if "usb" in (p.description or "").lower():
                return p.device
        return None

    @staticmethod
    def list_ports_text():
        return ["%s (%s)" % (p.device, p.description)
                for p in list_ports.comports()]

    def status(self):
        with self._lock:
            return {
                "connected": self.connected,
                "port": self.active_port,
                "samples": self.samples,
                "last_epoch": self.last_epoch,
                "last_line": self.last_line,
                "last_error": self.last_error,
            }

    def stop(self):
        self._stop.set()

    # ---------- 入库 ----------
    def _store(self, t, h, p, alt, wx=None):
        now = time.time()
        wx = wx or {}
        conn = sqlite3.connect(self.db_path, timeout=10)
        try:
            conn.execute(
                "INSERT INTO readings(epoch, ts, t, h, p, alt, "
                "city, cond, hi, lo, uv, cond2) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (now, datetime.fromtimestamp(now).isoformat(timespec="seconds"),
                 t, h, p, alt,
                 wx.get("city"), wx.get("cond"), _num_or_none(wx.get("hi")),
                 _num_or_none(wx.get("lo")), wx.get("uv"), wx.get("cond2")),
            )
            conn.commit()
        finally:
            conn.close()
        with self._lock:
            self.samples += 1
            self.last_epoch = now
        print("[%s] 已记录 #%d  T=%.2f  H=%.2f  P=%.1f  ALT=%.0f  天气=%s"
              % (datetime.now().strftime("%H:%M:%S"), self.samples, t, h, p,
                 alt, wx.get("cond") or "-"))

    def _handle_line(self, line):
        if not line:
            return
        with self._lock:
            self.last_line = line
        if line.startswith("BOOT"):
            print("[采集] 检测到板子启动/复位")
            # 板子重启后屏幕天气清空，立刻补发一次
            self._last_wx_sent = 0
            return
        if line.startswith("WXREQ"):
            # 板子主动请求天气（请求-应答模式，避免输入被 REPL 吞掉）
            print("[天气] 收到板子请求 WXREQ")
            self._wants_weather = True
            return
        if line.startswith("WXOK"):
            print("[%s] 屏幕已接收天气"
                  % datetime.now().strftime("%H:%M:%S"))
            return
        m2 = LINE2_RE.match(line)
        if m2:
            g = m2.groups()
            wx = dict(zip(WEATHER_COLS,
                          [None if v is None else v.strip() for v in g[4:]]))
            self._store(float(g[0]), float(g[1]), float(g[2]), float(g[3]), wx)
            self._hinted = False
            return
        m = LINE_RE.search(line)
        if m:
            self._store(*(float(v) for v in m.groups()))
            self._hinted = False
            return
        # 板子还在跑旧脚本（输出 "#1 T 24.3C ..."）时给出提示
        if not self._hinted and line.startswith("#"):
            self._hinted = True
            print("[采集] 收到数据但不是采集格式，板子上跑的可能还是旧脚本；"
                  "请把 esp32_logger.py 另存为开发板上的 main.py")

    # ---------- 主循环 ----------
    def _pulse_reset(self, ser):
        """用 RTS/DTR 复位开发板，让它开机后立刻输出一次 BOOT + DATA。"""
        try:
            ser.dtr = False          # IO0 保持高电平 -> 正常启动
            ser.rts = True           # 拉低 EN -> 复位
            time.sleep(0.15)
            ser.rts = False
            time.sleep(0.1)
            ser.reset_input_buffer()
            print("[采集] 已复位开发板，等待首次数据…")
        except Exception as exc:
            print("[采集] 复位失败（不影响继续使用）：%s" % exc)

    def run(self):
        while not self._stop.is_set():
            port = self.port or self.find_port()
            if not port:
                with self._lock:
                    self.connected = False
                    self.last_error = "没有找到串口设备"
                self._stop.wait(5)
                continue
            try:
                with serial.Serial(port, BAUDRATE, timeout=2) as ser:
                    with self._lock:
                        self.connected = True
                        self.active_port = port
                        self.last_error = None
                    print("[采集] 已打开串口 %s，等待数据…" % port)
                    if self.reset_on_open:
                        self._pulse_reset(ser)
                    self._read_loop(ser)
            except Exception as exc:
                with self._lock:
                    self.connected = False
                    self.last_error = str(exc)
                print("[采集] 串口 %s 异常：%s" % (port, exc))
            self._stop.wait(5)
        print("[采集] 已停止")

    def _read_loop(self, ser):
        buf = b""
        while not self._stop.is_set():
            try:
                chunk = ser.read(256)
            except Exception as exc:
                with self._lock:
                    self.last_error = str(exc)
                return
            if not chunk:
                if self.weather and SENV["key"] and self._wants_weather:
                    self._maybe_send_weather(ser, force=True)
                    self._wants_weather = False
                continue
            buf += chunk
            while b"\n" in buf:
                raw, buf = buf.split(b"\n", 1)
                line = raw.decode("utf-8", "ignore").strip()
                self._handle_line(line)
                if line.startswith("WXREQ"):
                    self._maybe_send_weather(ser, force=True)
                    self._wants_weather = False

    # ---------- 天气下发 ----------
    @staticmethod
    def _seniverse(path, **params):
        q = {"key": SENV["key"], "location": SENV["city"],
             "language": "en", "unit": "c"}
        q.update(params)
        url = "https://api.seniverse.com/v3%s?%s" % (path, urlencode(q))
        with urlopen(url, timeout=15) as r:
            return json.loads(r.read().decode())

    def _fetch_weather(self):
        """取实况 + 3日预报 + 紫外线。cond2 为明日白天天气。"""
        now = self._seniverse("/weather/now.json")["results"][0]
        city, cond = now["location"]["name"], now["now"]["text"]
        d = self._seniverse("/weather/daily.json", start=0, days=2)["results"][0]["daily"]
        uv = (self._seniverse("/life/suggestion.json")["results"][0]
              .get("suggestion", {}).get("uv", {}).get("brief", "--"))
        return (city, cond, d[0].get("high", "--"), d[0].get("low", "--"),
                uv, d[1].get("text_day", "--"))

    def _maybe_send_weather(self, ser, force=False):
        if not self.weather or not SENV["key"]:
            return
        now = time.time()
        if not force and now - self._last_wx_sent < WEATHER_SEC:
            return
        try:
            vals = self._fetch_weather()
            # 屏幕点阵字体只支持 ASCII，其余替换成 '?'
            fields = ["".join("?" if not (32 <= ord(c) < 127) else c
                              for c in str(v)) for v in vals]
            line = "WX," + ",".join(fields) + "\n"
            ser.write(line.encode())
            self._last_wx_sent = now
            print("[天气] 已下发:", line.strip())
        except Exception as exc:
            print("[天气] 获取/下发失败：%s" % exc)


class SimCollector(Collector):
    """没有硬件时用：生成一条温度日变化的模拟曲线，方便调试网页。"""

    def __init__(self, db_path=DB_PATH, step_sec=10, backfill_hours=48):
        super().__init__(db_path=db_path)
        self.step_sec = step_sec
        self.backfill_hours = backfill_hours
        self.active_port = "SIMULATED"

    def _fake(self, epoch):
        import math
        day = (epoch % 86400) / 86400.0
        t = 21.5 + 5.5 * math.sin((day - 0.3) * 2 * math.pi) + 0.15 * math.sin(epoch / 37)
        h = 58 + 18 * math.sin((day - 0.6) * 2 * math.pi) + 0.5 * math.sin(epoch / 23)
        p = 806 + 1.6 * math.sin(epoch / 5400) + 0.15 * math.sin(epoch / 311)
        alt = 44330 * (1 - (p / 1013.25) ** 0.1903)
        return t, h, p, alt

    def _insert_at(self, epoch, t, h, p, alt):
        conn = sqlite3.connect(self.db_path, timeout=10)
        try:
            conn.execute(
                "INSERT INTO readings(epoch, ts, t, h, p, alt) VALUES(?,?,?,?,?,?)",
                (epoch, datetime.fromtimestamp(epoch).isoformat(timespec="seconds"),
                 t, h, p, alt),
            )
            conn.commit()
        finally:
            conn.close()

    def run(self):
        # 先补一段历史数据，让图表一打开就有内容
        now = time.time()
        start = now - self.backfill_hours * 3600
        n = int((now - start) / 600)
        for i in range(n + 1):
            ep = start + i * 600
            self._insert_at(ep, *self._fake(ep))
        with self._lock:
            self.samples = n + 1
            self.connected = True
            self.last_epoch = now
        print("[模拟] 已生成 %d 条历史数据（间隔 10 分钟）" % (n + 1))

        while not self._stop.is_set():
            self._stop.wait(self.step_sec)
            if self._stop.is_set():
                break
            now = time.time()
            self._insert_at(now, *self._fake(now))
            with self._lock:
                self.samples += 1
                self.last_epoch = now
