import os
import re
import asyncio
import tempfile
from pathlib import Path

import yt_dlp
from flask import Flask
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

BOT_TOKEN = os.getenv("BOT_TOKEN")

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is not configured.")

app = Flask(__name__)


@app.get("/")
def home():
    return "Social Video Downloader Bot is running."


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Welcome!\n\n"
        "Send me a public video link and I'll try to download it."
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📥 Send a public video URL.\n\n"
        "The bot supports publicly accessible content that can legally "
        "be downloaded."
    )


def download_video(url, folder):
    output = str(Path(folder) / "video.%(ext)s")

    ydl_opts = {
        "outtmpl": output,
        "noplaylist": True,
        "quiet": False,
        "no_warnings": False,

        # Prefer MP4 formats containing both video and audio.
        # Keep the file reasonably small for Telegram.
        "format": (
            "best[ext=mp4][vcodec!=none][acodec!=none][filesize<50M]/"
            "best[ext=mp4][vcodec!=none][acodec!=none]/"
            "best[ext=mp4]/best"
        ),

        "socket_timeout": 60,
        "retries": 3,
        "fragment_retries": 3,
        "nocheckcertificate": True,
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)

        files = [
            p for p in Path(folder).glob("*")
            if p.is_file()
        ]

        if not files:
            raise RuntimeError("yt-dlp finished but no video file was created.")

        # Use the largest downloaded file.
        video_file = max(files, key=lambda p: p.stat().st_size)

        return str(video_file)


async def handle_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()

    match = re.search(r"https?://\S+", text)

    if not match:
        await update.message.reply_text(
            "🔗 Please send a valid video link."
        )
        return

    url = match.group(0)

    status = await update.message.reply_text(
        "🔍 Link received...\n⏳ Downloading..."
    )

    try:
        with tempfile.TemporaryDirectory() as temp_folder:

            file_path = await asyncio.to_thread(
                download_video,
                url,
                temp_folder
            )

            file_size = Path(file_path).stat().st_size

            # Telegram Bot API upload limit safety check.
            if file_size > 49 * 1024 * 1024:
                await status.edit_text(
                    "⚠️ The downloaded video is too large to send through Telegram."
                )
                return

            await status.edit_text(
                "📤 Download complete!\nUploading..."
            )

            with open(file_path, "rb") as video:
                await update.message.reply_video(
                    video=video,
                    supports_streaming=True,
                    caption="✅ Download complete!"
                )

            await status.delete()

    except Exception as error:
        print("========== DOWNLOAD ERROR ==========")
        print(repr(error))
        print("URL:", url)
        print("====================================")

        await status.edit_text(
            "❌ Download failed.\n\n"
            "The link may be unsupported, unavailable, "
            "restricted, or the video may be too large."
        )


async def run_bot():
    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .build()
    )

    application.add_handler(
        CommandHandler("start", start)
    )

    application.add_handler(
        CommandHandler("help", help_command)
    )

    application.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            handle_link
        )
    )

    await application.initialize()
    await application.start()
    await application.updater.start_polling()

    print("🤖 Social Video Downloader Bot is running.")

    while True:
        await asyncio.sleep(3600)


def start_web_server():
    port = int(os.getenv("PORT", "10000"))
    app.run(
        host="0.0.0.0",
        port=port
    )


if __name__ == "__main__":
    import threading

    web_thread = threading.Thread(
        target=start_web_server,
        daemon=True
    )

    web_thread.start()

    asyncio.run(run_bot())
