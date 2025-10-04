import os
import random
from flask import Flask, request
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, ContextTypes
)

# --- Environment Variables ---
TOKEN = os.getenv("BOT_TOKEN") or "YOUR_BOT_TOKEN_HERE"
RENDER_URL = os.getenv("RENDER_URL") or "https://tg-card-battle-bot.onrender.com"

# --- Flask App ---
app = Flask(__name__)

# --- Telegram Bot Application ---
application = Application.builder().token(TOKEN).build()

# --- In-memory challenge storage ---
challenges = {}

# --- Handlers ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Welcome! Use /battle @username to challenge someone."
    )

async def battle_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Please mention a user to challenge, e.g., /battle @username")
        return

    if not update.message.entities or not update.message.entities[0].user:
        await update.message.reply_text("Could not find that user.")
        return

    challenged_user = update.message.entities[0].user
    challenges[challenged_user.id] = update.effective_user.id

    keyboard = [
        [
            InlineKeyboardButton(
                "Accept", callback_data=f"accept_{challenged_user.id}_{update.effective_user.id}"
            ),
            InlineKeyboardButton(
                "Decline", callback_data=f"decline_{challenged_user.id}_{update.effective_user.id}"
            ),
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(
        f"{challenged_user.first_name}, you have been challenged by {update.effective_user.first_name}!",
        reply_markup=reply_markup
    )

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data = query.data.split("_")
    action, challenged_id, challenger_id = data[0], int(data[1]), int(data[2])

    if action == "accept":
        # Minimal test battle (replace later with real card stats)
        player1_power = random.randint(50, 100)
        player2_power = random.randint(50, 100)
        if player1_power > player2_power:
            result = f"Challenger wins! ({player1_power} vs {player2_power})"
        elif player2_power > player1_power:
            result = f"Challenged wins! ({player2_power} vs {player1_power})"
        else:
            result = f"It's a tie! ({player1_power} vs {player2_power})"

        await query.edit_message_text(f"Challenge accepted! ⚔️\n{result}")
    else:
        await query.edit_message_text("Challenge declined ❌")

# --- Register handlers ---
application.add_handler(CommandHandler("start", start))
application.add_handler(CommandHandler("battle", battle_command))
application.add_handler(CallbackQueryHandler(button_callback))

# --- Webhook route ---
@app.route(f"/webhook/{TOKEN}", methods=["POST"])
async def webhook():
    update = Update.de_json(request.get_json(force=True), application.bot)
    await application.process_update(update)
    return "ok"

# --- Health check route ---
@app.route("/", methods=["GET"])
def home():
    return "Bot is running!"

# --- Set webhook on startup ---
async def set_webhook():
    url = f"{RENDER_URL}/webhook/{TOKEN}"
    await application.bot.set_webhook(url)
    print(f"Webhook set to {url}")

# --- Main entry point ---
def main():
    import asyncio
    loop = asyncio.get_event_loop()
    loop.run_until_complete(set_webhook())
    # Hypercorn runs Flask app in production
    print("Bot is ready to receive webhooks.")

if __name__ == "__main__":
    main()
