# WoodMood always-on logger

Runs 24/7 on a small always-free cloud VM and logs `status/full` to daily
CSV files — no database, no browser tab needed. Also exposes
`GET /export?hours=N` so you can pull "last N hours" as a CSV download
from anywhere.

## 1. Create a free Oracle Cloud VM

1. Sign up at [oracle.com/cloud/free](https://www.oracle.com/cloud/free/) (a
   card is required for identity verification, but the "Always Free"
   resources below never get charged as long as you stay within them).
2. Create a Compute instance:
   - Shape: **Always Free eligible** — either `VM.Standard.E2.1.Micro` (AMD)
     or an `Ampere A1` flex shape with 1 OCPU / 6 GB (both free forever).
   - Image: **Ubuntu** (22.04 or newer).
   - When creating it, download the SSH key pair it offers you.
3. Open the port for the export endpoint:
   - In the instance's **Virtual Cloud Network → Security List** (or
     Network Security Group), add an **Ingress Rule**: TCP, destination
     port `8090`, source `0.0.0.0/0` (or your home IP only, if it's static).
   - Ubuntu's Oracle images also run their own firewall on top of that —
     you still need to open it on the VM itself (step 4 below), or the
     Security List rule alone won't be enough.

## 2. SSH in and install dependencies

```bash
ssh -i /path/to/downloaded/key.pem ubuntu@<vm-public-ip>
sudo apt update && sudo apt install -y python3-pip
pip3 install paho-mqtt

# Open the port in the VM's own firewall (the Oracle image ships with
# iptables rules that block inbound traffic by default):
sudo iptables -I INPUT -p tcp --dport 8090 -j ACCEPT
sudo netfilter-persistent save   # if not installed: sudo apt install -y iptables-persistent
```

## 3. Deploy the logger

```bash
sudo mkdir -p /opt/woodmood-logger
sudo chown ubuntu:ubuntu /opt/woodmood-logger
```

Copy `logger.py` and `.env.example` into `/opt/woodmood-logger/` (scp, git
clone, or paste via a heredoc — whatever's easiest). Then:

```bash
cd /opt/woodmood-logger
cp .env.example .env
nano .env   # fill in MQTT_BROKER / MQTT_USER / MQTT_PASS / MQTT_SERIAL
            # and set WM_EXPORT_KEY to something random, since the port is public
```

Install the systemd service:

```bash
sudo cp woodmood-logger.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now woodmood-logger
sudo systemctl status woodmood-logger      # should say "active (running)"
sudo journalctl -u woodmood-logger -f      # watch it connect + start logging
```

## 4. Download data

From any browser or `curl`, anywhere:

```
http://<vm-public-ip>:8090/export?hours=6&key=<your WM_EXPORT_KEY>
```

- `hours=6` → last 6 hours. Omit `hours` entirely for everything currently
  retained (up to `WM_RETENTION_DAYS`, default 30 days).
- Opening that URL in a browser downloads a CSV directly.

## Notes

- Data lives at `/opt/woodmood-logger/data/history_YYYY-MM-DD.csv` — one
  file per day, auto-pruned after `WM_RETENTION_DAYS`.
- Sample interval defaults to 15s (`WM_SAMPLE_INTERVAL_SEC`), matching the
  browser debug app's own recording rate.
- The logger ACKs `status/heartbeat` like the other apps do, so leaving it
  running doesn't itself trigger `DIAG_HEARTBEAT_UNHEALTHY`.
- `.env` and `data/` are git-ignored — never commit real credentials or
  logged data to this repo.
