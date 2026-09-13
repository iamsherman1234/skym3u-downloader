# SkyM3U Bypass Downloader

A lightweight, automated Python tool to fetch Xtream server lists and M3U playlists directly from SkyM3U pages, bypassing client-side ad timers and verification steps.

## Features

- **No Third-Party Dependencies**: Runs on standard Python 3 (`urllib`, `re`, `argparse`).
- **Dynamic Configuration Extraction**: Automatically scrapes the latest backend worker endpoint and authentication tokens from the page.
- **Multiple Download Modes**: Supports downloading Xtream server lists, BDIX M3U playlists, or both simultaneously.

## Usage

### 1. Download Xtream Server List (Default)
```bash
python3 skym3u_downloader.py
```
*Output is saved to `xtream_servers.txt`.*

### 2. Download and Automatically Test Servers
```bash
python3 skym3u_downloader.py --test
```

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

