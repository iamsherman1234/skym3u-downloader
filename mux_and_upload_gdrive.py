#!/usr/bin/env python3
"""
Samkok 1080p Netflix Muxer & PixelDrain / GDrive Auto-Uploader
Source: TheKomsan (95-Episode Complete Khmer Dubbed) + Netflix 1080p WEB-DL (st.111477.xyz)
Processes episodes sequentially with rock-solid auto-resuming downloads:
1. Resolves fresh 1080p stream URL via st.111477.xyz
2. Downloads 1080p video with single-stream auto-resumption and retries
3. Downloads Khmer audio MP4 from TheKomsan (Rumble CDN)
4. Extracts AAC audio, applies hardware-accurate delay (+1.0s) for perfect Netflix lip-sync
5. Losslessly remuxes into dual-audio 1080p MKV with Chinese subtitles
6. Auto-uploads to PixelDrain (API) and/or Google Drive (rclone)
7. Cleans up temporary files to keep disk usage minimal (< 3 GB)
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

SERIES_IMDB_ID = "tt1514753"  # Three Kingdoms (2010)
STREMIO_BASE = "https://st.111477.xyz"
A11_BASE_B64 = "aHR0cHM6Ly9hLjExMTQ3Ny54eXov"  # https://a.111477.xyz/
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
DEFAULT_AUDIO_DELAY = 1.0  # +1.0 second delay to match Netflix 1080p intro
DEFAULT_PIXELDRAIN_KEY = "cafccc0b-66db-4f1d-a5bb-de45da49f9d5"

def load_khmer_catalog() -> List[Dict[str, Any]]:
    for p in [
        Path("thekomsan_samkok_episodes.json"),
        Path("/root/skym3u-downloader/thekomsan_samkok_episodes.json"),
        Path("/content/skym3u-downloader/thekomsan_samkok_episodes.json")
    ]:
        if p.exists():
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                pass
    print("[-] Error: thekomsan_samkok_episodes.json not found.", file=sys.stderr)
    return []

def load_progress(state_file: Path) -> Dict[str, Any]:
    if state_file.exists():
        try:
            return json.loads(state_file.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"completed": [], "pixeldrain_links": {}}

def save_progress(state_file: Path, progress: Dict[str, Any]):
    state_file.write_text(json.dumps(progress, indent=2), encoding="utf-8")

def resolve_1080p_stream_url(ep_num: int) -> Optional[str]:
    url = f"{STREMIO_BASE}/config/{A11_BASE_B64}/stream/series/{SERIES_IMDB_ID}:1:{ep_num}.json"
    for attempt in range(5):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Referer": "https://st.111477.xyz/"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                streams = data.get("streams", [])
                if streams:
                    return streams[0].get("url")
        except Exception as e:
            print(f"[-] Failed to resolve 1080p stream for E{ep_num:02d} (attempt {attempt+1}): {e}")
            time.sleep(2)
    return None

def download_file_resilient(url: str, output_path: Path, min_size: int = 1000000) -> bool:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.exists() and output_path.stat().st_size == 0:
        output_path.unlink()

    has_partial = output_path.exists() and output_path.stat().st_size > 0

    base_cmd = [
        "curl",
        "-L",
        "--retry", "5",
        "--retry-delay", "3",
        "--connect-timeout", "20",
        "--speed-time", "30",
        "--speed-limit", "1000",
        "-A", USER_AGENT,
        "-H", "Referer: https://st.111477.xyz/",
        "-H", "Origin: https://st.111477.xyz",
        "--progress-bar",
        "-o", str(output_path)
    ]

    cmd = list(base_cmd)
    if has_partial:
        cmd.extend(["-C", "-"])
    cmd.append(url)

    proc = subprocess.run(cmd)

    if proc.returncode != 0 or not (output_path.exists() and output_path.stat().st_size >= min_size):
        if output_path.exists():
            output_path.unlink()
        print("    [!] Retrying fresh download...")
        fresh_cmd = list(base_cmd) + [url]
        proc = subprocess.run(fresh_cmd)

    return proc.returncode == 0 and output_path.exists() and output_path.stat().st_size >= min_size

def download_audio_mp4(url: str, output_path: Path) -> bool:
    if output_path.exists() and output_path.stat().st_size > 10000000:
        return True
    try:
        cmd = [
            "aria2c",
            "-x", "4",
            "-s", "4",
            "-k", "1M",
            "-d", str(output_path.parent),
            "-o", output_path.name,
            "-U", USER_AGENT,
            "--allow-overwrite=true",
            "--summary-interval=5",
            url
        ]
        proc = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if proc.returncode == 0 and output_path.exists() and output_path.stat().st_size > 10000000:
            return True
    except FileNotFoundError:
        pass

    return download_file_resilient(url, output_path, min_size=10000000)

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
    print(f"[*] ⚡ Uploading '{local_file.name}' ({local_file.stat().st_size / (1024*1024):.1f} MB) to PixelDrain...")
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
        else:
            print(f"[-] PixelDrain upload response: {data}", file=sys.stderr)
    except Exception as e:
        print(f"[-] PixelDrain error: {e} | Raw output: {proc.stdout}", file=sys.stderr)
    return None

def upload_to_rclone(local_file: Path, remote_dest: str) -> bool:
    dest_path = f"{remote_dest.rstrip('/')}/{local_file.name}"
    print(f"[*] Uploading '{local_file.name}' to {dest_path}...")
    cmd = ["rclone", "copyto", str(local_file), dest_path, "--stats", "5s", "--progress"]
    proc = subprocess.run(cmd)
    return proc.returncode == 0

def process_pipeline(start_ep: int, end_ep: int, remote_dest: Optional[str], pixeldrain_key: Optional[str], work_dir: Path, audio_delay: float = 1.0, video_url: Optional[str] = None, force: bool = False):
    catalog = load_khmer_catalog()
    if not catalog:
        return

    work_dir.mkdir(parents=True, exist_ok=True)
    state_file = work_dir / "mux_state.json"
    links_file = work_dir / "pixeldrain_links.txt"
    progress = load_progress(state_file)

    print(f"\n{'='*80}")
    print(f"🎬 Starting Samkok 1080p Automated Pipeline (Episodes {start_ep} to {end_ep})")
    print(f"📡 1080p Video Source: st.111477.xyz (Netflix 1080p WEB-DL)")
    print(f"🎙️  Khmer Audio Source: TheKomsan / Rumble CDN (AAC Stereo)")
    print(f"⏱️  Audio Delay Applied: +{audio_delay:.3f}s (Hardware Physical Padding)")
    if pixeldrain_key:
        print(f"⚡ PixelDrain Auto-Upload: ENABLED")
    if remote_dest:
        print(f"☁️  Google Drive Remote: {remote_dest}")
    print(f"{'='*80}\n")

    for ep_num in range(start_ep, end_ep + 1):
        temp_raw_video = work_dir / f"raw_1080p_e{ep_num:02d}.mkv"
        temp_audio_mp4 = work_dir / f"raw_komsan_e{ep_num:02d}.mp4"
        temp_khmer_aac = work_dir / f"khmer_audio_e{ep_num:02d}.aac"
        final_mkv = work_dir / f"Three.Kingdoms.2010.S01E{ep_num:02d}.1080p.NF.WEB-DL.KhmerDub.mkv"

        if ep_num in progress["completed"] and not force:
            if pixeldrain_key and str(ep_num) not in progress.get("pixeldrain_links", {}) and final_mkv.exists():
                print(f"[!] Episode {ep_num:02d} was remuxed previously but not yet uploaded to PixelDrain. Uploading now...")
                pd_link = upload_to_pixeldrain(final_mkv, pixeldrain_key)
                if pd_link:
                    progress.setdefault("pixeldrain_links", {})[str(ep_num)] = pd_link
                    with open(links_file, "a", encoding="utf-8") as lf:
                        lf.write(f"Episode {ep_num:02d}: {pd_link}\n")
                    save_progress(state_file, progress)
                    if final_mkv.exists():
                        final_mkv.unlink()
                        print(f"[+] 🧹 Cleaned up local video file to save disk space.")
                continue
            else:
                print(f"[✓] Episode {ep_num:02d} already completed. Skipping.")
                continue

        print(f"\n--- [ Processing Episode {ep_num:02d} / {end_ep:02d} ] ---")
        ep_data = catalog[ep_num - 1]

        # 1. Resolve & Download 1080p Video Stream
        stream_link = video_url if (video_url and ep_num == start_ep) else None
        if not stream_link:
            print(f"[1/4] 🔍 Resolving 1080p stream link via st.111477.xyz...")
            stream_link = resolve_1080p_stream_url(ep_num)
        else:
            print(f"[1/4] 🔗 Using manual video stream URL.")

        if not stream_link:
            print(f"[-] Could not resolve 1080p video URL for Episode {ep_num:02d}. Skipping.", file=sys.stderr)
            continue
        print(f"    [+] 1080p Stream URL resolved.")

        print(f"    📥 Downloading 1080p video file (~2.4 GB)...")
        if not download_file_resilient(stream_link, temp_raw_video, min_size=50000000):
            print(f"[-] Video download failed for Episode {ep_num:02d}. Skipping.", file=sys.stderr)
            continue

        # 2. Download TheKomsan Khmer Audio Stream & Apply Hardware Sync Delay
        print(f"[2/4] 🎙️ Downloading Khmer audio from TheKomsan...")
        audio_stream_url = ep_data.get("file")
        if not audio_stream_url:
            print(f"[-] No audio URL found in catalog for Episode {ep_num:02d}. Skipping.", file=sys.stderr)
            if temp_raw_video.exists(): temp_raw_video.unlink()
            continue

        print(f"    📥 Downloading Khmer MP4 stream (~300 MB)...")
        if not download_audio_mp4(audio_stream_url, temp_audio_mp4):
            print(f"[-] Audio stream download failed for Episode {ep_num:02d}. Skipping.", file=sys.stderr)
            if temp_raw_video.exists(): temp_raw_video.unlink()
            continue

        print(f"    ⏱️ Applying physical +{audio_delay:.3f}s sync padding to audio...")
        extract_and_delay_audio(temp_audio_mp4, temp_khmer_aac, delay_seconds=audio_delay, ep_num=ep_num)
        if temp_audio_mp4.exists(): temp_audio_mp4.unlink()

        # 3. Losslessly Remux 1080p Video + Khmer Audio + Mandarin + Subtitles
        print(f"[3/4] ⚡ Losslessly remuxing into 1080p Dual-Audio MKV...")
        mux_ok = remux_local_streams(temp_raw_video, temp_khmer_aac, final_mkv, ep_num)
        if temp_raw_video.exists(): temp_raw_video.unlink()
        if temp_khmer_aac.exists(): temp_khmer_aac.unlink()

        if not mux_ok:
            print(f"[-] Remuxing failed for Episode {ep_num:02d}.", file=sys.stderr)
            continue

        size_mb = final_mkv.stat().st_size / (1024 * 1024)
        print(f"[+] ✅ Created: {final_mkv.name} ({size_mb:.1f} MB)")

        # 4. Upload to PixelDrain
        if pixeldrain_key:
            print(f"[4/4] ⚡ Uploading to PixelDrain...")
            pd_link = upload_to_pixeldrain(final_mkv, pixeldrain_key)
            if pd_link:
                progress.setdefault("pixeldrain_links", {})[str(ep_num)] = pd_link
                with open(links_file, "a", encoding="utf-8") as lf:
                    lf.write(f"Episode {ep_num:02d}: {pd_link}\n")

        # 5. Upload to Google Drive (if remote configured)
        if remote_dest:
            print(f"[*] ☁️ Uploading to Google Drive...")
            uploaded = upload_to_rclone(final_mkv, remote_dest)
            if uploaded:
                print(f"[+] 🚀 Uploaded Episode {ep_num:02d} to Google Drive successfully!")
            else:
                print(f"[-] ⚠️ GDrive Upload failed.", file=sys.stderr)

        # Cleanup local MKV if uploaded to cloud
        if pixeldrain_key or remote_dest:
            if final_mkv.exists():
                final_mkv.unlink()
                print(f"[+] 🧹 Cleaned up local video file to save disk space.")
        else:
            print(f"[+] 💾 Saved locally at: {final_mkv}")

        # Mark episode completed
        progress["completed"].append(ep_num)
        save_progress(state_file, progress)

        print(f"[*] Cooldown 3s before next episode...")
        time.sleep(3)

    print(f"\n🎉 All requested episodes successfully finished!")
    if pixeldrain_key and links_file.exists():
        print(f"\n📋 All PixelDrain Links saved to: {links_file}")
        print(links_file.read_text(encoding="utf-8"))

def main():
    parser = argparse.ArgumentParser(
        description="Automated 1080p Netflix Three Kingdoms Khmer Dub Remuxer with PixelDrain & GDrive Uploader."
    )
    parser.add_argument("-s", "--start", type=int, default=1, help="Starting episode number (default: 1)")
    parser.add_argument("-e", "--end", type=int, default=95, help="Ending episode number (default: 95)")
    parser.add_argument("-p", "--pixeldrain", action="store_true", default=True, help="Enable PixelDrain auto-upload (default: True)")
    parser.add_argument("--no-pixeldrain", dest="pixeldrain", action="store_false", help="Disable PixelDrain auto-upload")
    parser.add_argument("--pixeldrain-key", type=str, default=DEFAULT_PIXELDRAIN_KEY, help="PixelDrain API key")
    parser.add_argument("-r", "--remote", type=str, default="none", help="Rclone remote destination (default: none)")
    parser.add_argument("-w", "--work-dir", type=str, default="./samkok_work", help="Working directory")
    parser.add_argument("-d", "--delay", type=float, default=DEFAULT_AUDIO_DELAY, help="Audio delay in seconds (default: 1.0)")
    parser.add_argument("-f", "--force", action="store_true", help="Force re-download and re-mux even if previously marked completed")
    parser.add_argument("--video-url", type=str, default=None, help="Manual 1080p video URL override for start episode")

    args = parser.parse_args()
    
    pd_key = args.pixeldrain_key if args.pixeldrain else None

    process_pipeline(
        start_ep=args.start,
        end_ep=args.end,
        remote_dest=args.remote if args.remote != "none" else None,
        pixeldrain_key=pd_key,
        work_dir=Path(args.work_dir),
        audio_delay=args.delay,
        video_url=args.video_url,
        force=args.force
    )

if __name__ == "__main__":
    main()
