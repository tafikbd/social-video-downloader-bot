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
    return "Social Video Bot is running."


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Welcome!\n\n"
        "Send me a supported video link that you have permission to download.\n\n"
        "📥 I'll download the video and send it back to you."
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📥 Send me a supported video URL.\n\n"
        "Only download content you own or have permission to download."
    )


def download_video(url, folder):
    output = str(Path(folder) / "%(title).80s.%(ext)s")

    options = {
        "outtmpl": output,
        "format": (
            "best[ext=mp4][vcodec!=none][acodec!=none]/"
            "best[ext=mp4]/best"
        ),
        "noplaylist": True,
        "max_filesize": 50 * 1024 * 1024,
        "quiet": True,
        "no_warnings": True,
        "socket_timeout": 30,
    }

    with yt_dlp.YoutubeDL(options) as ydl:
        info = ydl.extract_info(url, download=True)
        downloaded = ydl.prepare_filename(info)

        files = list(Path(folder).glob("*"))
        if not files:
            raise RuntimeError("Downloaded file was not found.")

        return str(files[0])


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
        "🔍 Link received.\n\n"
        "⏳ Downloading video..."
    )

    try:
        with tempfile.TemporaryDirectory() as temp_folder:
            file_path = await asyncio.to_thread(
                download_video,
                url,
                temp_folder,
            )

            await status.edit_text("📤 Uploading video...")

            with open(file_path, "rb") as video:
                await update.message.reply_video(
                    video=video,
                    caption="✅ Download complete!"
                )

            await status.delete()

    except Exception as e:
        print("DOWNLOAD ERROR:", repr(e))

        await status.edit_text(
            "❌ I couldn't download this video.\n\n"
            "Make sure the link is supported and the content is "
            "available without login or DRM restrictions."
        )


async def run_bot():
    application = Application.builder().token(BOT_TOKEN).build()

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

    print("🤖 Social Video Bot is running.")

    while True:
        await asyncio.sleep(3600)


def start_web_server():
    port = int(os.getenv("PORT", "10000"))
    app.run(host="0.0.0.0", port=port)


if __name__ == "__main__":
    import threading

    web_thread = threading.Thread(
        target=start_web_server,
        daemon=True
    )

    web_thread.start()

    asyncio.run(run_bot())
