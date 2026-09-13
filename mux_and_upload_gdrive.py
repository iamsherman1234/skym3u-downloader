#!/usr/bin/env python3
"""
Samkok 1080p Netflix Muxer & Google Drive Auto-Uploader
Sequentially processes 1 episode at a time:
1. Resolves 1080p stream URL via st.111477.xyz API
2. Downloads 1080p video & Khmer AAC audio sequentially (1 connection at a time)
3. Remuxes losslessly into dual-audio 1080p MKV
4. Uploads to Google Drive via rclone
5. Cleans up temporary files to keep disk usage under 3 GB
"""

import sys
import os
import re
import json
import time
import base64
import argparse
import subprocess
import urllib.request
from pathlib import Path
from typing import List, Dict, Any, Optional

SERIES_IMDB_ID = "tt1514753"  # Three Kingdoms (2010)
STREMIO_BASE = "https://st.111477.xyz"
A11_BASE_B64 = "aHR0cHM6Ly9hLjExMTQ3Ny54eXov"  # https://a.111477.xyz/
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

def load_khmer_catalog() -> List[Dict[str, Any]]:
    for p in [Path("samkok_khmer_episodes.json"), Path("/root/skym3u-downloader/samkok_khmer_episodes.json")]:
        if p.exists():
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                pass
    print("[-] Error: samkok_khmer_episodes.json not found.", file=sys.stderr)
    return []

def load_progress(state_file: Path) -> Dict[str, Any]:
    if state_file.exists():
        try:
            return json.loads(state_file.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"completed": []}

def save_progress(state_file: Path, progress: Dict[str, Any]):
    state_file.write_text(json.dumps(progress, indent=2), encoding="utf-8")

def resolve_1080p_stream_url(ep_num: int) -> Optional[str]:
    url = f"{STREMIO_BASE}/config/{A11_BASE_B64}/stream/series/{SERIES_IMDB_ID}:1:{ep_num}.json"
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                streams = data.get("streams", [])
                if streams:
                    return streams[0].get("url")
        except Exception as e:
            print(f"[-] Failed to resolve 1080p stream for E{ep_num:02d} (attempt {attempt+1}): {e}")
            time.sleep(2)
    return None

def resolve_okru_audio_stream(embed_url: str) -> Optional[str]:
    try:
        req = urllib.request.Request(embed_url, headers={"User-Agent": USER_AGENT})
        html = urllib.request.urlopen(req, timeout=12).read().decode("utf-8", errors="ignore")
        m = re.search(r'data-options=["\']([^"\']+)["\']', html)
        if not m:
            return None
        raw_opt = m.group(1).replace("&quot;", '"')
        data = json.loads(raw_opt)
        videos = data.get("flashvars", {}).get("metadata", {}).get("videos", [])
        for v in videos:
            if v.get("name") in ["lowest", "mobile", "low"]:
                return v.get("url")
        if videos:
            return videos[0].get("url")
    except Exception as e:
        print(f"[-] OK.ru stream resolution failed: {e}", file=sys.stderr)
    return None

