#!/usr/bin/env python3
"""
Samkok 1080p Colab Remuxer & PixelDrain / GDrive Auto-Uploader
Source: Jiang Hu 1080p HD (Direct CDN URLs from urleps8795.txt or Torrent) + TheKomsan Khmer Dub
Features:
- Fast multi-connection downloading via direct CDN links (urleps8795.txt) with auto-fallback to Torrent
- Extracts Khmer AAC audio from TheKomsan (Rumble CDN)
- Applies audio sync delay (+0.0s for Jiang Hu TV cut) and ad cutting
- Losslessly remuxes into 1080p Dual Audio MKV with Chinese Subtitles / Original Audio
- Auto-uploads directly to PixelDrain and/or mounted Google Drive
- Sequential processing with immediate temp file cleanup (< 3 GB disk usage)
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
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
DEFAULT_PIXELDRAIN_KEY = "cafccc0b-66db-4f1d-a5bb-de45da49f9d5"

DEFAULT_MAGNET_LINK = (
    "magnet:?xt=urn:btih:E02118B52E1818B89C4EF6CEAC62BC68FF55241E"
    "&dn=%5B1080p%5D%20Three%20Kingdoms%202010%20(HC%20English%20Subtitles)"
    "&tr=UDP://TRACKER.OPENTRACKR.ORG:1337/ANNOUNCE"
    "&tr=udp://tracker.openbittorrent.com:6969/announce"
    "&tr=udp://open.stealth.si:80/announce"
    "&tr=udp://www.torrent.eu.org:451/announce"
    "&tr=udp://tracker.torrent.eu.org:451/announce"
    "&tr=udp://opentracker.i2p.rocks:6969/announce"
    "&tr=https://opentracker.i2p.rocks:443/announce"
    "&tr=udp://ipv4.tracker.harry.lu:80/announce"
    "&tr=udp://exodus.desync.com:6969/announce"
    "&tr=udp://tracker.tiny-vps.com:6969/announce"
    "&tr=udp://opentor.org:2710/announce"
    "&tr=udp://tracker.dler.org:6969/announce"
    "&tr=udp://explodie.org:6969/announce"
    "&tr=udp://tracker.opentrackr.org:1337/announce"
    "&tr=http://tracker.openbittorrent.com:80/announce"
    "&tr=udp://opentracker.i2p.rocks:6969/announce"
    "&tr=udp://tracker.internetwarriors.net:1337/announce"
    "&tr=udp://tracker.leechers-paradise.org:6969/announce"
    "&tr=udp://coppersurfer.tk:6969/announce"
    "&tr=udp://tracker.zer0day.to:1337/announce"
)

def load_khmer_catalog() -> List[Dict[str, Any]]:
    candidates = [
        Path("thekomsan_samkok_episodes.json"),
        Path("/content/skym3u-downloader/thekomsan_samkok_episodes.json"),
        Path("/root/skym3u-downloader/thekomsan_samkok_episodes.json"),
        Path(__file__).parent / "thekomsan_samkok_episodes.json" if "__file__" in globals() else None
    ]
    for p in candidates:
        if p and p.exists():
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

def load_url_file(custom_path: Optional[str] = None) -> Dict[int, str]:
    candidates = []
    if custom_path:
        candidates.append(Path(custom_path))
    candidates.extend([
        Path("urleps8795.json"),
        Path("urleps8795.txt"),
        Path("/content/skym3u-downloader/urleps8795.json"),
        Path("/content/skym3u-downloader/urleps8795.txt"),
        Path("/root/skym3u-downloader/urleps8795.json"),
        Path("/root/skym3u-downloader/urleps8795.txt"),
        Path(__file__).parent / "urleps8795.json" if "__file__" in globals() else None,
        Path(__file__).parent / "urleps8795.txt" if "__file__" in globals() else None,
    ])

    for p in candidates:
        if p and p.exists():
            try:
                content = p.read_text(encoding="utf-8").strip()
                # Try JSON format
                try:
                    data = json.loads(content)
                    if isinstance(data, dict):
                        res = {int(k): v.strip() for k, v in data.items() if str(k).isdigit() and v.strip().startswith("http")}
                        if res:
                            print(f"[+] Loaded {len(res)} direct episode URLs from '{p.name}'")
                            return res
                except Exception:
                    pass

                # Try Line-by-line format
                mapping = {}
                for line in content.splitlines():
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    m = re.match(r"^(\d+)\s*[:=\s]\s*(https?://\S+)", line)
                    if m:
                        mapping[int(m.group(1))] = m.group(2).strip()
                if mapping:
                    print(f"[+] Loaded {len(mapping)} direct episode URLs from '{p.name}'")
                    return mapping
            except Exception as e:
                print(f"[-] Warning parsing {p}: {e}", file=sys.stderr)
    return {}

def find_torrent_source(custom_torrent: Optional[str] = None) -> str:
    if custom_torrent:
        if Path(custom_torrent).exists():
            return str(Path(custom_torrent).resolve())
        return custom_torrent

    candidates = [
        Path("samkok_1080p.torrent"),
        Path("e02118b52e1818b89c4ef6ceac62bc68ff55241e.torrent"),
        Path("/content/skym3u-downloader/samkok_1080p.torrent"),
        Path("/content/skym3u-downloader/e02118b52e1818b89c4ef6ceac62bc68ff55241e.torrent"),
        Path("/root/skym3u-downloader/samkok_1080p.torrent"),
        Path("/root/skym3u-downloader/e02118b52e1818b89c4ef6ceac62bc68ff55241e.torrent"),
    ]
    for p in candidates:
        if p.exists() and p.stat().st_size > 1000:
            return str(p.resolve())

    return DEFAULT_MAGNET_LINK

def download_http_aria2c(url: str, output_path: Path, connections: int = 4, min_size: int = 50000000) -> bool:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.exists() and output_path.stat().st_size >= min_size:
        return True

    cmd = [
        "aria2c",
        "-x", str(connections),
        "-s", str(connections),
        "-k", "2M",
        "-d", str(output_path.parent),
        "-o", output_path.name,
        "-U", USER_AGENT,
        "--check-certificate=false",
        "--allow-overwrite=true",
        "--auto-file-renaming=false",
        "--summary-interval=3",
        "--max-tries=5",
        "--retry-wait=2",
        url
    ]
    try:
        proc = subprocess.run(cmd)
        return proc.returncode == 0 and output_path.exists() and output_path.stat().st_size >= min_size
    except Exception as e:
        print(f"    [-] aria2c HTTP error: {e}", file=sys.stderr)
        return False

def download_file_python(url: str, output_path: Path, min_size: int = 1000000, desc: str = "Audio") -> bool:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.exists() and output_path.stat().st_size >= min_size:
        return True

    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "*/*",
    }
    
    start_time = time.time()
    for attempt in range(1, 6):
        try:
            if HAS_CURL_CFFI:
                r = cffi_requests.get(url, headers=headers, impersonate="chrome", timeout=60, stream=True)
                if r.status_code not in (200, 206):
                    raise Exception(f"HTTP {r.status_code}")
                total_size = int(r.headers.get("content-length", 0))
                downloaded = 0
                with open(output_path, "wb") as f:
                    for chunk in r.iter_content(chunk_size=1024*1024):
                        if chunk:
                            f.write(chunk)
                            downloaded += len(chunk)
                            elapsed = time.time() - start_time
                            speed = (downloaded / (1024 * 1024)) / elapsed if elapsed > 0 else 0
                            mb_cur = downloaded / (1024 * 1024)
                            if total_size > 0:
                                pct = (downloaded / total_size) * 100
                                mb_tot = total_size / (1024 * 1024)
                                print(f"\r    📥 [{desc}] {pct:5.1f}% ({mb_cur:.1f}/{mb_tot:.1f} MB) at {speed:.2f} MB/s", end="", flush=True)
                            else:
                                print(f"\r    📥 [{desc}] {mb_cur:.1f} MB at {speed:.2f} MB/s", end="", flush=True)
            else:
                req = urllib.request.Request(url, headers=headers)
                with urllib.request.urlopen(req, timeout=60) as resp:
                    total_size = int(resp.headers.get("Content-Length", 0))
                    downloaded = 0
                    with open(output_path, "wb") as f:
                        while True:
                            chunk = resp.read(1024 * 1024)
                            if not chunk:
                                break
                            f.write(chunk)
                            downloaded += len(chunk)
                            elapsed = time.time() - start_time
                            speed = (downloaded / (1024 * 1024)) / elapsed if elapsed > 0 else 0
                            mb_cur = downloaded / (1024 * 1024)
                            if total_size > 0:
                                pct = (downloaded / total_size) * 100
                                mb_tot = total_size / (1024 * 1024)
                                print(f"\r    📥 [{desc}] {pct:5.1f}% ({mb_cur:.1f}/{mb_tot:.1f} MB) at {speed:.2f} MB/s", end="", flush=True)
                            else:
                                print(f"\r    📥 [{desc}] {mb_cur:.1f} MB at {speed:.2f} MB/s", end="", flush=True)
            print()
            return output_path.exists() and output_path.stat().st_size >= min_size
        except Exception as e:
            print(f"\n    [!] Download attempt {attempt} failed: {e}. Retrying...", file=sys.stderr)
            time.sleep(3)

    return False

def download_torrent_aria2c(ep_num: int, work_dir: Path, torrent_source: str) -> Optional[Path]:
    work_dir.mkdir(parents=True, exist_ok=True)
    file_index = (ep_num * 2) + 1
    expected_rel_name = f"[Jiang Hu] Three Kingdoms 2010 HD {ep_num:02d}.mp4"
    expected_full_path = work_dir / "[Jiang Hu] Three Kingdoms 2010 HD" / expected_rel_name

    if expected_full_path.exists() and expected_full_path.stat().st_size > 500000000:
        aria2_ctrl = work_dir / f"[Jiang Hu] Three Kingdoms 2010 HD.aria2"
        if not aria2_ctrl.exists():
            print(f"    [✓] Torrent video already downloaded: {expected_full_path.name}")
            return expected_full_path

    print(f"    🧲 Downloading Episode {ep_num:02d} via BitTorrent aria2c (Index: {file_index})...")
    
    dht_file = Path("/tmp/dht.dat")
    if not dht_file.exists():
        try: dht_file.touch()
        except Exception: pass

    cmd = [
        "aria2c",
        f"--select-file={file_index}",
        "--seed-time=0",
        "--file-allocation=none",
        "--disable-ipv6=true",
        "--bt-enable-lpd=true",
        "--enable-dht=true",
        "--dht-listen-port=6881-6999",
        "--listen-port=6881-6999",
        f"--dht-file-path={dht_file}",
        "--dht-entry-point=router.bittorrent.com:6881",
        "--dht-entry-point=dht.transmissionbt.com:6881",
        "--dht-entry-point=router.utorrent.com:6881",
        "--enable-peer-exchange=true",
        "--bt-max-peers=150",
        "--max-overall-upload-limit=10K",
        "--summary-interval=3",
        "--console-log-level=warn",
        "--peer-id-prefix=-TR3000-",
        "--user-agent=Transmission/3.00",
        f"--dir={work_dir}",
        torrent_source
    ]

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            universal_newlines=True
        )

        for line in proc.stdout:
            line_str = line.strip()
            if not line_str:
                continue
            if "[" in line_str and "]" in line_str and ("MiB" in line_str or "GiB" in line_str or "ETA" in line_str or "DL:" in line_str):
                print(f"\r    📥 [Torrent E{ep_num:02d}] {line_str}", end="", flush=True)
            elif "Download complete" in line_str or "Seeds:" in line_str:
                print(f"\n    [aria2] {line_str}")

        proc.wait()
        print()

        if expected_full_path.exists() and expected_full_path.stat().st_size > 500000000:
            print(f"    [+] Successfully downloaded: {expected_full_path.name} ({expected_full_path.stat().st_size / (1024*1024):.1f} MB)")
            return expected_full_path
        else:
            alt_path = work_dir / expected_rel_name
            if alt_path.exists() and alt_path.stat().st_size > 500000000:
                return alt_path
            print(f"    [-] Expected torrent output file not found or incomplete: {expected_full_path}", file=sys.stderr)
            return None

    except Exception as e:
        print(f"    [-] aria2c execution error: {e}", file=sys.stderr)
        return None

def extract_and_delay_audio(input_video: Path, output_aac: Path, delay_seconds: float = 0.0, ep_num: int = 1) -> bool:
    delay_ms = int(delay_seconds * 1000)
    if ep_num == 1:
        filter_str = f"[0:a]asplit=2[a1][a2]; [a1]atrim=0:1456.0,asetpts=PTS-STARTPTS[p1]; [a2]atrim=start=1471.8,asetpts=PTS-STARTPTS[p2]; [p1][p2]concat=n=2:v=0:a=1[acut]; [acut]adelay={delay_ms}|{delay_ms}[aout]" if abs(delay_seconds) > 0.001 else f"[0:a]asplit=2[a1][a2]; [a1]atrim=0:1456.0,asetpts=PTS-STARTPTS[p1]; [a2]atrim=start=1471.8,asetpts=PTS-STARTPTS[p2]; [p1][p2]concat=n=2:v=0:a=1[aout]"
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

def process_pipeline(
    start_ep: int,
    end_ep: int,
    gdrive_dir: Optional[Path],
    pixeldrain_key: Optional[str],
    work_dir: Path,
    url_file: Optional[str] = None,
    custom_video_url: Optional[str] = None,
    custom_torrent: Optional[str] = None,
    audio_delay: float = 0.0,
    manual_audio_url: Optional[str] = None
):
    catalog = load_khmer_catalog()
    url_map = load_url_file(url_file)
    work_dir.mkdir(parents=True, exist_ok=True)
    
    state_dir = gdrive_dir if gdrive_dir else work_dir
    state_dir.mkdir(parents=True, exist_ok=True)
    state_file = state_dir / "mux_state.json"
    links_file = state_dir / "pixeldrain_links.txt"
    progress = load_progress(state_file)

    torrent_source = find_torrent_source(custom_torrent)

    print(f"\n{'='*80}")
    print(f"🎬 Starting Samkok 1080p Colab Pipeline (Episodes {start_ep} to {end_ep})")
    print(f"📡 1080p Video Source: Direct CDN URLs / Torrent Fallback")
    print(f"🎙️  Khmer Audio Source: TheKomsan / Rumble CDN (AAC Stereo)")
    print(f"⏱️  Audio Delay Applied: +{audio_delay:.3f}s (Lip Sync Padding)")
    if pixeldrain_key:
        print(f"⚡ PixelDrain Upload: ENABLED")
    if gdrive_dir:
        print(f"📁 Output Target (GDrive): {gdrive_dir}")
    print(f"{'='*80}\n")

    for ep_num in range(start_ep, end_ep + 1):
        if ep_num in progress["completed"]:
            print(f"[✓] Episode {ep_num:02d} already completed. Skipping.")
            continue

        target_name = f"Three.Kingdoms.2010.S01E{ep_num:02d}.1080p.KhmerDub.mkv"
        final_gdrive_path = (gdrive_dir / target_name) if gdrive_dir else None

        if final_gdrive_path and final_gdrive_path.exists() and final_gdrive_path.stat().st_size > 100000000:
            print(f"[✓] File already exists on Google Drive ({target_name}). Skipping.")
            progress["completed"].append(ep_num)
            save_progress(state_file, progress)
            continue

        print(f"\n--- [ Processing Episode {ep_num:02d} / {end_ep:02d} ] ---")
        ep_data = catalog[ep_num - 1] if catalog and ep_num - 1 < len(catalog) else {}

        temp_raw_video = work_dir / f"raw_1080p_e{ep_num:02d}.mp4"
        temp_audio_mp4 = work_dir / f"raw_komsan_e{ep_num:02d}.mp4"
        temp_khmer_aac = work_dir / f"khmer_audio_e{ep_num:02d}.aac"
        temp_final_mkv = work_dir / target_name

        # 1. Download 1080p Video
        direct_url = custom_video_url if (custom_video_url and ep_num == start_ep) else url_map.get(ep_num)
        raw_video_path = None

        if direct_url:
            print(f"[1/4] 🚀 Downloading 1080p video from Direct CDN link...")
            ok = download_http_aria2c(direct_url, temp_raw_video, connections=4, min_size=50000000)
            if not ok:
                print("    [!] aria2c failed, trying direct python stream downloader...", file=sys.stderr)
                ok = download_file_python(direct_url, temp_raw_video, min_size=50000000, desc="1080p Video")
            if ok:
                raw_video_path = temp_raw_video
            else:
                print(f"    [-] Direct CDN download failed for Episode {ep_num:02d}. Trying torrent fallback...", file=sys.stderr)

        if not raw_video_path:
            print(f"[1/4] 🧲 Fetching 1080p video for Episode {ep_num:02d} via BitTorrent...")
            raw_video_path = download_torrent_aria2c(ep_num, work_dir, torrent_source)

        if not raw_video_path or not raw_video_path.exists():
            print(f"[-] Video download failed for Episode {ep_num:02d}. Skipping.", file=sys.stderr)
            continue

        # 2. Download Khmer Audio Stream from TheKomsan
        print(f"[2/4] 🎙️ Downloading Khmer audio stream from TheKomsan...")
        audio_stream_url = manual_audio_url or ep_data.get("file")
        if not audio_stream_url:
            print(f"[-] No audio URL found for Episode {ep_num:02d}. Skipping.", file=sys.stderr)
            if raw_video_path.exists(): raw_video_path.unlink()
            continue

        if not download_file_python(audio_stream_url, temp_audio_mp4, min_size=10000000, desc="Khmer Audio"):
            print(f"[-] Audio download failed for Episode {ep_num:02d}. Skipping.", file=sys.stderr)
            if raw_video_path.exists(): raw_video_path.unlink()
            continue

        # 3. Apply Audio Delay & Ad Cut
        print(f"[3/4] ⏱️ Applying +{audio_delay:.3f}s sync padding to audio...")
        extract_and_delay_audio(temp_audio_mp4, temp_khmer_aac, delay_seconds=audio_delay, ep_num=ep_num)
        if temp_audio_mp4.exists(): temp_audio_mp4.unlink()

        # 4. Losslessly Remux 1080p Video + Khmer Audio + Original Audio
        print(f"[4/4] ⚡ Losslessly remuxing into 1080p Dual-Audio MKV...")
        mux_ok = remux_local_streams(raw_video_path, temp_khmer_aac, temp_final_mkv, ep_num)
        
        # Immediate cleanup of raw video & audio to keep disk usage strictly under 3GB
        if raw_video_path.exists():
            raw_video_path.unlink()
        if temp_khmer_aac.exists():
            temp_khmer_aac.unlink()
        for f in work_dir.glob("*.aria2"):
            try: f.unlink()
            except Exception: pass

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
        description="Google Colab 1080p Three Kingdoms Khmer Dub Remuxer (Direct CDN & Torrent)."
    )
    parser.add_argument("-s", "--start", type=int, default=87, help="Starting episode number (default: 87)")
    parser.add_argument("-e", "--end", type=int, default=95, help="Ending episode number (default: 95)")
    parser.add_argument("-p", "--pixeldrain", action="store_true", default=True, help="Enable PixelDrain auto-upload (default: True)")
    parser.add_argument("--no-pixeldrain", dest="pixeldrain", action="store_false", help="Disable PixelDrain auto-upload")
    parser.add_argument("--pixeldrain-key", type=str, default=DEFAULT_PIXELDRAIN_KEY, help="PixelDrain API key")
    parser.add_argument("-g", "--gdrive-dir", type=str, default="/content/drive/MyDrive/ThreeKingdoms_1080p_Khmer", help="Target Google Drive directory (or 'none')")
    parser.add_argument("-w", "--work-dir", type=str, default="/content/samkok_work", help="Working directory for temporary files")
    parser.add_argument("--url-file", type=str, default=None, help="Custom path to URL mapping file (e.g. urleps8795.txt or urleps8795.json)")
    parser.add_argument("--video-url", type=str, default=None, help="Manual direct video URL override")
    parser.add_argument("--torrent", type=str, default=None, help="Path to .torrent file or magnet link fallback")
    parser.add_argument("--delay", type=float, default=0.0, help="Audio delay in seconds (default: 0.0)")
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
        url_file=args.url_file,
        custom_video_url=args.video_url,
        custom_torrent=args.torrent,
        audio_delay=args.delay,
        manual_audio_url=args.audio_url
    )

if __name__ == "__main__":
    main()
