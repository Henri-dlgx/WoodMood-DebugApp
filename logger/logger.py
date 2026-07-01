#!/usr/bin/env python3
"""WoodMood MQTT history logger + CSV export HTTP endpoint.

Runs forever: subscribes to <prefix>/status/full and appends one row per
sample interval to a daily CSV file under WM_DATA_DIR. No database — just
flat files on disk, pruned after WM_RETENTION_DAYS.

Also serves GET /export?hours=N&key=<WM_EXPORT_KEY> over HTTP so you can
pull "last N hours" as a CSV download without SSH'ing into the machine.
Omit hours (or pass 0) to get everything currently retained.

Configure via environment variables (see .env.example):
  MQTT_BROKER, MQTT_PORT (default 8883), MQTT_USER, MQTT_PASS, MQTT_SERIAL
  WM_DATA_DIR (default ./data), WM_SAMPLE_INTERVAL_SEC (default 15)
  WM_RETENTION_DAYS (default 30), WM_EXPORT_PORT (default 8090)
  WM_EXPORT_KEY (optional but recommended if the port is internet-reachable)
"""
import csv
import glob
import io
import json
import os
import ssl
import threading
import time
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import paho.mqtt.client as mqtt

BROKER = os.environ["MQTT_BROKER"]
MQTT_PORT = int(os.environ.get("MQTT_PORT", "8883"))
USER = os.environ["MQTT_USER"]
PASS = os.environ["MQTT_PASS"]
SERIAL = os.environ["MQTT_SERIAL"]
PREFIX = f"WoodMood{SERIAL}"

DATA_DIR = os.environ.get("WM_DATA_DIR", os.path.join(os.path.dirname(os.path.abspath(__file__)), "data"))
SAMPLE_INTERVAL_SEC = int(os.environ.get("WM_SAMPLE_INTERVAL_SEC", "15"))
RETENTION_DAYS = int(os.environ.get("WM_RETENTION_DAYS", "30"))
EXPORT_PORT = int(os.environ.get("WM_EXPORT_PORT", "8090"))
EXPORT_KEY = os.environ.get("WM_EXPORT_KEY", "")

os.makedirs(DATA_DIR, exist_ok=True)
_last_written = 0.0


# ============================================================
# CSV WRITING
# ============================================================
def _csv_path_for(dt):
    return os.path.join(DATA_DIR, f"history_{dt.strftime('%Y-%m-%d')}.csv")


def _prune_old_files():
    cutoff = time.time() - RETENTION_DAYS * 86400
    for name in os.listdir(DATA_DIR):
        path = os.path.join(DATA_DIR, name)
        if name.startswith("history_") and name.endswith(".csv") and os.path.getmtime(path) < cutoff:
            os.remove(path)


def _append_row(payload):
    now = datetime.now(timezone.utc)
    path = _csv_path_for(now)
    file_exists = os.path.exists(path)
    if file_exists:
        # Reuse the column order already committed to today's file so rows
        # stay aligned even if the firmware's field set changes mid-day.
        with open(path, newline="") as f:
            keys = next(csv.reader(f))[1:]
    else:
        keys = sorted(payload.keys())
        _prune_old_files()
    with open(path, "a", newline="") as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(["timestamp"] + keys)
        writer.writerow([now.isoformat()] + [payload.get(k, "") for k in keys])


# ============================================================
# MQTT
# ============================================================
def on_connect(client, userdata, flags, rc):
    print(f"[mqtt] connected rc={rc}")
    client.subscribe(f"{PREFIX}/status/full", qos=1)
    client.subscribe(f"{PREFIX}/status/heartbeat", qos=1)


def on_message(client, userdata, msg):
    global _last_written
    if msg.topic.endswith("/status/heartbeat"):
        # Acts as a valid heartbeat client so running this logger doesn't
        # itself cause DIAG_HEARTBEAT_UNHEALTHY / suspend auto-insertion.
        client.publish(f"{PREFIX}/cmd/heartbeat_ack", "1")
        return
    if not msg.topic.endswith("/status/full"):
        return
    now = time.time()
    if now - _last_written < SAMPLE_INTERVAL_SEC:
        return
    try:
        payload = json.loads(msg.payload)
    except json.JSONDecodeError:
        return
    _append_row(payload)
    _last_written = now


def run_mqtt():
    client = mqtt.Client(client_id=f"woodmood-logger-{SERIAL}-{os.getpid()}")
    client.username_pw_set(USER, PASS)
    client.tls_set(cert_reqs=ssl.CERT_REQUIRED)
    client.on_connect = on_connect
    client.on_message = on_message
    client.reconnect_delay_set(min_delay=1, max_delay=30)
    while True:
        try:
            client.connect(BROKER, MQTT_PORT, keepalive=60)
            client.loop_forever()
        except Exception as e:
            print(f"[mqtt] connection error: {e} — retrying in 5s")
            time.sleep(5)


# ============================================================
# HTTP EXPORT ENDPOINT
# ============================================================
def build_csv(hours):
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours) if hours else None
    files = sorted(glob.glob(os.path.join(DATA_DIR, "history_*.csv")))
    out = io.StringIO()
    writer = None
    for path in files:
        with open(path, newline="") as f:
            reader = csv.reader(f)
            file_header = next(reader, None)
            if file_header is None:
                continue
            if writer is None:
                writer = csv.writer(out)
                writer.writerow(file_header)
            for row in reader:
                if not row:
                    continue
                if cutoff is not None:
                    try:
                        if datetime.fromisoformat(row[0]) < cutoff:
                            continue
                    except ValueError:
                        continue
                writer.writerow(row)
    return out.getvalue()


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path != "/export":
            self.send_response(404)
            self.end_headers()
            return
        qs = parse_qs(parsed.query)
        if EXPORT_KEY and qs.get("key", [None])[0] != EXPORT_KEY:
            self.send_response(403)
            self.end_headers()
            self.wfile.write(b"Forbidden")
            return
        hours_param = qs.get("hours", [None])[0]
        try:
            hours = float(hours_param) if hours_param else None
        except ValueError:
            hours = None
        body = build_csv(hours).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/csv")
        self.send_header("Content-Disposition", 'attachment; filename="woodmood_export.csv"')
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        pass  # keep the systemd journal quiet — MQTT logs are enough


def run_http():
    server = ThreadingHTTPServer(("0.0.0.0", EXPORT_PORT), Handler)
    print(f"[export] listening on :{EXPORT_PORT} — GET /export?hours=6")
    server.serve_forever()


def main():
    threading.Thread(target=run_http, daemon=True).start()
    run_mqtt()


if __name__ == "__main__":
    main()
