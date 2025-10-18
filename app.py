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

# --- Utility functions ---
def calculate_temp_hp(card1, card2):
    """Create temporary HP based on stats."""
    base1 = card1.get("power", 10) + card1.get("defense", 10)
    base2 = card2.get("power", 10) + card2.get("defense", 10)
    # Normalize to 50-100 range
    hp1 = min(max(base1, 50), 100)
    hp2 = min(max(base2, 50), 100)
    return hp1, hp2

async def generate_battle_gif(card1, card2):
    """
    Simulate async GIF generation of battle.
    Replace this with your actual GIF generation logic.
    """
    await asyncio.sleep(1)  # simulate processing delay
    # For now, return a placeholder file path
    return "placeholder_battle.gif"

async def run_battle(chat_id):
    battle = ongoing_battles.get(chat_id)
    if not battle:
        return
    card1 = battle.get("player1")
    card2 = battle.get("player2")
    if not card1 or not card2:
        return

    hp1, hp2 = calculate_temp_hp(card1, card2)

    # Simulate battle result
    winner = "player1" if hp1 >= hp2 else "player2"

    # Generate GIF
    gif_path = await generate_battle_gif(card1, card2)

    # Send result
    bot = telegram_app.bot
    result_text = f"⚔️ Battle complete!\nWinner: {winner}\n{card1['name']} vs {card2['name']}"
    await bot.send_message(chat_id=chat_id, text=result_text)
    try:
        with open(gif_path, "rb") as f:
            await bot.send_animation(chat_id=chat_id, animation=InputFile(f))
    except FileNotFoundError:
        await bot.send_message(chat_id=chat_id, text="Battle GIF not found.")

    # Clear battle
    ongoing_battles.pop(chat_id, None)

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

    # For demonstration, parse card name and stats from caption
    caption = update.message.caption or "Unnamed Card|power:10|defense:10"
    parts = caption.split("|")
    card_name = parts[0]
    stats = {kv.split(":")[0]: int(kv.split(":")[1]) for kv in parts[1:] if ":" in kv}
    card_data = {"name": card_name, **stats}

    # Assign to first empty slot
    if not battle["player1"]:
        battle["player1"] = card_data
        await update.message.reply_text(f"✅ Player 1 card received: {card_name}")
    elif not battle["player2"]:
        battle["player2"] = card_data
        await update.message.reply_text(f"✅ Player 2 card received: {card_name}")
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
