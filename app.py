import os
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters
import random

# --- Config ---
BOT_TOKEN = os.getenv("BOT_TOKEN")
RENDER_EXTERNAL_URL = os.getenv("RENDER_EXTERNAL_URL")
PORT = int(os.getenv("PORT", 8000))

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN not found in environment.")
if not RENDER_EXTERNAL_URL:
    raise RuntimeError("RENDER_EXTERNAL_URL not found in environment.")

WEBHOOK_PATH = f"/webhook/{BOT_TOKEN}"
WEBHOOK_URL = f"{RENDER_EXTERNAL_URL}{WEBHOOK_PATH}"

# --- FastAPI App ---
app = FastAPI()
telegram_app: Application = None

# --- In-memory storage ---
user_cards = {}         # {user_id: card_data}
active_battles = {}     # {chat_id: {"challenger": id, "opponent": username}}

# --- Helper Functions ---
def generate_card_stats(file_id):
    """Generate temporary stats for a card based on some randomness."""
    rarity_multiplier = random.choice([1, 1.2, 1.5])  # Common, Rare, Epic
    power = random.randint(20, 50) * rarity_multiplier
    defense = random.randint(10, 40) * rarity_multiplier
    return {"power": int(power), "defense": int(defense)}

def get_user_id(username):
    """Placeholder: in real case, map @username to user_id"""
    # In a live bot, you would track user IDs during /battle initiation
    return None

def run_battle(card1, card2):
    """Simple turn-based battle simulation."""
    hp1 = card1["stats"]["power"] + card1["stats"]["defense"]
    hp2 = card2["stats"]["power"] + card2["stats"]["defense"]

    turn = 0
    while hp1 > 0 and hp2 > 0:
        if turn % 2 == 0:
            dmg = max(1, card1["stats"]["power"] - card2["stats"]["defense"] // 2)
            hp2 -= dmg
        else:
            dmg = max(1, card2["stats"]["power"] - card1["stats"]["defense"] // 2)
            hp1 -= dmg
        turn += 1

    if hp1 > 0:
        return "🏆 Challenger wins!"
    else:
        return "🏆 Opponent wins!"

# --- Telegram Handlers ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🤖 Bot is alive! Use /battle @opponent to start a game.")

# --- In-memory storage ---
active_battles = {}  # {chat_id: {"challenger_id": int, "opponent_id": int, "cards": {user_id: card_data}}}

async def battle_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("⚔️ Usage: /battle @opponent")
        return

    opponent_username = context.args[0].lstrip("@")
    chat_id = update.effective_chat.id
    challenger_id = update.effective_user.id

    active_battles[chat_id] = {
        "challenger_id": challenger_id,
        "opponent_username": opponent_username,
        "opponent_id": None,  # to be filled when opponent uploads
        "cards": {}
    }

    await update.message.reply_text(
        f"Challenge sent to @{opponent_username}! Both players, please upload your trading cards."
    )


async def card_upload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    battle = active_battles.get(chat_id)

    if not battle:
        await update.message.reply_text("No active battle in this chat. Start one with /battle @username.")
        return

    if not update.message.photo:
        await update.message.reply_text("Please upload your trading card image.")
        return

    file_id = update.message.photo[-1].file_id
    card_stats = generate_card_stats(file_id)
    battle["cards"][user_id] = {"file_id": file_id, "stats": card_stats}

    # If this is the opponent uploading, set their user ID
    if user_id != battle["challenger_id"] and battle["opponent_id"] is None:
        battle["opponent_id"] = user_id

    await update.message.reply_text("✅ Card uploaded!")

    # Check if both players have uploaded
    if battle["challenger_id"] in battle["cards"] and battle["opponent_id"] in battle["cards"]:
        card1 = battle["cards"][battle["challenger_id"]]
        card2 = battle["cards"][battle["opponent_id"]]
        result_text = run_battle(card1, card2)
        await update.message.reply_text(result_text)

        # Cleanup
        del active_battles[chat_id]

# --- FastAPI Routes ---
@app.get("/")
async def root():
    return {"status": "ok", "service": "Card Battle Bot"}

@app.post(WEBHOOK_PATH)
async def webhook(request: Request):
    data = await request.json()
    update = Update.de_json(data, telegram_app.bot)
    await telegram_app.process_update(update)
    return JSONResponse({"ok": True})

# --- Lifecycle Events ---
@app.on_event("startup")
async def on_startup():
    global telegram_app
    telegram_app = Application.builder().token(BOT_TOKEN).build()
    telegram_app.add_handler(CommandHandler("start", start))
    telegram_app.add_handler(CommandHandler("battle", battle_command))
    telegram_app.add_handler(MessageHandler(filters.PHOTO, card_upload))
    await telegram_app.initialize()
    await telegram_app.bot.delete_webhook(drop_pending_updates=True)
    await telegram_app.bot.set_webhook(WEBHOOK_URL)
    print(f"✅ Webhook set to {WEBHOOK_URL}")

@app.on_event("shutdown")
async def on_shutdown():
    if telegram_app:
        await telegram_app.bot.delete_webhook()
        await telegram_app.shutdown()
        await telegram_app.stop()
    print("🛑 Bot stopped cleanly.")
