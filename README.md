# SkyM3U Bypass Downloader

A lightweight, automated Python tool to fetch Xtream server lists and M3U playlists directly from SkyM3U pages, bypassing client-side ad timers and verification steps.

## Features

- **No Third-Party Dependencies**: Runs on standard Python 3 (`urllib`, `re`, `argparse`).
- **Dynamic Configuration Extraction**: Automatically scrapes the latest backend worker endpoint and authentication tokens from the page.
- **Multiple Download Modes**: Supports downloading Xtream server lists, BDIX M3U playlists, or both simultaneously.
- **GitHub Actions Automation**: Automatically runs every day at 00:00 UTC to refresh and validate the active servers.

## ⚙️ GitHub Actions Automation

The repository includes a daily automated workflow ([`.github/workflows/daily_update.yml`](.github/workflows/daily_update.yml)):
- **Schedule**: Runs automatically every day at `00:00 UTC`.
- **Manual Trigger**: You can run it on-demand anytime from the **Actions** tab in GitHub by selecting **Daily SkyM3U Update & Test** -> **Run workflow**.
- **Auto-Commit**: Automatically pushes the freshly verified `active_servers.txt`, `xtream_servers.txt`, and `dedicated_ip.m3u` back to the repository.

## 🚀 One-Command Workflow (Download + Test + Show Active Only)

To automatically fetch without ads, test all servers concurrently, and display only working active servers:

```bash
python3 run.py
```

### Useful Options:
- **Quiet Mode** (Show only the verified results without progress logs):
  ```bash
  python3 run.py -q
  ```
- **Show All Servers** (Including expired/offline ones):
  ```bash
  python3 run.py -a
  ```
- **Output as JSON**:
  ```bash
  python3 run.py --json
  ```
- **Custom Export File**:
  ```bash
  python3 run.py -e my_working_servers.txt
  ```

---

## Modular Usage

### 3. Download All (Xtream + BDIX M3U Playlist)
```bash
python3 skym3u_downloader.py --type all
```

### 4. Download BDIX M3U Playlist Only
```bash
python3 skym3u_downloader.py --type bdix
```

---

## Standalone Xtream Tester (`xtream_tester.py`)

A fast, concurrent validator for Xtream Codes servers. Checks connectivity, auth credentials, active vs max connections, and subscription expiration.

### Test a file of Xtream servers
```bash
python3 xtream_tester.py -f xtream_servers.txt
```

### Test and Export only Active Servers
```bash
python3 xtream_tester.py -f xtream_servers.txt --export-active active_servers.txt
```

### Test a single server directly
```bash
python3 xtream_tester.py -s http://portal5458.com:8080 -u 552211 -p 552211
```

### Output in JSON format
```bash
python3 xtream_tester.py -f xtream_servers.txt --json
```

