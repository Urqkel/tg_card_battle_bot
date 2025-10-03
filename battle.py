import os
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, ContextTypes, CallbackQueryHandler
)
from flask import Flask, request

# --- Flask setup (keeps Render process alive) ---
app = Flask(__name__)

# --- Telegram Bot setup ---
TOKEN = os.environ.get("BOT_TOKEN")
HEROKU_URL = os.environ.get("HEROKU_URL")  # e.g. https://your-app.onrender.com
PORT = int(os.environ.get("PORT", 10000))

application = ApplicationBuilder().token(TOKEN).build()

challenges = {}

# --- Handlers ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Welcome! Use /battle @username to challenge someone.")

async def battle_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Please mention a user to challenge, e.g., /battle @username")
        return

    challenged_user = update.message.entities[0].user
    challenges[challenged_user.id] = update.effective_user.id

    await update.message.reply_text(
        f"{challenged_user.first_name}, you have been challenged by {update.effective_user.first_name}!"
    )

    keyboard = [
        [InlineKeyboardButton("Accept", callback_data=f"accept_{challenged_user.id}_{update.effective_user.id}")],
        [InlineKeyboardButton("Decline", callback_data=f"decline_{challenged_user.id}_{update.effective_user.id}")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text("Do you accept the challenge?", reply_markup=reply_markup)

# Simple accept/decline handler
async def handle_response(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data.startswith("accept"):
        await query.edit_message_text("Challenge accepted! 🚀 (Battle logic coming soon)")
    elif query.data.startswith("decline"):
        await query.edit_message_text("Challenge declined ❌")

# Register handlers
application.add_handler(CommandHandler("start", start))
application.add_handler(CommandHandler("battle", battle_command))
application.add_handler(CallbackQueryHandler(handle_response))

# --- Flask route for Telegram webhook ---
@app.route(f"/webhook/{TOKEN}", methods=["POST"])
def webhook():
    update = Update.de_json(request.get_json(force=True), application.bot)
    application.update_queue.put_nowait(update)
    return "ok", 200

# Root route (for health checks)
@app.route("/")
def home():
    return "Bot is running!", 200

if __name__ == "__main__":
    # Set webhook
    application.bot.set_webhook(f"{HEROKU_URL}/webhook/{TOKEN}")

    # Run Flask server
    app.run(host="0.0.0.0", port=PORT)
