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
            wx = conn.execute(
                "SELECT epoch, city, cond, hi, lo, uv, cond2 FROM readings "
                "WHERE city IS NOT NULL ORDER BY epoch DESC LIMIT 1").fetchone()
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
        rows = conn.execute(
            "SELECT epoch, ts, t, h, p, alt, city, cond, hi, lo, uv, cond2 "
            "FROM readings "
            "WHERE epoch >= ? ORDER BY epoch", (since,)).fetchall()
        conn.close()

        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(["epoch", "time", "temperature_c", "humidity_pct",
                         "pressure_hpa", "altitude_m", "city", "condition",
                         "high_c", "low_c", "uv", "tomorrow_condition"])
        for r in rows:
            writer.writerow([r["epoch"], r["ts"], r["t"], r["h"], r["p"],
                             r["alt"], r["city"], r["cond"], r["hi"],
                             r["lo"], r["uv"], r["cond2"]])
        data = "\ufeff" + buf.getvalue()          # BOM 让 Excel 正确识别中文/UTF-8
        return Response(
            data, mimetype="text/csv",
            headers={"Content-Disposition": "attachment; filename=weather.csv"})

    return app
