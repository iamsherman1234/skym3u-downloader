#!/usr/bin/env python3
"""
All-in-One SkyM3U Pipeline
Downloads, validates, and outputs active Xtream servers in one automated step.
"""

import sys
import json
import argparse
import datetime
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

from skym3u_downloader import fetch_live_credentials, DEFAULT_PAGE_URL, DEFAULT_WORKER, DEFAULT_TOKEN, USER_AGENT
from xtream_tester import parse_xtream_file, test_single_server, format_timestamp
import urllib.request
import urllib.parse
import re

def fetch_xtream_content(page_url: str = DEFAULT_PAGE_URL, quiet: bool = False) -> str:
    """Fetches the latest Xtream server list text directly without ads."""
    if not quiet:
        print("[1/3] 🌐 Fetching latest configuration from SkyM3U...")
    worker, token = fetch_live_credentials(page_url)
    
    if not quiet:
        print(f"[2/3] 📥 Fetching Xtream list from worker backend...")
    
    params = urllib.parse.urlencode({
        "type": "xtream",
        "step": "3",
        "token": token
    })
    full_url = f"{worker}?{params}"
    
    req = urllib.request.Request(full_url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=15) as resp:
        return resp.read().decode("utf-8", errors="ignore")

def parse_servers_from_text(content: str):
    """Extracts server entries from raw text."""
    pattern = re.compile(
        r"Server:\s*(?P<server>https?://[^\s\r\n]+)"
        r"(?:[\s\r\n]+ID:\s*(?P<id>[^\r\n]+))?"
        r"(?:[\s\r\n]+CODE:\s*(?P<code>[^\r\n]+))?",
        re.IGNORECASE
    )
    servers = []
    for match in pattern.finditer(content):
        server = match.group("server").strip()
        user_id = (match.group("id") or "").strip()
        code = (match.group("code") or "").strip()
        if server and user_id and code:
            servers.append({
                "server": server.rstrip("/"),
                "username": user_id,
                "password": code
            })
    return servers

def run_pipeline(page_url: str = DEFAULT_PAGE_URL, active_only: bool = True, export_file: str = None, as_json: bool = False, quiet: bool = False):
    """Executes the full download -> test -> present pipeline."""
    try:
        raw_text = fetch_xtream_content(page_url, quiet=quiet)
    except Exception as e:
        print(f"[-] Failed to fetch Xtream list: {e}", file=sys.stderr)
        sys.exit(1)

    targets = parse_servers_from_text(raw_text)
    if not targets:
        print("[-] No servers found in the downloaded response.", file=sys.stderr)
        sys.exit(1)

    if not quiet:
        print(f"[3/3] ⚡ Validating {len(targets)} server(s) concurrently...")

    results = []
    with ThreadPoolExecutor(max_workers=8) as executor:
        future_to_acc = {executor.submit(test_single_server, acc, 6): acc for acc in targets}
        for future in as_completed(future_to_acc):
            results.append(future.result())

    # Sort results: Active first, then lowest ping
    results.sort(key=lambda x: (not (x["is_authenticated"] and x["status"].lower() == "active"), x["response_time_ms"]))

    active_results = [r for r in results if r["is_authenticated"] and r["status"].lower() == "active"]

    if as_json:
        output_data = active_results if active_only else results
        print(json.dumps(output_data, indent=2))
        return

    # Clean display
    print("\n" + "=" * 95)
    print("                    🎯 VERIFIED ACTIVE XTREAM SERVERS                    ")
    print("=" * 95)

    if not active_results:
        print("\n❌ No active/working servers found at this time.\n")
    else:
        for idx, s in enumerate(active_results, 1):
            m3u_url = f"{s['server']}/get.php?username={urllib.parse.quote(s['username'])}&password={urllib.parse.quote(s['password'])}&type=m3u_plus&output=ts"
            print(f"\n[Server #{idx}] - Status: ✅ Active (Latency: {s['response_time_ms']}ms)")
            print(f"  • Server URL:   {s['server']}")
            print(f"  • Username:     {s['username']}")
            print(f"  • Password:     {s['password']}")
            print(f"  • Expiration:   {s['exp_date']}")
            print(f"  • Connections:  {s['connections']} / {s['max_connections']} max")
            print(f"  • M3U URL:      {m3u_url}")

    if not active_only:
        inactive = [r for r in results if not (r["is_authenticated"] and r["status"].lower() == "active")]
        if inactive:
            print("\n" + "-" * 95)
            print("                     ⚠️ INACTIVE / EXPIRED / OFFLINE                     ")
            print("-" * 95)
            for r in inactive:
                reason = r['status']
                print(f"  • {r['server']:<32} | User: {r['username']:<15} | Status: ❌ {reason}")

    print("\n" + "=" * 95)
    print(f"Total Discovered: {len(results)} | Verified Working: {len(active_results)}")
    print("=" * 95 + "\n")

    if export_file:
        export_path = Path(export_file)
        lines = [
            f"# SkyM3U Verified Active Servers ({len(active_results)} active)",
            f"# Generated: {datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}",
            ""
        ]
        for s in active_results:
            m3u = f"{s['server']}/get.php?username={s['username']}&password={s['password']}&type=m3u_plus&output=ts"
            lines.append(f"Server: {s['server']}")
            lines.append(f"    ID: {s['username']}")
            lines.append(f"  CODE: {s['password']}")
            lines.append(f"   EXP: {s['exp_date']}")
            lines.append(f"  CONN: {s['connections']}/{s['max_connections']}")
            lines.append(f"   M3U: {m3u}")
            lines.append("-" * 50)
        export_path.write_text("\n".join(lines), encoding="utf-8")
        print(f"[+] Saved active servers to: '{export_path}'\n")

def main():
    parser = argparse.ArgumentParser(
        description="All-in-one tool to download, test, and show only verified active Xtream servers."
    )
    parser.add_argument(
        "-a", "--all",
        action="store_true",
        help="Show all tested servers including expired/offline ones (default: show only active)"
    )
    parser.add_argument(
        "-e", "--export",
        type=str,
        default="active_servers.txt",
        help="Export active servers to a file (default: active_servers.txt, use '' to disable)"
    )
    parser.add_argument(
        "-q", "--quiet",
        action="store_true",
        help="Suppress download progress logs and show only the final results"
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output result in clean JSON format"
    )
    parser.add_argument(
        "--url",
        type=str,
        default=DEFAULT_PAGE_URL,
        help="Source page URL"
    )

    args = parser.parse_args()
    
    export_target = args.export if args.export != "" else None
    run_pipeline(
        page_url=args.url,
        active_only=not args.all,
        export_file=export_target,
        as_json=args.json,
        quiet=args.quiet
    )

if __name__ == "__main__":
    main()
