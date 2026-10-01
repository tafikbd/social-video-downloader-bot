"""
Telegram Social-Video Downloader Bot
Hosted on Render Web Service
Uses yt-dlp + Deno for JavaScript challenge solving.
"""

import os
import re
import asyncio
import logging
import tempfile
import subprocess
from pathlib import Path

from flask import Flask, request
from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)
from telegram.request import HTTPXRequest
import yt_dlp

# =============================================================================
# LOGGING — Visible in Render logs
# =============================================================================

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("video-bot")

# =============================================================================
# CONFIGURATION
# =============================================================================

BOT_TOKEN = os.environ.get("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN environment variable is not set.")

# Telegram limits: 50 MB for bots via Bot API
MAX_TELEGRAM_FILE_SIZE = 50 * 1024 * 1024  # 50 MB
DOWNLOAD_DIR = Path(tempfile.gettempdir()) / "video_downloads"
DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

# Flask app for Render health checks
flask_app = Flask(__name__)

# =============================================================================
# YT-DLP CONFIGURATION
# =============================================================================

def _find_deno_path() -> str | None:
    """Locate Deno binary. Render installs it via the build command."""
    candidates = [
        "/opt/render/project/.deno/bin/deno",
        "/usr/local/bin/deno",
        "/usr/bin/deno",
        str(Path.home() / ".deno" / "bin" / "deno"),
    ]
    for path in candidates:
        if Path(path).exists():
            return path
    # Fallback: check PATH
    try:
        result = subprocess.run(
            ["which", "deno"], capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except Exception:
        pass
    return None


DENO_PATH = _find_deno_path()
if DENO_PATH:
    logger.info("Deno found at: %s", DENO_PATH)
else:
    logger.warning(
        "Deno NOT found. YouTube downloads will likely fail with bot-detection errors."
    )


def build_ydl_options(output_template: str) -> dict:
    """Return yt-dlp options tuned for headless Render environment."""
    opts = {
        # Output
        "outtmpl": output_template,
        "quiet": True,
        "no_warnings": False,
        "noplaylist": True,

        # Format: prefer mp4, cap at 720p to stay under Telegram limits
        "format": (
            "bestvideo[ext=mp4][height<=720]+bestaudio[ext=m4a]/"
            "best[ext=mp4][height<=720]/best[height<=720]/best"
        ),
        "merge_output_format": "mp4",

        # Network resilience
        "socket_timeout": 30,
        "retries": 5,
        "fragment_retries": 5,
        "extractor_retries": 5,
        "retry_sleep": lambda n: min(4 ** n, 120),  # exponential backoff

        # Rate-limit friendliness (reduces 429 errors)
        "sleep_interval_requests": 2,
        "sleep_interval": 1,
        "max_sleep_interval": 5,

        # No cookies, no browser extraction, no auth
        "cookiesfrombrowser": None,
        "cookiefile": None,

        # Safety: no external post-processors that need extra binaries
        "postprocessors": [],
    }

    # Attach Deno if available
    if DENO_PATH:
        opts["js_runtimes"] = {"deno": {"path": DENO_PATH}}

    return opts


# =============================================================================
# DOWNLOAD LOGIC
# =============================================================================

SUPPORTED_URL_RE = re.compile(
    r"https?://(?:www\.)?"
    r"(?:youtube\.com|youtu\.be|instagram\.com|facebook\.com|fb\.watch|"
    r"twitter\.com|x\.com|tiktok\.com|vimeo\.com|dailymotion\.com|"
    r"reddit\.com|twitch\.tv|soundcloud\.com|bilibili\.com)"
    r"/\S+",
    re.IGNORECASE,
)


def extract_urls(text: str) -> list[str]:
    """Extract supported URLs from a message."""
    return SUPPORTED_URL_RE.findall(text)


async def download_video(url: str) -> tuple[Path | None, str | None]:
    """
    Download a video with yt-dlp.
    Returns (file_path, error_message).
    Only works for publicly accessible URLs — no DRM, no login, no paywall.
    """
    output_template = str(DOWNLOAD_DIR / "%(id)s.%(ext)s")

    ydl_opts = build_ydl_options(output_template)

    def _run() -> tuple[Path | None, str | None]:
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                if info is None:
                    return None, "Could not extract video information."

                # Handle playlists gracefully
                if "entries" in info:
                    entries = [e for e in info["entries"] if e]
                    if not entries:
                        return None, "Playlist is empty."
                    info = entries[0]

                file_path = Path(ydl.prepare_filename(info))

                # yt-dlp may change extension after merge
                if not file_path.exists():
                    for candidate in DOWNLOAD_DIR.glob(f"{info['id']}.*"):
                        if candidate.suffix in (".mp4", ".mkv", ".webm"):
                            file_path = candidate
                            break

                if not file_path.exists():
                    return None, "Downloaded file not found."

                return file_path, None

        except yt_dlp.utils.DownloadError as exc:
            msg = str(exc)
            logger.error("yt-dlp DownloadError for %s: %s", url, msg)

            if "429" in msg or "Too Many Requests" in msg:
                return None, (
                    "YouTube is rate-limiting requests from this server. "
                    "Please try again in a few minutes."
                )
            if "Sign in" in msg or "bot" in msg.lower():
                return None, (
                    "This video requires verification that the bot cannot provide. "
                    "It may be region-locked, age-restricted, or private."
                )
            if "Unsupported URL" in msg:
                return None, "This URL is not supported by the downloader."
            if "DRM" in msg:
                return None, "This video is DRM-protected and cannot be downloaded."

            return None, f"Download failed: {msg[:200]}"

        except Exception as exc:
            logger.exception("Unexpected error downloading %s", url)
            return None, f"Unexpected error: {exc}"

    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _run)


