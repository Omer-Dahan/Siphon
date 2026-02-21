"""
Logic and state management for the JDownloader 2 Telegram Bot.
This module handles session state, JDownloader monitoring, and file uploads.
"""
import os
import time
import asyncio
import logging
from typing import Dict, List
from concurrent.futures import ThreadPoolExecutor

from pyrogram.types import (
    InlineKeyboardMarkup, InlineKeyboardButton,
    InputMediaPhoto
)

from jd_client import get_jd_client
from utils import (
    format_size, moon_progress_bar, get_video_metadata,
    needs_conversion, convert_to_mp4, split_video
)

# Configuration & Logging
logger = logging.getLogger(__name__)
executor = ThreadPoolExecutor(max_workers=4)

VIDEO_EXTENSIONS = {
    '.mp4', '.mkv', '.avi', '.mov', '.wmv', '.flv', '.webm',
    '.m4v', '.mpg', '.mpeg', '.3gp', '.ts'
}

# ============ Shared State ============

# pylint: disable=too-few-public-methods
class SessionState:
    """Class to track the state of a user's download/upload session."""
    def __init__(self):
        self.is_active = True
        self.jd_downloads = []  # List of dicts from JD
        self.active_uploads = {}  # {filename: percentage}
        self.completed_tasks = []  # List of filenames
        self.start_time = time.time()

user_sessions: Dict[int, SessionState] = {}
jd_toggle_states: Dict[int, Dict[str, bool]] = {}  # {user_id: {link_uuid: bool}}
jd_linkgrabber_cache: Dict[int, List[dict]] = {}  # {user_id: [links]}
user_pagination: Dict[int, int] = {}  # {user_id: page_index}
active_scans: Dict[int, bool] = {}  # {user_id: bool}

# ============ UI Helpers ============

def format_jd_list_message(links: List[dict]) -> str:
    """Format the LinkGrabber results for Telegram message."""
    if not links:
        return "❌ No links found in LinkGrabber."

    msg = "📋 **קבצים שנמצאו בסריקה:**\n━━━━━━━━━━━━━━━━━━\n"
    for idx, link in enumerate(links):
        size = format_size(link.get("bytes_total", 0))
        name = link.get("name", "Unknown")
        msg += f"{idx+1}. 📦 `{name}`\n   └ ⚖️ {size}\n"
    msg += "━━━━━━━━━━━━━━━━━━\nבחר את הקבצים שברצונך להוריד המצאו מטה:"
    return msg

def get_jd_toggle_keyboard(user_id: int, links: List[dict], page: int = 0) -> InlineKeyboardMarkup:
    """Generate inline keyboard for JDownloader file selection with pagination."""
    start_idx = page * 8
    end_idx = start_idx + 8

    toggle_states = jd_toggle_states.get(user_id, {})
    buttons = []

    # Selection buttons
    for link in links[start_idx:end_idx]:
        uuid = str(link.get("uuid"))
        is_selected = toggle_states.get(uuid, True)
        icon = "✅" if is_selected else "❌"
        # Shorten name for button
        name = link.get("name", "Unknown")
        display_name = (name[:30] + '...') if len(name) > 33 else name
        buttons.append([InlineKeyboardButton(
            f"{icon} {display_name}",
            callback_data=f"jd_toggle_{uuid}"
        )])

    # Pagination row
    nav_row = []
    total_pages = (len(links) + 7) // 8
    if total_pages > 1:
        if page > 0:
            nav_row.append(InlineKeyboardButton("⬅️", callback_data=f"jd_page_{page-1}"))
        nav_row.append(InlineKeyboardButton(f"📄 {page+1}/{total_pages}", callback_data="jd_noop"))
        if page < total_pages - 1:
            nav_row.append(InlineKeyboardButton("➡️", callback_data=f"jd_page_{page+1}"))
    if nav_row:
        buttons.append(nav_row)

    # Bulk actions and controls
    buttons.append([
        InlineKeyboardButton("✨ בחר הכל", callback_data="jd_select_all"),
        InlineKeyboardButton("🧹 בטל הכל", callback_data="jd_deselect_all")
    ])
    buttons.append([
        InlineKeyboardButton("🔄 רענון", callback_data="jd_refresh"),
        InlineKeyboardButton("➕ הוסף קישורים", callback_data="jd_add_more")
    ])
    buttons.append([
        InlineKeyboardButton("🚀 התחל הורדה", callback_data="jd_confirm"),
        InlineKeyboardButton("🗑️ ביטול", callback_data="jd_cancel")
    ])

    return InlineKeyboardMarkup(buttons)

