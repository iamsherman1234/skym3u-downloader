#!/usr/bin/env bash
set -e

echo "=================================================================="
echo "🛡️ Setting up Cloudflare WARP & Dependencies for Google Colab..."
echo "=================================================================="

# 1. Install Cloudflare WARP keyring and repo if not already installed
if ! command -v warp-cli &> /dev/null; then
    echo "[+] Installing Cloudflare WARP..."
    curl -fsSL https://pkg.cloudflareclient.com/pubkey.gpg | gpg --yes --dearmor -o /usr/share/keyrings/cloudflare-warp-archive-keyring.gpg
    echo "deb [arch=amd64 signed-by=/usr/share/keyrings/cloudflare-warp-archive-keyring.gpg] https://pkg.cloudflareclient.com/ $(lsb_release -cs) main" | tee /etc/apt/sources.list.d/cloudflare-client.list >/dev/null
    apt-get update -qq
    apt-get install -y cloudflare-warp >/dev/null 2>&1
fi

# 2. Start warp-svc background daemon if not running
if ! pgrep -x "warp-svc" > /dev/null; then
    echo "[+] Starting Cloudflare WARP service..."
    nohup warp-svc >/dev/null 2>&1 &
    sleep 3
fi

# 3. Configure WARP in SOCKS5 proxy mode
echo "[+] Configuring WARP in proxy mode..."
warp-cli --accept-tos registration new 2>/dev/null || warp-cli --accept-tos register 2>/dev/null || true
warp-cli --accept-tos mode proxy 2>/dev/null || warp-cli --accept-tos set-mode proxy 2>/dev/null || true
warp-cli --accept-tos connect 2>/dev/null || true
sleep 2

# 4. Install python dependencies
echo "[+] Installing Python dependencies..."
pip install -q curl_cffi

# 5. Determine script directory
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"
if [ ! -f "$SCRIPT_DIR/colab_samkok_muxer.py" ]; then
    SCRIPT_DIR="/content/skym3u-downloader"
fi

# 6. Run colab_samkok_muxer.py with passed arguments
echo "[+] Running Samkok Muxer Pipeline..."
python3 "$SCRIPT_DIR/colab_samkok_muxer.py" "$@"
