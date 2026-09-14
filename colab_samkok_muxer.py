#!/usr/bin/env python3
"""
Samkok 1080p Netflix Khmer Dub Muxer with PixelDrain & GDrive Uploader for Google Colab
Source: TheKomsan (95-Episode Complete Khmer Dubbed) + Netflix 1080p WEB-DL (st.111477.xyz)
Seamlessly processes episodes in Google Colab:
- Resolves 1080p Netflix stream from st.111477.xyz or uses manual URL override
- Extracts Khmer AAC audio from TheKomsan (Rumble CDN)
- Applies physical silence delay (+1.0s) for player-compatible lip sync
- Losslessly muxes into 1080p Dual Audio MKV with Chinese Subtitles
- Auto-uploads directly to PixelDrain and/or mounted Google Drive
"""

import sys
import os
import re
import json
import time
import argparse
import subprocess
import urllib.request
import urllib.parse
from pathlib import Path
from typing import List, Dict, Any, Optional

try:
    from curl_cffi import requests as cffi_requests
    HAS_CURL_CFFI = True
except ImportError:
    HAS_CURL_CFFI = False

SERIES_IMDB_ID = "tt1514753"  # Three Kingdoms (2010)
STREMIO_BASE = "https://st.111477.xyz"
A11_BASE_B64 = "aHR0cHM6Ly9hLjExMTQ3Ny54eXov"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
DEFAULT_AUDIO_DELAY = 1.0  # +1.0 second delay to match Netflix 1080p intro
DEFAULT_PIXELDRAIN_KEY = "cafccc0b-66db-4f1d-a5bb-de45da49f9d5"

def load_khmer_catalog() -> List[Dict[str, Any]]:
    candidates = [
        Path("thekomsan_samkok_episodes.json"),
        Path("/content/skym3u-downloader/thekomsan_samkok_episodes.json"),
        Path("/root/skym3u-downloader/thekomsan_samkok_episodes.json")
    ]
    for p in candidates:
        if p.exists():
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                pass
    url = "https://raw.githubusercontent.com/iamsherman1234/skym3u-downloader/main/thekomsan_samkok_episodes.json"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        data = json.loads(urllib.request.urlopen(req, timeout=10).read().decode("utf-8"))
        return data
    except Exception as e:
        print(f"[-] Error loading Khmer catalog: {e}", file=sys.stderr)
        return []

def load_netflix_streams_catalog() -> Dict[str, str]:
    candidates = [
        Path("netflix_samkok_1080p_streams.json"),
        Path("/content/skym3u-downloader/netflix_samkok_1080p_streams.json"),
        Path("/root/skym3u-downloader/netflix_samkok_1080p_streams.json")
    ]
    for p in candidates:
        if p.exists():
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                pass
    url = "https://raw.githubusercontent.com/iamsherman1234/skym3u-downloader/main/netflix_samkok_1080p_streams.json"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception:
        pass
    return {}

def get_default_proxy(explicit_proxy: Optional[str] = None) -> Optional[str]:
    """Auto-detects Cloudflare WARP proxy (127.0.0.1:40000) or environment proxies."""
    if explicit_proxy and explicit_proxy.lower() != "none":
        return explicit_proxy
    env_proxy = os.environ.get("ALL_PROXY") or os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY") or os.environ.get("all_proxy") or os.environ.get("https_proxy")
    if env_proxy:
        return env_proxy
    try:
        import socket
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.2)
            if s.connect_ex(("127.0.0.1", 40000)) == 0:
                return "socks5://127.0.0.1:40000"
    except Exception:
        pass
    return None