def render_dashboard(state: SessionState) -> str:
    """Render a comprehensive status dashboard for active downloads/uploads."""
    text = "🚀 **מרכז בקרה - Siphon Bot**\n"
    text += "━━━━━━━━━━━━━━━━━━\n\n"

    # 1. JDownloader Active Downloads
    active = [d for d in state.jd_downloads if not d.get("finished")]
    if active:
        text += "📥 **בהורדה מ-JD2:**\n"
        for dl in active[:3]: # Show top 3
            name = dl.get("name", "Unknown")
            prog = dl.get("progress", 0)
            p_bar = moon_progress_bar(prog)
            speed = format_size(dl.get("speed", 0)) + "/s"
            text += f"🔹 `{name[:35]}...`\n   {p_bar} {prog:.1f}%\n"
            text += f"   └ ⚡ {speed}\n"
        if len(active) > 3:
            text += f"   _...ועוד {len(active)-3} קבצים בתור_\n"
        text += "\n"
    elif state.is_active:
        text += "⌛ מעבד נתונים ב-JDownloader...\n\n"

    # 2. Telegram Uploads
    if state.active_uploads:
        text += "📤 **בהעלאה לטלגרם:**\n"
        for name, prog in state.active_uploads.items():
            p_bar = moon_progress_bar(prog)
            text += f"🔹 `{name[:35]}...`\n   {p_bar} {prog:.1f}%\n"
        text += "\n"

    # 3. Completion Summary
    if state.completed_tasks and not state.is_active and not state.active_uploads:
        text += f"✅ **הסתיים!**\nקבצים שהושלמו: {len(state.completed_tasks)}\n"
        return text

    if not active and not state.active_uploads and not state.is_active:
        return text + "🏁 כל המשימות הושלמו או שהתור ריק."

    # ETA Calculation (Simple)
    elapsed = time.time() - state.start_time
    text += f"⏱️ זמן שחלף: `{int(elapsed//60)}m {int(elapsed%60)}s`\n"
    return text

# ============ Background Tasks ============

async def dashboard_loop(_client, _user_id, message, state: SessionState):
    """Periodically update the dashboard message."""
    last_text = ""
    error_count = 0
    # pylint: disable=simplifiable-condition
    active_cond = (
        state.is_active or state.active_uploads or
        (state.jd_downloads and not all(d.get("finished") for d in state.jd_downloads))
    )
    # pylint: enable=simplifiable-condition
    while active_cond:
        try:
            current_text = render_dashboard(state)
            if current_text != last_text:
                await message.edit_text(
                    current_text,
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("🛑 ביטול הורדה", callback_data="jd_cancel_active")]
                    ])
                )
                last_text = current_text
                error_count = 0
            await asyncio.sleep(2.5)
        except Exception as err:  # pylint: disable=broad-except
            logger.error("Error in dashboard_loop: %s", err)
            error_count += 1
            if error_count > 5:
                logger.error("Too many errors in dashboard_loop, stopping.")
                break
            await asyncio.sleep(5)
        # pylint: disable=simplifiable-condition
        active_cond = (
            state.is_active or state.active_uploads or
            (state.jd_downloads and not all(d.get("finished") for d in state.jd_downloads))
        )
        # pylint: enable=simplifiable-condition
    # Final update
    try:
        await message.delete()
    except Exception as err:  # pylint: disable=broad-except
        logger.error("Error on final dashboard update: %s", err)

async def send_album_to_telegram(_client, user_id: int, image_batch: list, state: SessionState):
    """Send a batch of images as an album."""
    if not image_batch:
        return

    jd = None
    try:
        jd = get_jd_client()
    except Exception as err: # pylint: disable=broad-except
        logger.error("Failed to get JD client for album cleanup: %s", err)

    # Telegram allows max 10 files per album
    for i in range(0, len(image_batch), 10):
        chunk = image_batch[i:i+10]
        media_group = [InputMediaPhoto(media=item['path'], caption=item['name'] if j==0 else "")
                       for j, item in enumerate(chunk)]

        paths_to_clean = [item['path'] for item in chunk]
        uuids_to_remove = [item['uuid'] for item in chunk if item.get('uuid')]

        try:
            await _client.send_media_group(chat_id=user_id, media=media_group)
            for path in paths_to_clean:
                if os.path.exists(path):
                    try:
                        os.remove(path)
                    except Exception as err:  # pylint: disable=broad-except
                        logger.error("Failed to remove file %s: %s", path, err)
            if jd and uuids_to_remove:
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(executor, jd.remove_links, uuids_to_remove)
            for item in chunk:
                state.completed_tasks.append(item['name'])
        except Exception as err:  # pylint: disable=broad-except
            logger.error("Failed to send album: %s", err)

