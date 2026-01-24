import os
import asyncio
import logging
from pyrogram import Client, filters, types
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from video_scraper import VideoScraper
from playwright.sync_api import sync_playwright
from dotenv import load_dotenv
from utils import moon_progress_bar, get_video_metadata, generate_thumbnail
import threading
from concurrent.futures import ThreadPoolExecutor

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Bot configuration
API_ID = os.getenv("API_ID", "YOUR_API_ID")
API_HASH = os.getenv("API_HASH", "YOUR_API_HASH")
BOT_TOKEN = os.getenv("BOT_TOKEN")

# Authorized Users
ADMIN_IDS = [int(i.strip()) for i in os.getenv("ADMIN_IDS", "").split(",") if i.strip()]
USER_IDS = [int(i.strip()) for i in os.getenv("USER_IDS", "").split(",") if i.strip()]
AUTHORIZED_USERS = set(ADMIN_IDS + USER_IDS)

app = Client("video_scraper_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# Authorization Filter
async def is_authorized(_, __, message):
    user_id = message.from_user.id
    if user_id in AUTHORIZED_USERS:
        return True
    await message.reply_text("⛔ You are not authorized to use this bot.")
    return False

auth_filter = filters.create(is_authorized)

# User states: {user_id: mode}
user_modes = {}
# Pending downloads: {user_id: video_info}
pending_downloads = {}
# Pending selections: {user_id: [videos]}
pending_selections = {}
# Pending keyword edit: {user_id: True}
pending_keyword_edits = {}

# User Settings: {user_id: {setting: value}}
user_settings = {}

def get_user_settings(user_id):
    if user_id not in user_settings:
        # Default settings from scraper's initial load
        scraper = VideoScraper()
        user_settings[user_id] = {
            "keywords_enabled": False,
            "discovery_mode": "list", # "list" or "first"
            "keywords": scraper.config.get("keywords", [])
        }
    return user_settings[user_id]

executor = ThreadPoolExecutor(max_workers=5)
TG_MAX_SIZE = 2 * 1024 * 1024 * 1024 # 2GB

def get_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🎬 Single Download", callback_data="set_single"),
            InlineKeyboardButton("📂 Full Page Scrape", callback_data="set_full")
        ]
    ])

def get_settings_keyboard(user_id, mode):
    settings = get_user_settings(user_id)
    kw_status = "✅ ON" if settings["keywords_enabled"] else "❌ OFF"
    disc_status = "📋 List" if settings["discovery_mode"] == "list" else "⬇️ First"
    kw_list = ", ".join(settings["keywords"]) if settings["keywords"] else "None"
    if len(kw_list) > 20: kw_list = kw_list[:17] + "..."

    buttons = [
        [InlineKeyboardButton(f"🏷️ Keywords: {kw_status}", callback_data=f"toggle_kw_{mode}")],
        [InlineKeyboardButton(f"📋 Discovery: {disc_status}", callback_data=f"toggle_disc_{mode}")],
        [InlineKeyboardButton(f"✍️ Edit Keywords ({kw_list})", callback_data=f"edit_kw_{mode}")],
        [InlineKeyboardButton("🚀 Start Scanning", callback_data=f"start_{mode}")],
        [InlineKeyboardButton("◀️ Back", callback_data="back_to_main")]
    ]
    return InlineKeyboardMarkup(buttons)

def get_large_file_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🖥️ Download to PC", callback_data="large_pc"),
            InlineKeyboardButton("❌ Skip", callback_data="large_skip")
        ]
    ])

def format_video_list(videos):
    """Format list of found videos for display"""
    lines = ["🎬 **Found these videos:**\n"]
    for i, v in enumerate(videos, 1):
        size = v.get('size', 'Unknown')
        duration = v.get('duration')
        dur_str = f" | ⏱️ {duration:.0f}s" if duration else ""
        lines.append(f"{i}. 📦 {size}{dur_str}")
    lines.append("\n📥 Select a video to download:")
    return "\n".join(lines)

def get_video_selection_keyboard(videos):
    """Create inline buttons for video selection"""
    buttons = []
    # Maximum 10 buttons to avoid UI clutter
    for i, v in enumerate(videos[:10]):
        size = v.get('size', '?')
        buttons.append([InlineKeyboardButton(f"#{i+1} - {size}", callback_data=f"vid_{i}")])
    buttons.append([InlineKeyboardButton("❌ Cancel", callback_data="vid_cancel")])
    return InlineKeyboardMarkup(buttons)

