#!/usr/bin/env bash
set -e

echo "=================================================================="
echo "🎬 Setting up Samkok 1080p Pipeline for Google Colab..."
echo "=================================================================="

# 1. Disconnect WARP proxy if active to prevent Cloudflare loopback blocks
if command -v warp-cli &> /dev/null; then
    warp-cli disconnect 2>/dev/null || true
fi

# 2. Install python dependencies
echo "[+] Installing Python dependencies (curl_cffi)..."
pip install -q curl_cffi

# 3. Determine script directory
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"
if [ ! -f "$SCRIPT_DIR/colab_samkok_muxer.py" ]; then
    SCRIPT_DIR="/content/skym3u-downloader"
fi

# 4. Run colab_samkok_muxer.py with passed arguments
echo "[+] Running Samkok Muxer Pipeline..."
python3 "$SCRIPT_DIR/colab_samkok_muxer.py" "$@"
