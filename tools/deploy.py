"""一键部署：把固件推到开发板并复位。

用法：
    python tools/deploy.py            # 自动找串口
    python tools/deploy.py COM5       # 指定串口

板子不联网，天气密钥只留在电脑端（firmware/secrets.py 供 pc/collector.py 读取），
因此本脚本不把 secrets.py 复制到板子上。
"""

import os
import subprocess
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def run(args):
    print(">", " ".join(args))
    subprocess.run([sys.executable, "-m", "mpremote"] + args, check=True)


def run_quiet(args):
    """失败也不中断（例如板子上本来就没有该文件）。"""
    subprocess.run([sys.executable, "-m", "mpremote"] + args,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def main():
    port = sys.argv[1] if len(sys.argv) > 1 else "auto"
    conn = ["connect", port] if port != "auto" else []

    main_py = os.path.join(BASE, "firmware", "main.py")

    run(conn + ["cp", main_py, ":main.py"])
    # 板子不需要密钥：清掉早期版本可能留在板上的 secrets.py
    run_quiet(conn + ["rm", ":secrets.py"])

    run(conn + ["reset"])
    print("\n部署完成，开发板已复位。天气密钥只在电脑端使用。")


if __name__ == "__main__":
    main()
