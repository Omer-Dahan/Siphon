import os
import time
import asyncio
import logging
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, InputMediaPhoto
from dotenv import load_dotenv
from utils import moon_progress_bar, get_video_metadata, generate_thumbnail, split_video, convert_to_mp4, format_size

from concurrent.futures import ThreadPoolExecutor

# JDownloader 2 Integration
try:
    from jd_client import get_jd_client, JDownloaderClient
    JD_AVAILABLE = True
except ImportError:
    JD_AVAILABLE = False

# Load environment variables
load_dotenv()

# Configure logging
log_dir = "logs"
os.makedirs(log_dir, exist_ok=True)
log_file = os.path.join(log_dir, "bot.log")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler(log_file, encoding='utf-8'),
        logging.StreamHandler()
    ]
)
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
executor = ThreadPoolExecutor(max_workers=5)
TG_MAX_SIZE = 2 * 1024 * 1024 * 1024 # 2GB

# JD2 State Management
jd_toggle_states = {}       # {user_id: {link_uuid: enabled}}
jd_linkgrabber_cache = {}   # {user_id: [links]}
user_pagination = {}        # {user_id: current_page}

class SessionState:
    def __init__(self):
        self.jd_downloads = []        # List of dicts from JD
        self.active_uploads = {}      # {filename: progress_percent}
        self.completed_tasks = []     # List of filenames
        self.total_speed = 0          # Bytes/s
        self.status = "Initializing..."
        self.is_active = True

user_sessions = {} # {user_id: SessionState}

# Authorization Filter
async def is_authorized(_, __, message):
    user_id = message.from_user.id
    if user_id in AUTHORIZED_USERS:
        return True
    await message.reply_text("⛔ You are not authorized to use this bot.")
    return False

auth_filter = filters.create(is_authorized)


# ============ Helper Functions ============

def shorten_name(name: str, max_len: int = 25) -> str:
    """Shorten a filename to fit in button text."""
    if len(name) <= max_len:
        return name
    return name[:max_len-3] + "..."



def format_jd_list_message(links: list, page: int = 0) -> str:
    """Format the JD2 LinkGrabber results as a message header."""
    total = len(links)
    return (
        f"📥 **JDownloader LinkGrabber Results**\n\n"
        f"🔗 Found **{total}** downloadable file(s).\n"
        f"Toggle items below to select/deselect, then confirm to start download.\n"
    )

def get_jd_toggle_keyboard(user_id: int, links: list, page: int = 0) -> InlineKeyboardMarkup:
    """
    Create toggle keyboard for JD2 LinkGrabber results.
    Each button shows: [✅/❌] ShortName | Size
    """
    PAGE_SIZE = 8
    total_pages = max(1, (len(links) + PAGE_SIZE - 1) // PAGE_SIZE)
    start_idx = page * PAGE_SIZE
    end_idx = min(start_idx + PAGE_SIZE, len(links))
    
    buttons = []
    toggle_states = jd_toggle_states.get(user_id, {})
    
    for link in links[start_idx:end_idx]:
        uuid = link.get("uuid")
        name = link.get("name", "Unknown")
        size = link.get("size_str", format_size(link.get("size", 0)))
        enabled = toggle_states.get(uuid, True)  # Default enabled
        
        icon = "✅" if enabled else "❌"
        short_name = shorten_name(name, 20)
        btn_text = f"{icon} {short_name} | {size}"
        
        buttons.append([InlineKeyboardButton(btn_text, callback_data=f"jd_toggle_{uuid}")])
    
    # Pagination row
    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"jd_page_{page-1}"))
    nav_row.append(InlineKeyboardButton(f"{page+1}/{total_pages}", callback_data="jd_noop"))
    if page < total_pages - 1:
        nav_row.append(InlineKeyboardButton("Next ➡️", callback_data=f"jd_page_{page+1}"))
    if nav_row:
        buttons.append(nav_row)
    
    # Action buttons - count only selected items that are in current links cache
    link_uuids = {link.get("uuid") for link in links}
    selected_count = sum(1 for uuid, v in toggle_states.items() if v and uuid in link_uuids)
    buttons.append([InlineKeyboardButton(f"🚀 Download Selected ({selected_count})", callback_data="jd_confirm")])
    buttons.append([
        InlineKeyboardButton("✅ Select All", callback_data="jd_select_all"),
        InlineKeyboardButton("❌ Deselect All", callback_data="jd_deselect_all")
    ])
    buttons.append([
        InlineKeyboardButton("🔄 Refresh List", callback_data="jd_refresh"),
        InlineKeyboardButton("🗑️ Cancel & Clear", callback_data="jd_cancel")
    ])
    
    return InlineKeyboardMarkup(buttons)

