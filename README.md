# Automatic VPN

Very basic Python tool for Kali Linux that connects to free OpenVPN servers and switches to another server every 2 minutes.

It uses:

- VPNBook first
- Optional VPNGate support
- The Linux `openvpn` command

## Files

- `auto_vpn.py` - main script
- `configs/` - downloaded `.ovpn` files
- `auth.txt` - saved VPNBook username and password for OpenVPN

## Install on Kali Linux

```bash
sudo apt update
sudo apt install openvpn python3-pip -y
pip3 install -r requirements.txt
```

## Usage

Run the basic version:

```bash
sudo python3 auto_vpn.py --start
```

Run with optional VPNGate support:

```bash
sudo python3 auto_vpn.py --start --use-vpngate
```

## Notes

- The script downloads VPNBook configs from the public VPNBook OpenVPN page and stores them in `configs/`.
- The script automatically extracts the current public VPNBook username and password from the official VPNBook OpenVPN page and saves them in `auth.txt`.
- If VPNBook is temporarily unavailable, the script can reuse the saved `auth.txt` as a fallback.
- After each successful connection, the script checks the public IP with `https://api.ipify.org`.
- Before switching servers, the script safely stops the old `openvpn` process.
- If one server fails, the script moves to the next config file.

## Legal note

Use this only on networks and systems where you are allowed to test it. The script does not try to bypass captcha, login protection, or website restrictions.
