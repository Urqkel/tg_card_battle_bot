from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes
from server import start_battle

TOKEN = "YOUR_BOT_TOKEN"

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Welcome! Upload your card via /upload_card and challenge a player with /battle @username"
    )

async def battle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Please mention a user to challenge, e.g., /battle @username")
        return
    challenged_username = context.args[0]
    # Trigger battle animation + GIF
    gif_path = start_battle(update.effective_user.username, challenged_username)
    await update.message.reply_animation(open(gif_path, "rb"))

app = ApplicationBuilder().token(TOKEN).build()
app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("battle", battle))

# --- Webhook ---
from flask import Flask, request
flask_app = Flask(__name__)

@flask_app.route(f"/webhook/{TOKEN}", methods=["POST"])
def webhook():
    update = Update.de_json(request.get_json(force=True), app.bot)
    import asyncio
    asyncio.run(app.process_update(update))
    return "ok"

app.flask_app = flask_app

