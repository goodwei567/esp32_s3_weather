"""串口采集：读取 ESP32 打印的 DATA 行，写入 SQLite。

ESP32 每行格式：DATA,<温度>,<湿度>,<气压hPa>,<海拔m>
断线（拔线、板子复位、串口号变化）会自动重连。
"""

import os
import re
import sqlite3
import threading
import time
from datetime import datetime

import serial
from serial.tools import list_ports

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "weather.db")
BAUDRATE = 115200

LINE_RE = re.compile(
    r"DATA,\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*,"
    r"\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)"
)

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
            alt   REAL
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_readings_epoch ON readings(epoch)")
    conn.commit()
    conn.close()


class Collector:
    """后台线程：持续读串口并入库。"""

    def __init__(self, port=None, db_path=DB_PATH, reset_on_open=True):
        self.port = port
        self.db_path = db_path
        self.reset_on_open = reset_on_open
        self.connected = False
        self.active_port = None
        self.samples = 0
        self.last_epoch = None
        self.last_line = ""
        self.last_error = None
        self._hinted = False
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
    def _store(self, t, h, p, alt):
        now = time.time()
        conn = sqlite3.connect(self.db_path, timeout=10)
        try:
            conn.execute(
                "INSERT INTO readings(epoch, ts, t, h, p, alt) VALUES(?,?,?,?,?,?)",
                (now, datetime.fromtimestamp(now).isoformat(timespec="seconds"),
                 t, h, p, alt),
            )
            conn.commit()
        finally:
            conn.close()
        with self._lock:
            self.samples += 1
            self.last_epoch = now
        print("[%s] 已记录 #%d  T=%.2f  H=%.2f  P=%.1f  ALT=%.0f"
              % (datetime.now().strftime("%H:%M:%S"), self.samples, t, h, p, alt))

    def _handle_line(self, line):
        if not line:
            return
        with self._lock:
            self.last_line = line
        if line.startswith("BOOT"):
            print("[采集] 检测到板子启动/复位")
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
                continue
            buf += chunk
            while b"\n" in buf:
                raw, buf = buf.split(b"\n", 1)
                self._handle_line(raw.decode("utf-8", "ignore").strip())


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
