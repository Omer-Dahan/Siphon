"""Utility functions for Siphon Bot."""
import logging
import math
import os
import ffmpeg

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
        partial = "🌘"  # Waning Crescent (Lit Left)
    elif remainder < 0.75:
        partial = "🌗"  # Last Quarter (Lit Left)
    else:
        partial = "🌖"  # Waning Gibbous (Lit Left)

    # Construct bar
    # If partial is empty but we have remainder, it means it's close to 0 but maybe
    # should show something?
    # Logic:
    # Full cells: 🌕
    # Current cell: partial or 🌑 (if very low) or 🌕 (if almost full? handled by int)

    # Improved logic for partials:
    if full_cells >= total_cells:
        return "🌕" * total_cells

    res_bar = "🌕" * full_cells

    # Add partial if space remains
    if len(res_bar) < total_cells:
        if partial:
            res_bar += partial
        else:
            res_bar += "🌑"

    # Fill rest with empty
    res_bar += "🌑" * (total_cells - len(res_bar))

    # Return strictly 10 chars
    return res_bar[:total_cells]

def get_video_metadata(file_path: str):
    """Get metadata and generate thumb."""
    try:
        probe = ffmpeg.probe(file_path)
        v_stream = next((s for s in probe['streams'] if s['codec_type'] == 'video'), None)
        if not v_stream:
            return 0, 0, 0, None
        width = int(v_stream.get('width', 0))
        height = int(v_stream.get('height', 0))
        duration = int(float(v_stream.get('duration', 0)))
        thumb_path = f"{file_path}.jpg"
        (ffmpeg.input(file_path, ss=1).output(thumb_path, vframes=1)
         .overwrite_output().run(capture_stdout=True, capture_stderr=True))
        return width, height, duration, thumb_path
    except Exception as err:  # pylint: disable=broad-except
        logger.warning("Metadata probe failed for %s: %s", file_path, err)
        return 0, 0, 0, None

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
        video_stream = next((s for s in probe['streams']
                             if s['codec_type'] == 'video'), None)
        audio_stream = next((s for s in probe['streams']
                             if s['codec_type'] == 'audio'), None)

        if video_stream:
            video_codec = video_stream.get('codec_name', '').lower()
            if video_codec not in STREAMING_VIDEO_CODECS:
                return True

        if audio_stream:
            audio_codec = audio_stream.get('codec_name', '').lower()
            if audio_codec not in STREAMING_AUDIO_CODECS:
                return True

        return False
    except ffmpeg.Error as err:
        stderr = err.stderr.decode() if err.stderr else str(err)
        logger.warning("Could not probe %s: %s", os.path.basename(file_path), stderr)
        return True
    except Exception as err:  # pylint: disable=broad-except
        logger.warning("Could not probe %s: %s", os.path.basename(file_path), err)
        return True


def convert_to_mp4(file_path: str, delete_original: bool = True) -> str:
    """
    Convert a video file to MP4 (H.264 + AAC) for Telegram streaming.

    Uses fast re-mux if codecs are already compatible, otherwise re-encodes.
    Returns the path to the converted file, or original if no conversion needed.
    """
    if not os.path.exists(file_path):
        logger.error("File not found for conversion: %s", file_path)
        return file_path

    ext = os.path.splitext(file_path)[1].lower()

    # Already MP4 with correct codecs? Skip
    if ext == '.mp4' and not needs_conversion(file_path):
        logger.info("✅ %s already streaming-compatible, skipping conversion",
                    os.path.basename(file_path))
        return file_path

    # Generate output path
    base_name = os.path.splitext(file_path)[0]
    output_path = f"{base_name}_converted.mp4"

    try:
        # Probe to check codecs
        probe = ffmpeg.probe(file_path)
        v_s = next((s for s in probe['streams'] if s['codec_type'] == 'video'), None)
        a_s = next((s for s in probe['streams'] if s['codec_type'] == 'audio'), None)

        v_codec = v_s.get('codec_name', '').lower() if v_s else ''
        a_codec = a_s.get('codec_name', '').lower() if a_s else ''

        # Decide encoding strategy
        can_copy_v = v_codec in STREAMING_VIDEO_CODECS
        can_copy_a = a_codec in STREAMING_AUDIO_CODECS or not a_s

        logger.info("🔄 Converting %s to MP4...", os.path.basename(file_path))

        # Build output args
        o_args = {'format': 'mp4', 'movflags': '+faststart'}
        o_args['vcodec'] = 'copy' if can_copy_v else 'libx264'
        if not can_copy_v:
            o_args['preset'], o_args['crf'] = 'fast', '23'

        o_args['acodec'] = 'copy' if can_copy_a else 'aac'
        if not can_copy_a:
            o_args['audio_bitrate'] = '192k'

        # Run conversion
        (ffmpeg.input(file_path).output(output_path, **o_args)
         .overwrite_output().run(capture_stdout=True, capture_stderr=True))

        if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
            logger.info("✅ Conversion complete: %s", os.path.basename(output_path))
            if delete_original and file_path != output_path:
                try:
                    os.remove(file_path)
                    logger.info("🗑️ Deleted original: %s", os.path.basename(file_path))
                except OSError as err:
                    logger.warning("Could not delete original: %s", err)
            return output_path
        return file_path

    except ffmpeg.Error as err:
        stderr = err.stderr.decode() if err.stderr else str(err)
        logger.error("❌ FFmpeg conversion error for %s: %s", file_path, stderr[:500])
        return file_path
    except Exception as err:  # pylint: disable=broad-except
        logger.error("❌ Conversion error for %s: %s", file_path, err)
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

    logger.info("🔪 Splitting large file: %s (%s GB)",
                file_path, round(file_size / 1024**3, 2))

    # Determine segment time based on average bitrate
    # Duration / (Size / MaxSize) = Time per chunk
    meta = get_video_metadata(file_path)
    # pylint: disable=invalid-name
    _, _, duration, _ = meta

    if duration == 0:
        logger.warning("Could not determine duration, creating 1.9GB chunks.")
        segment_time = 600
    else:
        num_parts = math.ceil(file_size / (max_size_bytes * 0.95))
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

        logger.info("✅ Split into %s parts.", len(parts))
        return parts

    except Exception as err:  # pylint: disable=broad-except
        logger.error("❌ Error splitting video: %s", err)
        return []
