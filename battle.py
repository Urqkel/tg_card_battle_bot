import os
import json
import asyncio
from quart import Quart, request
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, ContextTypes

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
challenges = {}  # {challenged_id: {"challenger_id": int, "challenger_card": str}}

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
        return 0

# --- Telegram Handlers ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Welcome to Card Battle!\n"
        "Upload cards with /upload <card_name>\n"
        "Battle others with /battle @username <your_card_name>"
    )

async def upload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: /upload <card_name>")
        return

    card_name = context.args[0]
    user_id = str(update.effective_user.id)
    card = {
        "power": 10,
        "defense": 5,
        "rarity": "Rare",
        "ability": "Double Power",
        "tagline": f"{card_name} card!"
    }
    if user_id not in cards_db:
        cards_db[user_id] = {}
    cards_db[user_id][card_name] = card
    save_cards()
    await update.message.reply_text(f"Card '{card_name}' uploaded!")

async def battle_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 2:
        await update.message.reply_text("Usage: /battle @username <your_card_name>")
        return

    entities = update.message.entities
    if not entities or entities[0].type != "mention":
        await update.message.reply_text("Please mention a valid user.")
        return

    challenged_user = entities[0].user
    challenger_card_name = context.args[1]
    challenger_id = update.effective_user.id
    challenged_id = challenged_user.id

    user_cards = cards_db.get(str(challenger_id), {})
    if challenger_card_name not in user_cards:
        await update.message.reply_text(f"You don't have a card named '{challenger_card_name}'")
        return

    challenges[challenged_id] = {"challenger_id": challenger_id, "challenger_card": challenger_card_name}

    # Let challenged pick a card
    challenged_cards = cards_db.get(str(challenged_id), {})
    if not challenged_cards:
        await update.message.reply_text("The challenged user has no cards uploaded.")
        return

    keyboard = [
        [InlineKeyboardButton(name, callback_data=f"pick_{challenged_id}_{challenger_id}_{name}")]
        for name in challenged_cards.keys()
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        f"{challenged_user.first_name}, choose a card to battle!",
        reply_markup=reply_markup
    )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data.split("_")
    if data[0] != "pick":
        return

    challenged_id = int(data[1])
    challenger_id = int(data[2])
    challenged_card_name = data[3]

    # Retrieve cards
    challenger_card_name = challenges.get(challenged_id)["challenger_card"]
    challenger_card = cards_db[str(challenger_id)][challenger_card_name]
    challenged_card = cards_db[str(challenged_id)][challenged_card_name]

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

# --- Quart App for Webhook ---
app = Quart(__name__)

@app.route(f"/webhook/{BOT_TOKEN}", methods=["POST"])
async def webhook():
    data = await request.get_json(force=True)
    update = Update.de_json(data, application.bot)
    await application.process_update(update)
    return "ok"

@app.route("/", methods=["GET"])
async def index():
    return "Bot is running!"

# --- Run locally ---
if __name__ == "__main__":
    import hypercorn.asyncio
    import hypercorn.config
    config = hypercorn.config.Config()
    config.bind = [f"0.0.0.0:{os.environ.get('PORT', 5000)}"]
    asyncio.run(hypercorn.asyncio.serve(app, config))
