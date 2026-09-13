#!/usr/bin/env python3
"""
Universal IPTV Validator (Xtream Codes & Stalker MAC Portals)
Tests Xtream server credentials and Stalker/Ministra MAC addresses for validity,
account status, expiration date, and active connections.
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

USER_AGENT_XTREAM = "IPTVSmarters/1.0.0 (Linux; Android 10)"
USER_AGENT_STALKER = "Mozilla/5.0 (QtEmbedded; U; Linux; C) AppleWebKit/533.3 (KHTML, like Gecko) MAG200 stbapp ver: 2 rev: 250 Safari/533.3"
MAC_REGEX = re.compile(r"^([0-9A-Fa-f]{2}[:-]){5}([0-9A-Fa-f]{2})$")

def is_valid_mac(value: str) -> bool:
    """Checks if a string is a valid MAC address."""
    return bool(MAC_REGEX.match(value.strip()))

def format_timestamp(ts: Optional[str]) -> str:
    """Converts a unix timestamp or date string to a readable format."""
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

def parse_iptv_file(filepath: Path) -> List[Dict[str, Any]]:
    """
    Parses Xtream and Stalker portal accounts from text/config files.
    """
    if not filepath.exists():
        return []

    content = filepath.read_text(encoding="utf-8", errors="ignore")
    return parse_iptv_content(content)

def parse_iptv_content(content: str) -> List[Dict[str, Any]]:
    """
    Parses accounts from raw text content supporting:
    1. Server / ID / CODE (Xtream or Stalker)
    2. Portal / MAC (Stalker)
    3. M3U / query params
    """
    accounts = []
    
    # Split into logical blocks by Server: or Portal:
    blocks = re.split(r"(?=(?:Server|Portal):\s*https?://)", content, flags=re.IGNORECASE)
    
    for b in blocks:
        b = b.strip()
        if not b:
            continue
        server_m = re.search(r"(?:Server|Portal):\s*(https?://[^\s\r\n]+)", b, re.IGNORECASE)
        id_m = re.search(r"(?:ID|MAC|User|Username):\s*([^\r\n]+)", b, re.IGNORECASE)
        code_m = re.search(r"(?:CODE|Pass|Password):\s*([^\r\n]+)", b, re.IGNORECASE)
        
        if server_m and id_m:
            server = server_m.group(1).strip()
            user_id = id_m.group(1).strip()
            code = code_m.group(1).strip() if code_m else ""
            
            if is_valid_mac(user_id):
                accounts.append({
                    "type": "stalker",
                    "server": server.rstrip("/"),
                    "mac": user_id.upper(),
                    "password": code
                })
            else:
                accounts.append({
                    "type": "xtream",
                    "server": server.rstrip("/"),
                    "username": user_id,
                    "password": code
                })

    # Also handle standard URL query formats if blocks yielded nothing
    if not accounts:
        for line in content.splitlines():
            line = line.strip()
            if "username=" in line and "password=" in line:
                parsed = urllib.parse.urlparse(line)
                qs = urllib.parse.parse_qs(parsed.query)
                base = f"{parsed.scheme}://{parsed.netloc}"
                u = qs.get("username", [""])[0]
                p = qs.get("password", [""])[0]
                if base and u and p:
                    accounts.append({
                        "type": "xtream",
                        "server": base,
                        "username": u,
                        "password": p
                    })

    return accounts

def test_stalker_portal(account: Dict[str, Any], timeout: int = 8) -> Dict[str, Any]:
    """
    Tests a Stalker/Ministra MAC portal using handshake and get_profile.
    """
    server = account["server"].rstrip("/")
    mac = account.get("mac") or account.get("username", "")
    mac = mac.strip().upper()

    result = {
        "type": "stalker",
        "server": server,
        "mac": mac,
        "username": mac,
        "password": account.get("password", ""),
        "is_online": False,
        "is_authenticated": False,
        "status": "Offline / Unreachable",
        "exp_date": "N/A",
        "connections": "N/A",
        "max_connections": "1",
        "error": None,
        "response_time_ms": 0
    }

    endpoints = []
    if server.endswith("/c"):
        parent = server[:-2]
        endpoints.extend([f"{parent}/portal.php", f"{parent}/server/load.php", f"{server}/portal.php"])
    else:
        endpoints.extend([
            f"{server}/portal.php",
            f"{server}/server/load.php",
            f"{server}/c/portal.php",
            f"{server}/stalker_portal/c/portal.php"
        ])

    headers = {
        "User-Agent": USER_AGENT_STALKER,
        "X-User-Agent": "Model: MAG250; Link: WiFi",
        "Cookie": f"mac={urllib.parse.quote(mac)}; stb_lang=en; timezone=UTC",
        "Referer": server
    }

    start = time.time()
    for ep in endpoints:
        try:
            hs_url = f"{ep}?type=stb&action=handshake&token=&mac={urllib.parse.quote(mac)}"
            req = urllib.request.Request(hs_url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = resp.read().decode("utf-8", errors="ignore")
                try:
                    js = json.loads(data)
                except Exception:
                    continue

                token = None
                if isinstance(js, dict) and "js" in js and isinstance(js["js"], dict):
                    token = js["js"].get("token")
                elif isinstance(js, dict) and "token" in js:
                    token = js.get("token")

                if token:
                    result["is_online"] = True
                    # Step 2: Query profile
                    prof_headers = dict(headers)
                    prof_headers["Authorization"] = f"Bearer {token}"
                    prof_headers["Cookie"] += f"; token={token}"

                    prof_url = f"{ep}?type=stb&action=get_profile&mac={urllib.parse.quote(mac)}"
                    prof_req = urllib.request.Request(prof_url, headers=prof_headers)
                    with urllib.request.urlopen(prof_req, timeout=timeout) as prof_resp:
                        pdata = prof_resp.read().decode("utf-8", errors="ignore")
                        pjs = json.loads(pdata)
                        p_info = pjs.get("js", {}) if isinstance(pjs, dict) else {}
                        
                        elapsed = int((time.time() - start) * 1000)
                        result["response_time_ms"] = elapsed
                        
                        if isinstance(p_info, dict) and (p_info.get("id") or p_info.get("expire_date") or p_info.get("phone")):
                            result["is_authenticated"] = True
                            result["status"] = "Active"
                            result["exp_date"] = format_timestamp(p_info.get("expire_date") or p_info.get("phone"))
                            return result
                        elif pjs.get("js") is False or "denied" in str(pdata).lower():
                            result["status"] = "MAC Unauthorized / Blocked"
                            return result
        except Exception:
            continue

    result["response_time_ms"] = int((time.time() - start) * 1000)
    return result

def test_xtream_server(account: Dict[str, Any], timeout: int = 8) -> Dict[str, Any]:
    """
    Queries the Xtream player_api.php endpoint to validate credentials.
    """
    server = account["server"]
    username = account.get("username", "")
    password = account.get("password", "")

    api_url = f"{server}/player_api.php?username={urllib.parse.quote(username)}&password={urllib.parse.quote(password)}"
    
    result = {
        "type": "xtream",
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
        req = urllib.request.Request(api_url, headers={"User-Agent": USER_AGENT_XTREAM})
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
            result["status"] = f"Connection Failed: {reason[:25]}"
        result["error"] = str(e.reason)
    except Exception as e:
        result["response_time_ms"] = int((time.time() - start_time) * 1000)
        result["status"] = f"Error: {str(e)[:25]}"
        result["error"] = str(e)

    return result

def test_single_server(account: Dict[str, Any], timeout: int = 8) -> Dict[str, Any]:
    """Routes to Xtream or Stalker tester based on account type."""
    if account.get("type") == "stalker" or is_valid_mac(account.get("mac", "") or account.get("username", "")):
        return test_stalker_portal(account, timeout)
    else:
        return test_xtream_server(account, timeout)

def parse_xtream_file(filepath: Path) -> List[Dict[str, Any]]:
    """Backward-compatible alias for parse_iptv_file."""
    return parse_iptv_file(filepath)

def print_results_table(results: List[Dict[str, Any]]):
    """Prints a clean, formatted ASCII table of test results."""
    print("\n" + "=" * 105)
    print(f"{'TYPE':<9} | {'SERVER / PORTAL':<32} | {'USER / MAC':<19} | {'STATUS':<14} | {'CONN':<6} | {'EXPIRATION':<16} | {'PING'}")
    print("-" * 105)

    active_count = 0
    for r in results:
        status = r["status"]
        if r["is_authenticated"] and "active" in status.lower():
            status_display = f"✅ {status}"
            active_count += 1
        elif r["is_authenticated"] and "expired" in status.lower():
            status_display = f"⚠️ {status}"
        elif r["is_authenticated"]:
            status_display = f"🟡 {status}"
        else:
            status_display = f"❌ {status}"

        acc_type = r.get("type", "xtream").upper()
        server_disp = r["server"] if len(r["server"]) <= 32 else r["server"][:29] + "..."
        user_disp = r.get("username") or r.get("mac", "")
        user_disp = user_disp if len(user_disp) <= 19 else user_disp[:16] + "..."
        conns = f"{r.get('connections','N/A')}/{r.get('max_connections','1')}" if r.get("connections") != "N/A" else "N/A"
        exp = r.get("exp_date", "N/A")
        ping = f"{r['response_time_ms']}ms" if r["response_time_ms"] > 0 else "-"

        print(f"{acc_type:<9} | {server_disp:<32} | {user_disp:<19} | {status_display:<14} | {conns:<6} | {exp:<16} | {ping}")

    print("=" * 105)
    print(f"Summary: {len(results)} evaluated | {active_count} Active & Working\n")

def main():
    parser = argparse.ArgumentParser(description="Test Xtream and Stalker portal credentials")
    parser.add_argument(
        "-f", "--file",
        type=str,
        default="xtream_servers.txt",
        help="Path to file containing Xtream or Stalker credentials"
    )
    parser.add_argument("-s", "--server", type=str, help="Single Server / Portal URL")
    parser.add_argument("-u", "--username", type=str, help="Username for Xtream server")
    parser.add_argument("-p", "--password", type=str, default="", help="Password / Code")
    parser.add_argument("-m", "--mac", type=str, help="MAC address for Stalker portal (e.g. 00:1A:79:XX:XX:XX)")
    parser.add_argument("-t", "--timeout", type=int, default=8, help="Timeout in seconds (default: 8)")
    parser.add_argument("-w", "--workers", type=int, default=8, help="Worker threads (default: 8)")
    parser.add_argument("--json", action="store_true", help="Output raw JSON results")

    args = parser.parse_args()

    targets = []
    if args.server:
        if args.mac or is_valid_mac(args.username or ""):
            targets.append({
                "type": "stalker",
                "server": args.server.rstrip("/"),
                "mac": (args.mac or args.username).upper(),
                "password": args.password
            })
        elif args.username:
            targets.append({
                "type": "xtream",
                "server": args.server.rstrip("/"),
                "username": args.username,
                "password": args.password
            })
    else:
        targets = parse_iptv_file(Path(args.file))
        if not targets:
            print(f"[-] No valid credentials found in '{args.file}'.")
            sys.exit(1)

    print(f"[*] Testing {len(targets)} IPTV account(s)...")
    results = []
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        future_to_acc = {executor.submit(test_single_server, acc, args.timeout): acc for acc in targets}
        for future in as_completed(future_to_acc):
            results.append(future.result())

    results.sort(key=lambda x: (not (x["is_authenticated"] and "active" in x["status"].lower()), x["response_time_ms"]))

    if args.json:
        print(json.dumps(results, indent=2))
    else:
        print_results_table(results)

if __name__ == "__main__":
    main()
