#!/usr/bin/env python3
"""
Samkok 1080p Netflix Khmer Dub Muxer for Google Colab
Source: TheKomsan (95-Episode Complete Khmer Dubbed) + Netflix 1080p WEB-DL (st.111477.xyz)
Seamlessly processes episodes in Google Colab:
- Resolves 1080p Netflix stream from st.111477.xyz or uses manual URL override
- Extracts Khmer AAC audio from TheKomsan (Rumble CDN) or manual audio URL
- Losslessly muxes into 1080p Dual Audio MKV with Chinese Subtitles
- Saves directly into mounted Google Drive (/content/drive/MyDrive/ThreeKingdoms_1080p_Khmer)
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

SERIES_IMDB_ID = "tt1514753"  # Three Kingdoms (2010)
STREMIO_BASE = "https://st.111477.xyz"
A11_BASE_B64 = "aHR0cHM6Ly9hLjExMTQ3Ny54eXov"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
AUDIO_SYNC_OFFSET = "0.940"  # Seconds to trim from Khmer audio for Netflix 1080p alignment

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
            print(f"[-] Attempt {attempt+1} failed to resolve 1080p stream for E{ep_num:02d}: {e}")
            time.sleep(2)
    return None

def download_file_curl(url: str, output_path: Path, min_size: int = 1000000) -> bool:
    """Download single-stream via curl with clean error recovery and no Range header conflicts."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    # If 0-byte corrupted file exists, remove it
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

    # If curl failed (e.g. error 33 byte-range unsupported), remove file and retry fresh from offset 0
    if proc.returncode != 0 or not (output_path.exists() and output_path.stat().st_size >= min_size):
        if output_path.exists():
            output_path.unlink()
        print("    [!] Retrying fresh download from start...")
        fresh_cmd = list(base_cmd) + [url]
        proc = subprocess.run(fresh_cmd)

    return proc.returncode == 0 and output_path.exists() and output_path.stat().st_size >= min_size

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
        "-ss", AUDIO_SYNC_OFFSET,
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
        "-t", "2618",
        str(output_mkv)
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    return proc.returncode == 0 and output_mkv.exists() and output_mkv.stat().st_size > 10000000

def load_progress(state_file: Path) -> Dict[str, Any]:
    if state_file.exists():
        try:
            return json.loads(state_file.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"completed": []}

def save_progress(state_file: Path, progress: Dict[str, Any]):
    state_file.write_text(json.dumps(progress, indent=2), encoding="utf-8")

