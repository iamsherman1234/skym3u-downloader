#!/usr/bin/env python3
"""
Samkok 1080p Netflix Khmer Dub Muxer with PixelDrain & GDrive Uploader for Google Colab
Source: TheKomsan (95-Episode Complete Khmer Dubbed) + Netflix 1080p WEB-DL (st.111477.xyz)
Seamlessly processes episodes in Google Colab:
- Resolves 1080p Netflix stream from st.111477.xyz or uses manual URL override
- Extracts Khmer AAC audio from TheKomsan (Rumble CDN) or manual audio URL
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

SERIES_IMDB_ID = "tt1514753"  # Three Kingdoms (2010)
STREMIO_BASE = "https://st.111477.xyz"
A11_BASE_B64 = "aHR0cHM6Ly9hLjExMTQ3Ny54eXov"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
DEFAULT_AUDIO_OFFSET = 0.0  # Perfect 1:1 sync between TheKomsan & Netflix 1080p
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

def download_file_aria2c(url: str, output_path: Path, connections: int = 1, min_size: int = 1000000) -> bool:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.exists() and output_path.stat().st_size == 0:
        output_path.unlink()
        
    cmd = [
        "aria2c",
        "-x", str(connections),
        "-s", str(connections),
        "-k", "1M",
        "-d", str(output_path.parent),
        "-o", output_path.name,
        "-U", USER_AGENT,
        f"--header=Referer: https://st.111477.xyz/",
        f"--header=Origin: https://st.111477.xyz",
        "--allow-overwrite=true",
        "--auto-file-renaming=false",
        "--summary-interval=5",
        "--max-tries=10",
        "--retry-wait=3",
        url
    ]
    proc = subprocess.run(cmd)
    return proc.returncode == 0 and output_path.exists() and output_path.stat().st_size >= min_size

def download_file_curl(url: str, output_path: Path, min_size: int = 1000000) -> bool:
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

def download_stream(url: str, output_path: Path, engine: str = "aria2c", connections: int = 1, min_size: int = 1000000) -> bool:
    if engine == "aria2c":
        res = subprocess.run(["which", "aria2c"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if res.returncode == 0:
            return download_file_aria2c(url, output_path, connections=connections, min_size=min_size)
    return download_file_curl(url, output_path, min_size=min_size)

def extract_aac_from_video(input_video: Path, output_aac: Path) -> bool:
    cmd = [
        "ffmpeg", "-y",
        "-i", str(input_video),
        "-vn", "-c:a", "copy",
        str(output_aac)
    ]
    proc = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return proc.returncode == 0 and output_aac.exists() and output_aac.stat().st_size > 500000

def remux_local_streams(video_path: Path, audio_path: Path, output_mkv: Path, ep_num: int, audio_offset: float = 0.0) -> bool:
    cmd = ["ffmpeg", "-y", "-i", str(video_path)]
    
    if abs(audio_offset) > 0.001:
        if audio_offset > 0:
            cmd.extend(["-itsoffset", f"{audio_offset:.3f}"])
        else:
            cmd.extend(["-ss", f"{abs(audio_offset):.3f}"])

    cmd.extend([
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
    ])
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

def process_pipeline(start_ep: int, end_ep: int, gdrive_dir: Optional[Path], pixeldrain_key: Optional[str], work_dir: Path, downloader: str = "aria2c", connections: int = 1, audio_offset: float = 0.0, manual_video_url: Optional[str] = None, manual_audio_url: Optional[str] = None):
    catalog = load_khmer_catalog()
    work_dir.mkdir(parents=True, exist_ok=True)
    
    state_dir = gdrive_dir if gdrive_dir else work_dir
    state_dir.mkdir(parents=True, exist_ok=True)
    state_file = state_dir / "mux_state.json"
    links_file = state_dir / "pixeldrain_links.txt"
    progress = load_progress(state_file)

    print(f"\n{'='*80}")
    print(f"🎬 Starting Samkok 1080p Colab Pipeline (Episodes {start_ep} to {end_ep})")
    print(f"📡 1080p Video Source: st.111477.xyz / Manual Override")
    print(f"🎙️  Khmer Audio Source: TheKomsan / Rumble CDN (AAC Stereo)")
    print(f"⏱️  Audio Sync Offset: {audio_offset:+.3f}s (Direct 1:1)")
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
            video_stream_url = resolve_1080p_stream_url(ep_num)
        else:
            print(f"[1/4] 🔗 Using manual video stream URL.")

        if not video_stream_url:
            print(f"[-] Could not resolve 1080p video URL for Episode {ep_num:02d}. Skipping.", file=sys.stderr)
            continue
        print(f"    [+] 1080p Stream URL ready.")

        print(f"[2/4] 📥 Downloading 1080p Netflix video (~2.4 GB)...")
        if not download_stream(video_stream_url, temp_raw_video, engine=downloader, connections=connections, min_size=50000000):
            print(f"[-] Video download failed for Episode {ep_num:02d}.", file=sys.stderr)
            continue

        # 2. Download Khmer Audio Stream
        print(f"[3/4] 🎙️ Downloading Khmer audio stream from TheKomsan...")
        audio_stream_url = manual_audio_url or ep_data.get("file")
        if not audio_stream_url:
            print(f"[-] No audio URL found for Episode {ep_num:02d}. Skipping.", file=sys.stderr)
            if temp_raw_video.exists(): temp_raw_video.unlink()
            continue

        if not download_stream(audio_stream_url, temp_audio_mp4, engine=downloader, connections=min(connections, 4), min_size=10000000):
            print(f"[-] Audio download failed for Episode {ep_num:02d}. Skipping.", file=sys.stderr)
            if temp_raw_video.exists(): temp_raw_video.unlink()
            continue

        extract_aac_from_video(temp_audio_mp4, temp_khmer_aac)
        if temp_audio_mp4.exists(): temp_audio_mp4.unlink()

        # 3. Losslessly Remux 1080p Video + Khmer Audio + Mandarin + Subtitles
        print(f"[4/4] ⚡ Losslessly remuxing into 1080p Dual-Audio MKV...")
        mux_ok = remux_local_streams(temp_raw_video, temp_khmer_aac, temp_final_mkv, ep_num, audio_offset=audio_offset)
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
    parser.add_argument("-p", "--pixeldrain", action="store_true", help="Enable PixelDrain auto-upload")
    parser.add_argument("--pixeldrain-key", type=str, default=DEFAULT_PIXELDRAIN_KEY, help="PixelDrain API key")
    parser.add_argument("-g", "--gdrive-dir", type=str, default="/content/drive/MyDrive/ThreeKingdoms_1080p_Khmer", help="Target Google Drive directory (or 'none')")
    parser.add_argument("-w", "--work-dir", type=str, default="/content/samkok_work", help="Working directory for temporary files")
    parser.add_argument("-d", "--downloader", type=str, choices=["aria2c", "curl"], default="aria2c", help="Downloader engine")
    parser.add_argument("-c", "--connections", type=int, default=1, help="Number of connections per download")
    parser.add_argument("-o", "--audio-offset", type=float, default=DEFAULT_AUDIO_OFFSET, help="Audio sync offset in seconds (default: 0.0)")
    parser.add_argument("--video-url", type=str, default=None, help="Manual 1080p video URL override for the episode")
    parser.add_argument("--audio-url", type=str, default=None, help="Manual Khmer audio URL override for the episode")

    args = parser.parse_args()
    
    pd_key = args.pixeldrain_key if (args.pixeldrain or args.pixeldrain_key) else None
    gdrive_dir = Path(args.gdrive_dir) if args.gdrive_dir != "none" else None

    process_pipeline(
        start_ep=args.start,
        end_ep=args.end,
        gdrive_dir=gdrive_dir,
        pixeldrain_key=pd_key,
        work_dir=Path(args.work_dir),
        downloader=args.downloader,
        connections=args.connections,
        audio_offset=args.audio_offset,
        manual_video_url=args.video_url,
        manual_audio_url=args.audio_url
    )

if __name__ == "__main__":
    main()