def render_dashboard(state: SessionState) -> str:
    """
    Render the dashboard message matching the user's request.
    Priority: Uploading > Downloading > Initializing
    Shows only the active file.
    """
    # 1. Check for Active Uploads
    if state.active_uploads:
        # Get the first active upload
        filename, percent = next(iter(state.active_uploads.items()))
        
        # Calculate stats (Approximation as we don't have total size for uploads easily in current dict, 
        # but we can try to find it or just show percent)
        # User template:
        # 📥 מוריד... (Status)
        # ━━━━━━━━━━━━━━━━━━
        # [MoonBar] 5%
        # 📊 4.0MiB/73.2MiB
        # 📥 קובץ: filename
        # ⚡ מהירות: 8.44MiB/s
        # ⏱️ זמן משוער: 00:08
        # 🗂 הורדה 1/4
        # ━━━━━━━━━━━━━━━━━━
        
        # Determine counter
        current_idx = len(state.completed_tasks) + 1
        total_files = state.total_files if hasattr(state, 'total_files') and state.total_files > 0 else "?"
        
        bar = moon_progress_bar(percent)
        
        # We don't have speed/eta for uploads in current state dict, only percent.
        # We will render what we have.
        return (
            f"📤 מעלה לטלגרם...\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"{bar} `{percent:.1f}%`\n"
            f"📄 קובץ: `{filename}`\n"
            f"🗂 קובץ {current_idx}/{total_files}\n"
            f"━━━━━━━━━━━━━━━━━━"
        )

    # 2. Check for Active Downloads (JD)
    if state.jd_downloads:
        # Filter active
        active = [d for d in state.jd_downloads if not d.get("finished")]
        if not active:
             if state.is_active:
                 return "⌛ מעבד..."
             return "✅ הסתיים."
             
        # --- Global Progress Calculation ---
        total_bytes_job = sum(d.get("bytes_total", 0) for d in state.jd_downloads)
        loaded_bytes_job = sum(d.get("bytes_loaded", 0) for d in state.jd_downloads)
        total_speed = state.total_speed
        
        if total_bytes_job > 0:
            percent = (loaded_bytes_job / total_bytes_job) * 100
        else:
            percent = 0
            
        bar = moon_progress_bar(percent)
        
        # Formatting
        loaded_str = format_size(loaded_bytes_job)
        total_str = format_size(total_bytes_job)
        speed_str = format_size(total_speed) + "/s"
        
        # Pick the current file being worked on (max speed or first) for the "File:" line
        current_dl = max(active, key=lambda x: x.get("speed", 0), default=active[0])
        formatted_name = shorten_name(current_dl.get("name", "Unknown"), 30)
        
        # ETA Calculation (Global)
        if total_speed > 0:
            remaining = total_bytes_job - loaded_bytes_job
            eta_seconds = remaining / total_speed
            
            m, s = divmod(int(eta_seconds), 60)
            h, m = divmod(m, 60)
            if h > 0:
                eta_str = f"{h:02d}:{m:02d}:{s:02d}"
            else:
                eta_str = f"{m:02d}:{s:02d}"
        else:
            eta_str = "?"

        # Determine counter
        files_left = len(active)
        total_files_count = len(state.jd_downloads)
        files_done = total_files_count - files_left
        
        return (
            f"📥 מוריד... (סה\"כ)\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"{bar} `{percent:.1f}%`\n"
            f"📊 `{loaded_str}`/`{total_str}`\n"
            f"📥 קובץ נוכחי: `{formatted_name}`\n"
            f"⚡ מהירות: `{speed_str}`\n"
            f"⏱️ זמן משוער: `{eta_str}`\n"
            f"🗂 קבצים: {files_done}/{total_files_count}\n"
            f"━━━━━━━━━━━━━━━━━━"
        )
        
    # 3. Default / Done
    if state.completed_tasks and not state.is_active:
        return (
            f"✅ **הסתיים!**\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"קבצים שהושלמו: {len(state.completed_tasks)}\n"
            f"━━━━━━━━━━━━━━━━━━"
        )
        
    return "⌛ ממתין..."


async def dashboard_loop(client, user_id, message, state: SessionState):
    """Periodically update the dashboard message."""
    last_text = ""
    error_count = 0
    
    while state.is_active or state.active_uploads or (state.jd_downloads and not all(d.get("finished") for d in state.jd_downloads)):
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
            
            await asyncio.sleep(2.5) # Throttled updates
            
        except Exception as e:
            error_count += 1
            logger.warning(f"Dashboard update failed: {e}")
            if error_count > 5:
                break
            await asyncio.sleep(5)
    
    # Final update
    await message.edit_text(render_dashboard(state) + "\n\n🏁 **All Tasks Done!**")


