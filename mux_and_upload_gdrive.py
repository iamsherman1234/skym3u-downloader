#!/usr/bin/env python3
"""
Samkok 1080p Netflix Muxer & Google Drive Auto-Uploader
Sequentially downloads 1080p Netflix video + Khmer audio, losslessly remuxes into dual-audio MKV,
uploads to Google Drive via rclone, and cleans up local temporary files.
"""

import sys
import os
import re
import json
import time
import argparse
import subprocess
import urllib.request
from pathlib import Path
from typing import List, Dict, Any, Optional

NETFLIX_BASE_URL = "https://a.111477.xyz/asiandrama/Three.Kingdoms.S01.2010.1080p.NF.WEB-DL.AAC2.0.H264-HHWEB/"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

def load_khmer_catalog() -> List[Dict[str, Any]]:
    """Loads the 95-episode Khmer audio catalog."""
    for p in [Path("samkok_khmer_episodes.json"), Path("/root/skym3u-downloader/samkok_khmer_episodes.json")]:
        if p.exists():
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                pass
    print("[-] Error: samkok_khmer_episodes.json not found.", file=sys.stderr)
    return []

def load_progress(state_file: Path) -> Dict[str, Any]:
    """Loads the progress state to allow resumption."""
    if state_file.exists():
        try:
            return json.loads(state_file.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"completed": []}

def save_progress(state_file: Path, progress: Dict[str, Any]):
    """Saves progress state."""
    state_file.write_text(json.dumps(progress, indent=2), encoding="utf-8")

def download_file(url: str, output_path: Path, headers: Dict[str, str] = None) -> bool:
    """Downloads a file with progress indication."""
    req_headers = {"User-Agent": USER_AGENT}
    if headers:
        req_headers.update(headers)
    req = urllib.request.Request(url, headers=req_headers)
    
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            total_size = int(resp.headers.get("Content-Length", 0))
            downloaded = 0
            block_size = 1024 * 1024 # 1MB

            with open(output_path, "wb") as f:
                while True:
                    chunk = resp.read(block_size)
                    if not chunk:
                        break
                    f.write(chunk)
                    downloaded += len(chunk)
                    if total_size > 0:
                        pct = (downloaded / total_size) * 100
                        mb = downloaded / (1024 * 1024)
                        total_mb = total_size / (1024 * 1024)
                        print(f"\r    Downloading: {mb:.1f}/{total_mb:.1f} MB ({pct:.1f}%)", end="", flush=True)
            print()
            return True
    except Exception as e:
        print(f"\n[-] Download failed: {e}", file=sys.stderr)
        if output_path.exists():
            output_path.unlink()
        return False