def download_file(url: str, output_path: Path, label: str = "File") -> bool:
    """Downloads a file sequentially with live progress and speed calculation."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        start_time = time.time()
        with urllib.request.urlopen(req, timeout=30) as resp:
            total_size = int(resp.headers.get("Content-Length", 0))
            downloaded = 0
            block_size = 1024 * 1024  # 1 MB

            with open(output_path, "wb") as f:
                while True:
                    chunk = resp.read(block_size)
                    if not chunk:
                        break
                    f.write(chunk)
                    downloaded += len(chunk)
                    
                    elapsed = time.time() - start_time
                    speed = (downloaded / (1024 * 1024)) / elapsed if elapsed > 0 else 0
                    
                    if total_size > 0:
                        pct = (downloaded / total_size) * 100
                        mb = downloaded / (1024 * 1024)
                        total_mb = total_size / (1024 * 1024)
                        print(f"\r    [{label}] {mb:.1f}/{total_mb:.1f} MB ({pct:.1f}%) @ {speed:.2f} MB/s", end="", flush=True)
                    else:
                        mb = downloaded / (1024 * 1024)
                        print(f"\r    [{label}] {mb:.1f} MB downloaded @ {speed:.2f} MB/s", end="", flush=True)
            print()
            return output_path.exists() and output_path.stat().st_size > 100000
    except Exception as e:
        print(f"\n[-] Download failed for {label}: {e}", file=sys.stderr)
        if output_path.exists():
            output_path.unlink()
        return False

def extract_aac_from_video(input_video: Path, output_aac: Path) -> bool:
    cmd = [
        "ffmpeg", "-y",
        "-i", str(input_video),
        "-vn", "-c:a", "copy",
        str(output_aac)
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    return proc.returncode == 0 and output_aac.exists() and output_aac.stat().st_size > 500000

def remux_local_streams(video_path: Path, audio_path: Path, output_mkv: Path, ep_num: int) -> bool:
    cmd = [
        "ffmpeg", "-y",
        "-i", str(video_path),
        "-i", str(audio_path),
        "-map", "0:v:0",
        "-map", "1:a:0",
        "-map", "0:a:0?",
        "-c:v", "copy",
        "-c:a", "copy",
        "-metadata:s:a:0", "language=khm",
        "-metadata:s:a:0", "title=Khmer Dubbed (Hang Meas)",
        "-metadata:s:a:1", "language=zho",
        "-metadata:s:a:1", "title=Original Mandarin",
        "-disposition:a:0", "default",
        "-disposition:a:1", "none",
        "-metadata", f"title=Three Kingdoms (2010) - Episode {ep_num:02d} [1080p Khmer Dubbed]",
        "-t", "2605",
        str(output_mkv)
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    return proc.returncode == 0 and output_mkv.exists() and output_mkv.stat().st_size > 10000000

def upload_to_rclone(local_file: Path, remote_dest: str) -> bool:
    dest_path = f"{remote_dest.rstrip('/')}/{local_file.name}"
    print(f"[*] Uploading '{local_file.name}' to {dest_path}...")
    cmd = ["rclone", "copyto", str(local_file), dest_path, "--stats", "5s", "--progress"]
    proc = subprocess.run(cmd)
    return proc.returncode == 0

def process_pipeline(start_ep: int, end_ep: int, remote_dest: Optional[str], work_dir: Path):
    catalog = load_khmer_catalog()
    if not catalog:
        return

    work_dir.mkdir(parents=True, exist_ok=True)
    state_file = work_dir / "mux_state.json"
    progress = load_progress(state_file)

    print(f"\n{'='*80}")
    print(f"🎬 Starting Samkok 1080p Automated Pipeline (Episodes {start_ep} to {end_ep})")
    print(f"📡 1080p Video Source: st.111477.xyz (Netflix 1080p WEB-DL)")
    print(f"🎙️  Khmer Audio Source: movie-khmer.com (AAC Stereo)")
    if remote_dest:
        print(f"☁️  Google Drive Remote: {remote_dest}")
    print(f"{'='*80}\n")

    for ep_num in range(start_ep, end_ep + 1):
        if ep_num in progress["completed"]:
            print(f"[✓] Episode {ep_num:02d} already completed. Skipping.")
            continue

        print(f"\n--- [ Processing Episode {ep_num:02d} / {end_ep:02d} ] ---")
        ep_data = catalog[ep_num - 1]

        temp_raw_video = work_dir / f"raw_1080p_e{ep_num:02d}.mkv"
        temp_audio_mp4 = work_dir / f"raw_audio_e{ep_num:02d}.mp4"
        temp_khmer_aac = work_dir / f"khmer_audio_e{ep_num:02d}.aac"
        final_mkv = work_dir / f"Three.Kingdoms.2010.S01E{ep_num:02d}.1080p.NF.WEB-DL.KhmerDub.mkv"

        # 1. Resolve & Download 1080p Video Stream
        print(f"[1/4] 🔍 Resolving 1080p stream link via st.111477.xyz...")
        video_stream_url = resolve_1080p_stream_url(ep_num)
        if not video_stream_url:
            print(f"[-] Could not resolve 1080p video URL for Episode {ep_num:02d}. Skipping.", file=sys.stderr)
            continue
        print(f"    [+] 1080p Stream URL resolved.")

        print(f"    📥 Downloading 1080p video file (~2.4 GB)...")
        if not download_file(video_stream_url, temp_raw_video, label=f"Video E{ep_num:02d}"):
            print(f"[-] Video download failed for Episode {ep_num:02d}. Skipping.", file=sys.stderr)
            continue

        # 2. Resolve & Extract Khmer Audio
        print(f"[2/4] 🎙️ Extracting Khmer AAC audio from OK.ru...")
        embed_url = ep_data.get("embed_url") or ep_data.get("source_url")
        audio_stream_url = resolve_okru_audio_stream(embed_url)
        if not audio_stream_url:
            print(f"[-] Could not resolve OK.ru audio stream for Episode {ep_num:02d}. Skipping.", file=sys.stderr)
            if temp_raw_video.exists(): temp_raw_video.unlink()
            continue

        print(f"    📥 Downloading audio stream (~35 MB)...")
        if not download_file(audio_stream_url, temp_audio_mp4, label=f"Audio E{ep_num:02d}"):
            print(f"[-] Audio stream download failed for Episode {ep_num:02d}. Skipping.", file=sys.stderr)
            if temp_raw_video.exists(): temp_raw_video.unlink()
            continue

        extract_aac_from_video(temp_audio_mp4, temp_khmer_aac)
        if temp_audio_mp4.exists(): temp_audio_mp4.unlink()

        # 3. Losslessly Remux 1080p Video + Khmer Audio
        print(f"[3/4] ⚡ Losslessly remuxing into 1080p Dual-Audio MKV...")
        mux_ok = remux_local_streams(temp_raw_video, temp_khmer_aac, final_mkv, ep_num)
        if temp_raw_video.exists(): temp_raw_video.unlink()
        if temp_khmer_aac.exists(): temp_khmer_aac.unlink()

        if not mux_ok:
            print(f"[-] Remuxing failed for Episode {ep_num:02d}.", file=sys.stderr)
            continue

        size_mb = final_mkv.stat().st_size / (1024 * 1024)
        print(f"[+] ✅ Created: {final_mkv.name} ({size_mb:.1f} MB)")

        # 4. Upload to Google Drive (if remote configured)
        if remote_dest:
            print(f"[4/4] ☁️ Uploading to Google Drive...")
            uploaded = upload_to_rclone(final_mkv, remote_dest)
            if uploaded:
                print(f"[+] 🚀 Uploaded Episode {ep_num:02d} successfully!")
                if final_mkv.exists(): final_mkv.unlink()
                print(f"[+] 🧹 Cleaned up local temporary files.")
            else:
                print(f"[-] ⚠️ Upload failed. File retained locally at {final_mkv}", file=sys.stderr)
        else:
            print(f"[+] 💾 Saved locally at: {final_mkv}")

        # Mark episode completed
        progress["completed"].append(ep_num)
        save_progress(state_file, progress)

        print(f"[*] Cooldown 3s before next episode...")
        time.sleep(3)

    print(f"\n🎉 All requested episodes successfully finished!")

def main():
    parser = argparse.ArgumentParser(
        description="Automated 1080p Netflix Three Kingdoms Khmer Dub Remuxer & GDrive Uploader."
    )
    parser.add_argument("-s", "--start", type=int, default=1, help="Starting episode number (default: 1)")
    parser.add_argument("-e", "--end", type=int, default=95, help="Ending episode number (default: 95)")
    parser.add_argument("-r", "--remote", type=str, default="chumlayan95_google_drive_1773847968:ThreeKingdoms_1080p_Khmer", help="Rclone remote destination")
    parser.add_argument("-w", "--work-dir", type=str, default="/root/samkok_1080p_work", help="Working directory")

    args = parser.parse_args()
    process_pipeline(
        start_ep=args.start,
        end_ep=args.end,
        remote_dest=args.remote if args.remote != "none" else None,
        work_dir=Path(args.work_dir)
    )

if __name__ == "__main__":
    main()
