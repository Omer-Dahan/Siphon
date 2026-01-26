import os
import ffmpeg
import logging
import math

logger = logging.getLogger(__name__)


def format_size(size_bytes: int) -> str:
    """Format bytes to human readable string."""
    if size_bytes < 0:
        return "Unknown"
    if size_bytes == 0:
        return "0 B"
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size_bytes < 1024:
            return f"{size_bytes:.2f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.2f} TB"

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

# Formats that support Telegram streaming
STREAMING_FORMATS = ['.mp4', '.m4v', '.mov']
# Codecs compatible with Telegram streaming
STREAMING_VIDEO_CODECS = ['h264', 'mpeg4', 'avc']
STREAMING_AUDIO_CODECS = ['aac', 'mp3']

def needs_conversion(file_path: str) -> bool:
    """
    Check if a video file needs conversion for Telegram streaming.
    Returns True if conversion is needed.
    """
    ext = os.path.splitext(file_path)[1].lower()
    
    # If format doesn't support streaming, needs conversion
    if ext not in STREAMING_FORMATS:
        return True
    
    # Check codecs
    try:
        probe = ffmpeg.probe(file_path)
        video_stream = next((s for s in probe['streams'] if s['codec_type'] == 'video'), None)
        audio_stream = next((s for s in probe['streams'] if s['codec_type'] == 'audio'), None)
        
        if video_stream:
            video_codec = video_stream.get('codec_name', '').lower()
            if video_codec not in STREAMING_VIDEO_CODECS:
                return True
        
        if audio_stream:
            audio_codec = audio_stream.get('codec_name', '').lower()
            if audio_codec not in STREAMING_AUDIO_CODECS:
                return True
                
        return False
    except Exception as e:
        logger.warning(f"Could not probe {file_path}, assuming conversion needed: {e}")
        return True

def convert_to_mp4(file_path: str, delete_original: bool = True) -> str:
    """
    Convert a video file to MP4 (H.264 + AAC) for Telegram streaming.
    
    Uses fast re-mux if codecs are already compatible, otherwise re-encodes.
    Returns the path to the converted file, or original if no conversion needed.
    """
    if not os.path.exists(file_path):
        logger.error(f"File not found for conversion: {file_path}")
        return file_path
    
    ext = os.path.splitext(file_path)[1].lower()
    
    # Already MP4 with correct codecs? Skip
    if ext == '.mp4' and not needs_conversion(file_path):
        logger.info(f"✅ {os.path.basename(file_path)} already streaming-compatible, skipping conversion")
        return file_path
    
    # Generate output path
    base_name = os.path.splitext(file_path)[0]
    output_path = f"{base_name}_converted.mp4"
    
    try:
        # Probe to check codecs
        probe = ffmpeg.probe(file_path)
        video_stream = next((s for s in probe['streams'] if s['codec_type'] == 'video'), None)
        audio_stream = next((s for s in probe['streams'] if s['codec_type'] == 'audio'), None)
        
        video_codec = video_stream.get('codec_name', '').lower() if video_stream else ''
        audio_codec = audio_stream.get('codec_name', '').lower() if audio_stream else ''
        
        # Decide encoding strategy
        can_copy_video = video_codec in STREAMING_VIDEO_CODECS
        can_copy_audio = audio_codec in STREAMING_AUDIO_CODECS or not audio_stream
        
        logger.info(f"🔄 Converting {os.path.basename(file_path)} to MP4...")
        logger.info(f"   Video: {video_codec} -> {'copy' if can_copy_video else 'h264'}")
        logger.info(f"   Audio: {audio_codec} -> {'copy' if can_copy_audio else 'aac'}")
        
        # Build ffmpeg command
        input_stream = ffmpeg.input(file_path)
        
        output_args = {
            'format': 'mp4',
            'movflags': '+faststart',  # Important for streaming!
        }
        
        if can_copy_video:
            output_args['vcodec'] = 'copy'
        else:
            output_args['vcodec'] = 'libx264'
            output_args['preset'] = 'fast'
            output_args['crf'] = '23'
        
        if can_copy_audio:
            output_args['acodec'] = 'copy'
        else:
            output_args['acodec'] = 'aac'
            output_args['audio_bitrate'] = '192k'
        
        # Run conversion
        (
            ffmpeg
            .output(input_stream, output_path, **output_args)
            .overwrite_output()
            .run(capture_stdout=True, capture_stderr=True)
        )
        
        if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
            logger.info(f"✅ Conversion complete: {os.path.basename(output_path)}")
            
            # Delete original if requested
            if delete_original and file_path != output_path:
                try:
                    os.remove(file_path)
                    logger.info(f"🗑️ Deleted original: {os.path.basename(file_path)}")
                except Exception as e:
                    logger.warning(f"Could not delete original: {e}")
            
            return output_path
        else:
            logger.error(f"Conversion failed - output file empty or missing")
            return file_path
            
    except ffmpeg.Error as e:
        stderr = e.stderr.decode() if e.stderr else str(e)
        logger.error(f"❌ FFmpeg conversion error: {stderr[:500]}")
        return file_path
    except Exception as e:
        logger.error(f"❌ Conversion error for {file_path}: {e}")
        return file_path

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
