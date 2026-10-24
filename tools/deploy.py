"""一键部署：把固件和 secrets.py 推到开发板并复位。

用法：
    python tools/deploy.py            # 自动找串口
    python tools/deploy.py COM5       # 指定串口

首次使用前，先把 firmware/secrets_example.py 复制为 firmware/secrets.py 并填好配置。
"""

import os
import subprocess
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def run(args):
    print(">", " ".join(args))
    subprocess.run([sys.executable, "-m", "mpremote"] + args, check=True)


def main():
    port = sys.argv[1] if len(sys.argv) > 1 else "auto"
    conn = ["connect", port] if port != "auto" else []

    main_py = os.path.join(BASE, "firmware", "main.py")
    secrets_py = os.path.join(BASE, "firmware", "secrets.py")

    run(conn + ["cp", main_py, ":main.py"])
    if os.path.exists(secrets_py):
        run(conn + ["cp", secrets_py, ":secrets.py"])
    else:
        print("\n[提示] 没找到 firmware/secrets.py，已只部署 main.py（无天气功能）。")
        print("       复制 secrets_example.py 为 secrets.py、填好 Wi-Fi 和心知 key，")
        print("       再跑一次本脚本即可。")
    run(conn + ["reset"])
    print("\n部署完成，开发板已复位。")


if __name__ == "__main__":
    main()