def process_pipeline(start_ep: int, end_ep: int, gdrive_dir: Path, work_dir: Path, manual_video_url: Optional[str] = None, manual_audio_url: Optional[str] = None, manual_urls_file: Optional[Path] = None):
    catalog = load_khmer_catalog()
    manual_urls_map = {}
    if manual_urls_file and manual_urls_file.exists():
        try:
            manual_urls_map = json.loads(manual_urls_file.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"[-] Could not load manual URLs file: {e}")

    work_dir.mkdir(parents=True, exist_ok=True)
    gdrive_dir.mkdir(parents=True, exist_ok=True)
    state_file = gdrive_dir / "mux_state.json"
    progress = load_progress(state_file)

    print(f"\n{'='*80}")
    print(f"🎬 Starting Samkok 1080p Colab Pipeline (Episodes {start_ep} to {end_ep})")
    print(f"📡 1080p Video Source: st.111477.xyz / Manual Override")
    print(f"🎙️  Khmer Audio Source: TheKomsan / Rumble CDN (AAC Stereo)")
    print(f"📁 Output Target (GDrive): {gdrive_dir}")
    print(f"{'='*80}\n")

    for ep_num in range(start_ep, end_ep + 1):
        if ep_num in progress["completed"]:
            print(f"[✓] Episode {ep_num:02d} already completed. Skipping.")
            continue

        target_name = f"Three.Kingdoms.2010.S01E{ep_num:02d}.1080p.NF.WEB-DL.KhmerDub.mkv"
        final_gdrive_path = gdrive_dir / target_name

        if final_gdrive_path.exists() and final_gdrive_path.stat().st_size > 100000000:
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
        video_stream_url = manual_video_url or manual_urls_map.get(str(ep_num)) or manual_urls_map.get(ep_num)
        if not video_stream_url:
            print(f"[1/4] 🔍 Resolving 1080p stream link via st.111477.xyz...")
            video_stream_url = resolve_1080p_stream_url(ep_num)
        else:
            print(f"[1/4] 🔗 Using manual video stream URL.")

        if not video_stream_url:
            print(f"[-] Could not resolve 1080p video URL for Episode {ep_num:02d}. You can pass --video-url \"<url>\". Skipping.", file=sys.stderr)
            continue
        print(f"    [+] 1080p Stream URL ready.")

        print(f"[2/4] 📥 Downloading 1080p Netflix video (~2.4 GB)...")
        if not download_file_curl(video_stream_url, temp_raw_video, min_size=50000000):
            print(f"[-] Video download failed for Episode {ep_num:02d}.", file=sys.stderr)
            print(f"    👉 TIP: If Cloudflare blocks automated download, provide direct link with --video-url \"<url>\".", file=sys.stderr)
            continue

        # 2. Download Khmer Audio Stream
        print(f"[3/4] 🎙️ Downloading Khmer audio stream from TheKomsan...")
        audio_stream_url = manual_audio_url or ep_data.get("file")
        if not audio_stream_url:
            print(f"[-] No audio URL found for Episode {ep_num:02d}. Skipping.", file=sys.stderr)
            if temp_raw_video.exists(): temp_raw_video.unlink()
            continue

        if not download_file_curl(audio_stream_url, temp_audio_mp4, min_size=10000000):
            print(f"[-] Audio download failed for Episode {ep_num:02d}. Skipping.", file=sys.stderr)
            if temp_raw_video.exists(): temp_raw_video.unlink()
            continue

        extract_aac_from_video(temp_audio_mp4, temp_khmer_aac)
        if temp_audio_mp4.exists(): temp_audio_mp4.unlink()

        # 3. Losslessly Remux 1080p Video + Khmer Audio + Mandarin + Subtitles
        print(f"[4/4] ⚡ Losslessly remuxing into 1080p Dual-Audio MKV...")
        mux_ok = remux_local_streams(temp_raw_video, temp_khmer_aac, temp_final_mkv, ep_num)
        if temp_raw_video.exists(): temp_raw_video.unlink()
        if temp_khmer_aac.exists(): temp_khmer_aac.unlink()

        if not mux_ok:
            print(f"[-] Remuxing failed for Episode {ep_num:02d}.", file=sys.stderr)
            continue

        # Move to GDrive directly
        print(f"[*] 🚀 Saving directly to Google Drive: {final_gdrive_path}")
        temp_final_mkv.replace(final_gdrive_path)

        # Mark episode completed
        progress["completed"].append(ep_num)
        save_progress(state_file, progress)
        print(f"[✓] Episode {ep_num:02d} completed and saved!")
        time.sleep(2)

    print(f"\n🎉 All requested episodes successfully finished!")

def main():
    parser = argparse.ArgumentParser(
        description="Google Colab 1080p Netflix Three Kingdoms Khmer Dub Remuxer."
    )
    parser.add_argument("-s", "--start", type=int, default=1, help="Starting episode number (default: 1)")
    parser.add_argument("-e", "--end", type=int, default=95, help="Ending episode number (default: 95)")
    parser.add_argument("-g", "--gdrive-dir", type=str, default="/content/drive/MyDrive/ThreeKingdoms_1080p_Khmer", help="Target Google Drive directory")
    parser.add_argument("-w", "--work-dir", type=str, default="/content/samkok_work", help="Working directory for temporary files")
    parser.add_argument("--video-url", type=str, default=None, help="Manual 1080p video URL override for the episode")
    parser.add_argument("--audio-url", type=str, default=None, help="Manual Khmer audio URL override for the episode")
    parser.add_argument("--manual-urls", type=str, default=None, help="JSON file mapping episode numbers to manual video URLs")

    args = parser.parse_args()
    process_pipeline(
        start_ep=args.start,
        end_ep=args.end,
        gdrive_dir=Path(args.gdrive_dir),
        work_dir=Path(args.work_dir),
        manual_video_url=args.video_url,
        manual_audio_url=args.audio_url,
        manual_urls_file=Path(args.manual_urls) if args.manual_urls else None
    )

if __name__ == "__main__":
    main()