@app.on_message(filters.command("start") & auth_filter)
async def start_command(client, message):
    user_id = message.from_user.id
    user_modes[user_id] = user_modes.get(user_id, "single")
    mode_text = "Single Download" if user_modes[user_id] == "single" else "Full Page Scrape"
    
    await message.reply_text(
        f"👋 Welcome to the Video Scraper Bot!\n\n"
        f"Current Mode: **{mode_text}**\n\n"
        "1️⃣ **Single Download**: Sends the video file from the link.\n"
        "2️⃣ **Full Page Scrape**: Scans the page and returns a CSV with all links.\n\n"
        "Choose your mode below and then send me a link!",
        reply_markup=get_keyboard()
    )

@app.on_callback_query(filters.regex("^(mode_|large_|vid_|set_|toggle_|edit_|start_|back_)"))
async def handle_callbacks(client, callback_query):
    user_id = callback_query.from_user.id
    if user_id not in AUTHORIZED_USERS:
        await callback_query.answer("⛔ Operation not allowed.", show_alert=True)
        return
    data = callback_query.data
    
    if data.startswith("set_"):
        mode = data.split("_")[1]
        user_modes[user_id] = mode
        mode_text = "Single Download" if mode == "single" else "Full Page Scrape"
        await callback_query.edit_message_text(
            f"⚙️ **{mode_text} Settings**\n\nConfigure your scan before sending a link:",
            reply_markup=get_settings_keyboard(user_id, mode)
        )

    elif data.startswith("toggle_"):
        _, target, mode = data.split("_")
        settings = get_user_settings(user_id)
        if target == "kw":
            settings["keywords_enabled"] = not settings["keywords_enabled"]
        elif target == "disc":
            settings["discovery_mode"] = "first" if settings["discovery_mode"] == "list" else "list"
        
        await callback_query.edit_message_reply_markup(reply_markup=get_settings_keyboard(user_id, mode))

    elif data.startswith("edit_kw_"):
        mode = data.split("_")[2]
        pending_keyword_edits[user_id] = mode
        settings = get_user_settings(user_id)
        curr = ", ".join(settings["keywords"]) if settings["keywords"] else "None"
        await callback_query.message.reply_text(
            f"✍️ **Edit Keywords**\n\nCurrent: `{curr}`\n\nSend a new list of keywords separated by commas, or send 'cancel' to abort."
        )
        await callback_query.answer()

    elif data.startswith("start_"):
        mode = data.split("_")[1]
        user_modes[user_id] = mode
        mode_text = "Single Download" if mode == "single" else "Full Page Scrape"
        await callback_query.edit_message_text(
            f"🚀 **{mode_text} Active**\n\nSend me a URL to begin scanning with current settings.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⚙️ Settings", callback_data=f"set_{mode}")]])
        )

    elif data == "back_to_main":
        await callback_query.edit_message_text(
            "👋 Welcome to the Video Scraper Bot!\n\nChoose your mode below and then send me a link!",
            reply_markup=get_keyboard()
        )

    elif data.startswith("mode_"):
        new_mode = data.split("_")[1]
        current_mode = user_modes.get(user_id, "single")
        mode_text = "Single Download" if new_mode == "single" else "Full Page Scrape"
        
        # Check if mode actually changed to avoid MESSAGE_NOT_MODIFIED error
        if current_mode == new_mode:
            await callback_query.answer(f"Already in {mode_text} mode")
            return
        
        user_modes[user_id] = new_mode
        await callback_query.answer(f"Mode changed to: {mode_text}")
        await callback_query.edit_message_text(
            f"👋 Welcome to the Video Scraper Bot!\n\n"
            f"Current Mode: **{mode_text}**\n\n"
            "1️⃣ **Single Download**: Sends the video file from the link.\n"
            "2️⃣ **Full Page Scrape**: Scans the page and returns a CSV with all links.\n\n"
            "Choose your mode below and then send me a link!",
            reply_markup=get_keyboard()
        )
    
    elif data == "large_pc":
        video_info = pending_downloads.get(user_id)
        if not video_info:
            await callback_query.answer("❌ Request expired or not found.")
            return
        
        await callback_query.edit_message_text("⏳ Downloading to local PC... please wait.")
        try:
            loop = asyncio.get_event_loop()
            results = await loop.run_in_executor(executor, run_download_only, [video_info])
            if results and results[0].get('local_path'):
                path = results[0]['local_path']
                await callback_query.message.reply_text(f"✅ Successfully downloaded to PC:\n`{path}`")
            else:
                await callback_query.message.reply_text("❌ Download failed.")
        except Exception as e:
            await callback_query.message.reply_text(f"❌ Error: {e}")
        finally:
            pending_downloads.pop(user_id, None)

    elif data == "large_skip":
        pending_downloads.pop(user_id, None)
        await callback_query.answer("Download cancelled.")
        await callback_query.edit_message_text("❌ Download cancelled by user.")

    elif data.startswith("vid_"):
        if data == "vid_cancel":
            pending_selections.pop(user_id, None)
            await callback_query.edit_message_text("❌ Download cancelled.")
            return

        idx = int(data.split("_")[1])
        videos = pending_selections.get(user_id, [])
        if not videos or idx >= len(videos):
            await callback_query.answer("❌ Selection expired or invalid.")
            return

        video_info = videos[idx]
        pending_selections.pop(user_id, None) # Clear after selection
        
        # Proceed with the selected video
        await callback_query.edit_message_text(f"⏳ Selected video ({video_info['size']}). Processing...")
        
        size_bytes = video_info.get('size_bytes', 0)
        if size_bytes > TG_MAX_SIZE:
            pending_downloads[user_id] = video_info
            await callback_query.edit_message_text(
                f"⚠️ **File is too large for Telegram!** ({video_info['size']})\n\n"
                "Telegram limits uploads to 2GB. What would you like to do?",
                reply_markup=get_large_file_keyboard()
            )
            return

        # Normal download and upload
        try:
            loop = asyncio.get_event_loop()
            results = await loop.run_in_executor(executor, run_download_only, [video_info])
            
            if results and results[0].get('local_path'):
                path = results[0]['local_path']
                title = results[0].get('title', 'video')
                upload_text = f"📤 **Uploading video to Telegram...**\n`{title}`"
                await callback_query.edit_message_text(upload_text)
                
                # Get metadata and thumb
                meta = get_video_metadata(path)
                thumb = generate_thumbnail(path)
                
                await callback_query.message.reply_video(
                    path, 
                    caption=f"✅ Captured: {title}",
                    duration=meta['duration'],
                    width=meta['width'],
                    height=meta['height'],
                    thumb=thumb,
                    progress=upload_progress,
                    progress_args=(callback_query.message, upload_text)
                )
                
                # Cleanup thumbnail if generated
                if thumb and os.path.exists(thumb):
                    os.remove(thumb)

                # Optional: cleanup
                # os.remove(path)
                try:
                    await callback_query.message.delete()
                except: pass
            else:
                await callback_query.edit_message_text("❌ Download failed. Check logs for details.")
        except Exception as e:
            logger.error(f"Error during video download/upload: {e}")
            await callback_query.edit_message_text(f"❌ Error: {str(e)[:100]}")

