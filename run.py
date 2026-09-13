#!/usr/bin/env python3
"""
All-in-One SkyM3U Pipeline (Xtream Codes & Stalker MAC Portals)
Downloads, validates, accumulates, and outputs active Xtream & Stalker servers across runs.
"""

import sys
import json
import argparse
import datetime
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Dict, Any

from skym3u_downloader import fetch_live_credentials, DEFAULT_PAGE_URL, USER_AGENT
from xtream_tester import (
    parse_iptv_file,
    parse_iptv_content,
    test_single_server,
    is_valid_mac,
    format_timestamp
)
import urllib.request
import urllib.parse
import re

def fetch_xtream_content(page_url: str = DEFAULT_PAGE_URL, quiet: bool = False) -> str:
    """Fetches the latest server list text directly without ads."""
    if not quiet:
        print("[1/3] 🌐 Fetching latest configuration from SkyM3U...")
    worker, token = fetch_live_credentials(page_url)
    
    if not quiet:
        print(f"[2/3] 📥 Fetching newest server list from worker backend...")
    
    params = urllib.parse.urlencode({
        "type": "xtream",
        "step": "3",
        "token": token
    })
    full_url = f"{worker}?{params}"
    
    req = urllib.request.Request(full_url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=15) as resp:
        return resp.read().decode("utf-8", errors="ignore")

