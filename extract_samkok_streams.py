#!/usr/bin/env python3
"""
Samkok (Three Kingdoms) Khmer Dubbed Stream Extractor & M3U Playlist Generator
Extracts all 95 episodes from movie-khmer.com and generates a direct streaming M3U playlist.
"""

import sys
import re
import json
import argparse
import urllib.request
import urllib.parse
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Dict, Any, Optional

SOURCE_PAGE = "https://movie-khmer.com/samkok-three-kingdoms/"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
HEADERS = {"User-Agent": USER_AGENT}

def fetch_raw_episodes_from_page(url: str = SOURCE_PAGE) -> List[Dict[str, str]]:
    """Extracts the list of 95 OK.ru embed URLs from movie-khmer.com."""
    print(f"[*] Fetching episode index from {url}...")
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=12) as resp:
            html = resp.read().decode("utf-8", errors="ignore")
            match = re.search(r"options\.player_list\s*=\s*(\[[^;]+\]);", html)
            if match:
                raw_json = re.sub(r",\s*\]", "]", match.group(1))
                episodes = json.loads(raw_json)
                return episodes
    except Exception as e:
        print(f"[-] Error fetching source page: {e}", file=sys.stderr)
    return []

def resolve_okru_direct_stream(embed_url: str, timeout: int = 10) -> Dict[str, Any]:
    """Resolves an OK.ru videoembed URL to direct MP4 streaming links."""
    clean_url = embed_url.split("?")[0]
    req = urllib.request.Request(clean_url, headers=HEADERS)
    result = {
        "embed_url": clean_url,
        "direct_mp4": None,
        "quality": "N/A",
        "qualities": {}
    }
    
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                html = resp.read().decode("utf-8", errors="ignore")
                m = re.search(r'data-options=["\']([^"\']+)["\']', html)
                if m:
                    raw_opt = m.group(1).replace("&quot;", '"')
                    data = json.loads(raw_opt)
                    flashvars = data.get("flashvars", {})
                    videos = flashvars.get("metadata", {}).get("videos", [])
                    
                    q_map = {}
                    for v in videos:
                        name = v.get("name")
                        v_url = v.get("url")
                        if name and v_url:
                            q_map[name] = v_url

                    result["qualities"] = q_map
                    # Prioritize highest available quality (hd -> sd -> low)
                    for pref in ["hd", "sd", "low", "lowest", "mobile"]:
                        if pref in q_map:
                            result["direct_mp4"] = q_map[pref]
                            result["quality"] = pref.upper()
                            break
                    return result
        except Exception:
            continue
    return result

def generate_m3u_playlist(episodes: List[Dict[str, Any]], output_path: Path):
    """Generates an M3U playlist file with all episodes."""
    lines = [
        "#EXTM3U",
        "#PLAYLIST:Samkok (Three Kingdoms 2010) - Khmer Dubbed",
        ""
    ]
    for idx, ep in enumerate(episodes, 1):
        title = ep.get("title", f"Samkok Episode {idx:02d}")
        stream_url = ep.get("direct_mp4") or ep.get("file")
        lines.append(f'#EXTINF:-1 tvg-id="samkok_{idx:02d}" tvg-name="{title}" tvg-logo="https://movie-khmer.com/wp-content/uploads/samkok-three-kingdoms.jpg" group-title="Samkok Khmer Dubbed",{title}')
        lines.append(stream_url)
        lines.append("")

    output_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"[+] Saved M3U playlist with {len(episodes)} episodes to '{output_path}'")

def main():
    parser = argparse.ArgumentParser(description="Extract Samkok (Three Kingdoms) Khmer Dubbed Streams")
    parser.add_argument("-o", "--output-json", type=str, default="samkok_khmer_episodes.json", help="Output JSON path")
    parser.add_argument("-m", "--output-m3u", type=str, default="samkok_khmer.m3u", help="Output M3U playlist path")
    parser.add_argument("-w", "--workers", type=int, default=10, help="Concurrent resolution workers (default: 10)")
    parser.add_argument("--direct", action="store_true", help="Resolve direct MP4 CDN links for all episodes")

    args = parser.parse_args()

    raw_episodes = fetch_raw_episodes_from_page(SOURCE_PAGE)
    if not raw_episodes:
        print("[-] Could not retrieve episodes from movie-khmer.com")
        sys.exit(1)

    print(f"[+] Found {len(raw_episodes)} episodes.")

    final_episodes = []
    if args.direct:
        print(f"[*] Resolving direct HD MP4 stream links for {len(raw_episodes)} episodes ({args.workers} workers)...")
        with ThreadPoolExecutor(max_workers=args.workers) as executor:
            future_to_ep = {executor.submit(resolve_okru_direct_stream, ep["file"]): ep for ep in raw_episodes}
            for future in as_completed(future_to_ep):
                ep = future_to_ep[future]
                stream_info = future.result()
                merged = dict(ep)
                merged.update(stream_info)
                final_episodes.append(merged)
        
        # Sort back in episode order
        final_episodes.sort(key=lambda x: x.get("title", ""))
    else:
        final_episodes = raw_episodes

    # Save JSON
    json_path = Path(args.output_json)
    json_path.write_text(json.dumps(final_episodes, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[+] Saved episode catalog to '{json_path}'")

    # Generate M3U playlist
    m3u_path = Path(args.output_m3u)
    generate_m3u_playlist(final_episodes, m3u_path)

if __name__ == "__main__":
    main()
