#!/usr/bin/env python3
"""
Xtream Codes Server Tester & Validator
Tests Xtream server credentials for validity, account status, expiration date,
and active connections using concurrent checks.
"""

import sys
import re
import json
import time
import argparse
import datetime
import urllib.request
import urllib.parse
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Dict, Any, Optional

USER_AGENT = "IPTVSmarters/1.0.0 (Linux; Android 10)"

def format_timestamp(ts: Optional[str]) -> str:
    """Converts a unix timestamp string to a readable date."""
    if not ts or ts == "None" or ts == "null":
        return "Unlimited / None"
    try:
        ts_int = int(ts)
        dt = datetime.datetime.fromtimestamp(ts_int, tz=datetime.timezone.utc)
        now = datetime.datetime.now(datetime.timezone.utc)
        diff = (dt - now).days
        date_str = dt.strftime("%Y-%m-%d")
        if diff >= 0:
            return f"{date_str} ({diff}d left)"
        else:
            return f"{date_str} (expired {abs(diff)}d ago)"
    except Exception:
        return str(ts)

def parse_xtream_file(filepath: Path) -> List[Dict[str, str]]:
    """
    Parses Xtream accounts from a text file with format:
    Server: http://...
        ID: ...
      CODE: ...
    """
    if not filepath.exists():
        print(f"[-] Error: File '{filepath}' not found.", file=sys.stderr)
        return []

    content = filepath.read_text(encoding="utf-8", errors="ignore")
    servers = []

    # Regex pattern to match Server / ID / CODE blocks
    pattern = re.compile(
        r"Server:\s*(?P<server>https?://[^\s\r\n]+)"
        r"(?:[\s\r\n]+ID:\s*(?P<id>[^\r\n]+))?"
        r"(?:[\s\r\n]+CODE:\s*(?P<code>[^\r\n]+))?",
        re.IGNORECASE
    )

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

    # Also handle m3u-style URLs or comma/colon separated entries if present
    if not servers:
        for line in content.splitlines():
            line = line.strip()
            # Example: http://host:port/get.php?username=xxx&password=yyy
            if "username=" in line and "password=" in line:
                parsed = urllib.parse.urlparse(line)
                qs = urllib.parse.parse_qs(parsed.query)
                base = f"{parsed.scheme}://{parsed.netloc}"
                u = qs.get("username", [""])[0]
                p = qs.get("password", [""])[0]
                if base and u and p:
                    servers.append({"server": base, "username": u, "password": p})

    return servers

