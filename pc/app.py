"""一键启动：后台读串口 + 前台跑网页。

    python app.py                 # 正常模式，自动找开发板串口
    python app.py --port COM5     # 指定串口
    python app.py --simulate      # 没有硬件，用模拟数据看网页效果
"""

import argparse
import os
import sys
import threading
import time

from collector import Collector, SimCollector, init_db, DB_PATH
from webui import create_app


def main():
    # 让日志即时写入（静默启动时会重定向到 monitor.log）
    try:
        sys.stdout.reconfigure(line_buffering=True)
        sys.stderr.reconfigure(line_buffering=True)
    except Exception:
        pass

    ap = argparse.ArgumentParser(description="ESP32-S3 气象站本地监控")
    ap.add_argument("--port", help="串口号，例如 COM5；不填则自动识别")
    ap.add_argument("--host", default="127.0.0.1",
                    help="监听地址，改成 0.0.0.0 可让同网段设备访问")
    ap.add_argument("--web-port", type=int, default=8000)
    ap.add_argument("--simulate", action="store_true", help="用模拟数据测试网页")
    ap.add_argument("--no-reset-on-open", action="store_true",
                    help="打开串口时不复位开发板（默认会复位一次，立刻拿到首个数据点）")
    args = ap.parse_args()

    init_db()

    if args.simulate:
        collector = SimCollector()
    else:
        collector = Collector(port=args.port,
                              reset_on_open=not args.no_reset_on_open)
        ports = Collector.list_ports_text()
        print("[采集] 可用串口：%s" % (", ".join(ports) if ports else "无"))

    thread = threading.Thread(target=collector.run, daemon=True)
    thread.start()

    app = create_app(collector)
    url = "http://%s:%d" % ("localhost" if args.host == "127.0.0.1" else args.host,
                            args.web_port)
    print("=" * 52)
    print(" 网页已启动：%s" % url)
    print(" 数据库文件：%s" % DB_PATH)
    print(" 按 Ctrl+C 退出")
    print("=" * 52)
    try:
        app.run(host=args.host, port=args.web_port, threaded=True,
                debug=False, use_reloader=False)
    except KeyboardInterrupt:
        pass
    finally:
        collector.stop()
        time.sleep(0.3)


if __name__ == "__main__":
    main()