def resolve_1080p_stream_url(ep_num: int, proxy: Optional[str] = None) -> Optional[str]:
    # Check pre-cached catalog first to avoid HTTP 429 rate limits
    catalog = load_netflix_streams_catalog()
    if str(ep_num) in catalog and catalog[str(ep_num)]:
        return catalog[str(ep_num)]

    active_proxy = get_default_proxy(proxy)
    url = f"{STREMIO_BASE}/config/{A11_BASE_B64}/stream/series/{SERIES_IMDB_ID}:1:{ep_num}.json"
    backoff = 3
    for attempt in range(5):
        try:
            if HAS_CURL_CFFI:
                r = cffi_requests.get(url, impersonate="chrome", timeout=15, headers={"Referer": "https://st.111477.xyz/"}, proxy=active_proxy)
                if r.status_code == 200:
                    data = r.json()
                    streams = data.get("streams", [])
                    if streams:
                        return streams[0].get("url")
                elif r.status_code == 429:
                    raise urllib.error.HTTPError(url, 429, "Too Many Requests", r.headers, None)
            else:
                req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Referer": "https://st.111477.xyz/"})
                with urllib.request.urlopen(req, timeout=15) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                    streams = data.get("streams", [])
                    if streams:
                        return streams[0].get("url")
        except urllib.error.HTTPError as e:
            if e.code == 429:
                print(f"[-] Rate limited (429) resolving stream for E{ep_num:02d}. Backing off {backoff}s...", file=sys.stderr)
                time.sleep(backoff)
                backoff = min(backoff + 5, 20)
            else:
                print(f"[-] HTTP {e.code} resolving stream for E{ep_num:02d} (attempt {attempt+1}): {e}", file=sys.stderr)
                time.sleep(3)
        except Exception as e:
            print(f"[-] Attempt {attempt+1} failed to resolve stream for E{ep_num:02d}: {e}", file=sys.stderr)
            time.sleep(3)
    return None