async def upload_progress(current, total, message, bot_msg_text):
    """Callback for Telegram upload progress"""
    if total == 0: return
    percent = (current / total) * 100
    bar = moon_progress_bar(percent)
    new_text = f"{bot_msg_text}\n\n{bar} **{percent:.1f}%**"
    
    # Simple rate limiting for edits (every 10%)
    if not hasattr(upload_progress, "last_percent"):
        upload_progress.last_percent = -10
        
    if abs(percent - upload_progress.last_percent) >= 5 or percent >= 100:
        upload_progress.last_percent = percent
        try:
            await message.edit_text(new_text)
        except:
            pass


def run_sniff_only(url, keywords=None, scraper_ref=None):
    config = {"wait_timeout": 30}
    if keywords:
        config["keywords"] = keywords
    scraper = VideoScraper(config=config)
    if scraper_ref is not None:
        scraper_ref[0] = scraper
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
        result = scraper._sniff_url(context, url)
        browser.close()
    return result

def run_download_only(results, scraper=None):
    if scraper is None:
        scraper = VideoScraper(config={"wait_timeout": 30})
    return scraper.download_videos(results, auto_download=True)

def run_scrape_full(url, keywords=None, scraper_ref=None):
    config = {"wait_timeout": 30}
    if keywords:
        config["keywords"] = keywords
    scraper = VideoScraper(config=config)
    if scraper_ref is not None:
        scraper_ref[0] = scraper
        
    results = scraper.scrape_full(url)
    if results:
        scraper.export_to_csv(results)
        output_file = os.path.join('output', scraper.config.get('output_file', 'videos.csv'))
        return output_file
    return None

