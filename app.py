import os
import asyncio
from fastapi import FastAPI, Request, UploadFile, Form
from fastapi.responses import JSONResponse
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters
from io import BytesIO
from PIL import Image, ImageDraw, ImageFont
import random

# --- Config ---
BOT_TOKEN = os.getenv("BOT_TOKEN")
RENDER_EXTERNAL_URL = os.getenv("RENDER_EXTERNAL_URL")
PORT = int(os.getenv("PORT", 8000))

if not BOT_TOKEN or not RENDER_EXTERNAL_URL:
    raise RuntimeError("BOT_TOKEN or RENDER_EXTERNAL_URL missing in environment")

WEBHOOK_PATH = f"/webhook/{BOT_TOKEN}"
WEBHOOK_URL = f"{RENDER_EXTERNAL_URL}{WEBHOOK_PATH}"

app = FastAPI()
telegram_app: Application = None

# --- In-memory battle state ---
active_challenges = {}  # challenger_id -> opponent_id
pending_cards = {}      # user_id -> card_data

# --- Card OCR / metadata parsing (stub) ---
def extract_card_stats(file_bytes: bytes):
    """
    Extract stats from uploaded card. Returns a dict:
    {
        "name": str,
        "power": int,
        "defense": int,
        "rarity": str,
        "serial": int
    }
    """
    # --- Replace with your OCR / metadata logic ---
    # For demo, generate random stats
    return {
        "name": f"Card{random.randint(100,999)}",
        "power": random.randint(50, 100),
        "defense": random.randint(30, 90),
        "rarity": random.choice(["Common","Rare","Ultra-Rare","Legendary"]),
        "serial": random.randint(1, 1999)
    }

# --- HP calculation ---
RARITY_MULTIPLIERS = {
    "Common": 1,
    "Rare": 1.2,
    "Ultra-Rare": 1.5,
    "Legendary": 2
}

def calculate_hp(card1, card2):
    """
    HP based on card stats, rarity multiplier, and serial number.
    Lower serial = more exclusive.
    """
    base1 = card1["power"] + card1["defense"]
    base2 = card2["power"] + card2["defense"]
    rarity_mult1 = RARITY_MULTIPLIERS.get(card1.get("rarity","Common"),1)
    rarity_mult2 = RARITY_MULTIPLIERS.get(card2.get("rarity","Common"),1)
    serial_bonus1 = max(1, 2000 - card1.get("serial",1000)) / 2000
    serial_bonus2 = max(1, 2000 - card2.get("serial",1000)) / 2000
    hp1 = int(base1 * rarity_mult1 * serial_bonus1)
    hp2 = int(base2 * rarity_mult2 * serial_bonus2)
    return hp1, hp2

# --- Simple local battle GIF generator ---
def generate_battle_gif(card1_name, card2_name):
    frames = []
    for i in range(5):
        img = Image.new("RGB", (400,100), color=(255,255,255))
        d = ImageDraw.Draw(img)
        d.text((20,20), f"{card1_name} attacks {card2_name}!", fill=(0,0,0))
        frames.append(img)
    bio = BytesIO()
    frames[0].save(bio, format="GIF", save_all=True, append_images=frames[1:], duration=500, loop=0)
    bio.seek(0)
    return bio

# --- Handlers ---
async def challenge(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args or len(context.args) < 1:
        await update.message.reply_text("Usage: /challenge @username")
        return

    opponent_username = context.args[0].lstrip("@")
    challenger_id = update.effective_user.id
    opponent_id = None  # We'll resolve this later when opponent uploads

    # Record active challenge
    active_challenges[challenger_id] = {"opponent_username": opponent_username, "opponent_id": None}
    await update.message.reply_text(
        f"⚔️ @{update.effective_user.username} has challenged @{opponent_username}!\n"
        "Both players must upload their cards to start the battle."
    )

async def upload_card(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not update.message.document:
        await update.message.reply_text("Please upload a card file (image).")
        return

    file = await update.message.document.get_file()
    file_bytes = await file.download_as_bytearray()
    card_data = extract_card_stats(file_bytes)
    pending_cards[user.id] = card_data

    # Check if this user is in a challenge
    challenger_info = None
    for cid, info in active_challenges.items():
        if cid == user.id:
            challenger_info = info
            break
        elif info["opponent_id"] == user.id:
            challenger_info = info
            break

    await update.message.reply_text(f"✅ @{user.username}'s card received!")

    # If we have both cards, start battle
    if challenger_info:
        challenger_id = user.id
        opponent_username = challenger_info["opponent_username"]
        opponent_id = challenger_info.get("opponent_id")
        # Try to resolve opponent_id if unknown
        for uid, _ in pending_cards.items():
            if uid != user.id and opponent_id is None:
                opponent_id = uid
                challenger_info["opponent_id"] = opponent_id

        if opponent_id and challenger_id in pending_cards and opponent_id in pending_cards:
            card1 = pending_cards[challenger_id]
            card2 = pending_cards[opponent_id]
            hp1, hp2 = calculate_hp(card1, card2)
            winner = user.username if hp1 >= hp2 else opponent_username

            # Generate GIF
            gif_bytes = generate_battle_gif(card1["name"], card2["name"])
            await update.message.reply_document(document=gif_bytes, filename="battle.gif")

            await update.message.reply_text(
                f"⚔️ Battle complete!\nWinner: @{winner}\n"
                f"{card1['name']} vs {card2['name']}"
            )

            # Clean up
            del pending_cards[challenger_id]
            del pending_cards[opponent_id]
            del active_challenges[challenger_id]

# --- FastAPI routes ---
@app.get("/")
async def root():
    return {"status": "ok", "service": "Card Battle Bot"}

@app.post(WEBHOOK_PATH)
async def webhook(request: Request):
    data = await request.json()
    update = Update.de_json(data, telegram_app.bot)
    await telegram_app.process_update(update)
    return JSONResponse({"ok": True})

# --- Lifecycle ---
@app.on_event("startup")
async def on_startup():
    global telegram_app
    telegram_app = Application.builder().token(BOT_TOKEN).build()
    telegram_app.add_handler(CommandHandler("challenge", challenge))
    telegram_app.add_handler(MessageHandler(filters.Document.ALL, upload_card))
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