async def upload_progress_hook(current, total, filename, user_id):
    """Callback hook for upload progress."""
    if total == 0: return
    percent = (current / total) * 100
    
    if user_id in user_sessions:
        user_sessions[user_id].active_uploads[filename] = percent


async def send_album_to_telegram(client, user_id: int, image_batch: list, state: SessionState):
    """
    Send a batch of images as an album.
    image_batch: List of dicts {'path': str, 'uuid': str, 'name': str}
    """
    if not image_batch:
        return

    # Chunk into groups of 10 (Telegram Limit)
    chunks = [image_batch[i:i + 10] for i in range(0, len(image_batch), 10)]
    
    jd = None
    if JD_AVAILABLE:
        try:
            jd = get_jd_client()
        except:
            pass

    for chunk in chunks:
        media_group = []
        paths_to_clean = []
        uuids_to_remove = []
        
        for item in chunk:
            path = item['path']
            # Shorten caption to avoid clutter, or just filename
            caption = f"🖼️ {item['name']}"
            media_group.append(InputMediaPhoto(path, caption=caption))
            paths_to_clean.append(path)
            if item.get('uuid'):
                uuids_to_remove.append(item['uuid'])
        
        try:
            # logger.info(f"📤 Sending album with {len(media_group)} photos...")
            await client.send_media_group(chat_id=user_id, media=media_group)
            
            # Cleanup
            for path in paths_to_clean:
                if os.path.exists(path):
                    try:
                        os.remove(path)
                    except Exception as e:
                        logger.error(f"Failed to delete {path}: {e}")
            
            if jd and uuids_to_remove:
                loop = asyncio.get_event_loop()
                # Remove from JD
                await loop.run_in_executor(executor, jd.remove_links, uuids_to_remove)
            
            # Mark as completed
            for item in chunk:
                state.completed_tasks.append(item['name'])
                
        except Exception as e:
            logger.error(f"Failed to send album: {e}")
            # Try individual fallback? for now just log
            
async def monitor_jd_downloads(client, user_id: int, status_msg, expected_uuids: list):
    """
    Monitor JDownloader and update shared session state.
    """
    if not JD_AVAILABLE: return
    
    state = user_sessions.get(user_id)
    if not state: return

    try:
        jd = get_jd_client()
    except Exception as e:
        logger.error(f"Failed to get JD client: {e}")
        return
    
    uploaded_files = set()
    poll_interval = 3
    max_wait = 7200 # 2 hours
    elapsed = 0
    
    # Album Buffering
    image_buffer = []  # [{'path':p, 'uuid':u, 'name':n, 'time':t}]
    last_image_time = 0
    image_exts = ['.jpg', '.jpeg', '.png', '.webp', '.bmp', '.gif']
    # video_exts used global VIDEO_EXTENSIONS
    
    # Start the UI loop
    asyncio.create_task(dashboard_loop(client, user_id, status_msg, state))
    
    while elapsed < max_wait:
        await asyncio.sleep(poll_interval)
        elapsed += poll_interval
        
        try:
            loop = asyncio.get_event_loop()
            downloads = await loop.run_in_executor(executor, jd.get_download_status)
            
            # Filter relevant (Strict Privacy: Only track UUIDs we expect)
            relevant = [d for d in downloads if d.get("uuid") in expected_uuids]
            
            # Update State
            state.jd_downloads = relevant
            state.total_speed = sum(d.get("speed", 0) for d in relevant)
            
            # Check for finished files
            for dl in relevant:
                if dl.get("finished"):
                    # Use package save location if available
                    local_path = dl.get("local_path")
                    uuid = dl.get("uuid")
                    
                    if local_path and local_path not in uploaded_files and os.path.exists(local_path):
                        # Determine File Type
                        uploaded_files.add(local_path)
                        filename = os.path.basename(local_path)
                        ext = os.path.splitext(filename)[1].lower()
                        
                        if ext in image_exts:
                            # Add to buffer
                            image_buffer.append({
                                'path': local_path,
                                'uuid': uuid,
                                'name': filename,
                                'time': time.time()
                            })
                            last_image_time = time.time()
                            logger.info(f"Buffered image: {filename} (Buffer: {len(image_buffer)})")
                        else:
                            # Video/Doc -> Upload immediately
                            asyncio.create_task(upload_jd_file_to_telegram(client, user_id, local_path, state, uuid))
            
            # --- Process Album Buffer ---
            current_time = time.time()
            # Flush if >= 10 OR (not empty AND idle for > 5s)
            if len(image_buffer) >= 10 or (len(image_buffer) > 0 and current_time - last_image_time > 5):
                batch = image_buffer[:]
                image_buffer = [] # clear immediately
                asyncio.create_task(send_album_to_telegram(client, user_id, batch, state))
            
            # Check if all JD tasks are done
            all_jd_finished = relevant and all(d.get("finished") for d in relevant)
            
            # We are done if all JD tasks are finished AND all local files are handled (uploaded or buffered)
            # Wait for buffer to be empty too
            if all_jd_finished and len(uploaded_files) >= len(relevant) and not image_buffer:
                # Wait for uploads to finish is tricky without tighter tracking, 
                # but basically we stop monitoring JD.
                state.is_active = False 
                break
                
        except Exception as e:
            logger.error(f"Monitor error: {e}")
            await asyncio.sleep(5)
            
    # Final Flush of buffer if any (just in case)
    if image_buffer:
        await send_album_to_telegram(client, user_id, image_buffer, state)


