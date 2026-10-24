"""ESP32-S3 室内气象站 · 采集端（本地传感 + 电脑下发天气 + 串口上报）。

部署方法：用 Thonny 打开本文件，另存为开发板上的 `main.py`，之后每次上电自动运行。

板子不联网。天气由电脑端通过串口下发，格式：

    WX,<城市>,<天气>,<最高温>,<最低温>,<紫外线>,<今日降水mm>,<明日降水mm>

板子每 LOG_SEC 秒上报一行（供电脑端解析入库）：

    DATA2,<温度>,<湿度>,<气压hPa>,<海拔m>,<城市>,<天气>,<最高温>,<最低温>,<紫外线>,<今日降水mm>,<明日降水mm>

硬件接线：

    1.54" IPS 屏 ST7789 (SPI) : SCL=GPIO4  SDA=GPIO5  RES=GPIO6  DC=GPIO7
                                CS=GPIO15  BLK=GPIO16  VCC=3V3  GND=GND
    AHT20 + BMP280      (I2C) : SCL=GPIO10 SDA=GPIO9   VDD=3V3  GND=GND
"""

from machine import Pin, SPI, I2C
import framebuf
import time

# ================= 屏幕 ST7789（SPI） =================
PIN_SCK, PIN_MOSI = 4, 5
PIN_RST, PIN_DC, PIN_CS, PIN_BLK = 6, 7, 15, 16
W, H = 240, 240

spi = SPI(2, baudrate=20_000_000, polarity=0, phase=0,
          sck=Pin(PIN_SCK), mosi=Pin(PIN_MOSI))
dc = Pin(PIN_DC, Pin.OUT, value=0)
cs = Pin(PIN_CS, Pin.OUT, value=1)
rst = Pin(PIN_RST, Pin.OUT, value=1)
blk = Pin(PIN_BLK, Pin.OUT, value=0)


def wr_cmd(c):
    dc(0); cs(0)
    spi.write(bytes([c]))
    cs(1)


def wr_data(buf):
    dc(1); cs(0)
    spi.write(buf)
    cs(1)


def lcd_init():
    rst(0); time.sleep_ms(20)
    rst(1); time.sleep_ms(120)
    seq = (
        (0x01, None, 150), (0x11, None, 120), (0x3A, b'\x55', 10),
        (0x36, b'\x00', 0), (0xB2, b'\x0c\x0c\x00\x33\x33', 0),
        (0xB7, b'\x35', 0), (0xBB, b'\x19', 0), (0xC0, b'\x2c', 0),
        (0xC2, b'\x01', 0), (0xC3, b'\x12', 0), (0xC4, b'\x20', 0),
        (0xC6, b'\x0f', 0), (0xD0, b'\xa4\xa1', 0), (0x21, None, 0),
        (0x13, None, 10), (0x29, None, 100),
    )
    for c, data, delay in seq:
        wr_cmd(c)
        if data:
            wr_data(data)
        if delay:
            time.sleep_ms(delay)


def set_window(x0, y0, x1, y1):
    wr_cmd(0x2A); wr_data(bytes([x0 >> 8, x0 & 0xFF, x1 >> 8, x1 & 0xFF]))
    wr_cmd(0x2B); wr_data(bytes([y0 >> 8, y0 & 0xFF, y1 >> 8, y1 & 0xFF]))
    wr_cmd(0x2C)


def fill_rect(x, y, w, h, color):
    set_window(x, y, x + w - 1, y + h - 1)
    line = bytes([color >> 8, color & 0xFF]) * w
    dc(1); cs(0)
    for _ in range(h):
        spi.write(line)
    cs(1)


def fill(color):
    fill_rect(0, 0, W, H, color)