def test_single_server(account: Dict[str, str], timeout: int = 6) -> Dict[str, Any]:
    """
    Queries the Xtream player_api.php endpoint to validate credentials.
    """
    server = account["server"]
    username = account["username"]
    password = account["password"]

    api_url = f"{server}/player_api.php?username={urllib.parse.quote(username)}&password={urllib.parse.quote(password)}"
    
    result = {
        "server": server,
        "username": username,
        "password": password,
        "is_online": False,
        "is_authenticated": False,
        "status": "Offline / Unreachable",
        "exp_date": "N/A",
        "connections": "N/A",
        "max_connections": "N/A",
        "error": None,
        "response_time_ms": 0,
        "raw_user_info": {}
    }

    start_time = time.time()
    try:
        req = urllib.request.Request(api_url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            elapsed = int((time.time() - start_time) * 1000)
            result["response_time_ms"] = elapsed
            result["is_online"] = True
            
            data = resp.read().decode("utf-8", errors="ignore")
            try:
                js = json.loads(data)
            except Exception:
                result["status"] = "Invalid API Response (Not JSON)"
                return result

            user_info = js.get("user_info", {})
            result["raw_user_info"] = user_info

            auth = user_info.get("auth", 0)
            status = user_info.get("status", "Unknown")

            if auth == 1 or str(auth).lower() == "true":
                result["is_authenticated"] = True
                result["status"] = status.capitalize() if status else "Active"
                result["exp_date"] = format_timestamp(user_info.get("exp_date"))
                result["connections"] = str(user_info.get("active_cons", "0"))
                result["max_connections"] = str(user_info.get("max_connections", "1"))
            else:
                result["status"] = "Auth Failed (Invalid Credentials)"

    except urllib.error.HTTPError as e:
        result["response_time_ms"] = int((time.time() - start_time) * 1000)
        result["error"] = f"HTTP {e.code}"
        result["status"] = f"HTTP Error {e.code}"
    except urllib.error.URLError as e:
        result["response_time_ms"] = int((time.time() - start_time) * 1000)
        reason = str(e.reason)
        if "Name or service not known" in reason:
            result["status"] = "DNS Resolution Failed"
        elif "timed out" in reason.lower():
            result["status"] = "Connection Timeout"
        else:
            result["status"] = f"Connection Failed: {reason[:30]}"
        result["error"] = str(e.reason)
    except Exception as e:
        result["response_time_ms"] = int((time.time() - start_time) * 1000)
        result["status"] = f"Error: {str(e)[:30]}"
        result["error"] = str(e)

    return result

def print_results_table(results: List[Dict[str, Any]]):
    """Prints a clean, formatted ASCII table of test results."""
    print("\n" + "=" * 100)
    print(f"{'SERVER':<35} | {'USER':<18} | {'STATUS':<14} | {'CONN':<8} | {'EXPIRATION':<20} | {'PING'}")
    print("-" * 100)

    active_count = 0
    for r in results:
        status = r["status"]
        if r["is_authenticated"] and status.lower() == "active":
            status_display = f"✅ {status}"
            active_count += 1
        elif r["is_authenticated"] and status.lower() == "expired":
            status_display = f"⚠️ {status}"
        elif r["is_authenticated"]:
            status_display = f"🟡 {status}"
        else:
            status_display = f"❌ {status}"

        server_disp = r["server"] if len(r["server"]) <= 35 else r["server"][:32] + "..."
        user_disp = r["username"] if len(r["username"]) <= 18 else r["username"][:15] + "..."
        conns = f"{r['connections']}/{r['max_connections']}" if r["connections"] != "N/A" else "N/A"
        exp = r["exp_date"]
        ping = f"{r['response_time_ms']}ms" if r["response_time_ms"] > 0 else "-"

        print(f"{server_disp:<35} | {user_disp:<18} | {status_display:<14} | {conns:<8} | {exp:<20} | {ping}")

    print("=" * 100)
    print(f"Summary: {len(results)} tested | {active_count} Active & Working\n")

def export_active_servers(results: List[Dict[str, Any]], export_path: Path):
    """Exports active servers to a clean text file."""
    active_servers = [r for r in results if r["is_authenticated"] and r["status"].lower() == "active"]
    
    lines = ["# Verified Active Xtream Servers", f"# Checked at: {datetime.datetime.now(datetime.timezone.utc).isoformat()}", ""]
    for s in active_servers:
        lines.append(f"Server: {s['server']}")
        lines.append(f"    ID: {s['username']}")
        lines.append(f"  CODE: {s['password']}")
        lines.append(f"  EXPIRES: {s['exp_date']}")
        lines.append(f"  CONNS: {s['connections']}/{s['max_connections']}")
        lines.append("")

    export_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"[+] Exported {len(active_servers)} active server(s) to '{export_path}'")

def main():
    parser = argparse.ArgumentParser(description="Test Xtream server credentials and validate account status")
    parser.add_argument(
        "-f", "--file",
        type=str,
        default="xtream_servers.txt",
        help="Path to file containing Xtream credentials (default: xtream_servers.txt)"
    )
    parser.add_argument(
        "-s", "--server",
        type=str,
        help="Single Xtream server URL (e.g. http://example.com:8080)"
    )
    parser.add_argument(
        "-u", "--username",
        type=str,
        help="Username for single server test"
    )
    parser.add_argument(
        "-p", "--password",
        type=str,
        help="Password for single server test"
    )
    parser.add_argument(
        "-t", "--timeout",
        type=int,
        default=6,
        help="Connection timeout per server in seconds (default: 6)"
    )
    parser.add_argument(
        "-w", "--workers",
        type=int,
        default=5,
        help="Concurrent worker threads (default: 5)"
    )
    parser.add_argument(
        "-o", "--export-active",
        type=str,
        help="File path to save only verified active servers"
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output raw JSON results"
    )

    args = parser.parse_args()

    targets = []
    if args.server and args.username and args.password:
        targets.append({
            "server": args.server.rstrip("/"),
            "username": args.username,
            "password": args.password
        })
    else:
        file_path = Path(args.file)
        targets = parse_xtream_file(file_path)
        if not targets:
            print(f"[-] No Xtream credentials found to test in '{args.file}'.")
            sys.exit(1)

    print(f"[*] Testing {len(targets)} Xtream server(s) with {args.workers} workers (timeout: {args.timeout}s)...")
    
    results = []
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        future_to_acc = {executor.submit(test_single_server, acc, args.timeout): acc for acc in targets}
        for future in as_completed(future_to_acc):
            res = future.result()
            results.append(res)

    # Sort results by active first, then response time
    results.sort(key=lambda x: (not (x["is_authenticated"] and x["status"].lower() == "active"), x["response_time_ms"]))

    if args.json:
        print(json.dumps(results, indent=2))
    else:
        print_results_table(results)

    if args.export_active:
        export_active_servers(results, Path(args.export_active))

if __name__ == "__main__":
    main()