async def upload_jd_file_to_telegram(client, user_id: int, file_path: str, state: SessionState, uuid: str = None):
    """
    Upload a single file from JDownloader to Telegram, updating shared state.
    Handles splitting for large files (>2GB) and cleanup after upload.
    """
    filename = os.path.basename(file_path)
    
    # Initialize in state
    state.active_uploads[filename] = 0.0
    
    try:
        # 0. Convert to MP4 (Streaming-friendly) if needed
        loop = asyncio.get_event_loop()
        
        # Check if conversion needed first to avoid unnecessary executor spawn if possible, 
        # but convert_to_mp4 handles checks efficiently too. 
        # For thread safety and non-blocking, we run it in executor.
        new_path = await loop.run_in_executor(None, convert_to_mp4, file_path)
        
        if new_path != file_path:
            # Update filename tracking if changed
            state.active_uploads.pop(filename, None)
            file_path = new_path
            filename = os.path.basename(file_path)
            state.active_uploads[filename] = 0.0
            
        # 1. Handle Large Files (Split if needed)
        # We also run split in executor to prevent blocking
        files_to_upload = await loop.run_in_executor(None, split_video, file_path, TG_MAX_SIZE)
        
        if not files_to_upload:
            logger.error(f"Failed to process/split file: {file_path}")
            state.active_uploads.pop(filename, None)
            return

        total_files = len(files_to_upload)
        
        # 2. Upload each part
        for i, part_path in enumerate(files_to_upload):
            part_name = os.path.basename(part_path)
            
            # Update state with current part name if multiple
            if total_files > 1:
                state.active_uploads.pop(filename, None)
                state.active_uploads[part_name] = 0.0
                current_tracking_name = part_name
            else:
                current_tracking_name = filename

            # Define progress callback wrapper with closure fix
            def make_progress_callback(tracking_name, uid):
                async def _progress(current, total):
                    await upload_progress_hook(current, total, tracking_name, uid)
                return _progress
            _progress = make_progress_callback(current_tracking_name, user_id)
            
            # Upload Logic
            ext = os.path.splitext(part_name)[1].lower()
            
            try:
                if ext in VIDEO_EXTENSIONS:
                    meta = get_video_metadata(part_path)
                    thumb = generate_thumbnail(part_path)
                    
                    caption = f"✅ {part_name}"
                    if total_files > 1:
                        caption += f" ({i+1}/{total_files})"
                    
                    await client.send_video(
                        chat_id=user_id,
                        video=part_path,
                        caption=caption,
                        duration=meta.get('duration', 0),
                        height=meta.get('height', 0),
                        thumb=thumb,
                        supports_streaming=True,
                        progress=_progress
                    )
                    
                    if thumb and os.path.exists(thumb):
                        os.remove(thumb)
                else:
                    await client.send_document(
                        chat_id=user_id,
                        document=part_path,
                        caption=f"✅ {part_name}",
                        progress=_progress
                    )
                
                # Success!
                logger.info(f"Uploaded: {part_name}")
                
            except Exception as e:
                logger.error(f"Failed to upload part {part_name}: {e}")
                # Don't delete original if upload fails
                raise e 
            finally:
                # Cleanup tracking
                state.active_uploads.pop(current_tracking_name, None)

            # 3. Delete part if it was a split chunk
            if part_path != file_path and os.path.exists(part_path):
                try:
                    os.remove(part_path)
                    logger.info(f"🗑️ Deleted temp part: {part_path}")
                except Exception as e:
                    logger.error(f"Failed to delete part {part_path}: {e}")

        # 4. Global Cleanup
        # Delete original file
        if os.path.exists(file_path):
            try:
                os.remove(file_path)
                logger.info(f"🗑️ Deleted original file: {file_path}")
            except Exception as e:
                logger.error(f"Failed to delete original {file_path}: {e}")
        
        # Remove from JDownloader
        if uuid and JD_AVAILABLE:
            try:
                jd = get_jd_client()
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(executor, jd.remove_links, [uuid])
                logger.info(f"🗑️ Removed link {uuid} from JD")
            except Exception as e:
                logger.error(f"Failed to remove link from JD: {e}")

        # Mark completed task
        state.completed_tasks.append(filename)
        
    except Exception as e:
        logger.error(f"Upload error for {file_path}: {e}")
        state.active_uploads.pop(filename, None)


