import os
import json
from flask import Flask, request
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler, CallbackQueryHandler

BOT_TOKEN = os.environ["BOT_TOKEN"]
STORAGE_FILE = "cards.json"

# --- Load/Save cards ---
if os.path.exists(STORAGE_FILE):
    with open(STORAGE_FILE, "r") as f:
        cards_db = json.load(f)
else:
    cards_db = {}

def save_cards():
    with open(STORAGE_FILE, "w") as f:
        json.dump(cards_db, f)

# --- In-memory challenges ---
challenges = {}  # {challenged_id: challenger_id}

# --- Battle Logic ---
def battle(card1, card2):
    rarity_bonus = {"Common": 0, "Rare": 5, "Ultra-Rare": 10, "Legendary": 20}
    eff1 = card1["power"] + rarity_bonus.get(card1["rarity"], 0) - card2["defense"]
    eff2 = card2["power"] + rarity_bonus.get(card2["rarity"], 0) - card1["defense"]

    if card1.get("ability") == "Double Power":
        eff1 *= 2
    if card2.get("ability") == "Double Power":
        eff2 *= 2

    if eff1 > eff2:
        return 1
    elif eff2 > eff1:
        return 2
    else:
        return 0  # Tie

# --- Telegram Bot Handlers ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Welcome to Card Battle!\n"
        "Use /upload to upload your card.\n"
        "Use /battle @username to challenge someone."
    )

async def upload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    # Example: hard-coded card (replace with real upload flow if needed)
    card = {
        "power": 10,
        "defense": 5,
        "rarity": "Rare",
        "ability": "Double Power",
        "tagline": "My first card!"
    }
    cards_db[user_id] = card
    save_cards()
    await update.message.reply_text("Your card has been uploaded!")

async def battle_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Mention a user to challenge, e.g., /battle @username")
        return

    if not update.message.entities:
        await update.message.reply_text("Please mention a valid username.")
        return

    challenged_user = update.message.entities[0].user
    if not challenged_user:
        await update.message.reply_text("Could not resolve username.")
        return

    # Store challenge
    challenges[challenged_user.id] = update.effective_user.id

    keyboard = [
        [
            InlineKeyboardButton(
                "Accept", callback_data=f"accept_{challenged_user.id}_{update.effective_user.id}"
            ),
            InlineKeyboardButton(
                "Decline", callback_data=f"decline_{challenged_user.id}_{update.effective_user.id}"
            )
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        f"{challenged_user.first_name}, you have been challenged by {update.effective_user.first_name}!",
        reply_markup=reply_markup
    )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data.split("_")
    action, challenged_id, challenger_id = data[0], int(data[1]), int(data[2])

    if action == "decline":
        await query.edit_message_text("Challenge declined.")
        challenges.pop(challenged_id, None)
        return

    # Accepted
    challenger_card = cards_db.get(str(challenger_id))
    challenged_card = cards_db.get(str(challenged_id))
    if not challenger_card or not challenged_card:
        await query.edit_message_text("One of the players has no card uploaded.")
        return

    winner = battle(challenger_card, challenged_card)
    if winner == 1:
        msg = f"{context.bot.get_chat(challenger_id).first_name} wins!"
    elif winner == 2:
        msg = f"{context.bot.get_chat(challenged_id).first_name} wins!"
    else:
        msg = "It's a tie!"

    await query.edit_message_text(msg)
    challenges.pop(challenged_id, None)

# --- Telegram Application ---
application = ApplicationBuilder().token(BOT_TOKEN).build()
application.add_handler(CommandHandler("start", start))
application.add_handler(CommandHandler("upload", upload))
application.add_handler(CommandHandler("battle", battle_command))
application.add_handler(CallbackQueryHandler(button_handler))

# --- Flask Webhook ---
from flask import Flask, request

app = Flask(__name__)

@app.route(f"/webhook/{BOT_TOKEN}", methods=["POST"])
def webhook():
    data = request.get_json(force=True)
    update = Update.de_json(data, application.bot)
    application.create_task(application.process_update(update))
    return "ok"

@app.route("/", methods=["GET"])
def index():
    return "Bot is running!"

# --- Run for local testing ---
if __name__ == "__main__":
    app.run(port=int(os.environ.get("PORT", 5000)))