def text(s, x, y, fg=0xFFE0, bg=0x0000, scale=2):
    cols = 8 * len(s)
    fb = framebuf.FrameBuffer(bytearray(cols), cols, 8, framebuf.MONO_VLSB)
    fb.fill(0)
    fb.text(s, 0, 0, 1)
    set_window(x, y, x + cols * scale - 1, y + 8 * scale - 1)
    dc(1); cs(0)
    for yy in range(8):
        row = bytearray()
        for xx in range(cols):
            c = fg if fb.pixel(xx, yy) else bg
            row += bytes([c >> 8, c & 0xFF]) * scale
        for _ in range(scale):
            spi.write(row)
    cs(1)


def ctext(s, y, fg, scale=2):
    text(s, (W - len(s) * 8 * scale) // 2, y, fg, 0x0000, scale)


# ================= 传感器（I2C） =================
i2c = I2C(0, scl=Pin(10), sda=Pin(9), freq=400000)
found = i2c.scan()


class AHT20:
    def __init__(self, i2c, addr=0x38):
        self.i2c, self.addr = i2c, addr
        time.sleep_ms(40)
        if not (self.i2c.readfrom(self.addr, 1)[0] & 0x08):
            self.i2c.writeto(self.addr, b'\xbe\x08\x00')
            time.sleep_ms(10)

    def read(self):
        self.i2c.writeto(self.addr, b'\xac\x33\x00')
        time.sleep_ms(80)
        d = self.i2c.readfrom(self.addr, 7)
        if d[0] & 0x80:
            return None, None
        raw_h = ((d[1] << 16) | (d[2] << 8) | d[3]) >> 4
        raw_t = ((d[3] & 0x0F) << 16) | (d[4] << 8) | d[5]
        return raw_t * 200.0 / 1048576.0 - 50.0, raw_h * 100.0 / 1048576.0


class BMP280:
    def __init__(self, i2c, addr):
        self.i2c, self.addr = i2c, addr
        self.calib = i2c.readfrom_mem(addr, 0x88, 24)
        i2c.writeto_mem(addr, 0xF4, b'\x57')
        i2c.writeto_mem(addr, 0xF5, b'\x90')
        time.sleep_ms(100)

    def _u16(self, i):
        return self.calib[i] | (self.calib[i + 1] << 8)

    def _s16(self, i):
        v = self._u16(i)
        return v - 0x10000 if v & 0x8000 else v

    def read(self):
        d = self.i2c.readfrom_mem(self.addr, 0xF7, 6)
        adc_p = (d[0] << 12) | (d[1] << 4) | (d[2] >> 4)
        adc_t = (d[3] << 12) | (d[4] << 4) | (d[5] >> 4)
        t1, t2, t3 = self._u16(0), self._s16(2), self._s16(4)
        p1 = self._u16(6)
        p = [self._s16(8 + 2 * i) for i in range(8)]
        v1 = (((adc_t >> 3) - (t1 << 1)) * t2) >> 11
        v2 = (((((adc_t >> 4) - t1) ** 2) >> 12) * t3) >> 14
        t_fine = v1 + v2
        temp = ((t_fine * 5 + 128) >> 8) / 100.0
        var1 = t_fine - 128000
        var2 = var1 * var1 * p[4] + ((var1 * p[3]) << 17) + (p[2] << 35)
        var1 = ((var1 * var1 * p[1]) >> 8) + ((var1 * p[0]) << 12)
        var1 = (((1 << 47) + var1) * p1) >> 33
        if var1 == 0:
            return temp, None
        pp = 1048576 - adc_p
        pp = (((pp << 31) - var2) * 3125) // var1
        v1 = (p[7] * (pp >> 13) * (pp >> 13)) >> 25
        v2 = (p[6] * pp) >> 19
        pp = ((pp + v1 + v2) >> 8) + (p[5] << 4)
        return temp, pp / 25600.0        # hPa


aht = AHT20(i2c)
bmp = BMP280(i2c, 0x76 if 0x76 in found else 0x77)

SEA_LEVEL = 1013.25      # 标准海平面气压，要更准就换成当地气象台的数值
LOG_SEC = 600            # 本地采样间隔（秒），600 = 10 分钟


# ================= 天气（由电脑下发） =================
WX = {"city": "--", "cond": "--", "hi": "--", "lo": "--",
      "uv": "--", "rain": "--", "rain2": "--"}


def handle_wx_line(line):
    """解析电脑下发的 WX,城市,天气,高,低,紫外线,降水,降水 行"""
    parts = line.split(",")
    if len(parts) != 8:
        return
    city, cond, hi, lo, uv, rain, rain2 = parts[1:]
    if city: WX["city"] = city
    if cond: WX["cond"] = cond
    if hi:   WX["hi"] = hi
    if lo:   WX["lo"] = lo
    if uv:   WX["uv"] = uv
    if rain:   WX["rain"] = rain
    if rain2:  WX["rain2"] = rain2
    draw_weather_rows()
    # 收到新天气后立刻补发一条 DATA2，电脑端数据库马上有完整记录
    try:
        t, h = aht.read()
        pt, pa = bmp.read()
        if t is not None and pa:
            alt = 44330 * (1 - (pa / SEA_LEVEL) ** 0.1903)
            print("DATA2,%.2f,%.2f,%.1f,%.0f,%s,%s,%s,%s,%s,%s,%s"
                  % (t, h, pa, alt, WX["city"], WX["cond"], WX["hi"],
                     WX["lo"], WX["uv"], WX["rain"], WX["rain2"]))
    except Exception as e:
        print("WXDATA: 补发读数失败 -", e)
    print("WXOK")            # 回执，电脑端据此知道下发成功


def poll_serial():
    """非阻塞读串口（板子同时是 REPL，读不到就返回）"""
    try:
        line = input()
        if line.startswith("WX,"):
            handle_wx_line(line)
    except (OSError, ValueError, EOFError):
        pass


# ================= 启动 =================
lcd_init()
blk(1)
fill(0x0000)
ctext("WEATHER", 6, 0x07FF, 2)
print("BOOT")

t0 = time.ticks_ms()
count = 0
next_log = 0


def draw_weather_rows():
    ctext(WX["city"], 104, 0xFD20, 2)
    ctext("%s %s/%sC" % (WX["cond"], WX["hi"], WX["lo"]), 126, 0x07E0, 2)
    ctext("UV " + WX["uv"], 148, 0xFFE0, 2)
    ctext("T-RAIN " + WX["rain"] + "mm", 170, 0x07FF, 2)
    ctext("M-RAIN " + WX["rain2"] + "mm", 192, 0x8410, 2)


while True:
    now = time.ticks_diff(time.ticks_ms(), t0) // 1000

    if now >= next_log:
        t, h = aht.read()
        pt, pa = bmp.read()
        if t is None or not pa:
            next_log = now + 30              # 读取失败，30 秒后重试
        else:
            alt = 44330 * (1 - (pa / SEA_LEVEL) ** 0.1903)
            count += 1
            next_log = now + LOG_SEC

            # 屏幕上半部分：本地温湿度（气压/海拔只上传，不再显示）
            ctext("T %.1fC" % t, 32, 0xFFE0, 3)
            ctext("H %.1f%%" % h, 64, 0x07FF, 3)
            fill_rect(20, 96, W - 40, 2, 0x39E7)     # 分隔线
            draw_weather_rows()

            # 串口输出（电脑端按这行解析）
            print("DATA2,%.2f,%.2f,%.1f,%.0f,%s,%s,%s,%s,%s,%s,%s"
                  % (t, h, pa, alt, WX["city"], WX["cond"], WX["hi"], WX["lo"],
                     WX["uv"], WX["rain"], WX["rain2"]))

    poll_serial()
    # 底部状态行：每秒刷新，用来确认程序还在运行
    ctext("#%d  %d:%02d" % (count, now // 60, now % 60), 220, 0x8410, 1)
    time.sleep_ms(200)
