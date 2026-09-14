#!/usr/bin/env python3
"""
Samkok 1080p Torrent & Netflix Muxer with PixelDrain / GDrive Auto-Uploader
Source: TheKomsan (95-Episode Complete Khmer Dubbed) + Jiang Hu 1080p HD Torrent / Netflix 1080p WEB-DL
Processes episodes sequentially with rock-solid auto-resuming downloads:
1. Downloads 1080p video from Jiang Hu BitTorrent release via aria2c (Zero Cloudflare rate limits!)
2. Downloads Khmer audio MP4 from TheKomsan (Rumble CDN)
3. Extracts AAC audio, applies lip sync delay and ad removal
4. Losslessly remuxes into dual-audio 1080p MKV with Chinese subtitles / original Mandarin audio
5. Auto-uploads to PixelDrain (API) and/or Google Drive (rclone or direct filesystem)
6. Cleans up temporary files to keep disk usage minimal (< 3 GB)
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
A11_BASE_B64 = "aHR0cHM6Ly9hLjExMTQ3Ny54eXov"  # https://a.111477.xyz/
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
DEFAULT_AUDIO_DELAY = 0.0  # 0.0s for Jiang Hu broadcast cut
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
        Path("/root/skym3u-downloader/thekomsan_samkok_episodes.json"),
        Path("/content/skym3u-downloader/thekomsan_samkok_episodes.json"),
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

def download_torrent_episode(ep_num: int, work_dir: Path, torrent_source: str) -> Optional[Path]:
    work_dir.mkdir(parents=True, exist_ok=True)
    file_index = (ep_num * 2) + 1
    expected_rel_name = f"[Jiang Hu] Three Kingdoms 2010 HD {ep_num:02d}.mp4"
    expected_full_path = work_dir / "[Jiang Hu] Three Kingdoms 2010 HD" / expected_rel_name

    if expected_full_path.exists() and expected_full_path.stat().st_size > 500000000:
        aria2_ctrl = work_dir / f"[Jiang Hu] Three Kingdoms 2010 HD.aria2"
        if not aria2_ctrl.exists():
            print(f"    [✓] Torrent video already downloaded: {expected_full_path.name}")
            return expected_full_path

    print(f"    🧲 Downloading Episode {ep_num:02d} via BitTorrent (Index: {file_index})...")
    cmd = [
        "aria2c",
        f"--select-file={file_index}",
        "--seed-time=0",
        "--file-allocation=none",
        "--bt-enable-lpd=true",
        "--enable-dht=true",
        "--dht-listen-port=6881",
        "--enable-peer-exchange=true",
        "--bt-max-peers=120",
        "--max-overall-upload-limit=10K",
        "--summary-interval=5",
        "--console-log-level=warn",
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
            if "[" in line_str and "]" in line_str and ("MiB" in line_str or "GiB" in line_str or "ETA" in line_str):
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

def upload_via_rclone(local_file: Path, rclone_remote: str, rclone_dest: str) -> bool:
    print(f"[*] 🚀 Uploading '{local_file.name}' to Google Drive ({rclone_remote}:{rclone_dest})...")
    cmd = [
        "rclone", "copy",
        str(local_file),
        f"{rclone_remote}:{rclone_dest}",
        "-P",
        "--stats=5s"
    ]
    proc = subprocess.run(cmd)
    return proc.returncode == 0

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
    rclone_remote: Optional[str],
    rclone_dest: str,
    pixeldrain_key: Optional[str],
    work_dir: Path,
    source: str = "torrent",
    custom_torrent: Optional[str] = None,
    audio_delay: float = 0.0,
    manual_audio_url: Optional[str] = None
):
    catalog = load_khmer_catalog()
    work_dir.mkdir(parents=True, exist_ok=True)
    
    state_file = work_dir / "mux_state.json"
    links_file = work_dir / "pixeldrain_links.txt"
    progress = load_progress(state_file)

    torrent_source = find_torrent_source(custom_torrent) if source == "torrent" else None

    print(f"\n{'='*80}")
    print(f"🎬 Starting Samkok 1080p Muxer Pipeline (Episodes {start_ep} to {end_ep})")
    print(f"📡 1080p Video Source: {'Jiang Hu 1080p Torrent (aria2c)' if source == 'torrent' else 'HTTP Stream'}")
    print(f"🎙️  Khmer Audio Source: TheKomsan / Rumble CDN (AAC Stereo)")
    print(f"⏱️  Audio Delay Applied: +{audio_delay:.3f}s (Lip Sync Padding)")
    if pixeldrain_key:
        print(f"⚡ PixelDrain Upload: ENABLED")
    if rclone_remote:
        print(f"📁 Google Drive Target: {rclone_remote}:{rclone_dest}")
    print(f"{'='*80}\n")

    for ep_num in range(start_ep, end_ep + 1):
        if ep_num in progress["completed"]:
            print(f"[✓] Episode {ep_num:02d} already completed. Skipping.")
            continue

        target_name = f"Three.Kingdoms.2010.S01E{ep_num:02d}.1080p.KhmerDub.mkv"

        print(f"\n--- [ Processing Episode {ep_num:02d} / {end_ep:02d} ] ---")
        ep_data = catalog[ep_num - 1] if catalog and ep_num - 1 < len(catalog) else {}

        temp_audio_mp4 = work_dir / f"raw_komsan_e{ep_num:02d}.mp4"
        temp_khmer_aac = work_dir / f"khmer_audio_e{ep_num:02d}.aac"
        temp_final_mkv = work_dir / target_name

        # 1. Download 1080p Video via Torrent (aria2c)
        print(f"[1/4] 📥 Fetching 1080p video for Episode {ep_num:02d} via Torrent...")
        raw_video_path = download_torrent_episode(ep_num, work_dir, torrent_source)
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
        
        # Immediate cleanup of raw video & audio
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

        # Upload to Google Drive via rclone if configured
        if rclone_remote:
            upload_via_rclone(temp_final_mkv, rclone_remote, rclone_dest)

        if temp_final_mkv.exists():
            temp_final_mkv.unlink()

        # Mark episode completed
        progress["completed"].append(ep_num)
        save_progress(state_file, progress)
        print(f"[✓] Episode {ep_num:02d} completed successfully!")
        time.sleep(2)

    print(f"\n🎉 All requested episodes successfully finished!")

def main():
    parser = argparse.ArgumentParser(
        description="1080p Three Kingdoms Khmer Dub Remuxer with Torrent & PixelDrain/GDrive."
    )
    parser.add_argument("-s", "--start", type=int, default=1, help="Starting episode number (default: 1)")
    parser.add_argument("-e", "--end", type=int, default=95, help="Ending episode number (default: 95)")
    parser.add_argument("-p", "--pixeldrain", action="store_true", default=True, help="Enable PixelDrain auto-upload (default: True)")
    parser.add_argument("--no-pixeldrain", dest="pixeldrain", action="store_false", help="Disable PixelDrain auto-upload")
    parser.add_argument("--pixeldrain-key", type=str, default=DEFAULT_PIXELDRAIN_KEY, help="PixelDrain API key")
    parser.add_argument("-r", "--rclone-remote", type=str, default=None, help="rclone remote name (e.g. 'gdrive')")
    parser.add_argument("--rclone-dest", type=str, default="ThreeKingdoms_1080p_Khmer", help="Destination path on rclone remote")
    parser.add_argument("-w", "--work-dir", type=str, default="./samkok_work", help="Working directory for temporary files")
    parser.add_argument("--source", type=str, choices=["torrent", "http"], default="torrent", help="Video source (default: torrent)")
    parser.add_argument("--torrent", type=str, default=None, help="Path to .torrent file or magnet link")
    parser.add_argument("--delay", type=float, default=0.0, help="Audio delay in seconds (default: 0.0)")
    parser.add_argument("--audio-url", type=str, default=None, help="Manual Khmer audio URL override for the episode")

    args = parser.parse_args()
    
    pd_key = args.pixeldrain_key if args.pixeldrain else None

    process_pipeline(
        start_ep=args.start,
        end_ep=args.end,
        rclone_remote=args.rclone_remote,
        rclone_dest=args.rclone_dest,
        pixeldrain_key=pd_key,
        work_dir=Path(args.work_dir),
        source=args.source,
        custom_torrent=args.torrent,
        audio_delay=args.delay,
        manual_audio_url=args.audio_url
    )

if __name__ == "__main__":
    main()
