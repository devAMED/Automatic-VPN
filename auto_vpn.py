#!/usr/bin/env python3

import argparse
import base64
import csv
import os
import random
import re
import shutil
import subprocess
import sys
import time
from io import StringIO
from pathlib import Path

try:
    import requests
except ModuleNotFoundError:
    requests = None


BASE_DIR = Path(__file__).resolve().parent
CONFIG_DIR = BASE_DIR / "configs"
AUTH_FILE = BASE_DIR / "auth.txt"
OPENVPN_LOG = CONFIG_DIR / "openvpn.log"
SWITCH_INTERVAL = 120
CONNECT_TIMEOUT = 45
VPNBOOK_PROTOCOL = "tcp443"
VPNGATE_LIMIT = 5

VPNBOOK_PAGE = "https://www.vpnbook.com/freevpn/openvpn"
VPNBOOK_API = "https://www.vpnbook.com/api/openvpn"
VPNGATE_API = "http://www.vpngate.net/api/iphone/"
IP_CHECK_URL = "https://api.ipify.org?format=json"
REQUEST_HEADERS = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) auto-vpn-basic/1.0"}
BANNER = r"""
    _    _   _ __  __ _____ ____
   / \  | | | |  \/  | ____|  _ \
  / _ \ | |_| | |\/| |  _| | | | |
 / ___ \|  _  | |  | | |___| |_| |
/_/   \_\_| |_|_|  |_|_____|____/
"""


def log(message):
    print(message, flush=True)


def print_banner():
    print(BANNER, flush=True)


def ensure_root():
    if os.geteuid() != 0:
        log("Please run this script with sudo.")
        sys.exit(1)


def ensure_openvpn():
    if shutil.which("openvpn") is None:
        log("openvpn command not found. Install it first.")
        sys.exit(1)


def ensure_requests_installed():
    if requests is None:
        log("Python package 'requests' is not installed.")
        log("Run: pip3 install -r requirements.txt")
        sys.exit(1)


def ensure_config_dir():
    CONFIG_DIR.mkdir(exist_ok=True)


def fetch_vpnbook_page():
    try:
        response = requests.get(VPNBOOK_PAGE, headers=REQUEST_HEADERS, timeout=20)
        response.raise_for_status()
    except requests.RequestException as error:
        log(f"VPNBook page error: {error}")
        return None

    return response.text


def save_auth_file(username, password):
    AUTH_FILE.write_text(f"{username}\n{password}\n", encoding="utf-8")
    os.chmod(AUTH_FILE, 0o600)


def read_saved_auth():
    if not AUTH_FILE.exists():
        return None

    try:
        lines = AUTH_FILE.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None

    if len(lines) >= 2 and lines[0].strip() and lines[1].strip():
        return lines[0].strip(), lines[1].strip()

    return None


def update_vpnbook_auth(page_text):
    log("Extracting VPNBook password")

    username_match = re.search(r">Username</label>.*?<code[^>]*>([^<]+)</code>", page_text, re.DOTALL)
    password_match = re.search(r">Password</label>.*?<code[^>]*>([^<]+)</code>", page_text, re.DOTALL)

    if username_match and password_match:
        username = username_match.group(1).strip()
        password = password_match.group(1).strip()
        if username and password:
            save_auth_file(username, password)
            log("Saved VPNBook credentials to auth.txt")
            return

    saved_auth = read_saved_auth()
    if saved_auth is not None:
        log("Could not extract the latest VPNBook password. Using saved auth.txt")
        return

    log("Could not extract VPNBook credentials from the public VPNBook page.")
    sys.exit(1)


def download_vpnbook_configs(page_text):
    log("Downloading configs from VPNBook")

    hostnames = sorted(set(re.findall(r"\b[a-z]{2}\d+\.vpnbook\.com\b", page_text)))
    if not hostnames:
        log("VPNBook download error: no servers found on the page")
        return 0

    saved = 0
    for hostname in hostnames:
        params = {"hostname": hostname, "protocol": VPNBOOK_PROTOCOL}
        try:
            config_response = requests.get(
                VPNBOOK_API,
                params=params,
                headers=REQUEST_HEADERS,
                timeout=30,
            )
            config_response.raise_for_status()
        except requests.RequestException as error:
            log(f"VPNBook config failed for {hostname}: {error}")
            continue

        if "client" not in config_response.text:
            log(f"VPNBook config failed for {hostname}: invalid config response")
            continue

        filename = f"vpnbook_{hostname.replace('.', '_')}_{VPNBOOK_PROTOCOL}.ovpn"
        path = CONFIG_DIR / filename
        path.write_text(config_response.text, encoding="utf-8")
        saved += 1

    log(f"Saved {saved} VPNBook config file(s)")
    return saved