async def upload_jd_file_to_telegram(
        _client, user_id: int, file_path: str, state: SessionState, uuid: str = None
):
    """Upload a single file to Telegram, handling large files."""
    filename = os.path.basename(file_path)
    state.active_uploads[filename] = 0

    try:
        # Check if needs conversion
        target_path = file_path
        loop = asyncio.get_event_loop()
        needs_conv = await loop.run_in_executor(executor, needs_conversion, file_path)
        if needs_conv:
            state.active_uploads[filename] = 0.5 # Dummy progress for conversion
            target_path = await loop.run_in_executor(executor, convert_to_mp4, file_path)
            if target_path != file_path:
                state.active_uploads.pop(filename, None)
                filename = os.path.basename(target_path)
                state.active_uploads[filename] = 0.5

        # Check size (2GB limit)
        files_to_upload = [target_path]
        file_size = await loop.run_in_executor(executor, os.path.getsize, target_path)
        if file_size > 1.9 * 1024**3:
            # pylint: disable=too-many-function-args
            files_to_upload = await loop.run_in_executor(executor, split_video, target_path)

        try:
            for part_path in files_to_upload:
                p_name = os.path.basename(part_path)
                t_name = p_name if len(files_to_upload) > 1 else filename
                
                # If we switched to part names, remove the primary filename
                if len(files_to_upload) > 1 and filename in state.active_uploads:
                    state.active_uploads.pop(filename, None)

                # Closure for progress tracking
                # pylint: disable=cell-var-from-loop
                async def progress(current, total, name=t_name):
                    state.active_uploads[name] = (current / total) * 100

                try:
                    await _send_upload_chunk(_client, user_id, part_path, p_name, progress)
                finally:
                    state.active_uploads.pop(t_name, None)

                if part_path != target_path and os.path.exists(part_path):
                    os.remove(part_path)
        except Exception as chunk_err:
            logger.error("Error during chunk upload loop: %s", chunk_err)
            raise chunk_err

        # Cleanup and task completion
        if uuid:
            try:
                await asyncio.get_event_loop().run_in_executor(
                    executor, get_jd_client().remove_links, [uuid]
                )
            except Exception as err:  # pylint: disable=broad-except
                logger.warning("Failed to remove JD link %s: %s", uuid, err)

        state.completed_tasks.append(filename)

        if os.path.exists(file_path):
            os.remove(file_path)
        if target_path != file_path and os.path.exists(target_path):
            os.remove(target_path)

    except Exception as err:  # pylint: disable=broad-except
        logger.error("Upload error for %s: %s", file_path, err)
        try:
            await _client.send_message(user_id, f"❌ **שגיאה בהעלאת הקובץ:** `{filename}`\n{str(err)[:100]}")
        except Exception:
            pass
async def _send_upload_chunk(client, user_id, part_path, part_name, progress):
    """Helper to send a single video or document chunk."""
    try:
        if part_path.lower().endswith(tuple(VIDEO_EXTENSIONS)):
            # get_video_metadata is now sync and runs ffmpeg, so run in executor
            meta = await asyncio.get_event_loop().run_in_executor(
                executor, get_video_metadata, part_path
            )
            width, height, duration, thumb = meta
            await client.send_video(
                chat_id=user_id, video=part_path, caption=f"✅ {part_name}",
                duration=duration, width=width, height=height, thumb=thumb,
                progress=progress
            )
            if thumb and os.path.exists(thumb):
                os.remove(thumb)
        else:
            await client.send_document(
                chat_id=user_id, document=part_path, caption=f"✅ {part_name}",
                progress=progress
            )
    except Exception as err: # pylint: disable=broad-except
        logger.error("Upload failed for %s: %s", part_name, err)
        raise err

async def monitor_jd_downloads(_client, user_id: int, status_msg, expected_uuids: list):
    """Monitor JD downloads and trigger uploads."""
    state = SessionState()
    user_sessions[user_id] = state
    state.is_active = True

    try:
        get_jd_client().start_downloads()
    except Exception as err:  # pylint: disable=broad-except
        logger.error("Failed to start downloads: %s", err)
        state.is_active = False
        return

    asyncio.create_task(dashboard_loop(_client, user_id, status_msg, state))

    uploaded_files = set()
    image_buffer = []
    last_batch_time = time.time()

    while True:
        await asyncio.sleep(3)
        relevant = await _get_relevant_downloads(expected_uuids, state)
        if not relevant:
            continue

        for dl in relevant:
            if dl.get("finished") and dl.get("uuid") not in uploaded_files:
                uploaded_files.add(dl.get("uuid"))
                _handle_downloaded_file(_client, user_id, dl, state, image_buffer)

        _check_batch_uploads(_client, user_id, state, image_buffer, last_batch_time)

        if all(d.get("finished") for d in relevant) and \
           len(uploaded_files) >= len(relevant) and not image_buffer:
            state.is_active = False
            break

