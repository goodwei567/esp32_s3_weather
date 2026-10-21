"""临时工具：打开串口读若干秒，检查板子输出。

    python tools/read_serial.py COM5 8          # 只读
    python tools/read_serial.py COM5 12 --reset # 先用 RTS 复位板子再读
"""

import sys
import time

import serial

port = sys.argv[1] if len(sys.argv) > 1 else "COM5"
seconds = float(sys.argv[2]) if len(sys.argv) > 2 else 8.0
do_reset = "--reset" in sys.argv

ser = serial.Serial(port, 115200, timeout=0.5)
if do_reset:
    # DTR=False 让 IO0 保持高电平（正常启动），RTS 拉低 EN 复位后释放
    ser.dtr = False
    ser.rts = True
    time.sleep(0.15)
    ser.rts = False
    time.sleep(0.1)
    ser.reset_input_buffer()
deadline = time.time() + seconds
lines = []
while time.time() < deadline:
    raw = ser.readline()
    if raw:
        lines.append(raw.decode("utf-8", "ignore").strip())
ser.close()

print("--- %d lines in %.1fs ---" % (len(lines), seconds))
for line in lines:
    print(line)
