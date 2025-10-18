import os
import threading
from flask import Flask, render_template
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

# --- Config ---
BOT_TOKEN = os.getenv("BOT_TOKEN")
PORT = int(os.getenv("PORT", 8443))
RENDER_EXTERNAL_URL = os.getenv("RENDER_EXTERNAL_URL")

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN is missing. Add it in Render Environment Variables.")

if not RENDER_EXTERNAL_URL:
    raise ValueError("RENDER_EXTERNAL_URL is missing. Set RENDER_EXTERNAL_URL in Render.")

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

# --- Telegram Bot handlers ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🚀 Bot is alive! Use /battle to try the game.")

async def battle_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    game_url = f"{RENDER_EXTERNAL_URL}/battle"
    await update.message.reply_text(f"⚔️ Open the battle arena: {game_url}")

def run_bot():
    application = ApplicationBuilder().token(BOT_TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("battle", battle_command))

    application.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        url_path=BOT_TOKEN,
        webhook_url=WEBHOOK_URL,
    )

# --- Run both bot + flask in one service ---
if __name__ == "__main__":
    # Start bot in background thread
    threading.Thread(target=run_bot, daemon=True).start()

    # Start Flask server (serves /health + /battle)
    app.run(host="0.0.0.0", port=PORT)
