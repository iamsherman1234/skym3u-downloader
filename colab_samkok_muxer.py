#!/usr/bin/env python3
"""
Samkok 1080p Netflix Khmer Dub Muxer for Google Colab
Seamlessly processes episodes in Google Colab:
- Resolves 1080p Netflix stream from st.111477.xyz
- Extracts Khmer AAC audio from OK.ru (movie-khmer.com)
- Losslessly muxes into 1080p Dual Audio MKV
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
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

def load_khmer_catalog() -> List[Dict[str, Any]]:
    candidates = [
        Path("samkok_khmer_episodes.json"),
        Path("/content/skym3u-downloader/samkok_khmer_episodes.json"),
        Path("/root/skym3u-downloader/samkok_khmer_episodes.json")
    ]
    for p in candidates:
        if p.exists():
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                pass
    url = "https://raw.githubusercontent.com/iamsherman1234/skym3u-downloader/main/samkok_khmer_episodes.json"
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
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                streams = data.get("streams", [])
                if streams:
                    return streams[0].get("url")
        except Exception as e:
            print(f"[-] Attempt {attempt+1} failed to resolve 1080p stream for E{ep_num:02d}: {e}")
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

def download_chunked_robust(url: str, output_path: Path, label: str = "File", min_size: int = 1000000, max_retries: int = 10) -> bool:
    """Robust chunked downloader with HTTP Range resumption and retry loop."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    block_size = 2 * 1024 * 1024  # 2 MB chunks

    for attempt in range(max_retries):
        downloaded = output_path.stat().st_size if output_path.exists() else 0
        headers = {"User-Agent": USER_AGENT}
        if downloaded > 0:
            headers["Range"] = f"bytes={downloaded}-"

        req = urllib.request.Request(url, headers=headers)
        start_time = time.time()
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                status = getattr(resp, 'status', 200)
                content_len = int(resp.headers.get("Content-Length", 0))
                
                if status == 206:
                    total_size = downloaded + content_len
                    mode = "ab"
                else:
                    total_size = content_len
                    downloaded = 0
                    mode = "wb"

                with open(output_path, mode) as f:
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
                if output_path.exists() and output_path.stat().st_size >= min_size:
                    return True
        except Exception as e:
            print(f"\n[-] Download hiccup for {label} (attempt {attempt+1}/{max_retries}): {e}")
            time.sleep(3)

    return output_path.exists() and output_path.stat().st_size >= min_size

