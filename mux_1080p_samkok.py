#!/usr/bin/env python3
"""
1080p Samkok (Three Kingdoms) Lossless Remuxer
Merges the pristine Netflix 1080p WEB-DL video stream with the Khmer Dubbed audio track
without re-encoding (lossless stream copy).
"""

import sys
import os
import re
import json
import argparse
import subprocess
import urllib.request
from pathlib import Path
from typing import List, Dict, Any, Optional

SOURCE_PAGE = "https://movie-khmer.com/samkok-three-kingdoms/"
NETFLIX_BASE_URL = "https://a.111477.xyz/asiandrama/Three.Kingdoms.S01.2010.1080p.NF.WEB-DL.AAC2.0.H264-HHWEB/"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

def get_khmer_episodes() -> List[Dict[str, Any]]:
    """Loads or fetches the Khmer episodes list."""
    json_path = Path("samkok_khmer_episodes.json")
    if not json_path.exists():
        for fallback in [Path("/root/skym3u-downloader/samkok_khmer_episodes.json"), Path("/root/samkok_khmer_episodes.json")]:
            if fallback.exists():
                json_path = fallback
                break

    if json_path.exists():
        try:
            return json.loads(json_path.read_text(encoding="utf-8"))
        except Exception:
            pass

    # Fallback to fetching directly
    from extract_samkok_streams import fetch_raw_episodes_from_page, resolve_okru_direct_stream
    raw = fetch_raw_episodes_from_page(SOURCE_PAGE)
    resolved = []
    for ep in raw:
        r = resolve_okru_direct_stream(ep["file"])
        d = dict(ep)
        d.update(r)
        resolved.append(d)
    return resolved

def remux_episode(ep_num: int, khmer_audio_url: str, output_dir: Path) -> Optional[Path]:
    """Remuxes the 1080p Netflix video with the Khmer audio track into a single MKV."""
    netflix_video_url = f"{NETFLIX_BASE_URL}Three.Kingdoms.S01E{ep_num:02d}.2010.1080p.NF.WEB-DL.AAC2.0.H264-HHWEB.mkv"
    output_file = output_dir / f"Three.Kingdoms.2010.S01E{ep_num:02d}.1080p.NF.WEB-DL.KhmerDub.mkv"

    print(f"\n[*] Processing Episode {ep_num:02d}...")
    print(f"  • Video Source (1080p NF): {netflix_video_url}")
    print(f"  • Khmer Audio Source:     {khmer_audio_url[:60]}...")
    print(f"  • Output Destination:      {output_file}")

    # FFmpeg command for lossless stream copy:
    # Input 0: Netflix 1080p MKV (Video + Original Mandarin Audio)
    # Input 1: Khmer Dubbed MP4 (Khmer Audio Track)
    # Map video from 0, Khmer audio from 1, original audio from 0
    cmd = [
        "ffmpeg", "-y",
        "-user_agent", USER_AGENT,
        "-i", netflix_video_url,
        "-user_agent", USER_AGENT,
        "-i", khmer_audio_url,
        "-map", "0:v:0",
        "-map", "1:a:0",
        "-map", "0:a:0?",
        "-c", "copy",
        "-metadata:s:a:0", "language=khm",
        "-metadata:s:a:0", "title=Khmer Dubbed (Hang Meas / CTN)",
        "-metadata:s:a:1", "language=zho",
        "-metadata:s:a:1", "title=Original Mandarin",
        "-metadata", f"title=Three Kingdoms 2010 - Episode {ep_num:02d} (1080p Khmer Dubbed)",
        str(output_file)
    ]

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode == 0 and output_file.exists() and output_file.stat().st_size > 1000000:
            size_mb = output_file.stat().st_size / (1024 * 1024)
            print(f"[+] ✅ Episode {ep_num:02d} successfully created! ({size_mb:.2f} MB)")
            return output_file
        else:
            print(f"[-] FFmpeg failed for Episode {ep_num:02d}:\n{proc.stderr[-500:]}", file=sys.stderr)
    except Exception as e:
        print(f"[-] Error executing FFmpeg: {e}", file=sys.stderr)
    return None

def main():
    parser = argparse.ArgumentParser(description="Create True 1080p Full HD Samkok MKVs with Khmer Dubbed Audio")
    parser.add_argument("-e", "--episode", type=str, default="1", help="Episode number or range to remux (e.g. '1', '1-5', 'all')")
    parser.add_argument("-o", "--output-dir", type=str, default="./samkok_1080p_khmer", help="Output directory for 1080p MKVs")

    args = parser.parse_args()

    episodes = get_khmer_episodes()
    if not episodes:
        print("[-] Could not load Khmer audio sources.")
        sys.exit(1)

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # Determine which episodes to process
    ep_indices = []
    if args.episode.lower() == "all":
        ep_indices = list(range(1, len(episodes) + 1))
    elif "-" in args.episode:
        start_e, end_e = map(int, args.episode.split("-"))
        ep_indices = list(range(start_e, end_e + 1))
    else:
        ep_indices = [int(args.episode)]

    print(f"[*] Starting 1080p Remux for {len(ep_indices)} episode(s)...")

    for ep_num in ep_indices:
        if 1 <= ep_num <= len(episodes):
            ep_data = episodes[ep_num - 1]
            khmer_url = ep_data.get("direct_mp4") or ep_data.get("file")
            remux_episode(ep_num, khmer_url, out_dir)
        else:
            print(f"[-] Episode {ep_num} is out of range (1 - {len(episodes)}).")

if __name__ == "__main__":
    main()