def download_vpngate_configs():
    log("Downloading configs from VPNGate")

    try:
        response = requests.get(VPNGATE_API, headers=REQUEST_HEADERS, timeout=30)
        response.raise_for_status()
    except requests.RequestException as error:
        log(f"VPNGate download error: {error}")
        return 0

    lines = [line for line in response.text.splitlines() if line and not line.startswith("*")]
    if len(lines) < 2:
        log("VPNGate download error: invalid CSV data")
        return 0

    header = next(csv.reader([lines[0]]))
    header[0] = header[0].lstrip("#")
    reader = csv.DictReader(StringIO("\n".join(lines[1:])), fieldnames=header)

    rows = list(reader)
    rows.sort(key=lambda row: safe_int(row.get("Score", "0")), reverse=True)

    saved = 0
    for row in rows[:VPNGATE_LIMIT]:
        hostname = row.get("HostName", "").strip()
        encoded_config = row.get("OpenVPN_ConfigData_Base64", "").strip()
        if not hostname or not encoded_config:
            continue

        try:
            config_text = base64.b64decode(encoded_config).decode("utf-8", errors="ignore")
        except (ValueError, OSError):
            continue

        filename = f"vpngate_{hostname.replace('.', '_')}.ovpn"
        path = CONFIG_DIR / filename
        path.write_text(config_text, encoding="utf-8")
        saved += 1

    log(f"Saved {saved} VPNGate config file(s)")
    return saved


def get_config_files():
    configs = sorted(CONFIG_DIR.glob("*.ovpn"))
    random.shuffle(configs)
    return configs


def safe_int(value):
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def wait_for_connection(log_path, process):
    start_time = time.time()
    success_text = "Initialization Sequence Completed"
    failure_texts = [
        "AUTH_FAILED",
        "TLS Error",
        "Cannot resolve host address",
        "Exiting due to fatal error",
        "Connection reset",
    ]

    while time.time() - start_time < CONNECT_TIMEOUT:
        if process.poll() is not None:
            return False

        if log_path.exists():
            try:
                content = log_path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                content = ""

            if success_text in content:
                return True

            if any(text in content for text in failure_texts):
                return False

        time.sleep(2)

    return False


def stop_openvpn(process):
    if process is None or process.poll() is not None:
        return

    process.terminate()
    try:
        process.wait(timeout=15)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def connect_with_config(config_path):
    log(f"Connecting to server: {config_path.name}")

    if OPENVPN_LOG.exists():
        try:
            OPENVPN_LOG.unlink()
        except OSError:
            pass

    command = [
        "openvpn",
        "--config",
        str(config_path),
        "--log-append",
        str(OPENVPN_LOG),
    ]

    if config_path.name.startswith("vpnbook_"):
        command.extend(["--auth-user-pass", str(AUTH_FILE)])

    try:
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError as error:
        log(f"OpenVPN start failed: {error}")
        return None

    if wait_for_connection(OPENVPN_LOG, process):
        return process

    stop_openvpn(process)
    log(f"Connection failed: {config_path.name}")
    return None


def get_public_ip():
    try:
        response = requests.get(IP_CHECK_URL, headers=REQUEST_HEADERS, timeout=10)
        response.raise_for_status()
        data = response.json()
    except requests.RequestException as error:
        log(f"IP check failed: {error}")
        return None

    return data.get("ip")


def keep_connection_alive(process):
    end_time = time.time() + SWITCH_INTERVAL
    while time.time() < end_time:
        if process.poll() is not None:
            log("VPN process stopped unexpectedly")
            return
        time.sleep(5)


def start_rotation(use_vpngate):
    ensure_config_dir()
    ensure_openvpn()
    ensure_requests_installed()

    vpnbook_page = fetch_vpnbook_page()
    if vpnbook_page is None:
        if read_saved_auth() is None:
            sys.exit(1)
        log("Using saved auth.txt because VPNBook page could not be loaded")
    else:
        update_vpnbook_auth(vpnbook_page)
        download_vpnbook_configs(vpnbook_page)

    if use_vpngate:
        download_vpngate_configs()

    configs = get_config_files()
    if not configs:
        log("No .ovpn files found in configs/")
        sys.exit(1)

    current_process = None
    index = 0

    try:
        while True:
            config_path = configs[index % len(configs)]
            current_process = connect_with_config(config_path)
            if current_process is None:
                index += 1
                time.sleep(3)
                continue

            log("VPN connected")
            current_ip = get_public_ip()
            if current_ip:
                log(f"Current IP: {current_ip}")
            else:
                log("Current IP: unavailable")

            keep_connection_alive(current_process)
            log("Switching server")
            stop_openvpn(current_process)
            current_process = None
            index += 1
    except KeyboardInterrupt:
        log("Stopping VPN")
        stop_openvpn(current_process)


def parse_args():
    parser = argparse.ArgumentParser(description="Basic automatic OpenVPN rotator")
    parser.add_argument("--start", action="store_true", help="Start automatic VPN switching")
    parser.add_argument(
        "--use-vpngate",
        action="store_true",
        help="Also download and use VPNGate configs",
    )
    return parser.parse_args()


def main():
    print_banner()
    args = parse_args()
    if not args.start:
        log("Use: sudo python3 auto_vpn.py --start")
        return

    ensure_root()
    start_rotation(args.use_vpngate)


if __name__ == "__main__":
    main()
