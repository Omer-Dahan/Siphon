"""
Main entry point for the JDownloader 2 Telegram Bot.
This module handles Telegram event registration and authentication.
"""
import os
import asyncio
import logging
from dotenv import load_dotenv
from pyrogram import Client, filters
from pyrogram.errors import MessageNotModified

from jd_client import get_jd_client
from bot_logic import (
    user_sessions, jd_toggle_states, jd_linkgrabber_cache,
    user_pagination, active_scans, executor, process_jd_links,
    monitor_jd_downloads, get_jd_toggle_keyboard
)

# Load environment variables
load_dotenv()

# Logger setup
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Constants
API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_IDS = [int(i.strip()) for i in os.getenv("ADMIN_IDS", "").split(",") if i.strip()]
USER_IDS = [int(i.strip()) for i in os.getenv("USER_IDS", "").split(",") if i.strip()]
AUTHORIZED_USERS = set(ADMIN_IDS + USER_IDS)

# Initialize Pyrogram Client
app = Client(
    "siphon_bot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN
)

# Check for JD availability
JD_AVAILABLE = bool(os.getenv("JD_EMAIL") and os.getenv("JD_PASSWORD"))

# Authorization Filter
async def is_authorized(_client, _query, message):
    """Filter to check if a user is authorized."""
    user_id = message.from_user.id if message.from_user else None
    return user_id in AUTHORIZED_USERS

auth_filter = filters.create(is_authorized)

# ============ Event Handlers ============

@app.on_message(filters.command("start") & auth_filter)
async def start_command(_client, message):  # vulture: ignore
    """Handle /start command."""
    await message.reply_text(
        "👋 Welcome to the JDownloader 2 Bot!\n\n"
        "Send me a link to scan and download files."
    )

@app.on_message(filters.text & auth_filter & ~filters.command(["start", "help", "settings"]))
async def handle_message(client, message):  # vulture: ignore
    """Handle incoming text messages (links)."""
    user_id = message.from_user.id
    text = message.text.strip()

    if not JD_AVAILABLE:
        await message.reply_text("❌ JDownloader 2 not configured.")
        return

    # Extract URL (Simple regex or basic string check)
    if "://" in text:
        urls = [text] # Simplified
        msg = await message.reply_text("🔍 **Processing link...**")
        await process_jd_links(client, user_id, msg, urls)
    else:
        await message.reply_text("❓ Please send a valid link.")

@app.on_callback_query(auth_filter)
async def handle_callbacks(client, callback_query):  # vulture: ignore
    """Handle callback queries."""
    user_id = callback_query.from_user.id
    data = callback_query.data

    if data == "scan_stop":
        active_scans[user_id] = False
        # Optional: Call abort_crawling here too
        jd = get_jd_client()
        await asyncio.get_event_loop().run_in_executor(executor, jd.abort_crawling)
        await callback_query.answer("🛑 Stopping scan...")
        return

    if data.startswith("jd_toggle_"):
        await _handle_toggle(user_id, data, callback_query)
    elif data.startswith("jd_page_"):
        await _handle_pagination(user_id, data, callback_query)
    elif data == "jd_select_all":
        await _handle_bulk_select(user_id, callback_query, True)
    elif data == "jd_deselect_all":
        await _handle_bulk_select(user_id, callback_query, False)
    elif data == "jd_refresh":
        await callback_query.answer("🔄 Refreshing...")
        await process_jd_links(client, user_id, callback_query.message, [], False)
    elif data == "jd_cancel":
        await _handle_cancel(user_id, callback_query)
    elif data == "jd_confirm":
        await _handle_confirm(client, user_id, callback_query)
    elif data == "jd_cancel_active":
        await _handle_cancel_active(user_id, callback_query)
    elif data == "jd_noop":
        await callback_query.answer()
    elif data == "jd_add_more":
        await callback_query.answer()
        await _client.send_message(user_id, "🔗 כעת שלח אליי עוד קישורים, ואז לחץ על '🔄 רענון'.")

async def _handle_toggle(user_id, data, callback_query):
    uuid = data.replace("jd_toggle_", "")
    toggles = jd_toggle_states.get(user_id, {})
    toggles[uuid] = not toggles.get(uuid, True)
    jd_toggle_states[user_id] = toggles
    links = jd_linkgrabber_cache.get(user_id, [])
    page = user_pagination.get(user_id, 0)
    try:
        await callback_query.edit_message_reply_markup(
            get_jd_toggle_keyboard(user_id, links, page)
        )
    except MessageNotModified:
        pass
    await callback_query.answer()

async def _handle_pagination(user_id, data, callback_query):
    page = int(data.split("_")[2])
    user_pagination[user_id] = page
    links = jd_linkgrabber_cache.get(user_id, [])
    try:
        await callback_query.edit_message_reply_markup(
            get_jd_toggle_keyboard(user_id, links, page)
        )
    except MessageNotModified:
        pass
    await callback_query.answer()

async def _handle_bulk_select(user_id, callback_query, select_all):
    links = jd_linkgrabber_cache.get(user_id, [])
    jd_toggle_states[user_id] = {str(l['uuid']): select_all for l in links}
    try:
        await callback_query.edit_message_reply_markup(
            get_jd_toggle_keyboard(user_id, links, user_pagination.get(user_id, 0))
        )
    except MessageNotModified:
        pass
    await callback_query.answer("✅ All selected" if select_all else "❌ All deselected")

async def _handle_cancel(user_id, callback_query):
    jd_linkgrabber_cache.pop(user_id, None)
    jd_toggle_states.pop(user_id, None)
    try:
        jd = get_jd_client()
        await asyncio.get_event_loop().run_in_executor(executor, jd.clear_linkgrabber)
    except Exception:  # pylint: disable=broad-except
        pass
    await callback_query.edit_message_text("❌ Session cancelled.")

async def _handle_confirm(client, user_id, callback_query):
    links = jd_linkgrabber_cache.get(user_id, [])
    toggles = jd_toggle_states.get(user_id, {})
    selected = [l['uuid'] for l in links if toggles.get(str(l['uuid']), True)]
    if not selected:
        await callback_query.answer("⚠️ Please select at least one file!", show_alert=True)
        return
    await callback_query.edit_message_text("🚀 **Starting downloads...**")

    # Actually move them to download list
    jd = get_jd_client()
    await asyncio.get_event_loop().run_in_executor(
        executor, jd.move_to_downloads, selected
    )

    asyncio.create_task(monitor_jd_downloads(client, user_id, callback_query.message, selected))
    jd_linkgrabber_cache.pop(user_id, None)
    jd_toggle_states.pop(user_id, None)
    # Clear linkgrabber in JD after moving links
    try:
        await asyncio.get_event_loop().run_in_executor(executor, jd.clear_linkgrabber)
    except Exception:  # pylint: disable=broad-except
        pass

async def _handle_cancel_active(user_id, callback_query):
    state = user_sessions.get(user_id)
    if state:
        state.is_active = False
        await callback_query.answer("⏹️ Stopping session...", show_alert=True)
    else:
        await callback_query.answer("⚠️ No active session found.")

if __name__ == "__main__":
    app.run()
