import os
import re
import asyncio
from pathlib import Path

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
        "Use /help for instructions."
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📥 Send a supported video URL.\n\n"
        "Only download content you own or have permission to download."
    )


async def handle_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()

    url_pattern = r"https?://\S+"

    if not re.search(url_pattern, text):
        await update.message.reply_text(
            "🔗 Please send a valid video link."
        )
        return

    await update.message.reply_text(
        "🔍 Link received.\n"
        "Downloader processing will be added next."
    )


async def run_bot():
    application = Application.builder().token(BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_link)
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