# ============ Event Handlers ============

@app.on_message(filters.command("start") & auth_filter)
async def start_command(client, message):
    await message.reply_text(
        "👋 Welcome to the JDownloader 2 Bot!\n\n"
        "Send me a link and I will add it to your JDownloader queue.\n"
        "I will let you choose which files to download and upload them here when finished."
    )

# Pending URL store for callbacks
pending_urls = {} # {user_id: url}

@app.on_message(filters.text & auth_filter)
async def handle_message(client, message):
    user_id = message.from_user.id
    text = message.text.strip()
    
    # Check JD2 availability
    if not JD_AVAILABLE:
        await message.reply_text("❌ JDownloader 2 client not installed/configured. Check logs.")
        return

    if text.startswith(("http://", "https://")):
        url = text
        
        # Store for callback
        pending_urls[user_id] = url
        
        # Ask for scan type
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("🚀 הורדה רגילה", callback_data="scan_regular"),
                InlineKeyboardButton("🕷️ סריקה עמוקה", callback_data="scan_deep")
            ],
            [InlineKeyboardButton("🗑️ ביטול", callback_data="scan_cancel")]
        ])
        
        await message.reply_text(
            f"🔎 **נמצא קישור:**\n`{url}`\n\nבחר את סוג הסריקה שתרצה לבצע:",
            reply_markup=keyboard
        )
    else:
        if not text.startswith("/"):
            await message.reply_text("❌ Please send a valid URL starting with http or https.")


# File Extensions
VIDEO_EXTENSIONS = {'.mp4', '.mkv', '.webm', '.avi', '.mov', '.ts', '.m4v', '.wmv', '.flv', '.3gp', '.mpg', '.mpeg'}

# State to track active scans
active_scans = {}  # {user_id: True/False}

