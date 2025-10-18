import os
import io
import asyncio
from fastapi import FastAPI, Request, UploadFile
from fastapi.responses import JSONResponse
from telegram import Update, InputFile
from telegram.ext import (
    Application, CommandHandler, MessageHandler, filters, ContextTypes
)
from PIL import Image, ImageDraw, ImageFont
import pytesseract
import imageio

# --- Config ---
BOT_TOKEN = os.getenv("BOT_TOKEN")
RENDER_EXTERNAL_URL = os.getenv("RENDER_EXTERNAL_URL")
PORT = int(os.getenv("PORT", 8000))

if not BOT_TOKEN or not RENDER_EXTERNAL_URL:
    raise RuntimeError("BOT_TOKEN and RENDER_EXTERNAL_URL must be set in environment variables.")

WEBHOOK_PATH = f"/webhook/{BOT_TOKEN}"
WEBHOOK_URL = f"{RENDER_EXTERNAL_URL}{WEBHOOK_PATH}"

# --- FastAPI app ---
app = FastAPI()
telegram_app: Application = None

# --- Game state ---
pending_challenges = {}  # {challenger_id: opponent_username}
uploaded_cards = {}      # {user_id: card_data}

# --- Card tier ranges ---
tier_values = {"Common": 1, "Rare": 2, "Ultra-Rare": 3, "Legendary": 4}
tier_serials = {
    "Common": (1000, 1999),
    "Rare": (300, 999),
    "Ultra-Rare": (100, 299),
    "Legendary": (1, 99)
}

# --- Handlers ---
async def battle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Initiate a challenge to another user."""
    if not context.args:
        await update.message.reply_text(
            "Usage: /battle @opponent_username\nChallenge someone to a battle!"
        )
        return

    opponent_username = context.args[0].replace("@", "")
    challenger_id = update.message.from_user.id

    pending_challenges[challenger_id] = opponent_username
    await update.message.reply_text(
        f"⚔️ Challenge sent! @{opponent_username}, upload your card to accept the battle."
    )


async def handle_card_upload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle card file uploads for both challenger and opponent."""
    user_id = update.message.from_user.id
    username = update.message.from_user.username

    if not update.message.document:
        await update.message.reply_text("Please upload a card file (image/pdf).")
        return

    # Download the file
    file = await update.message.document.get_file()
    file_bytes = await file.download_as_bytearray()
    card_data = await extract_card_stats(file_bytes, username)
    uploaded_cards[user_id] = card_data
    await update.message.reply_text("✅ Card received!")

    # Check if both players have uploaded
    for challenger_id, opponent_username in pending_challenges.items():
        if username == opponent_username or user_id == challenger_id:
            challenger_card = uploaded_cards.get(challenger_id)
            opponent_card = None
            for uid, c in uploaded_cards.items():
                if uid != challenger_id and c["username"] == opponent_username:
                    opponent_card = c
                    break
            if challenger_card and opponent_card:
                await run_battle(challenger_card, opponent_card, update)
                # Clear state
                uploaded_cards.pop(challenger_id, None)
                uploaded_cards.pop(opponent_card["user_id"], None)
                pending_challenges.pop(challenger_id, None)
            break


async def extract_card_stats(file_bytes: bytes, username: str):
    """
    Extract stats from the uploaded card using OCR.
    Expects stats in format:
        Tier: Rare
        Serial: 450
        Power: 70
        Defense: 50
    """
    try:
        image = Image.open(io.BytesIO(file_bytes)).convert("RGB")
        text = pytesseract.image_to_string(image)

        tier = next((t for t in tier_values if t.lower() in text.lower()), "Common")
        serial = int(next((s for s in text.split() if s.isdigit()), 1000))
        power = int(next((s for s in text.split() if s.isdigit() and 0 < int(s) <= 100), 50))
        defense = int(next((s for s in text.split() if s.isdigit() and 0 < int(s) <= 100), 50))

        return {
            "tier": tier,
            "serial": serial,
            "power": power,
            "defense": defense,
            "username": username,
            "user_id": username  # for state tracking
        }
    except Exception as e:
        return {
            "tier": "Common",
            "serial": 1000,
            "power": 50,
            "defense": 50,
            "username": username,
            "user_id": username
        }


async def run_battle(card1, card2, update: Update):
    """Calculate HP, determine winner, and generate local battle GIF."""
    def calculate_hp(card):
        base = card["power"] + card["defense"]
        tier_factor = tier_values.get(card["tier"], 1) * 10
        serial_factor = max(0, 2000 - card["serial"]) // 50
        return base + tier_factor + serial_factor

    hp1 = calculate_hp(card1)
    hp2 = calculate_hp(card2)

    winner = card1["username"] if hp1 >= hp2 else card2["username"]

    # Generate battle GIF
    gif_bytes = generate_battle_gif(card1, card2, hp1, hp2)
    await update.message.reply_document(
        document=InputFile(gif_bytes, filename="battle.gif"),
        caption=f"⚔️ Battle complete!\nWinner: @{winner}\n"
                f"@{card1['username']} HP: {hp1} vs @{card2['username']} HP: {hp2}"
    )


def generate_battle_gif(card1, card2, hp1, hp2):
    """Create a simple animated GIF showing HP bars."""
    frames = []
    width, height = 400, 200
    for i in range(5):
        img = Image.new("RGB", (width, height), color="white")
        draw = ImageDraw.Draw(img)
        # Draw HP bars
        draw.rectangle([50, 50, 50 + hp1, 70], fill="red")
        draw.rectangle([50, 150, 50 + hp2, 170], fill="blue")
        draw.text((50, 30), f"{card1['username']} HP: {hp1}", fill="black")
        draw.text((50, 130), f"{card2['username']} HP: {hp2}", fill="black")
        frames.append(img)

    gif_bytes = io.BytesIO()
    imageio.mimsave(gif_bytes, frames, format="GIF", duration=0.5)
    gif_bytes.seek(0)
    return gif_bytes


# --- FastAPI webhook route ---
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

    telegram_app.add_handler(CommandHandler("battle", battle))
    telegram_app.add_handler(MessageHandler(filters.Document.ALL, handle_card_upload))

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


# --- Health check ---
@app.get("/health")
async def health():
    return {"status": "ok", "service": "Card Battle Bot"}