async def _get_relevant_downloads(uuids, state):
    """Fetch status and update state, return relevant items."""
    downloads = await asyncio.get_event_loop().run_in_executor(
        executor, get_jd_client().get_download_status
    )
    str_uuids = [str(u) for u in uuids]
    relevant = [d for d in downloads if str(d.get("uuid")) in str_uuids]
    state.jd_downloads = relevant
    return relevant

async def process_jd_links(_client, user_id, message_to_edit, urls, deep_scan=False):
    """Fetch links and show selection UI."""
    try:
        active_scans[user_id] = True
        for url in urls:
            await asyncio.get_event_loop().run_in_executor(
                executor, get_jd_client().add_to_linkgrabber, url, None, deep_scan
            )

        # Periodic check for links
        links = await _wait_for_links(user_id, message_to_edit, deep_scan)

        links = await asyncio.get_event_loop().run_in_executor(
            executor, get_jd_client().get_linkgrabber_links, False
        )
        unique_links = _deduplicate_links(links)

        jd_linkgrabber_cache[user_id] = unique_links
        jd_toggle_states[user_id] = {str(l['uuid']): True for l in unique_links}

        await message_to_edit.edit_text(
            format_jd_list_message(unique_links),
            reply_markup=get_jd_toggle_keyboard(user_id, unique_links, 0)
        )
    except Exception as err: # pylint: disable=broad-except
        logger.error("JD Process error: %s", err)
        await message_to_edit.edit_text(f"❌ Error: {str(err)[:50]}")
    finally:
        active_scans.pop(user_id, None)

def _deduplicate_links(links):
    """Remove duplicates and limit count."""
    unique = []
    seen = set()
    for l_item in links:
        if l_item['uuid'] not in seen:
            unique.append(l_item)
            seen.add(l_item['uuid'])
    return unique[:250]
def _is_scan_stable(links, last_count, stable_duration, deep_scan):
    if not links or len(links) <= 5 or deep_scan:
        return False
    if len(links) == last_count:
        stable_duration += 2
    else:
        stable_duration = 0
    return stable_duration >= 6

async def _update_scan_msg(msg, count):
    try:
        await msg.edit_text(
            f"⏳ **מעבד... נמצאו {count} קישורים**\nלחץ על עצור לסיום.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🛑 עצור וסנן", callback_data="scan_stop")
            ]])
        )
    except Exception:  # pylint: disable=broad-except
        pass
def _handle_downloaded_file(client, user_id, dl, state, buffer):
    """Process a finished download."""
    uuid = dl.get("uuid")
    pkg_name = dl.get("package_name", "Unknown")
    file_name = dl.get("name", "Unknown")
    dir_path = os.getenv("JD_DOWNLOAD_DIR", "downloads")
    path = os.path.join(dir_path, pkg_name, file_name)
    if not os.path.exists(path):
        path = os.path.join(dir_path, file_name)

    if os.path.exists(path):
        ext = os.path.splitext(path)[1].lower()
        if ext in {'.jpg', '.jpeg', '.png', '.webp'}:
            buffer.append({'path': path, 'uuid': uuid, 'name': file_name})
        else:
            asyncio.create_task(upload_jd_file_to_telegram(client, user_id, path, state, uuid))
    else:
        logger.warning("File not found: %s", path)

def _check_batch_uploads(client, user_id, state, buffer, last_time):
    if buffer and (len(buffer) >= 10 or time.time() - last_time > 10):
        batch = buffer[:]
        buffer.clear()
        asyncio.create_task(send_album_to_telegram(client, user_id, batch, state))
async def _wait_for_links(user_id, msg, deep_scan):
    """Wait for LinkGrabber to stabilize and return links."""
    total_wait = 30 if deep_scan else 10
    elapsed, last_count, stable_duration = 0, 0, 0
    links = []
    while elapsed < total_wait:
        if not active_scans.get(user_id):
            break
        links = await asyncio.get_event_loop().run_in_executor(
            executor, get_jd_client().get_linkgrabber_links, False
        )
        if _is_scan_stable(links, last_count, stable_duration, deep_scan):
            break
        elapsed += 2
        await asyncio.sleep(2)
        if elapsed % 4 == 0:
            await _update_scan_msg(msg, len(links))
        last_count = len(links)
    return links