def extract_aac_from_video(input_video: Path, output_aac: Path) -> bool:
    cmd = [
        "ffmpeg", "-y",
        "-i", str(input_video),
        "-vn", "-c:a", "copy",
        str(output_aac)
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    return proc.returncode == 0 and output_aac.exists() and output_aac.stat().st_size > 500000

def remux_dual_audio(video_path: Path, audio_path: Path, output_mkv: Path, ep_num: int) -> bool:
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

def run_colab_pipeline(start_ep: int, end_ep: int, output_dir: Path, temp_dir: Path):
    catalog = load_khmer_catalog()
    if not catalog:
        print("[-] Catalog could not be loaded. Exiting.", file=sys.stderr)
        return

    output_dir.mkdir(parents=True, exist_ok=True)
    temp_dir.mkdir(parents=True, exist_ok=True)

    print(f"\n{'='*80}")
    print(f"🚀 Google Colab Samkok 1080p Muxer (Episodes {start_ep} to {end_ep})")
    print(f"📁 Destination: {output_dir}")
    print(f"{'='*80}\n")

    for ep_num in range(start_ep, end_ep + 1):
        final_filename = f"Three.Kingdoms.2010.S01E{ep_num:02d}.1080p.NF.WEB-DL.KhmerDub.mkv"
        final_mkv = output_dir / final_filename

        if final_mkv.exists() and final_mkv.stat().st_size > 500000000:
            print(f"[✓] Episode {ep_num:02d} already exists on Google Drive ({final_mkv.stat().st_size / (1024*1024):.1f} MB). Skipping.")
            continue

        print(f"\n--- [ Processing Episode {ep_num:02d} / {end_ep:02d} ] ---")
        ep_data = catalog[ep_num - 1]

        temp_raw_video = temp_dir / f"temp_1080p_e{ep_num:02d}.mkv"
        temp_audio_mp4 = temp_dir / f"temp_audio_e{ep_num:02d}.mp4"
        temp_khmer_aac = temp_dir / f"temp_khmer_e{ep_num:02d}.aac"

        # 1. 1080p Video
        print(f"[1/3] 🔍 Resolving 1080p stream for Episode {ep_num:02d}...")
        v_url = resolve_1080p_stream_url(ep_num)
        if not v_url:
            print(f"[-] Could not resolve 1080p stream for Episode {ep_num:02d}. Skipping.", file=sys.stderr)
            continue

        print(f"    📥 Downloading 1080p Netflix video (~2.4 GB)...")
        if not download_chunked_robust(v_url, temp_raw_video, label=f"Video E{ep_num:02d}", min_size=50000000):
            print(f"[-] Video download failed for Episode {ep_num:02d}. Skipping.", file=sys.stderr)
            continue

        # 2. Khmer Audio
        print(f"[2/3] 🎙️ Resolving Khmer audio for Episode {ep_num:02d}...")
        embed_url = ep_data.get("embed_url") or ep_data.get("source_url")
        a_url = resolve_okru_audio_stream(embed_url)
        if not a_url:
            print(f"[-] Audio link resolution failed for Episode {ep_num:02d}. Skipping.", file=sys.stderr)
            if temp_raw_video.exists(): temp_raw_video.unlink()
            continue

        print(f"    📥 Downloading audio stream (~35 MB)...")
        if not download_chunked_robust(a_url, temp_audio_mp4, label=f"Audio E{ep_num:02d}", min_size=1000000):
            print(f"[-] Audio download failed for Episode {ep_num:02d}. Skipping.", file=sys.stderr)
            if temp_raw_video.exists(): temp_raw_video.unlink()
            continue

        extract_aac_from_video(temp_audio_mp4, temp_khmer_aac)
        if temp_audio_mp4.exists(): temp_audio_mp4.unlink()

        # 3. Losslessly Remux Directly to Google Drive
        print(f"[3/3] ⚡ Losslessly remuxing directly into Google Drive: {final_mkv.name}...")
        mux_ok = remux_dual_audio(temp_raw_video, temp_khmer_aac, final_mkv, ep_num)

        # Cleanup local scratch files
        if temp_raw_video.exists(): temp_raw_video.unlink()
        if temp_khmer_aac.exists(): temp_khmer_aac.unlink()

        if not mux_ok:
            print(f"[-] Remuxing failed for Episode {ep_num:02d}.", file=sys.stderr)
            continue

        size_mb = final_mkv.stat().st_size / (1024 * 1024)
        print(f"[+] 🎉 Successfully saved to Google Drive: {final_mkv.name} ({size_mb:.1f} MB)!")

        time.sleep(2)

    print(f"\n✨ All requested episodes completed successfully!")

def main():
    parser = argparse.ArgumentParser(description="Google Colab 1080p Samkok Khmer Dub Muxer")
    parser.add_argument("-s", "--start", type=int, default=95, help="Start episode (default: 95)")
    parser.add_argument("-e", "--end", type=int, default=95, help="End episode (default: 95)")
    parser.add_argument("-o", "--output-dir", type=str, default="/content/drive/MyDrive/ThreeKingdoms_1080p_Khmer", help="Destination folder in Google Drive")
    parser.add_argument("-t", "--temp-dir", type=str, default="/content/temp_mux_work", help="Local temporary work folder in Colab")

    args = parser.parse_args()
    run_colab_pipeline(
        start_ep=args.start,
        end_ep=args.end,
        output_dir=Path(args.output_dir),
        temp_dir=Path(args.temp_dir)
    )

if __name__ == "__main__":
    main()
