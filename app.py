import os
import asyncio
from flask import Flask, render_template
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

# --- Config ---
BOT_TOKEN = os.getenv("BOT_TOKEN")
RENDER_EXTERNAL_URL = os.getenv("RENDER_EXTERNAL_URL")
PORT = int(os.getenv("PORT", 8443))

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN missing.")
if not RENDER_EXTERNAL_URL:
    raise ValueError("RENDER_EXTERNAL_URL missing.")

WEBHOOK_PATH = f"/bot/{BOT_TOKEN}"
WEBHOOK_URL = f"{RENDER_EXTERNAL_URL}{WEBHOOK_PATH}"

# --- Flask app ---
app = Flask(__name__)

@app.route("/health")
def health():
    return {"status": "ok"}, 200

@app.route("/battle")
def battle():
    return render_template("battle.html")

# --- Telegram Handlers ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🚀 Bot is alive! Use /battle to try the game.")

async def battle_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"⚔️ Open the battle arena: {RENDER_EXTERNAL_URL}/battle")

# --- Combined main() ---
async def main():
    from telegram.ext import ApplicationBuilder
    import threading

    app_bot = (
        ApplicationBuilder()
        .token(BOT_TOKEN)
        .build()
    )

    app_bot.add_handler(CommandHandler("start", start))
    app_bot.add_handler(CommandHandler("battle", battle_command))

    # Initialize before starting webhook
    await app_bot.initialize()

    # Start webhook
    await app_bot.start()
    await app_bot.updater.start_webhook(
        listen="0.0.0.0",
        port=PORT,
        url_path=f"bot/{BOT_TOKEN}",
        webhook_url=WEBHOOK_URL,
    )

    print(f"✅ Webhook set: {WEBHOOK_URL}")

    # Run Flask server in separate thread
    def run_flask():
        app.run(host="0.0.0.0", port=PORT)

    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()

    # Idle loop keeps Telegram app alive
    await app_bot.updater.idle()

if __name__ == "__main__":
    asyncio.run(main())
