import os
import asyncio
from flask import Flask, render_template, jsonify, request
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

# --- Config ---
BOT_TOKEN = os.getenv("BOT_TOKEN")
PORT = int(os.getenv("PORT", 10000))  # Render detects this automatically
RENDER_EXTERNAL_URL = os.getenv("RENDER_EXTERNAL_URL")

if not BOT_TOKEN:
    raise ValueError("❌ BOT_TOKEN is missing. Add it in Render Environment Variables.")
if not RENDER_EXTERNAL_URL:
    raise ValueError("❌ RENDER_EXTERNAL_URL is missing. Example: https://your-app.onrender.com")

WEBHOOK_PATH = f"/webhook/{BOT_TOKEN}"
WEBHOOK_URL = f"{RENDER_EXTERNAL_URL}{WEBHOOK_PATH}"

# --- Flask app ---
app = Flask(__name__)

@app.route("/health")
def health():
    return jsonify({"status": "ok"}), 200

@app.route("/battle")
def battle():
    return render_template("battle.html")

# --- Telegram Bot handlers ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🚀 Bot is alive! Use /battle to try the game.")

async def battle_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    game_url = f"{RENDER_EXTERNAL_URL}/battle"
    await update.message.reply_text(f"⚔️ Open the battle arena: {game_url}")

# --- Combined startup function ---
async def main():
    app_bot = Application.builder().token(BOT_TOKEN).build()
    app_bot.add_handler(CommandHandler("start", start))
    app_bot.add_handler(CommandHandler("battle", battle_command))

    # Set webhook (delete old first, then set new)
    await app_bot.bot.delete_webhook(drop_pending_updates=True)
    await app_bot.bot.set_webhook(url=WEBHOOK_URL)

    # Flask runs in the same event loop via asyncio.create_task
    async def run_flask():
        # use waitress in production, Flask dev server for simplicity here
        from waitress import serve
        serve(app, host="0.0.0.0", port=PORT)

    # run Flask + Telegram webhook receiver
    await asyncio.gather(
        app_bot.start(),
        run_flask(),
    )

if __name__ == "__main__":
    asyncio.run(main())
