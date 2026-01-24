import os
import asyncio
import logging
from pyrogram import Client, filters, types
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from video_scraper import VideoScraper
from dotenv import load_dotenv
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

executor = ThreadPoolExecutor(max_workers=5)
TG_MAX_SIZE = 2 * 1024 * 1024 * 1024 # 2GB

def get_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📥 Single Download", callback_data="mode_single"),
            InlineKeyboardButton("📂 Full Page Scrape", callback_data="mode_full")
        ]
    ])

def get_large_file_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🖥️ Download to PC", callback_data="large_pc"),
            InlineKeyboardButton("❌ Skip", callback_data="large_skip")
        ]
    ])

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

@app.on_callback_query(filters.regex("^(mode_|large_)"))
async def handle_callbacks(client, callback_query):
    user_id = callback_query.from_user.id
    if user_id not in AUTHORIZED_USERS:
        await callback_query.answer("⛔ Operation not allowed.", show_alert=True)
        return
    data = callback_query.data
    
    if data.startswith("mode_"):
        new_mode = data.split("_")[1]
        user_modes[user_id] = new_mode
        mode_text = "Single Download" if new_mode == "single" else "Full Page Scrape"
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

def run_sniff_only(url):
    scraper = VideoScraper(config={"wait_timeout": 30})
    # We use a modified internal call or just scrape_single but stop before download
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(user_agent="Mozilla/5.0 ...")
        result = scraper._sniff_url(context, url)
        browser.close()
    return result

def run_download_only(results):
    scraper = VideoScraper(config={"wait_timeout": 30})
    return scraper.download_videos(results, auto_download=True)

def run_scrape_full(url):
    scraper = VideoScraper(config={"wait_timeout": 30})
    results = scraper.scrape_full(url)
    if results:
        scraper.export_to_csv(results)
        output_file = os.path.join('output', scraper.config.get('output_file', 'videos.csv'))
        return output_file
    return None

@app.on_message(filters.text & ~filters.command(["start"]) & auth_filter)
async def handle_link(client, message):
    url = message.text.strip()
    if not url.startswith(("http://", "https://")):
        await message.reply_text("❌ Please send a valid URL starting with http or https.")
        return

    user_id = message.from_user.id
    mode = user_modes.get(user_id, "single")
    
    status_msg = await message.reply_text("🔍 Processing your request... this may take a minute.")
    
    try:
        loop = asyncio.get_event_loop()
        
        if mode == "single":
            video_info = await loop.run_in_executor(executor, run_sniff_only, url)
            
            if not video_info:
                await status_msg.edit_text("❌ No video stream detected on this page.")
                return

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

            # Normal download and upload
            await status_msg.edit_text(f"⏳ Found video ({video_info['size']}). Downloading...")
            results = await loop.run_in_executor(executor, run_download_only, [video_info])
            
            if results and results[0].get('local_path'):
                path = results[0]['local_path']
                title = results[0].get('title', 'video')
                await status_msg.edit_text("📤 Uploading video to Telegram...")
                await message.reply_video(path, caption=f"✅ Captured: {title}")
                # Optional: cleanup
                # os.remove(path)
            else:
                await status_msg.edit_text("❌ Download failed.")
        
        else: # Full scrape
            csv_path = await loop.run_in_executor(executor, run_scrape_full, url)
            if csv_path and os.path.exists(csv_path):
                await status_msg.edit_text("📤 Uploading results...")
                await message.reply_document(csv_path, caption="✅ Scrape results exported to CSV.")
            else:
                await status_msg.edit_text("❌ No videos found on this page and its links.")

    except Exception as e:
        logger.error(f"Error during processing: {e}")
        await status_msg.edit_text(f"❌ An error occurred: {str(e)[:100]}")

if __name__ == "__main__":
    print("🤖 Bot is starting...")
    app.run()
