import os
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

BOT_TOKEN = os.environ["BOT_TOKEN"]
BASE_URL = os.environ["BASE_URL"]  # e.g., https://tg-card-battle-bot.onrender.com

user_cards = {}  # user_id -> card info

# Start command
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Welcome! Upload your card and use /battle @username to fight!"
    )

# Upload card command
async def upload_card(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Send your card info: /upload_card Name Power Defense")
        return
    user_cards[update.effective_user.id] = {
        "name": context.args[0],
        "power": int(context.args[1]),
        "defense": int(context.args[2])
    }
    await update.message.reply_text(f"Card registered: {context.args[0]}")

# Battle command
async def battle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Mention a user to battle, e.g., /battle @username")
        return
    challenged = update.message.entities[0].user
    if challenged.id not in user_cards or update.effective_user.id not in user_cards:
        await update.message.reply_text("Both players must upload cards first!")
        return

    # Send battle HTML link
    battle_url = f"{BASE_URL}/battle?p1={update.effective_user.id}&p2={challenged.id}"
    await update.message.reply_text(f"Battle ready! Open this link to start: {battle_url}")

# Main
if __name__ == "__main__":
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("upload_card", upload_card))
    app.add_handler(CommandHandler("battle", battle))
    app.run_webhook(
        listen="0.0.0.0",
        port=int(os.environ.get("PORT", 5000)),
        webhook_url=f"{BASE_URL}/webhook/{BOT_TOKEN}"
    )