async def process_jd_links(client, user_id, message_to_edit, urls, deep_scan=False):
    """Helper to process a list of URLs with JD2."""
    try:
        jd = get_jd_client()
        loop = asyncio.get_event_loop()
        
        # 1. Clear previous LinkGrabber session
        await loop.run_in_executor(executor, jd.clear_linkgrabber)
        
        # 2. Add Links to LinkGrabber
        combined_links = "\n".join(urls)
        # Pass deep_scan to jd_client
        success = await loop.run_in_executor(executor, jd.add_to_linkgrabber, combined_links, None, deep_scan)
        
        if not success:
            await message_to_edit.edit_text("❌ Failed to add links to JDownloader.")
            return
        
        # 3. Wait for results with Stop Button
        active_scans[user_id] = True
        
        await message_to_edit.edit_text(
            f"⏳ **מעבד {len(urls)} קישורים... (LinkGrabber)**\nסריקה עמוקה עשויה לקחת זמן...",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🛑 עצור סריקה והצג תוצאות", callback_data="scan_stop")]
            ])
        )
        
        # We need a custom wait loop here to check for user interrupt (scan_stop)
        links = []
        elapsed = 0
        timeout = 180 if deep_scan else 45
        
        while elapsed < timeout:
            if not active_scans.get(user_id):
                # User stopped safely
                break
                
            links = await loop.run_in_executor(executor, jd.get_linkgrabber_links, False)
            if links and len(links) > 5 and not deep_scan: # Fast exit for regular
                 if active_scans.get(user_id): # Check again
                     # Let it stabilize a bit unless stopped
                     pass
            
            await asyncio.sleep(2)
            elapsed += 2
            
            # Update count in UI occasionally
            if elapsed % 4 == 0:
                try:
                    await message_to_edit.edit_text(
                        f"⏳ **מעבד... נמצאו {len(links)} קישורים**\nלחץ על עצור כדי לסיים ולסנן.",
                        reply_markup=InlineKeyboardMarkup([
                            [InlineKeyboardButton("🛑 עצור סריקה והצג תוצאות", callback_data="scan_stop")]
                        ])
                    )
                except: pass

        # Final fetch
        links = await loop.run_in_executor(executor, jd.get_linkgrabber_links, False)
        
        if not links:
            await message_to_edit.edit_text(
                "❌ לא נמצאו קבצים להורדה.\nייתכן שהסריקה עדיין נמשכת, נסה לרענן.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔄 רענון נתוני סריקה", callback_data="jd_refresh")],
                    [InlineKeyboardButton("🗑️ ביטול", callback_data="scan_cancel")]
                ])
            )
            return
        
        # 4. Filter duplicates AND Valid Video Files
        unique_links = []
        seen_uuids = set()
        seen_files = set() # (name, size)
        new_toggle_state = {}
        
        # Limit processing for responsiveness
        limit = 500  # Process max 500 candidates
        processed_count = 0 
        
        for link in links:
            if processed_count >= limit:
                break
            
            # Check extension
            name = link.get("name", "").lower()
            size = link.get("size", 0)
            ext = os.path.splitext(name)[1]
            
            if ext not in VIDEO_EXTENSIONS:
                continue
            
            # Deduplication: Name + Size
            file_signature = (name, size)
            if file_signature in seen_files:
                continue
                
            if link['uuid'] not in seen_uuids:
                unique_links.append(link)
                seen_uuids.add(link['uuid'])
                seen_files.add(file_signature)
                new_toggle_state[link['uuid']] = True
                processed_count += 1
        
        # If no videos found after filtering
        if not unique_links:
            await message_to_edit.edit_text(
                f"❌ לא נמצאו קבצי וידאו ב-{len(links)} הקישורים שנמצאו.\nנסה סריקה עמוקה יותר או בדוק את הקישור.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔄 רענון נתוני סריקה", callback_data="jd_refresh")],
                    [InlineKeyboardButton("🗑️ ביטול", callback_data="scan_cancel")]
                ])
            )
            return

        # Cap results at 250 as requested to avoid freezes
        if len(unique_links) > 250:
            unique_links = unique_links[:250]
            await message_to_edit.reply_text("⚠️ **הערה:** התקבלו יותר מ-250 תוצאות via סריקה עמוקה. הרשימה קוצצה ל-250 הראשונות כדי לשמור על ביצועים.")
        
        # Store in cache
        jd_linkgrabber_cache[user_id] = unique_links
        jd_toggle_states[user_id] = new_toggle_state
        user_pagination[user_id] = 0
        
        # 5. Show Selection UI
        await message_to_edit.edit_text(
            format_jd_list_message(unique_links, 0),
            reply_markup=get_jd_toggle_keyboard(user_id, unique_links, 0)
        )

    except Exception as e:
        logger.error(f"Error during JD2 processing: {e}")
        await message_to_edit.edit_text(f"❌ An error occurred: {str(e)[:100]}")
    finally:
        active_scans.pop(user_id, None)