def remux_streams(video_path: Path, audio_path: Path, output_mkv: Path, ep_num: int) -> bool:
    """Losslessly muxes the 1080p video stream and Khmer audio into dual-audio MKV."""
    cmd = [
        "ffmpeg", "-y",
        "-i", str(video_path),
        "-i", str(audio_path),
        "-map", "0:v:0",
        "-map", "1:a:0",
        "-map", "0:a:0?",
        "-c", "copy",
        "-metadata:s:a:0", "language=khm",
        "-metadata:s:a:0", "title=Khmer Dubbed (Hang Meas / CTN)",
        "-metadata:s:a:1", "language=zho",
        "-metadata:s:a:1", "title=Original Mandarin",
        "-metadata", f"title=Three Kingdoms (2010) - Episode {ep_num:02d} [1080p Dual Audio]",
        str(output_mkv)
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    return proc.returncode == 0 and output_mkv.exists() and output_mkv.stat().st_size > 1000000

def upload_to_rclone(local_file: Path, remote_dest: str) -> bool:
    """Uploads the file to Google Drive using rclone."""
    print(f"[*] Uploading '{local_file.name}' to {remote_dest}...")
    cmd = ["rclone", "copy", str(local_file), remote_dest, "--stats", "5s", "--progress"]
    proc = subprocess.run(cmd)
    return proc.returncode == 0

def process_pipeline(
    start_ep: int,
    end_ep: int,
    remote_dest: Optional[str],
    work_dir: Path,
    raw_video_dir: Optional[Path] = None,
    cookie_str: Optional[str] = None
):
    """Sequentially processes each episode."""
    catalog = load_khmer_catalog()
    if not catalog:
        return

    work_dir.mkdir(parents=True, exist_ok=True)
    state_file = work_dir / "mux_state.json"
    progress = load_progress(state_file)

    print(f"\n{'='*80}")
    print(f"🎬 Starting Samkok 1080p Muxing & GDrive Pipeline (Episodes {start_ep} to {end_ep})")
    print(f"{'='*80}\n")

    for ep_num in range(start_ep, end_ep + 1):
        if ep_num in progress["completed"]:
            print(f"[✓] Episode {ep_num:02d} already completed. Skipping.")
            continue

        print(f"\n--- [ Processing Episode {ep_num:02d} of {end_ep:02d} ] ---")
        ep_data = catalog[ep_num - 1]
        
        temp_video = work_dir / f"temp_video_e{ep_num:02d}.mkv"
        temp_audio = work_dir / f"temp_audio_e{ep_num:02d}.mp4"
        final_mkv = work_dir / f"Three.Kingdoms.2010.S01E{ep_num:02d}.1080p.NF.WEB-DL.KhmerDub.mkv"

        # 1. Obtain 1080p Netflix Video
        has_video = False
        if raw_video_dir:
            # Check local file in raw_video_dir
            candidates = list(raw_video_dir.glob(f"*E{ep_num:02d}*.mkv")) + list(raw_video_dir.glob(f"*E{ep_num:02d}*.mp4"))
            if candidates:
                temp_video = candidates[0]
                print(f"[1/4] 📁 Using local 1080p video file: {temp_video.name}")
                has_video = True

        if not has_video:
            nf_url = f"{NETFLIX_BASE_URL}Three.Kingdoms.S01E{ep_num:02d}.2010.1080p.NF.WEB-DL.AAC2.0.H264-HHWEB.mkv"
            headers = {"Referer": "https://a.111477.xyz/"}
            if cookie_str:
                headers["Cookie"] = cookie_str
            print(f"[1/4] 📥 Downloading 1080p video from {nf_url}...")
            has_video = download_file(nf_url, temp_video, headers)

        if not has_video:
            print(f"[-] Could not obtain 1080p video for Episode {ep_num:02d}. Skipping.", file=sys.stderr)
            continue

        # 2. Download Khmer Audio stream
        khmer_audio_url = ep_data.get("direct_mp4") or ep_data.get("file")
        print(f"[2/4] 📥 Downloading Khmer Audio track (Episode {ep_num:02d})...")
        has_audio = download_file(khmer_audio_url, temp_audio)

        if not has_audio:
            print(f"[-] Could not download Khmer audio for Episode {ep_num:02d}. Skipping.", file=sys.stderr)
            continue

        # 3. Losslessly Mux
        print(f"[3/4] ⚡ Muxing lossless 1080p dual-audio MKV...")
        mux_success = remux_streams(temp_video, temp_audio, final_mkv, ep_num)

        if not mux_success:
            print(f"[-] Muxing failed for Episode {ep_num:02d}.", file=sys.stderr)
            continue

        size_mb = final_mkv.stat().st_size / (1024 * 1024)
        print(f"[+] ✅ Successfully created: {final_mkv.name} ({size_mb:.1f} MB)")

        # 4. Upload to Google Drive (if remote destination provided)
        if remote_dest:
            print(f"[4/4] ☁️ Uploading to Google Drive ({remote_dest})...")
            uploaded = upload_to_rclone(final_mkv, remote_dest)
            if uploaded:
                print(f"[+] 🚀 Uploaded to Google Drive successfully!")
                # Delete local temporary files
                if final_mkv.exists(): final_mkv.unlink()
                if temp_audio.exists(): temp_audio.unlink()
                if not raw_video_dir and temp_video.exists(): temp_video.unlink()
                print(f"[+] 🧹 Cleaned up local temporary files.")
            else:
                print(f"[-] Upload to Google Drive failed.", file=sys.stderr)

        # Mark as completed
        progress["completed"].append(ep_num)
        save_progress(state_file, progress)

        # Rate limiting delay to respect 1-connection/IP rule
        print(f"[*] Sleeping 3 seconds before next episode to respect single-connection rule...")
        time.sleep(3)

    print(f"\n🎉 All requested episodes finished!")

def main():
    parser = argparse.ArgumentParser(
        description="Mux 1080p Netflix Three Kingdoms with Khmer Dub and upload to Google Drive via rclone."
    )
    parser.add_argument("-s", "--start", type=int, default=1, help="Starting episode number (default: 1)")
    parser.add_argument("-e", "--end", type=int, default=95, help="Ending episode number (default: 95)")
    parser.add_argument("-r", "--remote", type=str, default="chumlayan95_google_drive_1773847968:ThreeKingdoms1080p", help="Rclone remote destination (e.g. 'my_gdrive:Samkok1080p')")
    parser.add_argument("-d", "--raw-video-dir", type=str, help="Directory containing pre-downloaded 1080p MKVs from a.111477.xyz")
    parser.add_argument("-c", "--cookie", type=str, help="Cloudflare / session cookie string for direct a.111477.xyz downloads")
    parser.add_argument("-w", "--work-dir", type=str, default="./temp_mux_work", help="Working directory for temporary files")

    args = parser.parse_args()

    raw_dir = Path(args.raw_video_dir) if args.raw_video_dir else None
    process_pipeline(
        start_ep=args.start,
        end_ep=args.end,
        remote_dest=args.remote if args.remote != "" else None,
        work_dir=Path(args.work_dir),
        raw_video_dir=raw_dir,
        cookie_str=args.cookie
    )

if __name__ == "__main__":
    main()
