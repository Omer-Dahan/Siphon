
import os
import time
import myjdapi
from dotenv import load_dotenv
import json

load_dotenv()

email = os.getenv("JD_EMAIL")
password = os.getenv("JD_PASSWORD")
device_name = os.getenv("JD_DEVICE_NAME")

print(f"Connecting to JD: {email} / ***** / {device_name}")

try:
    jd = myjdapi.Myjdapi()
    jd.connect(email, password)
    jd.update_devices()
    device = jd.get_device(device_name=device_name)
    print(f"Connected to {device.name}")

    # Access advanced config
    # The API for advanced config listing might vary, let's try to list standard config first or search
    # device.advanced.list() usually returns list of interfaces
    
    # Let's try to find relevant keys in LinkGrabberSettings or GeneralSettings
    # Note: myjdapi might not have a direct 'search_config' helper, we might need to iterate.
    
    print("Querying Advanced Config Enums...")
    # Usually we can list all config interfaces and then list keys?
    # Or maybe there is a search?
    
    # Based on myjdapi common usage:
    # device.advanced.list() -> returns list of config entries
    
    configs = device.advanced.list()
    
    found_keys = []
    
    keywords = ["deep", "crawl", "level", "scan"]
    
    for config_interface in configs:
        # Each 'config_interface' is a definition of a config class, e.g. "org.jdownloader.settings.GeneralSettings"
        interface_name = config_interface.get("interfaceName", "")
        
        # We need to get the actual entries for this interface?
        # No, 'list()' lists the storage ids?
        
        # Let's just try to list entries for 'org.jdownloader.settings.GeneralSettings' and 'org.jdownloader.controlling.linkcrawler.LinkCrawlerConfig'
        pass

    # Direct check for LinkCrawlerConfig
    target_interfaces = [
        "org.jdownloader.controlling.linkcrawler.LinkCrawlerConfig",
        "org.jdownloader.settings.GeneralSettings",
        "org.jdownloader.gui.views.linkgrabber.LinkGrabberPanel"
    ]

    for iface in target_interfaces:
        print(f"\nChecking Interface: {iface}")
        try:
             # listEntries(interfaceName)
             entries = device.advanced.listEntries(interfaceName=iface)
             for entry in entries:
                 key = entry.get("key", "")
                 doc = entry.get("doc", "")
                 if any(k in key.lower() for k in keywords) or any(k in doc.lower() for k in keywords):
                     # Get value
                     value = device.advanced.get(iface, "", key)
                     print(f"FOUND: {key} = {value}\nDoc: {doc}")
        except Exception as e:
            print(f"Error checking {iface}: {e}")

except Exception as e:
    print(f"Failed: {e}")
