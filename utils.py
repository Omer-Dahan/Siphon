import os
import ffmpeg
import logging
import math

logger = logging.getLogger(__name__)

def moon_progress_bar(percent: float, total_cells: int = 10) -> str:
    """
    Build a moon phase progress bar (LTR).
    🌑 empty → 🌒 quarter → 🌓 half → 🌔 three-quarter → 🌕 full
    """
    percent = max(0, min(100, percent))
    val = percent / 100 * total_cells
    full_cells = int(val)
    remainder = val - full_cells
    
    # Select partial moon (Using Waning phases - Lit Left - as requested "reversed")
    if remainder < 0.25:
        partial = "" 
    elif remainder < 0.5:
        partial = "🌘" # Waning Crescent (Lit Left)
    elif remainder < 0.75:
        partial = "🌗" # Last Quarter (Lit Left)
    else:
        partial = "🌖" # Waning Gibbous (Lit Left)
        
    # Construct bar
    # If partial is empty but we have remainder, it means it's close to 0 but maybe should show something? 
    # Logic: 
    # Full cells: 🌕
    # Current cell: partial or 🌑 (if very low) or 🌕 (if almost full? handled by int)
    
    # Improved logic for partials:
    if full_cells >= total_cells:
        return "🌕" * total_cells
        
    bar = "🌕" * full_cells
    
    # Add partial if space remains
    if len(bar) < total_cells:
        if partial:
            bar += partial
        else:
            bar += "🌑"
            
    # Fill rest with empty
    bar += "🌑" * (total_cells - len(bar))
    
    # Return strictly 10 chars
    return bar[:total_cells]

def get_video_metadata(path: str):
    """Extract duration, width, and height using ffprobe."""
    try:
        probe = ffmpeg.probe(path)
        video_stream = next((stream for stream in probe['streams'] if stream['codec_type'] == 'video'), None)
        if video_stream:
            width = int(video_stream['width'])
            height = int(video_stream['height'])
            duration = float(probe.get('format', {}).get('duration', 0))
            return {"duration": int(duration), "width": width, "height": height}
    except Exception as e:
        logger.error(f"Error probing video {path}: {e}")
    return {"duration": 0, "width": 0, "height": 0}

def generate_thumbnail(path: str):
    """Generate a thumbnail at 1s mark."""
    thumb_path = path + ".jpg"
    try:
        (
            ffmpeg
            .input(path, ss=1)
            .filter('scale', 320, -1)
            .output(thumb_path, vframes=1)
            .overwrite_output()
            .run(capture_stdout=True, capture_stderr=True)
        )
        if os.path.exists(thumb_path):
            return thumb_path
    except Exception as e:
        logger.error(f"Error generating thumbnail for {path}: {e}")
    return None

def split_video(file_path: str, max_size_bytes: int = 2 * 1024 * 1024 * 1024) -> list:
    """
    Split a video file into chunks smaller than max_size_bytes.
    Returns a list of file paths (the original if no split occurred, or parts).
    """
    if not os.path.exists(file_path):
        return []
        
    file_size = os.path.getsize(file_path)
    if file_size <= max_size_bytes:
        return [file_path]
    
    logger.info(f"🔪 Splitting large file: {file_path} ({file_size / 1024**3:.2f} GB)")
    
    # Determine segment time based on average bitrate
    # Duration / (Size / MaxSize) = Time per chunk
    meta = get_video_metadata(file_path)
    duration = meta.get("duration", 0)
    
    if duration == 0:
        logger.warning("Could not determine duration, creating 1.9GB chunks blindly.")
        # Fallback: strict size limit without intelligent cutting points
        # Not easily done with ffmpeg segment without re-encoding or complex piping.
        # We will try a safe estimated duration per 1.9GB chunk assuming high bitrate (e.g. 20Mbps)
        # 1.9GB * 8 = 15.2 Gbit. 15200 Mbit / 20 Mbit/s = 760 seconds.
        segment_time = 600 # 10 mins safe bet
    else:
        num_parts = math.ceil(file_size / (max_size_bytes * 0.95)) # 5% safety margin
        segment_time = int(duration / num_parts)
    
    output_pattern = f"{file_path}.part%03d.mp4"
    
    try:
        # Use segment muxer for stream copying (fast, no re-encode)
        (
            ffmpeg
            .input(file_path)
            .output(
                output_pattern, 
                c="copy", 
                map="0", 
                f="segment", 
                segment_time=segment_time,
                reset_timestamps=1
            )
            .overwrite_output()
            .run(capture_stdout=True, capture_stderr=True)
        )
        
        # Find generated parts
        parts = []
        directory = os.path.dirname(file_path)
        basename = os.path.basename(file_path)
        
        for f in sorted(os.listdir(directory)):
             if f.startswith(basename + ".part") and f.endswith(".mp4"):
                 parts.append(os.path.join(directory, f))
        
        logger.info(f"✅ Split into {len(parts)} parts.")
        return parts
        
    except Exception as e:
        logger.error(f"❌ Error splitting video: {e}")
        return []