def download_file_python(url: str, output_path: Path, min_size: int = 1000000, desc: str = "Video", max_speed_mb: float = 10.0, proxy: Optional[str] = None) -> bool:
    """True HTTP Range Resumable Downloader with Chrome TLS impersonation, bandwidth pacing, and zero-loss retries."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.exists() and output_path.stat().st_size >= min_size:
        return True

    active_proxy = get_default_proxy(proxy)
    backoff = 8
    max_retries = 15
    total_size = 0

    for attempt in range(1, max_retries + 1):
        current_size = output_path.stat().st_size if output_path.exists() else 0
        if total_size > 0 and current_size >= total_size:
            return True

        try:
            headers = {
                "User-Agent": USER_AGENT,
                "Referer": "https://st.111477.xyz/",
                "Accept": "*/*",
                "Sec-Fetch-Dest": "video",
                "Sec-Fetch-Mode": "no-cors",
                "Sec-Fetch-Site": "cross-site",
            }
            if current_size > 0:
                headers["Range"] = f"bytes={current_size}-"

            if HAS_CURL_CFFI:
                r = cffi_requests.get(
                    url,
                    headers=headers,
                    impersonate="chrome",
                    stream=True,
                    timeout=60,
                    proxy=active_proxy
                )
                if r.status_code == 429:
                    raise urllib.error.HTTPError(url, 429, "Too Many Requests", r.headers, None)
                if r.status_code not in (200, 206):
                    raise Exception(f"HTTP {r.status_code}")

                # Determine total file size
                cr = r.headers.get("content-range")
                if cr and "/" in cr:
                    try:
                        total_size = int(cr.split("/")[-1])
                    except Exception:
                        pass
                if total_size == 0:
                    cl = int(r.headers.get("content-length", 0))
                    total_size = current_size + cl if r.status_code == 206 else cl

                open_mode = "ab" if (current_size > 0 and r.status_code == 206) else "wb"
                bytes_downloaded = current_size
                start_time = time.time()
                last_print = 0

                with open(output_path, open_mode) as out_f:
                    for chunk in r.iter_content(chunk_size=512 * 1024):
                        chunk_start = time.time()
                        if not chunk:
                            break
                        out_f.write(chunk)
                        bytes_downloaded += len(chunk)

                        # Pacing
                        if max_speed_mb > 0:
                            target_time = len(chunk) / (max_speed_mb * 1024 * 1024)
                            chunk_elapsed = time.time() - chunk_start
                            if chunk_elapsed < target_time:
                                time.sleep(target_time - chunk_elapsed)

                        now = time.time()
                        if now - last_print >= 0.5:
                            last_print = now
                            elapsed = now - start_time
                            speed = ((bytes_downloaded - current_size) / (1024 * 1024)) / elapsed if elapsed > 0 else 0
                            mb_cur = bytes_downloaded / (1024 * 1024)
                            if total_size > 0:
                                pct = (bytes_downloaded / total_size) * 100
                                mb_tot = total_size / (1024 * 1024)
                                print(f"\r    📥 [{desc}] {pct:5.1f}% ({mb_cur:.1f}/{mb_tot:.1f} MB) at {speed:.2f} MB/s", end="", flush=True)
                            else:
                                print(f"\r    📥 [{desc}] {mb_cur:.1f} MB at {speed:.2f} MB/s", end="", flush=True)

                print()
                if output_path.exists() and output_path.stat().st_size >= min_size:
                    return True
            else:
                req = urllib.request.Request(url, headers=headers)
                with urllib.request.urlopen(req, timeout=60) as resp:
                    cl = int(resp.headers.get("content-length", 0))
                    total_size = current_size + cl if resp.status == 206 else cl
                    open_mode = "ab" if (current_size > 0 and resp.status == 206) else "wb"
                    bytes_downloaded = current_size
                    start_time = time.time()
                    last_print = 0

                    with open(output_path, open_mode) as out_f:
                        while True:
                            chunk_start = time.time()
                            chunk = resp.read(512 * 1024)
                            if not chunk:
                                break
                            out_f.write(chunk)
                            bytes_downloaded += len(chunk)

                            # Pacing
                            if max_speed_mb > 0:
                                target_time = len(chunk) / (max_speed_mb * 1024 * 1024)
                                chunk_elapsed = time.time() - chunk_start
                                if chunk_elapsed < target_time:
                                    time.sleep(target_time - chunk_elapsed)

                            now = time.time()
                            if now - last_print >= 0.5:
                                last_print = now
                                elapsed = now - start_time
                                speed = ((bytes_downloaded - current_size) / (1024 * 1024)) / elapsed if elapsed > 0 else 0
                                mb_cur = bytes_downloaded / (1024 * 1024)
                                if total_size > 0:
                                    pct = (bytes_downloaded / total_size) * 100
                                    mb_tot = total_size / (1024 * 1024)
                                    print(f"\r    📥 [{desc}] {pct:5.1f}% ({mb_cur:.1f}/{mb_tot:.1f} MB) at {speed:.2f} MB/s", end="", flush=True)
                                else:
                                    print(f"\r    📥 [{desc}] {mb_cur:.1f} MB at {speed:.2f} MB/s", end="", flush=True)

                    print()
                    if output_path.exists() and output_path.stat().st_size >= min_size:
                        return True
        except urllib.error.HTTPError as e:
            cur_mb = (output_path.stat().st_size / (1024 * 1024)) if output_path.exists() else 0
            if e.code == 429:
                print(f"\n    [!] HTTP 429 (Rate Limited). Saved {cur_mb:.1f} MB. Backing off {backoff}s before resuming (attempt {attempt}/{max_retries})...", file=sys.stderr)
                time.sleep(backoff)
                backoff = min(backoff + 8, 40)
            else:
                print(f"\n    [!] HTTP {e.code} error on attempt {attempt}: {e}. Saved {cur_mb:.1f} MB. Retrying in 5s...", file=sys.stderr)
                time.sleep(5)
        except Exception as e:
            cur_mb = (output_path.stat().st_size / (1024 * 1024)) if output_path.exists() else 0
            print(f"\n    [!] Stream disconnected: {e}. Saved {cur_mb:.1f} MB. Resuming in 5s (attempt {attempt}/{max_retries})...", file=sys.stderr)
            time.sleep(5)

    return output_path.exists() and output_path.stat().st_size >= min_size

def download_file_aria2c(url: str, output_path: Path, connections: int = 1, min_size: int = 1000000, max_speed_mb: float = 10.0) -> bool:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.exists() and output_path.stat().st_size == 0:
        output_path.unlink()
        
    cmd = [
        "aria2c",
        "-x", str(connections),
        "-s", str(connections),
        "-k", "1M",
        f"--max-download-limit={int(max_speed_mb)}M",
        "-d", str(output_path.parent),
        "-o", output_path.name,
        "-U", USER_AGENT,
        "--header=Referer: https://st.111477.xyz/",
        "--check-certificate=false",
        "--allow-overwrite=true",
        "--auto-file-renaming=false",
        "--summary-interval=5",
        "--max-tries=10",
        "--retry-wait=3",
        url
    ]
    proc = subprocess.run(cmd)
    return proc.returncode == 0 and output_path.exists() and output_path.stat().st_size >= min_size

def download_file_curl(url: str, output_path: Path, min_size: int = 1000000, max_speed_mb: float = 10.0) -> bool:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.exists():
        output_path.unlink()

    base_cmd = [
        "curl",
        "-L",
        "--limit-rate", f"{int(max_speed_mb)}M",
        "--retry", "5",
        "--retry-delay", "3",
        "--connect-timeout", "20",
        "-A", USER_AGENT,
        "-H", "Referer: https://st.111477.xyz/",
        "--progress-bar",
        "-o", str(output_path),
        url
    ]

    proc = subprocess.run(base_cmd)
    return proc.returncode == 0 and output_path.exists() and output_path.stat().st_size >= min_size

def download_stream(url: str, output_path: Path, engine: str = "python", connections: int = 1, min_size: int = 1000000, desc: str = "Video", max_speed_mb: float = 10.0, proxy: Optional[str] = None) -> bool:
    if engine == "python":
        return download_file_python(url, output_path, min_size=min_size, desc=desc, max_speed_mb=max_speed_mb, proxy=proxy)
    elif engine == "curl":
        ok = download_file_curl(url, output_path, min_size=min_size, max_speed_mb=max_speed_mb)
        if ok:
            return True
        print("    [!] curl failed, falling back to python stream downloader...", file=sys.stderr)
        return download_file_python(url, output_path, min_size=min_size, desc=desc, max_speed_mb=max_speed_mb, proxy=proxy)
    elif engine == "aria2c":
        res = subprocess.run(["which", "aria2c"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if res.returncode == 0:
            ok = download_file_aria2c(url, output_path, connections=connections, min_size=min_size, max_speed_mb=max_speed_mb)
            if ok:
                return True
            print("    [!] aria2c failed, falling back to python stream downloader...", file=sys.stderr)
    return download_file_python(url, output_path, min_size=min_size, desc=desc, max_speed_mb=max_speed_mb, proxy=proxy)

def extract_and_delay_audio(input_video: Path, output_aac: Path, delay_seconds: float = 1.0, ep_num: int = 1) -> bool:
    """Extracts audio, removes commercial ad if Episode 1, and applies physical silence delay for 100% Netflix lip-sync."""
    delay_ms = int(delay_seconds * 1000)
    if ep_num == 1:
        # Episode 1 contains a 15.8-second commercial ad inserted between 1456.0s and 1471.8s
        # Splice Part 1 (0 to 1456.0s) and Part 2 (1471.8s to end), then apply delay
        filter_str = f"[0:a]asplit=2[a1][a2]; [a1]atrim=0:1456.0,asetpts=PTS-STARTPTS[p1]; [a2]atrim=start=1471.8,asetpts=PTS-STARTPTS[p2]; [p1][p2]concat=n=2:v=0:a=1[acut]; [acut]adelay={delay_ms}|{delay_ms}[aout]"
        cmd = [
            "ffmpeg", "-y",
            "-i", str(input_video),
            "-filter_complex", filter_str,
            "-map", "[aout]",
            "-c:a", "aac", "-b:a", "192k",
            str(output_aac)
        ]
    else:
        if abs(delay_seconds) > 0.01:
            cmd = [
                "ffmpeg", "-y",
                "-i", str(input_video),
                "-vn",
                "-filter:a", f"adelay={delay_ms}|{delay_ms}",
                "-c:a", "aac", "-b:a", "192k",
                str(output_aac)
            ]
        else:
            cmd = [
                "ffmpeg", "-y",
                "-i", str(input_video),
                "-vn", "-c:a", "copy",
                str(output_aac)
            ]
    proc = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return proc.returncode == 0 and output_aac.exists() and output_aac.stat().st_size > 500000

def remux_local_streams(video_path: Path, audio_path: Path, output_mkv: Path, ep_num: int) -> bool:
    cmd = [
        "ffmpeg", "-y",
        "-i", str(video_path),
        "-i", str(audio_path),
        "-map", "0:v:0",
        "-map", "1:a:0",
        "-map", "0:a:0?",
        "-map", "0:s?",
        "-c:v", "copy",
        "-c:a", "copy",
        "-c:s", "copy",
        "-metadata:s:a:0", "language=khm",
        "-metadata:s:a:0", "title=Khmer Dubbed",
        "-metadata:s:a:1", "language=zho",
        "-metadata:s:a:1", "title=Original Mandarin",
        "-disposition:a:0", "default",
        "-disposition:a:1", "none",
        "-metadata", f"title=Three Kingdoms (2010) - Episode {ep_num:02d} [1080p Khmer Dubbed]",
        str(output_mkv)
    ]
    proc = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return proc.returncode == 0 and output_mkv.exists() and output_mkv.stat().st_size > 10000000

def upload_to_pixeldrain(local_file: Path, api_key: str) -> Optional[str]:
    print(f"[*] ⚡ Uploading '{local_file.name}' to PixelDrain...")
    url = f"https://pixeldrain.com/api/file/{urllib.parse.quote(local_file.name)}"
    cmd = [
        "curl",
        "-T", str(local_file),
        "-u", f":{api_key}",
        "--progress-bar",
        url
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    try:
        data = json.loads(proc.stdout)
        file_id = data.get("id")
        if file_id:
            pd_url = f"https://pixeldrain.com/u/{file_id}"
            print(f"[+] 🚀 PixelDrain Upload Successful: {pd_url}")
            return pd_url
    except Exception as e:
        print(f"[-] PixelDrain error: {e}")
    return None

def load_progress(state_file: Path) -> Dict[str, Any]:
    if state_file.exists():
        try:
            return json.loads(state_file.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"completed": [], "pixeldrain_links": {}}

def save_progress(state_file: Path, progress: Dict[str, Any]):
    state_file.write_text(json.dumps(progress, indent=2), encoding="utf-8")

def process_pipeline(start_ep: int, end_ep: int, gdrive_dir: Optional[Path], pixeldrain_key: Optional[str], work_dir: Path, downloader: str = "python", connections: int = 1, audio_delay: float = 1.0, manual_video_url: Optional[str] = None, manual_audio_url: Optional[str] = None, max_speed_mb: float = 10.0, proxy: Optional[str] = None):
    catalog = load_khmer_catalog()
    work_dir.mkdir(parents=True, exist_ok=True)
    
    state_dir = gdrive_dir if gdrive_dir else work_dir
    state_dir.mkdir(parents=True, exist_ok=True)
    state_file = state_dir / "mux_state.json"
    links_file = state_dir / "pixeldrain_links.txt"
    progress = load_progress(state_file)

    active_proxy = get_default_proxy(proxy)

    print(f"\n{'='*80}")
    print(f"🎬 Starting Samkok 1080p Colab Pipeline (Episodes {start_ep} to {end_ep})")
    print(f"📡 1080p Video Source: st.111477.xyz / Manual Override")
    print(f"🎙️  Khmer Audio Source: TheKomsan / Rumble CDN (AAC Stereo)")
    print(f"⏱️  Audio Delay Applied: +{audio_delay:.3f}s (Hardware Physical Padding)")
    print(f"🚀 Speed Limit: {max_speed_mb:.1f} MB/s (Anti-Throttling Protection)")
    if active_proxy:
        print(f"🛡️  Proxy / WARP: {active_proxy}")
    if pixeldrain_key:
        print(f"⚡ PixelDrain Upload: ENABLED")
    if gdrive_dir:
        print(f"📁 Output Target (GDrive): {gdrive_dir}")
    print(f"{'='*80}\n")

    for ep_num in range(start_ep, end_ep + 1):
        if ep_num in progress["completed"]:
            print(f"[✓] Episode {ep_num:02d} already completed. Skipping.")
            continue

        target_name = f"Three.Kingdoms.2010.S01E{ep_num:02d}.1080p.NF.WEB-DL.KhmerDub.mkv"
        final_gdrive_path = (gdrive_dir / target_name) if gdrive_dir else None

        if final_gdrive_path and final_gdrive_path.exists() and final_gdrive_path.stat().st_size > 100000000:
            print(f"[✓] File already exists on Google Drive ({target_name}). Skipping.")
            progress["completed"].append(ep_num)
            save_progress(state_file, progress)
            continue

        print(f"\n--- [ Processing Episode {ep_num:02d} / {end_ep:02d} ] ---")
        ep_data = catalog[ep_num - 1] if catalog and ep_num - 1 < len(catalog) else {}

        temp_raw_video = work_dir / f"raw_1080p_e{ep_num:02d}.mkv"
        temp_audio_mp4 = work_dir / f"raw_komsan_e{ep_num:02d}.mp4"
        temp_khmer_aac = work_dir / f"khmer_audio_e{ep_num:02d}.aac"
        temp_final_mkv = work_dir / target_name

        # 1. Resolve or Use Manual 1080p Video Stream
        video_stream_url = manual_video_url if (manual_video_url and ep_num == start_ep) else None
        if not video_stream_url:
            print(f"[1/4] 🔍 Resolving 1080p stream link via st.111477.xyz...")
            video_stream_url = resolve_1080p_stream_url(ep_num, proxy=active_proxy)
        else:
            print(f"[1/4] 🔗 Using manual video stream URL.")

        if not video_stream_url:
            print(f"[-] Could not resolve 1080p video URL for Episode {ep_num:02d}. Skipping.", file=sys.stderr)
            continue
        print(f"    [+] 1080p Stream URL ready.")
        time.sleep(2)  # Cooldown pause for stream handshake

        print(f"[2/4] 📥 Downloading 1080p Netflix video (~2.4 GB)...")
        if not download_stream(video_stream_url, temp_raw_video, engine=downloader, connections=connections, min_size=50000000, desc="1080p Video", max_speed_mb=max_speed_mb, proxy=active_proxy):
            print(f"[-] Video download failed for Episode {ep_num:02d}.", file=sys.stderr)
            continue

        # 2. Download Khmer Audio Stream & Apply Hardware Sync Delay
        print(f"[3/4] 🎙️ Downloading Khmer audio stream from TheKomsan...")
        audio_stream_url = manual_audio_url or ep_data.get("file")
        if not audio_stream_url:
            print(f"[-] No audio URL found for Episode {ep_num:02d}. Skipping.", file=sys.stderr)
            if temp_raw_video.exists(): temp_raw_video.unlink()
            continue

        if not download_stream(audio_stream_url, temp_audio_mp4, engine=downloader, connections=min(connections, 4), min_size=10000000, desc="Khmer Audio", max_speed_mb=max_speed_mb, proxy=active_proxy):
            print(f"[-] Audio download failed for Episode {ep_num:02d}. Skipping.", file=sys.stderr)
            if temp_raw_video.exists(): temp_raw_video.unlink()
            continue

        print(f"    ⏱️ Applying physical +{audio_delay:.3f}s sync padding to audio...")
        extract_and_delay_audio(temp_audio_mp4, temp_khmer_aac, delay_seconds=audio_delay, ep_num=ep_num)
        if temp_audio_mp4.exists(): temp_audio_mp4.unlink()

        # 3. Losslessly Remux 1080p Video + Khmer Audio + Mandarin + Subtitles
        print(f"[4/4] ⚡ Losslessly remuxing into 1080p Dual-Audio MKV...")
        mux_ok = remux_local_streams(temp_raw_video, temp_khmer_aac, temp_final_mkv, ep_num)
        if temp_raw_video.exists(): temp_raw_video.unlink()
        if temp_khmer_aac.exists(): temp_khmer_aac.unlink()

        if not mux_ok:
            print(f"[-] Remuxing failed for Episode {ep_num:02d}.", file=sys.stderr)
            continue

        # Upload to PixelDrain
        if pixeldrain_key:
            pd_link = upload_to_pixeldrain(temp_final_mkv, pixeldrain_key)
            if pd_link:
                progress.setdefault("pixeldrain_links", {})[str(ep_num)] = pd_link
                with open(links_file, "a", encoding="utf-8") as lf:
                    lf.write(f"Episode {ep_num:02d}: {pd_link}\n")

        # Save to Google Drive if configured
        if final_gdrive_path:
            print(f"[*] 🚀 Copying directly to Google Drive: {final_gdrive_path}")
            temp_final_mkv.replace(final_gdrive_path)
        elif pixeldrain_key and temp_final_mkv.exists():
            temp_final_mkv.unlink()

        # Mark episode completed
        progress["completed"].append(ep_num)
        save_progress(state_file, progress)
        print(f"[✓] Episode {ep_num:02d} completed successfully!")
        time.sleep(2)

    print(f"\n🎉 All requested episodes successfully finished!")

def main():
    parser = argparse.ArgumentParser(
        description="Google Colab 1080p Netflix Three Kingdoms Khmer Dub Remuxer."
    )
    parser.add_argument("-s", "--start", type=int, default=1, help="Starting episode number (default: 1)")
    parser.add_argument("-e", "--end", type=int, default=95, help="Ending episode number (default: 95)")
    parser.add_argument("-p", "--pixeldrain", action="store_true", default=True, help="Enable PixelDrain auto-upload (default: True)")
    parser.add_argument("--no-pixeldrain", dest="pixeldrain", action="store_false", help="Disable PixelDrain auto-upload")
    parser.add_argument("--pixeldrain-key", type=str, default=DEFAULT_PIXELDRAIN_KEY, help="PixelDrain API key")
    parser.add_argument("-g", "--gdrive-dir", type=str, default="/content/drive/MyDrive/ThreeKingdoms_1080p_Khmer", help="Target Google Drive directory (or 'none')")
    parser.add_argument("-w", "--work-dir", type=str, default="/content/samkok_work", help="Working directory for temporary files")
    parser.add_argument("-d", "--downloader", type=str, choices=["python", "curl", "aria2c"], default="python", help="Downloader engine (default: python)")
    parser.add_argument("-c", "--connections", type=int, default=1, help="Number of connections per download")
    parser.add_argument("--delay", type=float, default=DEFAULT_AUDIO_DELAY, help="Audio delay in seconds (default: 1.0)")
    parser.add_argument("--max-speed", type=float, default=10.0, help="Maximum download speed in MB/s to prevent Cloudflare burst limits (default: 10.0)")
    parser.add_argument("--proxy", type=str, default=None, help="Proxy URL (e.g. 'socks5://127.0.0.1:40000' for Cloudflare WARP)")
    parser.add_argument("--video-url", type=str, default=None, help="Manual 1080p video URL override for the episode")
    parser.add_argument("--audio-url", type=str, default=None, help="Manual Khmer audio URL override for the episode")

    args = parser.parse_args()
    
    pd_key = args.pixeldrain_key if args.pixeldrain else None
    gdrive_dir = Path(args.gdrive_dir) if args.gdrive_dir != "none" else None

    process_pipeline(
        start_ep=args.start,
        end_ep=args.end,
        gdrive_dir=gdrive_dir,
        pixeldrain_key=pd_key,
        work_dir=Path(args.work_dir),
        downloader=args.downloader,
        connections=args.connections,
        audio_delay=args.delay,
        manual_video_url=args.video_url,
        manual_audio_url=args.audio_url,
        max_speed_mb=args.max_speed,
        proxy=args.proxy
    )

if __name__ == "__main__":
    main()