@app.on_callback_query(filters.regex("^(jd_|scan_)"))
async def handle_callbacks(client, callback_query):
    user_id = callback_query.from_user.id
    if user_id not in AUTHORIZED_USERS:
        await callback_query.answer("⛔ Operation not allowed.", show_alert=True)
        return
    data = callback_query.data
    
    # --- Scan Selection Handlers ---
    if data == "scan_cancel":
        pending_urls.pop(user_id, None)
        if JD_AVAILABLE:
            try:
                jd = get_jd_client()
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(executor, jd.clear_linkgrabber)
            except Exception as e:
                logger.warning(f"Failed to clear JD2 LinkGrabber in scan_cancel: {e}")
        await callback_query.message.edit_text("❌ בוטל.")
        return

    if data == "jd_cancel_active":
        state = user_sessions.get(user_id)
        if state and state.is_active:
            state.is_active = False
            state.status = "Cancelled by user"
            
            # Remove from JD
            if JD_AVAILABLE and state.jd_downloads:
                try:
                    jd = get_jd_client()
                    uuids = [d.get("uuid") for d in state.jd_downloads]
                    if uuids:
                        loop = asyncio.get_event_loop()
                        await loop.run_in_executor(executor, jd.remove_links, uuids)
                except Exception as e:
                    logger.error(f"Failed to remove links on cancel: {e}")
            
            await callback_query.message.edit_text("🛑 ההורדה בוטלה על ידי המשתמש.")
        else:
            await callback_query.answer("⚠️ אין הורדה פעילה.", show_alert=True)
        return

    if data == "scan_stop":
        # Signal the loop to stop and process what we have
        if user_id in active_scans:
            active_scans[user_id] = False
            await callback_query.answer("🛑 עוצר סריקה... אנא המתן לסינון התוצאות.", show_alert=True)
            
            # Also tell JD to abort crawling
            if JD_AVAILABLE:
                try:
                    jd = get_jd_client()
                    loop = asyncio.get_event_loop()
                    
                    # 1. Attempt Graceful Abort
                    await loop.run_in_executor(executor, jd.abort_crawling)
                    
                    # 2. Verify Stop (Wait up to 3 seconds)
                    for _ in range(3):
                        await asyncio.sleep(1)
                        is_collecting = await loop.run_in_executor(executor, jd.is_collecting)
                        if not is_collecting:
                            break
                    
                    # 3. Force Stop if still collecting
                    if await loop.run_in_executor(executor, jd.is_collecting):
                        await callback_query.answer("⚠️ עצירה רגילה נכשלה, מבצע עצירה כפויה...", show_alert=True)
                        # To force stop, we might need to reset LinkGrabber or just accept it's stuck
                        # For now, we proceed to showing results, but warn user
                        logger.warning("LinkGrabber still collecting after abort.")
                        # Optional: await loop.run_in_executor(executor, jd.clear_linkgrabber) 
                        # Clearing might delete results user wants, so we skip clearing unless user cancels completely
                    else:
                        await callback_query.answer("✅ סריקה נעצרה בהצלחה.", show_alert=True)
                        
                except Exception as e:
                    logger.error(f"Failed to abort JD crawling: {e}")
        else:
            await callback_query.answer("⚠️ אין סריקה פעילה.", show_alert=True)
        return

    if data in ["scan_regular", "scan_deep"]:
        url = pending_urls.get(user_id)
        if not url:
            await callback_query.answer("❌ URL expired or missing.", show_alert=True)
            await callback_query.message.delete()
            return
        
        # Clear pending
        pending_urls.pop(user_id, None)
        
        if data == "scan_regular":
            await callback_query.message.edit_text("🔍 **מבצע סריקה רגילה...**")
            await process_jd_links(client, user_id, callback_query.message, [url], deep_scan=False)
            
        elif data == "scan_deep":
            # Using JDownloader Native Deep Decrypt (Level 2+)
            await callback_query.message.edit_text("🕷️ **מבצע סריקה עמוקה (JDownloader Deep-Decrypt)...**\nזה עשוי לקחת כמה שניות.")
            try:
                # Pass deep_scan=True to process_jd_links, which passes it to updated add_to_linkgrabber
                await process_jd_links(client, user_id, callback_query.message, [url], deep_scan=True)
                
            except Exception as e:
                logger.error(f"Deep scan error: {e}")
                await callback_query.message.edit_text(f"❌ שגיאה בסריקה עמוקה: {e}")
        return

    # --- Existing JD Handlers ---
    
    if data.startswith("jd_toggle_"):
        uuid_str = data.replace("jd_toggle_", "")
        try:
            uuid = int(uuid_str)
        except ValueError:
            uuid = uuid_str
        
        if user_id not in jd_toggle_states:
            jd_toggle_states[user_id] = {}
        
        current = jd_toggle_states[user_id].get(uuid, True)
        jd_toggle_states[user_id][uuid] = not current
        
        links = jd_linkgrabber_cache.get(user_id, [])
        page = user_pagination.get(user_id, 0)
        
        try:
            await callback_query.edit_message_text(
                format_jd_list_message(links, page),
                reply_markup=get_jd_toggle_keyboard(user_id, links, page)
            )
        except Exception:
            pass
        await callback_query.answer()
    
    elif data.startswith("jd_page_"):
        new_page = int(data.split("_")[2])
        user_pagination[user_id] = new_page
        links = jd_linkgrabber_cache.get(user_id, [])
        await callback_query.edit_message_text(
            format_jd_list_message(links, new_page),
            reply_markup=get_jd_toggle_keyboard(user_id, links, new_page)
        )
        await callback_query.answer()
    
    elif data == "jd_noop":
        await callback_query.answer()
    
    elif data == "jd_select_all":
        links = jd_linkgrabber_cache.get(user_id, [])
        jd_toggle_states[user_id] = {link.get("uuid"): True for link in links}
        page = user_pagination.get(user_id, 0)
        await callback_query.edit_message_text(
            format_jd_list_message(links, page),
            reply_markup=get_jd_toggle_keyboard(user_id, links, page)
        )
        await callback_query.answer("✅ All selected")
    
    elif data == "jd_deselect_all":
        links = jd_linkgrabber_cache.get(user_id, [])
        jd_toggle_states[user_id] = {link.get("uuid"): False for link in links}
        page = user_pagination.get(user_id, 0)
        await callback_query.edit_message_text(
            format_jd_list_message(links, page),
            reply_markup=get_jd_toggle_keyboard(user_id, links, page)
        )
        await callback_query.answer("❌ All deselected")

    elif data == "jd_refresh":
        if JD_AVAILABLE:
            try:
                await callback_query.answer("🔄 Refreshing...", show_alert=False)
                jd = get_jd_client()
                loop = asyncio.get_event_loop()
                links = await loop.run_in_executor(executor, jd.get_linkgrabber_links, False, 10) # Don't wait for extraction, just get current
                
                # Update cache but preserve toggles if possible?
                # For now we reset specific toggles but keep structure if UUID matches
                # Actually, standard behavior for refresh is showing what's currently there.
                
                unique_links = []
                seen_uuids = set()
                current_toggles = jd_toggle_states.get(user_id, {})
                new_toggle_state = {}

                for link in links:
                    # Check extension (Fix for zombie files showing up on refresh)
                    name = link.get("name", "").lower()
                    ext = os.path.splitext(name)[1]
                    
                    if ext not in VIDEO_EXTENSIONS:
                        continue
                        
                    if link['uuid'] not in seen_uuids:
                        unique_links.append(link)
                        seen_uuids.add(link['uuid'])
                        # Preserve existing toggle or default to True (like initial load)
                        # If user unchecked it, keep it unchecked. If new, check it.
                        if link['uuid'] in current_toggles:
                            new_toggle_state[link['uuid']] = current_toggles[link['uuid']]
                        else:
                            new_toggle_state[link['uuid']] = True
                
                # Check if we have anything legit left
                if not unique_links:
                    await callback_query.edit_message_text(
                        "❌ לא נמצאו קבצי וידאו.\nייתכן שהסריקה עדיין נמשכת, נסה לרענן שוב.",
                        reply_markup=InlineKeyboardMarkup([
                            [InlineKeyboardButton("🔄 רענון נתוני סריקה", callback_data="jd_refresh")],
                            [InlineKeyboardButton("🗑️ ביטול", callback_data="scan_cancel")]
                        ])
                    )
                    return
                
                jd_linkgrabber_cache[user_id] = unique_links
                jd_toggle_states[user_id] = new_toggle_state
                page = user_pagination.get(user_id, 0)
                
                # Check pagination bounds
                PAGE_SIZE = 8
                total_pages = max(1, (len(unique_links) + PAGE_SIZE - 1) // PAGE_SIZE)
                if page >= total_pages:
                     page = total_pages - 1
                     user_pagination[user_id] = page
                     
                await callback_query.edit_message_text(
                    format_jd_list_message(unique_links, page),
                    reply_markup=get_jd_toggle_keyboard(user_id, unique_links, page)
                )
            except Exception as e:
                logger.error(f"Refresh failed: {e}")
                await callback_query.answer(f"❌ Refresh failed: {str(e)[:50]}")
        else:
             await callback_query.answer("❌ JD not available")
    
    elif data == "jd_cancel":
        jd_linkgrabber_cache.pop(user_id, None)
        jd_toggle_states.pop(user_id, None)
        user_pagination.pop(user_id, None)
        if JD_AVAILABLE:
            try:
                jd = get_jd_client()
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(executor, jd.clear_linkgrabber)
            except Exception as e:
                logger.warning(f"Failed to clear JD2 LinkGrabber: {e}")
        await callback_query.edit_message_text("❌ Session cancelled. LinkGrabber cleared.")
    
    elif data == "jd_confirm":
        links = jd_linkgrabber_cache.get(user_id, [])
        toggle_states = jd_toggle_states.get(user_id, {})
        selected_uuids = [link.get("uuid") for link in links if toggle_states.get(link.get("uuid"), True)]
        
        if not selected_uuids:
            await callback_query.answer("❌ No files selected!", show_alert=True)
            return
        
        await callback_query.edit_message_text(f"🚀 **Starting download of {len(selected_uuids)} file(s)...**")
        

        # Initialize session state
        state = SessionState()
        user_sessions[user_id] = state
        state.total_files = len(selected_uuids) # Initialize counter
        
        try:
            jd = get_jd_client()
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(executor, jd.move_to_downloads, selected_uuids, None)
            await loop.run_in_executor(executor, jd.start_downloads)
            
            # Clear remaining/unselected links
            await loop.run_in_executor(executor, jd.clear_linkgrabber)
            
            status_msg = callback_query.message
            asyncio.create_task(monitor_jd_downloads(client, user_id, status_msg, selected_uuids))
            
        except Exception as e:
            logger.error(f"JD2 confirm error: {e}")
            await callback_query.edit_message_text(f"❌ Error starting downloads: {str(e)[:100]}")
        
        jd_linkgrabber_cache.pop(user_id, None)
        jd_toggle_states.pop(user_id, None)
        user_pagination.pop(user_id, None)


if __name__ == "__main__":
    print("🤖 Bot is starting (JD2 Version)...")
    app.run()
