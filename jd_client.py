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
            raise ValueError(
                "JD_EMAIL, JD_PASSWORD, and JD_DEVICE_NAME must be set in .env"
            )
        self.jd = myjdapi.Myjdapi()
        self.device = None
        self._connected = False

    def connect(self) -> bool:
        """Connect to My JDownloader."""
        try:
            logger.info("🔌 Connecting to My.JDownloader...")
            self.jd.connect(self.email, self.password)
            self.device = self.jd.get_device(self.device_name)
            if self.device:
                self._connected = True
                logger.info("✅ Connected to JDownloader: %s", self.device_name)
                return True
            logger.error("❌ Device '%s' not found.", self.device_name)
            return False
        except Exception as err: # pylint: disable=broad-except
            logger.error("❌ Failed to connect to My.JDownloader: %s", err)
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
        except Exception as err: # pylint: disable=broad-except
            logger.error("❌ Reconnection failed: %s", err)
            return False

    def _execute_with_retry(self, action, default_return=None):
        """Execute an API action with retry logic for token errors."""
        max_retries = 3
        for attempt in range(max_retries):
            try:
                if not self.ensure_connected():
                    return default_return
                return action()
            except Exception as err:
                err_name = type(err).__name__
                if err_name in ("TokenExpiredException", "TokenException"):
                    logger.warning("%s detected, reconnecting (attempt %s/%s)...",
                                   err_name, attempt + 1, max_retries)
                    self.reconnect()
                    continue
                
                logger.error("API error: %s", err)
                if attempt == max_retries - 1:
                    return default_return
                time.sleep(1)
        return default_return

    def add_to_linkgrabber(self, url: str, package_name: str = None,
                           deep_scan: Union[bool, int] = False) -> bool:
        """Add links to LinkGrabber."""
        def action():
            logger.info("📥 Adding to LinkGrabber: %s... (Deep: %s)", url[:50], deep_scan)
            # API expects boolean for deepDecrypt, regardless of what we pass in internally
            is_deep = bool(deep_scan)

            self.device.linkgrabber.add_links([{ # pylint: disable=no-member
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

    def get_linkgrabber_links(self, wait_for_extraction: bool = True,
                              timeout: int = 30) -> List[Dict]:
        """
        Get list of links from LinkGrabber.
        If wait_for_extraction is True, waits until JD2 finishes processing (link count stabilizes).
        Returns list of dicts with: uuid, name, url, size, enabled, etc.
        """
        if not self.ensure_connected():
            return []

        def action():
            start_time = time.time()
            last_count = -1
            stable_count_duration = 0
            stability_threshold = 3  # Seconds to wait for count to stop changing

            while True:
                # Query LinkGrabber for packages and links
                # If these fail with TOKEN_INVALID, the wrapper catches it.
                packages = self.device.linkgrabber.query_packages() # pylint: disable=no-member

                all_links = []
                for pkg in packages:
                    pkg_uuid = pkg.get("uuid")
                    l_params = [{
                        "packageUUIDs": [pkg_uuid],
                        "bytesTotal": True,
                        "url": True,
                        "status": True
                    }]
                    # pylint: disable=no-member
                    p_links = self.device.linkgrabber.query_links(
                        params=l_params
                    )
                    for link in p_links:
                        all_links.append({
                            "uuid": link.get("uuid"),
                            "name": link.get("name", "Unknown"),
                            "url": link.get("url", ""),
                            "size": link.get("size", 0),
                            "size_str": format_size(link.get("size", 0)),
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
                    if stable_count_duration >= stability_threshold:
                        logger.info("✅ LinkGrabber stabilized with %s links", current_count)
                        return all_links
                else:
                    # Count changed, reset stability timer
                    stable_count_duration = 0
                    last_count = current_count

                # Timeout check
                if time.time() - start_time > timeout:
                    logger.warning("⏱️ LinkGrabber extraction timeout (found %s links)",
                                   current_count)
                    return all_links

                time.sleep(1)

        result = self._execute_with_retry(action, default_return=[])
        return result if result is not None else []

    def move_to_downloads(self, link_uuids: List[int] = None,
                          package_uuids: List[int] = None) -> bool:
        """
        Move links from LinkGrabber to Downloads and start them.
        If no UUIDs provided, moves all.
        """
        def action():
            if link_uuids:
                self.device.linkgrabber.move_to_downloadlist(  # pylint: disable=no-member
                    link_uuids, []
                )
            elif package_uuids:
                self.device.linkgrabber.move_to_downloadlist(  # pylint: disable=no-member
                    [], package_uuids
                )
            else:
                self.device.linkgrabber.move_to_downloadlist(  # pylint: disable=no-member
                    [], []
                )

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
                    logger.info("📡 Attempting direct API call for abort...")
                    # The correct way in most versions is calling it via the device directly if it exists
                    self.device.action("/linkgrabberv2/abort")
            except Exception as err:
                logger.warning("Abort failed: %s", err)

            logger.info("🛑 Aborted LinkGrabber crawling")
            return True

        return self._execute_with_retry(action, default_return=False)

    def is_collecting(self) -> bool:
        """Check if LinkGrabber is currently collecting/crawling."""
        def action():
            # Based on debug output, is_collecting is a method on linkgrabber
            if hasattr(self.device.linkgrabber, "is_collecting"): # pylint: disable=no-member
                return self.device.linkgrabber.is_collecting() # pylint: disable=no-member

            # Fallback to direct API call if wrapper fails
            try:
                # myjdapi call format: call(enc_token, session_token, endpoint, device_id, args)
                # endpoint: /linkgrabberv2/isCollecting
                res = self.jd.app.call( # pylint: disable=no-member
                    self.device.device_encryption_token, # pylint: disable=no-member
                    self.device.session_token, # pylint: disable=no-member
                    "/linkgrabberv2/isCollecting",
                    self.device.device_id # pylint: disable=no-member
                )
                return bool(res)
            except Exception: # pylint: disable=broad-except
                return False

        return self._execute_with_retry(action, default_return=False)

    def clear_linkgrabber(self) -> bool:
        """Clear all links from LinkGrabber."""
        def action():
            self.device.linkgrabber.clear_list() # pylint: disable=no-member
            logger.info("🧹 LinkGrabber cleared")
            return True

        return self._execute_with_retry(action, default_return=False)

    def get_download_status(self) -> List[Dict]:
        """
        Get status of downloads in progress.
        Returns list with: uuid, name, progress, speed, status, etc.
        """
        def action():
            packages = self.device.downloads.query_packages() # pylint: disable=no-member
            all_downloads = []

            for pkg in packages:
                pkg_links = self.device.downloads.query_links(
                    params=[{  # pylint: disable=no-member
                        "packageUUIDs": [pkg.get("uuid")],
                        "bytesLoaded": True,
                        "bytesTotal": True,
                        "speed": True,
                        "eta": True,
                        "finished": True,
                        "status": True,
                        "running": True
                    }]
                )
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

    def start_downloads(self) -> bool:
        """Start/resume all downloads."""
        def action():
            self.device.downloadcontroller.start_downloads() # pylint: disable=no-member
            logger.info("▶️ Downloads started")
            return True

        return self._execute_with_retry(action, default_return=False)

    def pause_downloads(self, pause: bool = True) -> bool:
        """Pause or unpause all downloads."""
        def action():
            self.device.downloadcontroller.pause_downloads(pause) # pylint: disable=no-member
            status = "paused" if pause else "resumed"
            logger.info("⏸️ Downloads %s", status)
            return True

        return self._execute_with_retry(action, default_return=False)

    def remove_links(self, link_uuids: List[int]) -> bool:
        """Remove specific links by UUID."""
        def action():
            self.device.downloads.remove_links(link_uuids, []) # pylint: disable=no-member
            logger.info("🗑️ Removed %s links from JDownloader", len(link_uuids))
            return True

        return self._execute_with_retry(action, default_return=False)



# Singleton instance
JD_CLIENT_INSTANCE: Optional[JDownloaderClient] = None

def get_jd_client() -> JDownloaderClient:
    """Get or create singleton JDownloaderClient instance."""
    global JD_CLIENT_INSTANCE  # pylint: disable=global-statement
    if JD_CLIENT_INSTANCE is None:
        JD_CLIENT_INSTANCE = JDownloaderClient()
    return JD_CLIENT_INSTANCE