@app.on_message(filters.text & auth_filter)
async def handle_message(client, message):
    user_id = message.from_user.id
    text = message.text.strip()

    # Handle keyword editing
    if user_id in pending_keyword_edits:
        mode = pending_keyword_edits.pop(user_id)
        if text.lower() == 'cancel':
            await message.reply_text("❌ Keyword edit cancelled.")
        else:
            new_kws = [k.strip() for k in text.split(",") if k.strip()]
            settings = get_user_settings(user_id)
            settings["keywords"] = new_kws
            await message.reply_text(f"✅ Keywords updated to: `{', '.join(new_kws)}`")
        
        # Show settings again
        await message.reply_text(
            f"⚙️ Settings",
            reply_markup=get_settings_keyboard(user_id, mode)
        )
        return

    # Handle Link
    if text.startswith(("http://", "https://")):
        url = text
        mode = user_modes.get(user_id, "single")
        settings = get_user_settings(user_id)
        
        status_msg = await message.reply_text("🔍 **Initializing scan...**")
        
        try:
            loop = asyncio.get_event_loop()
            
            # Use user-specific keywords if enabled
            keywords = settings["keywords"] if settings["keywords_enabled"] else None
            
            # Shared scraper reference for live updates
            scraper_ref = [None]
            scan_task = None
            download_task = None
            
            # Background update loop
            async def update_progress():
                last_status = ""
                while True:
                    await asyncio.sleep(4) # Check every 4-5 seconds
                    if scraper_ref[0]:
                        curr_status = scraper_ref[0].status
                        found = scraper_ref[0].found_count
                        new_text = (
                            f"🔍 **Scanning Activity**\n"
                            f"🌐 URL: `{url[:40]}...`\n"
                            f"📡 Status: `{curr_status}`\n"
                            f"🎯 Found: `{found}` streams"
                        )
                        if new_text != last_status:
                            try:
                                await status_msg.edit_text(new_text)
                                last_status = new_text
                            except: pass
                    if scan_task and scan_task.done() and (not download_task or download_task.done()):
                        break

            if mode == "single":
                scan_task = loop.run_in_executor(executor, run_sniff_only, url, keywords, scraper_ref)
                updater_task = asyncio.create_task(update_progress())
                results = await scan_task
                await updater_task # Wait for final update
                
                if not results:
                    await status_msg.edit_text("❌ No video stream detected (or filtered). Check logs.")
                    return

                # Check discovery mode
                if settings["discovery_mode"] == "first":
                    video_info = results[0]
                    size_bytes = video_info.get('size_bytes', 0)
                    if size_bytes > TG_MAX_SIZE:
                        pending_downloads[user_id] = video_info
                        await status_msg.delete()
                        await message.reply_text(
                            f"⚠️ **File is too large for Telegram!** ({video_info['size']})\n\n"
                            "Telegram limits uploads to 2GB. What would you like to do?",
                            reply_markup=get_large_file_keyboard()
                        )
                        return

                    download_task = loop.run_in_executor(executor, run_download_only, [video_info], scraper_ref[0])
                    results = await download_task
                    if results and results[0].get('local_path'):
                        path = results[0]['local_path']
                        title = results[0].get('title', 'video')
                        upload_text = f"📤 **Uploading video to Telegram...**\n`{title}`"
                        await status_msg.edit_text(upload_text)
                        
                        # Get metadata and thumb
                        meta = get_video_metadata(path)
                        thumb = generate_thumbnail(path)
                        
                        await message.reply_video(
                            path, 
                            caption=f"✅ Captured: {title}",
                            duration=meta['duration'],
                            width=meta['width'],
                            height=meta['height'],
                            thumb=thumb,
                            progress=upload_progress,
                            progress_args=(status_msg, upload_text)
                        )
                        
                        # Cleanup thumbnail
                        if thumb and os.path.exists(thumb):
                            os.remove(thumb)
                        # await status_msg.delete() # Don't delete yet, it might be used by progress

                    else:
                        await status_msg.edit_text("❌ Download failed.")
                else:
                    # List mode
                    pending_selections[user_id] = results
                    await status_msg.delete()
                    await message.reply_text(
                        format_video_list(results),
                        reply_markup=get_video_selection_keyboard(results)
                    )
            
            else: # Full scrape
                scan_task = loop.run_in_executor(executor, run_scrape_full, url, keywords, scraper_ref)
                updater_task = asyncio.create_task(update_progress())
                csv_path = await scan_task
                await updater_task
                
                if csv_path and os.path.exists(csv_path):
                    upload_text = "📤 **Uploading results...**"
                    await status_msg.edit_text(upload_text)
                    await message.reply_document(
                        csv_path, 
                        caption="✅ Scrape results exported to CSV.",
                        progress=upload_progress,
                        progress_args=(status_msg, upload_text)
                    )

                else:
                    await status_msg.edit_text("❌ No videos found on this page and its links.")

        except Exception as e:
            logger.error(f"Error during processing: {e}")
            await status_msg.edit_text(f"❌ An error occurred: {str(e)[:100]}")
    else:
        if not text.startswith("/"):
            await message.reply_text("❌ Please send a valid URL starting with http or https.")

# Remove the old handle_link if it exists (it's covered by handle_message now)

if __name__ == "__main__":
    print("🤖 Bot is starting...")
    app.run()
