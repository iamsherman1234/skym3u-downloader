#!/usr/bin/env python3
"""
SkyM3U Direct Downloader
Fetches download files directly from skym3u.dev by extracting backend parameters
and downloading files without waiting through client-side ad timers.
"""

import sys
import re
import argparse
import urllib.request
import urllib.parse
from pathlib import Path

DEFAULT_PAGE_URL = "https://www.skym3u.dev/p/dedicated.html?m=1"
DEFAULT_WORKER = "https://dedicated-ip.manikdish50.workers.dev/"
DEFAULT_TOKEN = "skym3u_pass_2026"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

def fetch_live_credentials(page_url: str):
    """
    Scrapes the webpage to extract the latest worker endpoint and token
    if they change in the future.
    """
    req = urllib.request.Request(page_url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            html = resp.read().decode("utf-8", errors="ignore")

        worker_match = re.search(r'workerEndpoint\s*=\s*["\']([^"\']+)["\']', html)
        token_match = re.search(r'secretPass\s*=\s*["\']([^"\']+)["\']', html)

        worker = worker_match.group(1) if worker_match else DEFAULT_WORKER
        token = token_match.group(1) if token_match else DEFAULT_TOKEN
        return worker, token
    except Exception as e:
        print(f"[!] Warning: Could not scrape live page ({e}). Using defaults.")
        return DEFAULT_WORKER, DEFAULT_TOKEN

def download_file(worker_url: str, token: str, file_type: str, output_path: str = None):
    """
    Downloads the specified file type (xtream or bdix).
    """
    params = urllib.parse.urlencode({
        "type": file_type,
        "step": "3",
        "token": token
    })
    full_url = f"{worker_url}?{params}"

    print(f"[*] Requesting '{file_type}' from {worker_url}...")
    req = urllib.request.Request(full_url, headers={"User-Agent": USER_AGENT})
    
    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            content = response.read()
            final_url = response.geturl()

            # Determine default output filename
            if not output_path:
                if file_type == "xtream":
                    output_path = "xtream_servers.txt"
                elif file_type == "bdix":
                    output_path = "dedicated_ip.m3u"
                else:
                    output_path = f"{file_type}_download.txt"

            path = Path(output_path)
            path.write_bytes(content)
            print(f"[+] Successfully saved {len(content)} bytes to '{output_path}'")
            return path
    except Exception as e:
        print(f"[-] Error downloading {file_type}: {e}", file=sys.stderr)
        return None

def main():
    parser = argparse.ArgumentParser(description="Download Xtream and BDIX lists directly from SkyM3U")
    parser.add_argument(
        "-t", "--type",
        choices=["xtream", "bdix", "all"],
        default="xtream",
        help="Type of file to download: xtream, bdix, or all (default: xtream)"
    )
    parser.add_argument(
        "-o", "--output",
        type=str,
        default=None,
        help="Custom output file path (only used if a single type is selected)"
    )
    parser.add_argument(
        "--url",
        type=str,
        default=DEFAULT_PAGE_URL,
        help="URL of the SkyM3U page to extract configuration from"
    )

    args = parser.parse_args()

    print("[*] Fetching configuration from source page...")
    worker, token = fetch_live_credentials(args.url)

    if args.type == "all":
        download_file(worker, token, "xtream", "xtream_servers.txt")
        download_file(worker, token, "bdix", "dedicated_ip.m3u")
    else:
        download_file(worker, token, args.type, args.output)

if __name__ == "__main__":
    main()
