import os
import asyncio
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from telegram import Update, InputFile
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# --- Environment ---
BOT_TOKEN = os.getenv("BOT_TOKEN")
RENDER_EXTERNAL_URL = os.getenv("RENDER_EXTERNAL_URL")
PORT = int(os.getenv("PORT", 8000))

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN not set.")
if not RENDER_EXTERNAL_URL:
    raise RuntimeError("RENDER_EXTERNAL_URL not set.")

WEBHOOK_PATH = f"/webhook/{BOT_TOKEN}"
WEBHOOK_URL = f"{RENDER_EXTERNAL_URL}{WEBHOOK_PATH}"

# --- FastAPI ---
app = FastAPI()
telegram_app: Application = None

# --- In-memory storage for simplicity ---
# Map chat_id -> {'player1': card, 'player2': card}
ongoing_battles = {}

# --- Define rarity base HP and max serial ranges ---
RARITY_BASE_HP = {
    "Common": 100,
    "Rare": 200,
    "Ultra-Rare": 300,
    "Legendary": 400,
}

SERIAL_MAX = {
    "Common": 1999,
    "Rare": 999,
    "Ultra-Rare": 299,
    "Legendary": 99,
}

def compute_hp(card):
    rarity = card.get("rarity")
    serial = card.get("serial_number", SERIAL_MAX.get(rarity, 100))
    power = card.get("power", 50)
    defense = card.get("defense", 50)

    base_hp = RARITY_BASE_HP.get(rarity, 100)
    max_serial = SERIAL_MAX.get(rarity, 1000)
    serial_factor = (1 - (serial / max_serial)) * 50  # lower serial = higher HP

    temp_hp = base_hp + serial_factor + power + defense
    return temp_hp

async def run_battle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user1 = update.message.from_user
    chat_id = update.message.chat_id

    # Ensure both players have uploaded a card
    if len(context.chat_data.get("cards", {})) < 2:
        await update.message.reply_text("⚠️ Both players must upload a card to battle!")
        return

    cards = context.chat_data["cards"]
    (user1_id, card1), (user2_id, card2) = list(cards.items())

    # Compute temporary HP
    hp1 = compute_hp(card1)
    hp2 = compute_hp(card2)

    # Determine winner
    if hp1 > hp2:
        winner = f"@{user1.username}" if user1_id == user1.id else f"@{card1.get('owner_username', 'Player1')}"
    elif hp2 > hp1:
        winner = f"@{user2.username}" if user2_id != user1.id else f"@{card2.get('owner_username', 'Player2')}"
    else:
        winner = "It's a tie!"

    # Prepare battle summary
    card1_name = card1.get("name", "Unnamed Card")
    card2_name = card2.get("name", "Unnamed Card")
    result_text = (
        f"⚔️ Battle complete!\nWinner: {winner}\n"
        f"@{user1.username}'s {card1_name} ({hp1:.1f} HP) vs "
        f"@{user2.username}'s {card2_name} ({hp2:.1f} HP)"
    )

    # Send result
    await context.bot.send_message(chat_id=chat_id, text=result_text)

    # Clear cards for next battle
    context.chat_data["cards"] = {}

# --- Telegram Handlers ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🤖 Card Battle Bot is online! Use /battle to start a game.")

async def battle_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    ongoing_battles.setdefault(chat_id, {"player1": None, "player2": None})
    await update.message.reply_text(
        "⚔️ Battle started! Both players, please upload your cards as images or JSON."
    )

async def card_upload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    battle = ongoing_battles.setdefault(chat_id, {"player1": None, "player2": None})

    username = update.effective_user.username or update.effective_user.full_name

    # Parse stats from caption if provided
    caption = update.message.caption or ""
    parts = caption.split("|")
    stats = {}
    for kv in parts[1:]:
        if ":" in kv:
            key, val = kv.split(":")
            stats[key] = int(val)

    card_data = {
        "user_id": update.effective_user.id,
        "username": username,
        **stats
    }

    # Assign to first empty slot
    if not battle["player1"]:
        battle["player1"] = card_data
        await update.message.reply_text(f"✅ Card received from @{username} (Player 1)")
    elif not battle["player2"]:
        battle["player2"] = card_data
        await update.message.reply_text(f"✅ Card received from @{username} (Player 2)")
        # Both cards received, start battle
        asyncio.create_task(run_battle(chat_id))
    else:
        await update.message.reply_text("Battle already has two cards. Wait for the next round.")


# --- FastAPI Routes ---
@app.get("/")
async def root():
    return {"status": "ok", "service": "Card Battle Bot"}

@app.post(WEBHOOK_PATH)
async def webhook(request: Request):
    try:
        data = await request.json()
        update = Update.de_json(data, telegram_app.bot)
        await telegram_app.process_update(update)
        return JSONResponse({"ok": True})
    except Exception as e:
        print("❌ Error processing update:", e)
        return JSONResponse({"ok": False, "error": str(e)})

# --- Lifecycle ---
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
