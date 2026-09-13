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

### 2. Download All (Xtream + BDIX M3U Playlist)
```bash
python3 skym3u_downloader.py --type all
```

### 3. Download BDIX M3U Playlist Only
```bash
python3 skym3u_downloader.py --type bdix
```

### 4. Custom Output Path
```bash
python3 skym3u_downloader.py --type xtream --output custom_name.txt
```