# =============================================================================
# TELEGRAM HANDLERS
# =============================================================================

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "👋 Hello! Send me a public video URL and I'll download it for you.\n\n"
        "Supported platforms: YouTube, Instagram, Facebook, Twitter/X, TikTok, "
        "Vimeo, Reddit, Twitch, and more.\n\n"
        "⚠️ I can only download publicly accessible videos. "
        "I cannot bypass DRM, private content, or login requirements."
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "📖 *How to use:*\n"
        "1. Copy a public video URL.\n"
        "2. Paste it here.\n"
        "3. Wait for the download and upload.\n\n"
        "*Limitations:*\n"
        "• Files over 50 MB cannot be sent via Telegram.\n"
        "• Private, DRM-protected, or login-required videos are not supported.\n"
        "• Some platforms may rate-limit requests.",
        parse_mode="Markdown",
    )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.text:
        return

    text = update.message.text.strip()
    urls = extract_urls(text)

    if not urls:
        await update.message.reply_text(
            "Please send a valid video URL.\n"
            "Example: https://www.youtube.com/watch?v=..."
        )
        return

    url = urls[0]
    status_msg = await update.message.reply_text("🔍 Processing your link...")

    await update.message.chat.send_action(ChatAction.UPLOAD_VIDEO)

    file_path, error = await download_video(url)

    if error or not file_path:
        await status_msg.edit_text(f"❌ {error or 'Download failed.'}")
        return

    file_size = file_path.stat().st_size

    if file_size > MAX_TELEGRAM_FILE_SIZE:
        size_mb = file_size / (1024 * 1024)
        await status_msg.edit_text(
            f"❌ The video is {size_mb:.1f} MB, which exceeds Telegram's 50 MB limit.\n"
            "Try a shorter video or a lower quality source."
        )
        file_path.unlink(missing_ok=True)
        return

    await status_msg.edit_text("📤 Uploading to Telegram...")

    try:
        with open(file_path, "rb") as video_file:
            await update.message.reply_video(
                video=video_file,
                caption="✅ Downloaded successfully.",
                supports_streaming=True,
            )
        await status_msg.delete()
    except Exception as exc:
        logger.exception("Failed to send video to Telegram")
        await status_msg.edit_text(f"❌ Upload failed: {exc}")
    finally:
        file_path.unlink(missing_ok=True)


# =============================================================================
# FLASK HEALTH CHECK (Render Web Service)
# =============================================================================

@flask_app.route("/")
def health():
    return "OK", 200


@flask_app.route("/healthz")
def healthz():
    return {"status": "healthy", "deno": DENO_PATH or "not found"}, 200


# =============================================================================
# APPLICATION BOOTSTRAP
# =============================================================================

def build_application() -> Application:
    request = HTTPXRequest(
        connection_pool_size=8,
        connect_timeout=20.0,
        read_timeout=30.0,
        write_timeout=30.0,
    )

    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .request(request)
        .build()
    )

    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message)
    )

    return application


def run_flask_in_thread() -> None:
    port = int(os.environ.get("PORT", 10000))
    flask_app.run(host="0.0.0.0", port=port, threaded=True)


if __name__ == "__main__":
    import threading

    logger.info("Starting Flask health server...")
    threading.Thread(target=run_flask_in_thread, daemon=True).start()

    logger.info("Starting Telegram bot polling...")
    app = build_application()
    app.run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=True,
    )
