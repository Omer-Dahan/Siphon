#!/usr/bin/env python3
"""
Video Scraper - Script to scrape direct video links using Playwright Network Sniffing
mimics IDM behavior by intercepting browser traffic.
"""

import json
import csv
import os
import time
import re
import logging
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from tqdm import tqdm
from urllib.parse import urljoin, urlparse
from typing import List, Dict, Optional
from playwright.sync_api import sync_playwright, Response
from utils import moon_progress_bar


# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("scraper.log", encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)
class VideoScraper:
    def __init__(self, config: dict = None, config_path: str = "config.json"):
        """Initialize the script with a configuration dictionary or file"""
        if config:
            self.config = config
        else:
            self.config = self.load_config(config_path)
        self.video_urls = []
        self.status = "Initializing..."
        self.found_count = 0
        
    def load_config(self, config_path: str) -> dict:
        """Load configuration file"""
        try:
            if os.path.exists(config_path):
                with open(config_path, 'r', encoding='utf-8') as f:
                    return json.load(f)
            else:
                return {
                    "video_extensions": [".mp4", ".webm", ".m3u8", ".ts", ".mov", ".avi"],
                    "output_file": "videos.csv",
                    "wait_timeout": 30
                }
        except Exception as e:
            logger.error(f"❌ Error loading config: {e}")
            return {}

    def matches_keywords(self, text: str) -> bool:
        """Check if text contains one of the keywords"""
        if not self.config.get('keywords'):
            return True
        
        text_lower = text.lower()
        keywords = [kw.lower() for kw in self.config['keywords']]
        return any(keyword in text_lower for keyword in keywords)

    def is_video_content(self, response: Response) -> bool:
        """Check if response is a video file based on headers and URL"""
        try:
            url = response.url.lower()
            content_type = response.headers.get('content-type', '').lower()
            
            # Common video content types
            video_types = [
                'video/', 
                'application/x-mpegurl', 
                'application/vnd.apple.mpegurl',
                'application/dash+xml'
            ]
            
            # Common video extensions
            video_extensions = self.config.get('video_extensions', ['.mp4', '.webm', '.m3u8', '.ts', '.mov', '.avi'])
            
            # 1. Check Content-Type header
            if any(vt in content_type for vt in video_types):
                return True
                
            # 2. Check URL extension (fallback if content-type is missing/generic)
            # Remove query parameters for extension check
            url_clean = url.split('?')[0]
            if any(url_clean.endswith(ext) for ext in video_extensions):
                return True
                
            return False
            
        except Exception:
            return False

    def get_video_size(self, response: Response) -> str:
        """Extract content length from headers"""
        try:
            content_length = response.headers.get('content-length')
            if content_length:
                size_mb = int(content_length) / (1024 * 1024)
                return f"{size_mb:.2f} MB"
        except:
            pass
        return "Unknown"

    def _sniff_url(self, context, url) -> List[Dict]:
        """Sniff a single URL for video content and return ALL found streams"""
        self.status = f"🔍 Sniffing: {url[:30]}..."
        self.found_count = 0
        logger.info(f"--- 🚀 Starting Scrape Session ---")
        logger.info(f"    Target: {url}")
        
        page = context.new_page()
        found_videos = []
        
        # Event handler for network responses
        def handle_response(response):
            if self.found_count >= 15: return # Cap at 15 to prevent ad-bloat
            
            if self.is_video_content(response):
                cl = response.headers.get('content-length')
                if cl and int(cl) < 50000: 
                    return

                video_info = {
                    'url': response.url,
                    'size': self.get_video_size(response),
                    'size_bytes': int(response.headers.get('content-length', 0)),
                    'page_url': url,
                    'duration': None,
                    'content_type': response.headers.get('content-type', 'unknown')
                }
                
                # Avoid duplicates in the same session
                if not any(v['url'] == video_info['url'] for v in found_videos):
                    logger.info(f"    🎥 Sniffed: {response.url[:60]}... ({video_info['size']})")
                    found_videos.append(video_info)
                    self.found_count = len(found_videos)
                    self.status = f"🎯 Found {self.found_count} stream(s)..."

        page.on("response", handle_response)
        
        try:
            self.status = "🌍 Navigating to page..."
            logger.info(f"    🌍 Navigating to page...")
            page.goto(url, timeout=30000, wait_until="domcontentloaded")
            title = page.title()
            logger.info(f"    📃 Page Title: {title}")
            
            keywords = self.config.get('keywords')
            if keywords and not self.matches_keywords(title):
                self.status = "⏩ Filtered by keywords"
                logger.warning(f"    ⏩ Filtered out by keyword mismatch ('{title[:40]}...')")
                logger.info(f"       Required Keywords: {', '.join(keywords)}")
                page.close()
                return []
                
            for v in found_videos:
                v['title'] = title

            if not found_videos:
                self.status = "⏳ Attempting interaction..."
                logger.info("    ⏳ No direct video detected yet. Attempting interaction...")
                try:
                    # Try to trigger playback
                    page.evaluate("""() => {
                        const video = document.querySelector('video');
                        if (video) {
                            video.play().catch(() => {});
                            video.click();
                        }
                        const playButtons = [
                            ...document.querySelectorAll('button'),
                            ...document.querySelectorAll('.play-button'),
                            ...document.querySelectorAll('[class*="play"]')
                        ];
                        playButtons.forEach(b => b.click());
                    }""")
                except:
                    pass
                    
                start_wait = time.time()
                timeout_duration = self.config.get('wait_timeout', 30)
                logger.info(f"    ⏳ Waiting up to {timeout_duration}s for video streams...")
                while time.time() - start_wait < timeout_duration: 
                    if found_videos: 
                        # If we found at least one video, and it's been a few seconds, or we have many, stop.
                        if len(found_videos) >= 3 or (time.time() - start_wait > 5):
                            break
                    self.status = f"⏳ Sniffing... ({int(timeout_duration - (time.time() - start_wait))}s left)"
                    page.wait_for_timeout(500)
            
            if found_videos:
                self.status = f"🎯 Found {len(found_videos)} streams. Finalizing..."
                logger.info(f"    🎯 Found {len(found_videos)} video stream(s).")
                try:
                    # Duration check can sometimes hang, use a shorter timeout or skip if complex
                    duration = page.evaluate("() => { const v = document.querySelector('video'); return v ? v.duration : null; }")
                    if duration:
                        for v in found_videos:
                            v['duration'] = duration
                        logger.info(f"    ⏱️ Duration: {duration:.2f}s")
                except:
                    pass
                return found_videos
            else:
                logger.warning("    🛑 No video streams found after timeout.")
                return []
        
        except Exception as e:
            logger.warning(f"    ⚠️ Error loading page: {e}")
        finally:
            page.close()
        
        return None

    def scrape_single(self, url: str) -> List[Dict]:
        """Sniff only the provided URL"""
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True) 
            context = browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            )
            result = self._sniff_url(context, url)
            browser.close()
            return result

    def scrape_full(self, main_url: str) -> List[Dict]:
        """Existing behavior: scan main page for links and sniff each"""
        all_video_results = []
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True) 
            context = browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            )
            
            # --- Step 1: Scan Main Page for Links ---
            page = context.new_page()
            logger.info(f"🔍 Scanning main page: {main_url}")
            try:
                page.goto(main_url, timeout=30000, wait_until="domcontentloaded")
                page.wait_for_timeout(2000) 
                
                links = page.eval_on_selector_all('a[href]', "elements => elements.map(e => e.href)")
                target_links = []
                main_domain = urlparse(main_url).netloc
                for link in set(links):
                    if urlparse(link).netloc == main_domain:
                        target_links.append(link)
                logger.info(f"✅ Found {len(target_links)} links to scan")
            except Exception as e:
                logger.error(f"❌ Error scanning: {e}")
                browser.close()
                return []
            finally:
                page.close()

            # --- Step 2: Sniff each link ---
            for idx, link in enumerate(target_links, 1):
                logger.info(f"[{idx}/{len(target_links)}] Processing: {link}")
                res_list = self._sniff_url(context, link)
                if res_list:
                    all_video_results.extend(res_list)
            
            browser.close()
        return all_video_results

    def scrape(self):
        """Original entry point for legacy support or cli usage"""
        main_url_config = self.config.get('main_url')
        if not main_url_config:
            logger.error("❌ Main URL not defined!")
            return []
            
        if isinstance(main_url_config, list):
            start_urls = main_url_config
        else:
            start_urls = [main_url_config]
            
        final_results = []
        for url in start_urls:
            final_results.extend(self.scrape_full(url))
        return final_results

    def clean_duplicates(self, download_dir: str):
        """Scan download directory for existing duplicates (by size) and clean them up"""
        if not os.path.exists(download_dir):
            return

        logger.info("🧹 Verification: Checking for existing duplicates in downloads folder...")
        
        files_by_size = {}
        for f in os.listdir(download_dir):
            path = os.path.join(download_dir, f)
            if os.path.isfile(path):
                size = os.path.getsize(path)
                if size not in files_by_size:
                    files_by_size[size] = []
                files_by_size[size].append(path)
        
        cleaned_count = 0
        for size, paths in files_by_size.items():
            if len(paths) > 1 and size > 0:
                # Sort by creation time (oldest first) to keep the original, 
                # or by length of filename to keep the cleanest name.
                # Let's keep the one with the shortest filename, as it's likely the original 'clean' one.
                paths.sort(key=lambda p: (len(os.path.basename(p)), os.path.getctime(p)))
                
                keep = paths[0]
                remove = paths[1:]
                
                for p in remove:
                    try:
                        os.remove(p)
                        logger.info(f"    🗑️ Removed existing duplicate: {os.path.basename(p)} (Same size as {os.path.basename(keep)})")
                        cleaned_count += 1
                    except OSError as e:
                        logger.warning(f"    ⚠️ Failed to remove {p}: {e}")

        if cleaned_count > 0:
            logger.info(f"✨ Cleaned {cleaned_count} duplicate files from previous runs.")
        else:
            logger.info("✅ No existing duplicates found.")

    def download_videos(self, results: List[Dict[str, str]], auto_download: bool = False, progress_callback=None):

        """Interactive or automatic download of found videos"""
        if not results:
            return []

        if not auto_download:
            print(f"\n🎥 Found {len(results)} videos.")
            choice = input("📥 Do you want to download them? (y/n): ").lower().strip()
            if choice != 'y':
                logger.info("⏩ Download skipped by user.")
                return []

        download_dir = "downloads"
        os.makedirs(download_dir, exist_ok=True)
        
        # Clean existing duplicates first - DISABLED per user request
        # self.clean_duplicates(download_dir)
        
        # Configure retry strategy
        retry_strategy = Retry(
            total=3,
            backoff_factor=1,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["HEAD", "GET", "OPTIONS"]
        )
        adapter = HTTPAdapter(max_retries=retry_strategy)
        session = requests.Session()
        session.mount("https://", adapter)
        session.mount("http://", adapter)
        
        logger.info(f"📂 Downloading to '{download_dir}/'...")
        
        # Pre-scan local files sizes for quick deduplication
        local_files_sizes = {}
        for f in os.listdir(download_dir):
            path = os.path.join(download_dir, f)
            if os.path.isfile(path):
                local_files_sizes[path] = os.path.getsize(path)

        for idx, video in enumerate(results, 1):
            url = video['url']
            title = video.get('title', f'video_{idx}')
            
            # Sanitize filename
            safe_filename = re.sub(r'[<>:"/\\|?*]', '_', title)
            
            # Truncate filename if too long (max 50 chars for the name part)
            base_name, extension = os.path.splitext(safe_filename)
            if len(base_name) > 50:
                base_name = base_name[:50]
                safe_filename = base_name + extension
            
            # Ensure extension
            if not any(safe_filename.endswith(ext) for ext in ['.mp4', '.mkv', '.webm', '.avi', '.mov', '.ts']):
                safe_filename += ".mp4"
                
            final_file_path = os.path.join(download_dir, safe_filename)
            
            # --- Deduplication Logic ---
            remote_size = 0
            try:
                # HEAD request to get size
                with session.head(url, allow_redirects=True, timeout=10) as h:
                    remote_size = int(h.headers.get('content-length', 0))
            except:
                pass # Proceed even if size unknown, though dedupe is weaker
            
            skipped = False
            
            # 1. Check if ANY file has the same size
            if remote_size > 0:
                for existing_path, existing_size in local_files_sizes.items():
                    if existing_size == remote_size:
                        logger.info(f"    ⏭️  Skipping (Duplicate Size found in {os.path.basename(existing_path)})")
                        skipped = True
                        break
            
            if skipped: continue

            # 2. Check by Name
            if os.path.exists(final_file_path):
                local_size = os.path.getsize(final_file_path)
                if remote_size > 0 and local_size == remote_size:
                     logger.info(f"    ⏭️  Skipping existing file (Same Name & Size): {safe_filename}")
                     continue
                else:
                    # Name exists but size/content differs -> Rename
                    base, ext = os.path.splitext(safe_filename)
                    counter = 1
                    while os.path.exists(os.path.join(download_dir, f"{base}_{counter}{ext}")):
                        counter += 1
                    safe_filename = f"{base}_{counter}{ext}"
                    final_file_path = os.path.join(download_dir, safe_filename)
                    logger.info(f"    📝 Renaming to avoid collision: {safe_filename}")

            if skipped: continue # Double check
                
            logger.info(f"    ⬇️  Downloading ({idx}/{len(results)}): {safe_filename}...")
            
            try:
                with session.get(url, stream=True, timeout=30) as r:
                    r.raise_for_status()
                    total_size = int(r.headers.get('content-length', 0))
                    
                    with open(final_file_path, 'wb') as f, tqdm(
                        desc=safe_filename,
                        total=total_size,
                        unit='B',
                        unit_scale=True,
                        unit_divisor=1024,
                    ) as bar:
                        for chunk in r.iter_content(chunk_size=8192):
                            f.write(chunk)
                            bar.update(len(chunk))
                            if total_size > 0:
                                percent = (bar.n / total_size) * 100
                                bar_str = moon_progress_bar(percent)
                                self.status = f"📥 Downloading: {bar_str} {percent:.1f}%"
                                if progress_callback:
                                    progress_callback(bar_str, percent)

                            
                logger.info(f"    ✅ Download complete: {safe_filename}")
                video['local_path'] = os.path.abspath(final_file_path)
                # Update local cache for next iteration
                local_files_sizes[final_file_path] = os.path.getsize(final_file_path)
            
            except requests.exceptions.RequestException as e:
                logger.error(f"    ❌ Network error for {safe_filename}:")
                logger.error(f"       URL: {url[:100]}...")
                logger.error(f"       Type: {type(e).__name__}")
                logger.error(f"       Details: {str(e)}")
                if os.path.exists(final_file_path):
                    os.remove(final_file_path)
            except Exception as e:
                logger.error(f"    ❌ Download failed for {safe_filename}:")
                logger.error(f"       Type: {type(e).__name__}")
                logger.error(f"       Details: {str(e)}")
                if os.path.exists(final_file_path):
                    os.remove(final_file_path)
        
        return results

    def export_to_csv(self, results: List[Dict[str, str]]):
        """Export results to CSV file"""
        if not results:
            logger.warning("⚠️ No results to export")
            return
        
        os.makedirs('output', exist_ok=True)
        output_file = os.path.join('output', self.config.get('output_file', 'videos.csv'))
        
        with open(output_file, 'w', newline='', encoding='utf-8-sig') as f:
            writer = csv.DictWriter(f, fieldnames=['title', 'url', 'size', 'duration', 'page_url', 'size_bytes', 'local_path'], extrasaction='ignore')
            writer.writeheader()
            writer.writerows(results)
        
        logger.info(f"💾 Results saved to: {output_file}")


def main():
    """Script entry point"""
    logger.info("=" * 60)
    logger.info("🎬 Video Scraper (Playwright Sniffer)")
    logger.info("=" * 60)
    
    # Check if playwright browsers are installed
    if not os.path.exists(os.path.join(os.environ.get('USERPROFILE'), 'AppData/Local/ms-playwright')):
        logger.info("⚠️ First time run? Installing Playwright browsers...")
        os.system("playwright install chromium")

    try:
        scraper = VideoScraper()
        results = scraper.scrape()
        
        # Verify deduplication works by checking against existing files before downloading
        scraper.download_videos(results)
        
        scraper.export_to_csv(results)
        
    except KeyboardInterrupt:
        logger.info("⏹️ Scraping stopped by user")
    except Exception as e:
        logger.error(f"❌ General error: {e}", exc_info=True)


if __name__ == "__main__":
    main()
