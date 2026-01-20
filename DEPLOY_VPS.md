# 🚀 Deployment Guide: Solana Bot on VPS

This guide walks you through deploying the Antigravity Bot and Dashboard to a Linux VPS (e.g., Ubuntu 22.04/24.04).

## 1. Prerequisites

- A VPS with **Ubuntu 22.04+**.
- **Python 3.10** or higher installed.
- Root or sudo access.

## 2. Prepare Local Files

1.  **Update Requirements**: Ensure `requirements.txt` has everything. (We have updated it for you).
2.  **Zip the Project**: Compress the entire `solana` folder into `solana_bot.zip`.

## 3. Transfer to VPS

Use `scp` (Secure Copy) from your local terminal. Since you are using Vultr, the default user is usually `root`.

```powershell
# Replace your-vps-ip with your Vultr IP address
scp -r solana_bot.zip root@your-vps-ip:~/
```

## 4. VPS Setup (Run on Server)

SSH into your VPS:
```bash
ssh root@your-vps-ip
```

> **Note for Vultr Users**: If you cannot access port 8000 later, check the "Firewall" section in your Vultr Dashboard. You may need to add a rule to allow TCP port 8000, or disable the Vultr firewall and rely on `ufw` inside Ubuntu.

### Install System Dependencies
```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y python3 python3-pip python3-venv unzip tmux
```

### Setup Project Directory
```bash
# Extract files
unzip solana_bot.zip -d solana
cd solana

# Create Virtual Environment
python3 -m venv venv
source venv/bin/activate

# Install Python Dependencies
pip install -r requirements.txt
```

### Configure Environment
```bash
cp .env.example .env
nano .env
# Paste your keys and settings here (RPC URLs, etc.)
# Ctrl+X, Y, Enter to save.
```

## 5. Running with Systemd (Recommended for 24/7)

We will create two services: one for the bot and one for the dashboard.

### Service 1: The Trading Bot

Create the file:
```bash
sudo nano /etc/systemd/system/solana-bot.service
```

Paste this content (adjust paths/user):
```ini
[Unit]
Description=Antigravity Solana Bot
After=network.target

[Service]
User=root
WorkingDirectory=/root/solana
ExecStart=/root/solana/venv/bin/python -m solana_bot.main
Restart=always
RestartSec=10
EnvironmentFile=/root/solana/.env

[Install]
WantedBy=multi-user.target
```

### Service 2: The Dashboard

Create the file:
```bash
sudo nano /etc/systemd/system/solana-dashboard.service
```

Paste this content:
```ini
[Unit]
Description=Antigravity Dashboard
After=network.target

[Service]
User=root
WorkingDirectory=/root/solana
ExecStart=/root/solana/venv/bin/python -m uvicorn dashboard.app:app --host 0.0.0.0 --port 8000
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

### Start Services

```bash
# Reload systemd
sudo systemctl daemon-reload

# Enable (autostart on boot) and Start
sudo systemctl enable --now solana-bot
sudo systemctl enable --now solana-dashboard

# Check Status
sudo systemctl status solana-bot
sudo systemctl status solana-dashboard
```

## 6. Accessing the Dashboard

Open your browser and navigate to:
```
http://<YOUR_VPS_IP>:8000
```
*(Make sure port 8000 is allowed in your VPS firewall / Security Group)*.

## 7. Viewing Logs

To see what the bot is doing:

```bash
# Follow bot logs
journalctl -u solana-bot -f

# Follow dashboard logs
journalctl -u solana-dashboard -f
```

## 8. Managing the Bot

- **Stop**: `sudo systemctl stop solana-bot`
- **Restart**: `sudo systemctl restart solana-bot`

---

### Alternative: Using tmux (Simpler, Manual)

If you don't want to use systemd, use `tmux`:

```bash
tmux new -s bot
source venv/bin/activate
python -m solana_bot.main
# Press Ctrl+B, then D to detach
```

```bash
tmux new -s dash
source venv/bin/activate
python -m uvicorn dashboard.app:app --host 0.0.0.0 --port 8000
# Press Ctrl+B, then D to detach
```
