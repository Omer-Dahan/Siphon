import os
import ffmpeg
import logging

logger = logging.getLogger(__name__)

def moon_progress_bar(percent: float, total_cells: int = 10) -> str:
    """
    Build a moon phase progress bar (RTL - right to left).
    
    Uses waxing phases for RTL visual (progress fills from right):
    🌑 empty → 🌒 quarter → 🌓 half → 🌔 three-quarter → 🌕 full
    """
    progress = max(0, min(100, percent)) / 100
    filled_cells = int(progress * total_cells)
    remainder = (progress * total_cells) - filled_cells
    
    # Calculate partial moon (using waxing phases: 🌒🌓🌔)
    partial_moon = ""
    if filled_cells < total_cells and remainder > 0:
        if remainder >= 0.67:
            partial_moon = "🌔"
            filled_cells += 1
        elif remainder >= 0.34:
            partial_moon = "🌓"
            filled_cells += 1
        else:
            partial_moon = "🌒"
            filled_cells += 1
    
    # RTL: full moons on right (start), partial in middle, empty on left (end)
    empty_count = total_cells - filled_cells
    full_count = filled_cells - (1 if partial_moon else 0)
    
    return "🌕" * full_count + partial_moon + "🌑" * empty_count

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
