"""
JDownloader 2 Client Wrapper
Uses myjdapi to communicate with JDownloader 2 via My.JDownloader API.
"""

import os
import logging
import time
from typing import List, Dict, Optional, Union
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
    
    def reconnect(self) -> bool:
        """Force reconnection to My.JDownloader."""
        try:
            logger.info("🔄 Token invalid or session expired. Reconnecting...")
            self.device = None
            self._connected = False
            # Optional: self.jd.disconnect() if supported, but usually just re-calling connect works
            return self.connect()
        except Exception as e:
            logger.error(f"❌ Reconnection failed: {e}")
            return False

    def _execute_with_retry(self, func, default_return=False):
        """
        Execute a function and retry once if a token error occurs.
        """
        if not self.ensure_connected():
            return default_return

        try:
            return func()
        except Exception as e:
            # Check for token invalid or other auth errors
            # "TOKEN_INVALID" is the specific error from the user logs
            error_str = str(e)
            if "TOKEN_INVALID" in error_str or "Auth" in error_str or "ip check failed" in error_str.lower():
                logger.warning(f"⚠️ API Error ({error_str}). Attempting to reconnect...")
                
                if self.reconnect():
                    try:
                        return func()
                    except Exception as retry_e:
                        logger.error(f"❌ Action failed after reconnect: {retry_e}")
                        return default_return
                else:
                    logger.error("❌ Could not reconnect to retry action.")
                    return default_return
            
            # If not a token error, or if it's a different error
            logger.error(f"❌ Action failed: {e}")
            return default_return

    def add_to_linkgrabber(self, url: str, package_name: str = None, deep_scan: Union[bool, int] = False) -> bool:
        """
        Add a URL to LinkGrabber for extraction.
        Returns True if successfully added.
        """
        def action():
            logger.info(f"📥 Adding to LinkGrabber: {url[:50]}... (Deep: {deep_scan})")
            # API expects boolean for deepDecrypt, regardless of what we pass in internally
            is_deep = bool(deep_scan)
            
            self.device.linkgrabber.add_links([{
                "autostart": False,
                "links": url,
                "packageName": package_name or "Siphon Bot",
                "extractPassword": None,
                "priority": "DEFAULT",
                "downloadPassword": None,
                "destinationFolder": self.download_dir,
                "overwritePackagizerRules": True,
                "deepDecrypt": is_deep
            }])
            logger.info("✅ Link added to LinkGrabber")
            return True

        return self._execute_with_retry(action, default_return=False)
    
    def get_linkgrabber_links(self, wait_for_extraction: bool = True, timeout: int = 30) -> List[Dict]:
        """
        Get list of links from LinkGrabber.
        If wait_for_extraction is True, waits until JD2 finishes processing (link count stabilizes).
        Returns list of dicts with: uuid, name, url, size, enabled, etc.
        """
        if not self.ensure_connected():
            return []
        
        def action():
            start_time = time.time()
            links = []
            last_count = -1
            stable_count_duration = 0
            STABILITY_THRESHOLD = 3  # Seconds to wait for count to stop changing
            
            while True:
                # Query LinkGrabber for packages and links
                # If these fail with TOKEN_INVALID, the wrapper catches it.
                packages = self.device.linkgrabber.query_packages()
                
                all_links = []
                for pkg in packages:
                    pkg_links = self.device.linkgrabber.query_links(params=[{
                        "packageUUIDs": [pkg.get("uuid")],
                        "bytesTotal": True,
                        "url": True,
                        "availability": True,
                        "enabled": True
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
                
                current_count = len(all_links)
                
                # If not waiting for extraction, return immediately
                if not wait_for_extraction:
                    return all_links
                
                # Stability check: wait until link count stops changing
                if current_count == last_count and current_count > 0:
                    stable_count_duration += 1
                    if stable_count_duration >= STABILITY_THRESHOLD:
                        logger.info(f"✅ LinkGrabber stabilized with {current_count} links")
                        return all_links
                else:
                    # Count changed, reset stability timer
                    stable_count_duration = 0
                    last_count = current_count
                
                # Timeout check
                if time.time() - start_time > timeout:
                    logger.warning(f"⏱️ LinkGrabber extraction timeout (found {current_count} links)")
                    return all_links
                
                time.sleep(1)

        result = self._execute_with_retry(action, default_return=[])
        # If result is None (from default_return generic usage? No, I passed [])
        # Actually _execute_with_retry returns default_return on failure.
        return result if result is not None else []
    
    def move_to_downloads(self, link_uuids: List[int] = None, package_uuids: List[int] = None) -> bool:
        """
        Move links from LinkGrabber to Downloads and start them.
        If no UUIDs provided, moves all.
        """
        def action():
            if link_uuids:
                self.device.linkgrabber.move_to_downloadlist(link_uuids, [])
            elif package_uuids:
                self.device.linkgrabber.move_to_downloadlist([], package_uuids)
            else:
                # Move all
                self.device.linkgrabber.move_to_downloadlist()
            
            logger.info("✅ Moved links to Downloads")
            return True

        return self._execute_with_retry(action, default_return=False)

    def abort_crawling(self) -> bool:
        """Abort current LinkGrabber crawling process."""
        def action():
            try:
                if hasattr(self.device.linkgrabber, "abort"):
                    self.device.linkgrabber.abort()
                else:
                    logger.info("📡 Manually calling linkgrabberv2/abort...")
                    self.jd.app.call(
                       self.device.device_encryption_token,
                       self.device.session_token,
                       "/linkgrabberv2/abort",
                       self.device.device_id
                    )

            except Exception as e:
                logger.warning(f"Standard abort failed, trying fallback: {e}")
                self.device.request("linkgrabberv2", "abort")
                
            logger.info("🛑 Aborted LinkGrabber crawling")
            return True

        return self._execute_with_retry(action, default_return=False)

    def is_collecting(self) -> bool:
        """Check if LinkGrabber is currently collecting/crawling."""
        def action():
            # Based on debug output, is_collecting is a method on linkgrabber
            if hasattr(self.device.linkgrabber, "is_collecting"):
                return self.device.linkgrabber.is_collecting()
            
            # Fallback to direct API call if wrapper fails
            try:
                # myjdapi call format: call(enc_token, session_token, endpoint, device_id, args)
                # endpoint: /linkgrabberv2/isCollecting
                res = self.jd.app.call(
                    self.device.device_encryption_token,
                    self.device.session_token,
                    "/linkgrabberv2/isCollecting",
                    self.device.device_id
                )
                return bool(res)
            except:
                return False

        return self._execute_with_retry(action, default_return=False)
    
    def clear_linkgrabber(self) -> bool:
        """Clear all links from LinkGrabber."""
        def action():
            self.device.linkgrabber.clear_list()
            logger.info("🧹 LinkGrabber cleared")
            return True

        return self._execute_with_retry(action, default_return=False)
    
    def get_download_status(self) -> List[Dict]:
        """
        Get status of downloads in progress.
        Returns list with: uuid, name, progress, speed, status, etc.
        """
        def action():
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

        return self._execute_with_retry(action, default_return=[])
    
    def get_finished_downloads(self) -> List[Dict]:
        """Get list of completed downloads."""
        downloads = self.get_download_status()
        return [d for d in downloads if d.get("finished")]
    
    def start_downloads(self) -> bool:
        """Start/resume all downloads."""
        def action():
            self.device.downloadcontroller.start_downloads()
            logger.info("▶️ Downloads started")
            return True

        return self._execute_with_retry(action, default_return=False)
    
    def pause_downloads(self, pause: bool = True) -> bool:
        """Pause or unpause all downloads."""
        def action():
            self.device.downloadcontroller.pause_downloads(pause)
            status = "paused" if pause else "resumed"
            logger.info(f"⏸️ Downloads {status}")
            return True

        return self._execute_with_retry(action, default_return=False)
    
    def remove_finished(self) -> bool:
        """Remove finished downloads from the list."""
        def action():
            self.device.downloads.cleanup(
                action="DELETE_FINISHED",
                mode="REMOVE_LINKS_ONLY",
                selection_type="ALL"
            )
            logger.info("🧹 Finished downloads removed from list")
            return True

        return self._execute_with_retry(action, default_return=False)
    
    def remove_links(self, link_uuids: List[int]) -> bool:
        """Remove specific links by UUID."""
        def action():
            self.device.downloads.remove_links(link_uuids, [])
            logger.info(f"🗑️ Removed {len(link_uuids)} links from JDownloader")
            return True

        return self._execute_with_retry(action, default_return=False)
    



# Singleton instance
_jd_client: Optional[JDownloaderClient] = None

def get_jd_client() -> JDownloaderClient:
    """Get or create JDownloader client singleton."""
    global _jd_client
    if _jd_client is None:
        _jd_client = JDownloaderClient()
    return _jd_client
