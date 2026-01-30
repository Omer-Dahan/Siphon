# Siphon Project Context

This document provides a comprehensive overview of the **Siphon** project, a Telegram bot tailored for managing downloads via **JDownloader 2** and uploading them back to Telegram.

## Project Overview
Siphon is an automation tool that bridges Telegram and JDownloader 2. Users interact with a Telegram bot to send links, which are then processed by JDownloader running locally (or on a server). The bot manages the entire lifecycle: adding links, selecting files to download, monitoring progress, and finally uploading the downloaded media back to the user on Telegram.

## File Structure & key Files

### 1. `bot.py` (Main Application Entry)
*   **Purpose**: The core Telegram bot application.
*   **Key Responsibilities**:
    *   **User Interaction**: Handles `/start` command, text messages (URLs), and callback queries (button clicks).
    *   **Authorization**: Restricts access to authorized users defined in `.env`.
    *   **Session Management**: Tracks user state (`SessionState`), including active downloads, uploads, and progress.
    *   **Dashboard**: Displays a real-time status dashboard for downloads/uploads.
    *   **Integration**: orchestrates calls to `jd_client.py` and `utils.py`.
    *   **File Handling**: Manages large file splitting and sending media albums.

### 2. `jd_client.py` (JDownloader Integration)
*   **Purpose**: A wrapper around the `myjdapi` library to interact with the My.JDownloader API.
*   **Key Responsibilities**:
    *   **Connection**: Authenticates and connects to the specified JDownloader device.
    *   **LinkGrabber**: Adds links, triggers deep scans, lists discovered files, and manages the aggregation phase.
    *   **Downloads**: Moves files from LinkGrabber to the download queue, starts/pauses/stops downloads, and monitors progress.
    *   **Cleanup**: Removes finished downloads and links.

### 3. `utils.py` (Media Processing & Helpers)
*   **Purpose**: Utility functions for file processing and UI helpers.
*   **Key Responsibilities**:
    *   **FFmpeg Wrapper**: Uses `ffmpeg-python` to:
        *   Extract video metadata (duration, resolution).
        *   Generate thumbnails.
        *   Convert videos to Telegram-compatible MP4 (H.264/AAC) if needed.
        *   Split large files (>2GB) into chunks.
    *   **Formatting**: Human-readable file sizes and a custom "moon phase" progress bar.

### 4. `update_jd_rules.py` (Configuration Automation)
*   **Purpose**: A script to automatically update the local JDownloader 2 configuration file.
*   **Specifics**: Adds a "Deep Scan" rule to `jd.controlling.linkcrawler.LinkCrawlerConfig.linkcrawlerrules.json` to ensure deep link analysis (up to depth 3).

### 5. `requirements.txt`
*   Lists dependencies: `pyrogram`, `tgcrypto`, `python-dotenv`, `myjdapi`, `ffmpeg-python`, `psutil`.

## Work Flow

1.  **Initialization**:
    *   `bot.py` starts, connects to Telegram, and initializes the `JDownloaderClient`.
    *   It listens for messages from authorized users.

2.  **Adding Links**:
    *   User sends a URL to the bot.
    *   Bot calls `jd.add_to_linkgrabber(url, deep_scan=True)`.
    *   JDownloader processes the link (LinkGrabber phase).

3.  **Selection**:
    *   Bot fetches the list of found files from LinkGrabber.
    *   Bot presents an inline keyboard (Toggle UI) allowing the user to select specific files.
    *   User confirms selection.

4.  **Download Phase**:
    *   Bot moves selected links to the Download list.
    *   Bot starts a "Dashboard" loop (`render_dashboard`), updating the message every few seconds with:
        *   Download speed.
        *   Progress bar (Moon phase style).
        *   Active filename.

5.  **Post-Processing & Upload**:
    *   Once a download completes, `bot.py` picks it up.
    *   **Validation**: Checks if it's a video file.
    *   **Conversion**: Checks if conversion is needed (`utils.needs_conversion`). If so, converts to MP4.
    *   **Splitting**: If file > 2GB (Telegram limit), splits it into parts.
    *   **Upload**: Sends the file(s) to the user via Pyrogram.
    *   **Cleanup**: Deletes local files and removes the link from JDownloader.

## Environment Variables (.env)
*   `API_ID`, `API_HASH`: Telegram Client API credentials.
*   `BOT_TOKEN`: Telegram Bot Token.
*   `USER_IDS`: Comma-separated list of authorized User IDs.
*   `JD_EMAIL`, `JD_PASSWORD`: My.JDownloader account credentials.
*   `JD_DEVICE_NAME`: The name of the JDownloader instance to control.

## Notable Features
*   **Deep Scan**: Support for "deep" crawling of links.
*   **Smart Dashboard**: Prioritizes showing upload status over download status if both are active.
*   **Interactive UI**: Toggle buttons for selecting files from a grabbed package.
*   **Robust Error Handling**: Connection retries for JD API, fallback for missing metadata.

## Development Guidelines
*   **Use Skills**: Always prefer using the installed skills for tasks where applicable.
    *   **find-skills**: Helps discover and install new skills.
    *   **web-design-guidelines**: Use when reviewing or designing UI/UX to ensure compliance with best practices.
    *   **vercel-composition-patterns**: Use for React component architecture, refactoring boolean props, and state management.
    *   **vercel-react-best-practices**: Guidelines for React performance and optimizations (implied by folder name).
    *   **vercel-react-native-skills**: Skills specific to React Native development (implied by folder name).

