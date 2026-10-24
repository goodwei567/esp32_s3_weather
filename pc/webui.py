"""本地网页：用 Flask 把 SQLite 里的数据画成曲线。"""

import csv
import io
import os
import sqlite3
import time

from flask import Flask, Response, jsonify, render_template, request

from collector import DB_PATH

MAX_POINTS = 1500        # 单个图表最多返回的采样点数


def _connect(db_path):
    conn = sqlite3.connect(db_path, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def _columns(conn, table):
    return {r[1] for r in conn.execute("PRAGMA table_info(%s)" % table)}


def create_app(collector, db_path=DB_PATH):
    app = Flask(__name__, template_folder="templates", static_folder="static")
    app.config["JSON_AS_ASCII"] = False

    @app.route("/")
    def index():
        return render_template("index.html")

    @app.route("/api/status")
    def api_status():
        st = collector.status()
        st["now"] = time.time()
        st["db"] = db_path
        try:
            conn = _connect(db_path)
            row = conn.execute(
                "SELECT COUNT(*) AS n, MIN(epoch) AS first, MAX(epoch) AS last FROM readings"
            ).fetchone()
            # 首次启动时 collector 会补新列，这里兜底处理尚未迁移的旧库
            available = _columns(conn, "readings")
            wx_cols = [c for c in ("epoch", "city", "cond", "hi", "lo", "uv",
                                   "cond2", "hi2", "lo2", "rain", "rain2",
                                   "out_h") if c in available]
            wx = (conn.execute(
                "SELECT %s FROM readings WHERE city IS NOT NULL "
                "ORDER BY epoch DESC LIMIT 1" % ", ".join(wx_cols)
            ).fetchone() if wx_cols else None)
            conn.close()
            st["total"] = row["n"]
            st["first_epoch"] = row["first"]
            st["wx"] = dict(wx) if wx else None
        except Exception as exc:
            st["total"] = 0
            st["first_epoch"] = None
            st["last_error"] = str(exc)
        return jsonify(st)

    @app.route("/api/data")
    def api_data():
        try:
            hours = float(request.args.get("hours", 24))
        except ValueError:
            hours = 24.0
        since = time.time() - hours * 3600

        conn = _connect(db_path)
        rows = conn.execute(
            "SELECT epoch, t, h, p, alt, rain, rain2 FROM readings "
            "WHERE epoch >= ? ORDER BY epoch", (since,)).fetchall()
        conn.close()

        total = len(rows)
        if total > MAX_POINTS:
            step = total // MAX_POINTS + 1
            rows = rows[::step]

        return jsonify({
            "hours": hours,
            "count": total,
            "points": len(rows),
            "rows": [[r["epoch"], r["t"], r["h"], r["p"], r["alt"]]
                     for r in rows],
        })

    @app.route("/api/export.csv")
    def api_export():
        hours = float(request.args.get("hours", 24 * 30))
        since = time.time() - hours * 3600
        conn = _connect(db_path)
        available = _columns(conn, "readings")
        csv_cols = ("epoch", "ts", "t", "h", "p", "alt", "city", "cond", "hi",
                    "lo", "uv", "cond2", "hi2", "lo2", "rain", "rain2", "out_h")
        select_cols = ", ".join(c for c in csv_cols if c in available)
        rows = conn.execute(
            "SELECT %s FROM readings WHERE epoch >= ? ORDER BY epoch"
            % select_cols, (since,)).fetchall()
        conn.close()

        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(["epoch", "time", "temperature_c", "humidity_pct",
                         "pressure_hpa", "altitude_m", "city", "condition",
                         "high_c", "low_c", "uv", "tomorrow_condition",
                         "tomorrow_high_c", "tomorrow_low_c", "rain_mm",
                         "rain_tomorrow_mm", "outdoor_humidity_pct"])
        for r in rows:
            writer.writerow([r["epoch"], r["ts"], r["t"], r["h"], r["p"],
                             r["alt"], r["city"], r["cond"], r["hi"],
                             r["lo"], r["uv"], r["cond2"],
                             r["hi2"] if "hi2" in r.keys() else "",
                             r["lo2"] if "lo2" in r.keys() else "",
                             r["rain"] if "rain" in r.keys() else "",
                             r["rain2"] if "rain2" in r.keys() else "",
                             r["out_h"] if "out_h" in r.keys() else ""])
        data = "\ufeff" + buf.getvalue()          # BOM 让 Excel 正确识别中文/UTF-8
        return Response(
            data, mimetype="text/csv",
            headers={"Content-Disposition": "attachment; filename=weather.csv"})

    return app
