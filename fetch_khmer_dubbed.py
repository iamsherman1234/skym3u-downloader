#!/usr/bin/env python3
"""
Khmer Dubbed Catalog Scraper & Search CLI (KhDiaMonD)
Scrapes, indexes, and searches all Khmer Dubbed movies and TV series from khdiamond.net.
"""

import sys
import json
import argparse
import urllib.request
import urllib.parse
from bs4 import BeautifulSoup
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Dict, Any

BASE_URL = "https://khdiamond.net/genre/khdub/"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

def get_total_pages() -> int:
    """Finds the total number of pages in the Khmer Dubbed category."""
    req = urllib.request.Request(BASE_URL, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            html = resp.read().decode("utf-8", errors="ignore")
            soup = BeautifulSoup(html, "html.parser")
            pagination = soup.find(class_=lambda c: c and any(k in c for k in ["pagination", "pages", "nav-links"]))
            if pagination:
                span = pagination.find("span")
                if span:
                    import re
                    m = re.search(r"of\s+(\d+)", span.get_text(), re.IGNORECASE)
                    if m:
                        return int(m.group(1))
            return 20
    except Exception:
        return 20

def scrape_single_page(page_num: int) -> List[Dict[str, Any]]:
    """Extracts all movie/show items from a single page."""
    url = f"{BASE_URL}page/{page_num}/" if page_num > 1 else BASE_URL
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=12) as resp:
                html = resp.read().decode("utf-8", errors="ignore")
                soup = BeautifulSoup(html, "html.parser")
                
                items = []
                for it in soup.find_all("article"):
                    data_tag = it.find("div", class_="data")
                    a_tag = data_tag.find("a") if data_tag else it.find("a")
                    
                    title = a_tag.get_text(strip=True) if a_tag else ""
                    link = a_tag.get("href", "") if a_tag else ""
                    
                    year_tag = data_tag.find("span") if data_tag else None
                    year = year_tag.get_text(strip=True) if year_tag else ""
                    
                    rating_tag = it.find("div", class_="rating") or it.find("span", class_="rating")
                    rating = rating_tag.get_text(strip=True) if rating_tag else "N/A"
                    
                    posters = [img.get("src") for img in it.find_all("img") if img.get("src") and not "sss1.png" in img.get("src")]
                    poster = posters[0] if posters else ""
                    
                    item_type = "TV Series" if "/tvshows/" in link else "Movie"
                    
                    if title and link:
                        items.append({
                            "title": title,
                            "type": item_type,
                            "year": year,
                            "rating": rating,
                            "link": link,
                            "poster": poster
                        })
                return items
        except Exception:
            continue
    return []

def scrape_all_khmer_dubbed(max_workers: int = 10, export_file: str = "khmer_dubbed_catalog.json") -> List[Dict[str, Any]]:
    """Crawls all pages and saves the master Khmer Dubbed catalog."""
    print("[*] Detecting total catalog pages...")
    total_pages = get_total_pages()
    print(f"[*] Fetching {total_pages} pages of Khmer Dubbed content with {max_workers} concurrent workers...")

    all_items = []
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(scrape_single_page, p): p for p in range(1, total_pages + 1)}
        for future in as_completed(futures):
            p = futures[future]
            res = future.result()
            all_items.extend(res)

    # Deduplicate by URL link
    seen = set()
    deduped = []
    for it in all_items:
        if it["link"] not in seen:
            seen.add(it["link"])
            deduped.append(it)

    # Sort by Year (newest first)
    deduped.sort(key=lambda x: str(x.get("year", "")), reverse=True)

    if export_file:
        out_path = Path(export_file)
        out_path.write_text(json.dumps(deduped, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[+] Saved master catalog with {len(deduped)} titles to '{out_path}'")

    return deduped

def search_catalog(items: List[Dict[str, Any]], query: str = None, filter_type: str = None):
    """Filters and searches titles in the catalog."""
    filtered = items
    if filter_type:
        filtered = [i for i in filtered if filter_type.lower() in i["type"].lower()]
    if query:
        q = query.lower()
        filtered = [i for i in filtered if q in i["title"].lower() or q in i["link"].lower()]

    print("\n" + "=" * 105)
    print(f"{'TYPE':<11} | {'TITLE':<45} | {'YEAR':<6} | {'IMDb':<6} | {'LINK'}")
    print("-" * 105)

    for it in filtered[:100]:
        title_disp = it["title"] if len(it["title"]) <= 45 else it["title"][:42] + "..."
        print(f"{it['type']:<11} | {title_disp:<45} | {it['year']:<6} | {it['rating']:<6} | {it['link']}")

    print("=" * 105)
    print(f"Displaying {min(len(filtered), 100)} of {len(filtered)} matching titles\n")

def main():
    parser = argparse.ArgumentParser(description="Khmer Dubbed Content Scraper & Search (KhDiaMonD)")
    parser.add_argument("-s", "--search", type=str, help="Search query for title / keyword")
    parser.add_argument("-t", "--type", choices=["movie", "tv", "all"], default="all", help="Filter by type (movie, tv, all)")
    parser.add_argument("-o", "--output", type=str, default="khmer_dubbed_catalog.json", help="Export catalog JSON path")
    parser.add_argument("--refresh", action="store_true", help="Force re-scrape from website even if local file exists")
    parser.add_argument("--json", action="store_true", help="Output search results as JSON")

    args = parser.parse_args()

    cache_file = Path(args.output)
    if cache_file.exists() and not args.refresh:
        try:
            items = json.loads(cache_file.read_text(encoding="utf-8"))
        except Exception:
            items = scrape_all_khmer_dubbed(export_file=args.output)
    else:
        items = scrape_all_khmer_dubbed(export_file=args.output)

    filter_t = None if args.type == "all" else ("Movie" if args.type == "movie" else "TV Series")

    if args.json:
        if args.search or filter_t:
            results = [i for i in items if (not filter_t or filter_t == i["type"]) and (not args.search or args.search.lower() in i["title"].lower())]
        else:
            results = items
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        search_catalog(items, query=args.search, filter_type=filter_t)

if __name__ == "__main__":
    main()
