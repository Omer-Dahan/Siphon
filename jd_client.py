"""
JDownloader 2 Client Wrapper
Uses myjdapi to communicate with JDownloader 2 via My.JDownloader API.
"""

import os
import logging
import time
from typing import List, Dict, Optional
from dotenv import load_dotenv
from utils import format_size

try:
    import myjdapi
except ImportError:
    myjdapi = None
    
load_dotenv()
logger = logging.getLogger(__name__)


class JDownloaderClient:
    """Wrapper for My.JDownloader API interactions."""
    
    def __init__(self):
        if myjdapi is None:
            raise ImportError("myjdapi is not installed. Run: pip install myjdapi")
        
        self.email = os.getenv("JD_EMAIL")
        self.password = os.getenv("JD_PASSWORD")
        self.device_name = os.getenv("JD_DEVICE_NAME")
        self.download_dir = os.getenv("JD_DOWNLOAD_DIR", "downloads")
        
        if not all([self.email, self.password, self.device_name]):
            raise ValueError("JD_EMAIL, JD_PASSWORD, and JD_DEVICE_NAME must be set in .env")
        
        self.jd = myjdapi.Myjdapi()
        self.device = None
        self._connected = False
    
    def connect(self) -> bool:
        """Connect to My.JDownloader and get device."""
        try:
            logger.info("🔌 Connecting to My.JDownloader...")
            self.jd.connect(self.email, self.password)
            self.jd.update_devices()
            
            # Find device by name
            devices = self.jd.list_devices()
            for dev in devices:
                if dev.get("name") == self.device_name:
                    self.device = self.jd.get_device(device_name=self.device_name)
                    self._connected = True
                    logger.info(f"✅ Connected to device: {self.device_name}")
                    return True
            
            logger.error(f"❌ Device '{self.device_name}' not found. Available: {[d.get('name') for d in devices]}")
            return False
            
        except Exception as e:
            logger.error(f"❌ Failed to connect to My.JDownloader: {e}")
            return False
    
    def ensure_connected(self) -> bool:
        """Ensure we have an active connection."""
        if not self._connected or self.device is None:
            return self.connect()
        return True
    
    def add_to_linkgrabber(self, url: str, package_name: str = None, deep_scan: bool = False) -> bool:
        """
        Add a URL to LinkGrabber for extraction.
        Returns True if successfully added.
        """
        if not self.ensure_connected():
            return False
        
        try:
            logger.info(f"📥 Adding to LinkGrabber: {url[:50]}... (Deep: {deep_scan})")
            self.device.linkgrabber.add_links([{
                "autostart": False,
                "links": url,
                "packageName": package_name or "Siphon Bot",
                "extractPassword": None,
                "priority": "DEFAULT",
                "downloadPassword": None,
                "destinationFolder": self.download_dir,
                "overwritePackagizerRules": True,
                "deepDecrypt": deep_scan
            }])
            logger.info("✅ Link added to LinkGrabber")
            return True
        except Exception as e:
            logger.error(f"❌ Failed to add link: {e}")
            return False
    
    def get_linkgrabber_links(self, wait_for_extraction: bool = True, timeout: int = 30) -> List[Dict]:
        """
        Get list of links from LinkGrabber.
        If wait_for_extraction is True, waits until JD2 finishes processing.
        Returns list of dicts with: uuid, name, url, size, enabled, etc.
        """
        if not self.ensure_connected():
            return []
        
        try:
            start_time = time.time()
            links = []
            last_count = -1
            stable_count_duration = 0
            STABILITY_THRESHOLD = 3  # Seconds to wait for count to stop changing
            
            while True:
                # Query LinkGrabber for packages and links
                packages = self.device.linkgrabber.query_packages()
                
                all_links = []
                for pkg in packages:
                    pkg_links = self.device.linkgrabber.query_links(params=[{
                        "packageUUIDs": [pkg.get("uuid")]
                    }])
                    for link in pkg_links:
                        all_links.append({
                            "uuid": link.get("uuid"),
                            "name": link.get("name", "Unknown"),
                            "url": link.get("url", ""),
                            "size": link.get("bytesTotal", 0),
                            "size_str": format_size(link.get("bytesTotal", 0)),
                            "enabled": link.get("enabled", True),
                            "package_uuid": pkg.get("uuid"),
                            "availability": link.get("availability", "UNKNOWN")
                        })
                
                if not wait_for_extraction or all_links:
                    links = all_links
                    break
                
                if time.time() - start_time > timeout:
                    logger.warning("⏱️ LinkGrabber extraction timeout")
                    break
                
                time.sleep(1)
            
            logger.info(f"📋 Found {len(links)} links in LinkGrabber")
            return links
            
        except Exception as e:
            logger.error(f"❌ Failed to query LinkGrabber: {e}")
            return []
    
    def move_to_downloads(self, link_uuids: List[int] = None, package_uuids: List[int] = None) -> bool:
        """
        Move links from LinkGrabber to Downloads and start them.
        If no UUIDs provided, moves all.
        """
        if not self.ensure_connected():
            return False
        
        try:
            if link_uuids:
                self.device.linkgrabber.move_to_downloadlist(link_uuids, [])
            elif package_uuids:
                self.device.linkgrabber.move_to_downloadlist([], package_uuids)
            else:
                # Move all
                self.device.linkgrabber.move_to_downloadlist()
            
            logger.info("✅ Moved links to Downloads")
            return True
        except Exception as e:
            logger.error(f"❌ Failed to move to downloads: {e}")
            return False
    
    def clear_linkgrabber(self) -> bool:
        """Clear all links from LinkGrabber."""
        if not self.ensure_connected():
            return False
        
        try:
            self.device.linkgrabber.clear_list()
            logger.info("🧹 LinkGrabber cleared")
            return True
        except Exception as e:
            logger.error(f"❌ Failed to clear LinkGrabber: {e}")
            return False
    
    def get_download_status(self) -> List[Dict]:
        """
        Get status of downloads in progress.
        Returns list with: uuid, name, progress, speed, status, etc.
        """
        if not self.ensure_connected():
            return []
        
        try:
            packages = self.device.downloads.query_packages()
            all_downloads = []
            
            for pkg in packages:
                pkg_links = self.device.downloads.query_links(params=[{
                    "packageUUIDs": [pkg.get("uuid")],
                    "bytesLoaded": True,
                    "bytesTotal": True,
                    "speed": True,
                    "eta": True,
                    "finished": True,
                    "status": True
                }])
                # Use package save location if available
                save_location = pkg.get("saveLocation", self.download_dir)
                
                for link in pkg_links:
                    bytes_total = link.get("bytesTotal", 0)
                    bytes_loaded = link.get("bytesLoaded", 0)
                    progress = (bytes_loaded / bytes_total * 100) if bytes_total > 0 else 0
                    
                    # Calculate correct local path for this specific link
                    save_location = pkg.get("saveLocation", self.download_dir)
                    local_path = os.path.join(save_location, link.get("name", ""))
                    
                    # Fallback to default download dir if not found in package dir
                    if not os.path.exists(local_path):
                        flat_path = os.path.join(self.download_dir, link.get("name", ""))
                        if os.path.exists(flat_path):
                            local_path = flat_path
                    
                    all_downloads.append({
                        "uuid": link.get("uuid"),
                        "name": link.get("name", "Unknown"),
                        "progress": progress,
                        "bytes_total": bytes_total,
                        "bytes_loaded": bytes_loaded,
                        "speed": link.get("speed", 0),
                        "eta": link.get("eta", 0),
                        "status": link.get("status", "UNKNOWN"),
                        "finished": link.get("finished", False),
                        "running": link.get("running", False),
                        "local_path": local_path
                    })
            
            return all_downloads
            
        except Exception as e:
            logger.error(f"❌ Failed to query downloads: {e}")
            return []
    
    def get_finished_downloads(self) -> List[Dict]:
        """Get list of completed downloads."""
        downloads = self.get_download_status()
        return [d for d in downloads if d.get("finished")]
    
    def start_downloads(self) -> bool:
        """Start/resume all downloads."""
        if not self.ensure_connected():
            return False
        
        try:
            self.device.downloadcontroller.start_downloads()
            logger.info("▶️ Downloads started")
            return True
        except Exception as e:
            logger.error(f"❌ Failed to start downloads: {e}")
            return False
    
    def pause_downloads(self, pause: bool = True) -> bool:
        """Pause or unpause all downloads."""
        if not self.ensure_connected():
            return False
        
        try:
            self.device.downloadcontroller.pause_downloads(pause)
            status = "paused" if pause else "resumed"
            logger.info(f"⏸️ Downloads {status}")
            return True
        except Exception as e:
            logger.error(f"❌ Failed to pause downloads: {e}")
            return False
    
    def remove_finished(self) -> bool:
        """Remove finished downloads from the list."""
        if not self.ensure_connected():
            return False
        
        try:
            self.device.downloads.cleanup(
                action="DELETE_FINISHED",
                mode="REMOVE_LINKS_ONLY",
                selection_type="ALL"
            )
            logger.info("🧹 Finished downloads removed from list")
            return True
        except Exception as e:
            logger.error(f"❌ Failed to cleanup: {e}")
            return False
    
    def remove_links(self, link_uuids: List[int]) -> bool:
        """Remove specific links by UUID."""
        if not self.ensure_connected():
            return False
        
        try:
            self.device.downloads.remove_links(link_uuids, [])
            logger.info(f"🗑️ Removed {len(link_uuids)} links from JDownloader")
            return True
        except Exception as e:
            logger.error(f"❌ Failed to remove links: {e}")
            return False
    



# Singleton instance
_jd_client: Optional[JDownloaderClient] = None

def get_jd_client() -> JDownloaderClient:
    """Get or create JDownloader client singleton."""
    global _jd_client
    if _jd_client is None:
        _jd_client = JDownloaderClient()
    return _jd_client