def merge_and_deduplicate(*server_lists: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Combines multiple server lists and removes duplicates."""
    seen = set()
    merged = []
    for s_list in server_lists:
        for item in s_list:
            server = (item.get("server") or "").strip().rstrip("/")
            uid = (item.get("mac") or item.get("username") or "").strip()
            pwd = (item.get("password") or "").strip()
            acc_type = item.get("type", "stalker" if is_valid_mac(uid) else "xtream")

            if not server or not uid:
                continue

            key = (acc_type, server.lower(), uid.upper() if acc_type == "stalker" else uid, pwd)
            if key not in seen:
                seen.add(key)
                merged.append({
                    "type": acc_type,
                    "server": server,
                    "mac" if acc_type == "stalker" else "username": uid.upper() if acc_type == "stalker" else uid,
                    "password": pwd
                })
    return merged

def update_raw_history_file(servers: List[Dict[str, Any]], filepath: Path = Path("xtream_servers.txt")):
    """Appends newly discovered servers to xtream_servers.txt without duplicates."""
    existing = parse_iptv_file(filepath) if filepath.exists() else []
    combined = merge_and_deduplicate(existing, servers)
    
    lines = [
        "# SkyM3U Discovered Servers History (Xtream & Stalker)",
        f"# Updated: {datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}",
        ""
    ]
    for s in combined:
        if s.get("type") == "stalker":
            lines.append(f"Portal: {s['server']}")
            lines.append(f"   MAC: {s.get('mac') or s.get('username')}")
        else:
            lines.append(f"Server: {s['server']}")
            lines.append(f"    ID: {s.get('username')}")
            lines.append(f"  CODE: {s.get('password', '')}")
        lines.append("")
    filepath.write_text("\n".join(lines), encoding="utf-8")

def run_pipeline(
    page_url: str = DEFAULT_PAGE_URL,
    active_only: bool = True,
    export_file: str = "active_servers.txt",
    as_json: bool = False,
    quiet: bool = False,
    accumulate: bool = True
):
    """Executes download -> parse Xtream & Stalker -> test -> present & accumulate."""
    # 1. Fetch newly scraped servers
    try:
        raw_text = fetch_xtream_content(page_url, quiet=quiet)
        newly_scraped = parse_iptv_content(raw_text)
    except Exception as e:
        print(f"[-] Warning: Could not fetch live SkyM3U page ({e}). Evaluating existing servers.", file=sys.stderr)
        newly_scraped = []

    # 2. Collect existing servers if accumulating
    existing_active = []
    existing_history = []
    export_path = Path(export_file) if export_file else None
    
    if accumulate:
        if export_path and export_path.exists():
            existing_active = parse_iptv_file(export_path)
        history_path = Path("xtream_servers.txt")
        if history_path.exists():
            existing_history = parse_iptv_file(history_path)

    # Set of previously active server keys
    prev_active_keys = {
        (
            s.get("type", "xtream"),
            s["server"].lower().rstrip("/"),
            (s.get("mac") or s.get("username", "")).strip().upper()
        )
        for s in existing_active
    }

    # 3. Merge all server sources
    all_targets = merge_and_deduplicate(existing_active, existing_history, newly_scraped)

    if not all_targets:
        print("[-] No servers available to test.", file=sys.stderr)
        sys.exit(1)

    # Save to history file
    update_raw_history_file(all_targets, Path("xtream_servers.txt"))

    if not quiet:
        xtream_count = sum(1 for s in all_targets if s.get("type") == "xtream")
        stalker_count = sum(1 for s in all_targets if s.get("type") == "stalker")
        print(f"[3/3] ⚡ Validating {len(all_targets)} total server(s) [{xtream_count} Xtream, {stalker_count} Stalker]...")

    # 4. Test all servers concurrently
    results = []
    with ThreadPoolExecutor(max_workers=10) as executor:
        future_to_acc = {executor.submit(test_single_server, acc, 8): acc for acc in all_targets}
        for future in as_completed(future_to_acc):
            results.append(future.result())

    # Build active list: Keep verified active servers & preserve previously active servers on transient lag
    final_active = []
    for r in results:
        key = (
            r.get("type", "xtream"),
            r["server"].lower().rstrip("/"),
            (r.get("mac") or r.get("username", "")).strip().upper()
        )
        if r["is_authenticated"] and "active" in r["status"].lower():
            final_active.append(r)
        elif key in prev_active_keys and any(err in str(r.get("status", "")).lower() for err in ["timeout", "resolution", "failed"]):
            r["status"] = "Active (Retained)"
            final_active.append(r)

    # Sort results
    results.sort(key=lambda x: (not (x["is_authenticated"] and "active" in x["status"].lower()), x["response_time_ms"]))
    final_active.sort(key=lambda x: (x.get("type") != "xtream", x["response_time_ms"]))

    if as_json:
        output_data = final_active if active_only else results
        print(json.dumps(output_data, indent=2))
        return

    # Clean display
    print("\n" + "=" * 95)
    print("                 🎯 VERIFIED ACTIVE SERVERS (XTREAM & STALKER)                 ")
    print("=" * 95)

    active_xtream = [s for s in final_active if s.get("type") == "xtream"]
    active_stalker = [s for s in final_active if s.get("type") == "stalker"]

    if not final_active:
        print("\n❌ No active/working servers found at this time.\n")
    else:
        if active_xtream:
            print("\n📺 --- [ XTREAM CODES ACCOUNTS ] ---")
            for idx, s in enumerate(active_xtream, 1):
                m3u_url = f"{s['server']}/get.php?username={urllib.parse.quote(s['username'])}&password={urllib.parse.quote(s.get('password',''))}&type=m3u_plus&output=ts"
                ping_disp = f"{s['response_time_ms']}ms" if s['response_time_ms'] > 0 else "Cached"
                print(f"\n[Xtream #{idx}] - Status: ✅ {s['status']} (Latency: {ping_disp})")
                print(f"  • Server URL:   {s['server']}")
                print(f"  • Username:     {s['username']}")
                print(f"  • Password:     {s.get('password','')}")
                print(f"  • Expiration:   {s.get('exp_date', 'N/A')}")
                print(f"  • Connections:  {s.get('connections','N/A')} / {s.get('max_connections','1')} max")
                print(f"  • M3U URL:      {m3u_url}")

        if active_stalker:
            print("\n📡 --- [ STALKER / MAG PORTALS ] ---")
            for idx, s in enumerate(active_stalker, 1):
                ping_disp = f"{s['response_time_ms']}ms" if s['response_time_ms'] > 0 else "Cached"
                mac_addr = s.get("mac") or s.get("username", "")
                print(f"\n[Stalker #{idx}] - Status: ✅ {s['status']} (Latency: {ping_disp})")
                print(f"  • Portal URL:   {s['server']}")
                print(f"  • MAC Address:  {mac_addr}")
                print(f"  • Expiration:   {s.get('exp_date', 'N/A')}")

    if not active_only:
        inactive = [r for r in results if r not in final_active]
        if inactive:
            print("\n" + "-" * 95)
            print("                     ⚠️ INACTIVE / EXPIRED / OFFLINE                     ")
            print("-" * 95)
            for r in inactive:
                acc_type = r.get("type", "xtream").upper()
                identifier = r.get("mac") or r.get("username", "")
                reason = r['status']
                print(f"  • [{acc_type:<7}] {r['server']:<32} | {identifier:<17} | Status: ❌ {reason}")

    print("\n" + "=" * 95)
    print(f"Total Evaluated: {len(results)} | Active & Preserved: {len(final_active)} ({len(active_xtream)} Xtream, {len(active_stalker)} Stalker)")
    print("=" * 95 + "\n")

    # 5. Export / Update active_servers.txt
    if export_path:
        lines = [
            f"# SkyM3U Verified Active Servers ({len(active_xtream)} Xtream, {len(active_stalker)} Stalker)",
            f"# Updated: {datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}",
            ""
        ]
        if active_xtream:
            lines.append("### XTREAM SERVERS ###")
            for s in active_xtream:
                m3u = f"{s['server']}/get.php?username={s['username']}&password={s.get('password','')}&type=m3u_plus&output=ts"
                lines.append(f"Server: {s['server']}")
                lines.append(f"    ID: {s['username']}")
                lines.append(f"  CODE: {s.get('password','')}")
                lines.append(f"   EXP: {s.get('exp_date', 'N/A')}")
                lines.append(f"  CONN: {s.get('connections','N/A')}/{s.get('max_connections','1')}")
                lines.append(f"   M3U: {m3u}")
                lines.append("-" * 50)
            lines.append("")

        if active_stalker:
            lines.append("### STALKER PORTALS ###")
            for s in active_stalker:
                mac_addr = s.get("mac") or s.get("username", "")
                lines.append(f"Portal: {s['server']}")
                lines.append(f"   MAC: {mac_addr}")
                lines.append(f"   EXP: {s.get('exp_date', 'N/A')}")
                lines.append("-" * 50)
            lines.append("")

        export_path.write_text("\n".join(lines), encoding="utf-8")
        print(f"[+] Saved {len(final_active)} active server(s) to: '{export_path}'\n")

def main():
    parser = argparse.ArgumentParser(
        description="All-in-one tool to download, test, accumulate, and show active Xtream & Stalker servers."
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
        "--no-accumulate",
        action="store_true",
        help="Do not merge with previous runs, test only the newly downloaded batch"
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
        quiet=args.quiet,
        accumulate=not args.no_accumulate
    )

if __name__ == "__main__":
    main()
