import os
from fastapi import FastAPI, Request, UploadFile, File
from fastapi.responses import JSONResponse
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
import random

# --- Config ---
BOT_TOKEN = os.getenv("BOT_TOKEN")
RENDER_EXTERNAL_URL = os.getenv("RENDER_EXTERNAL_URL")
PORT = int(os.getenv("PORT", 8000))

if not BOT_TOKEN or not RENDER_EXTERNAL_URL:
    raise RuntimeError("BOT_TOKEN or RENDER_EXTERNAL_URL missing.")

WEBHOOK_PATH = f"/webhook/{BOT_TOKEN}"
WEBHOOK_URL = f"{RENDER_EXTERNAL_URL}{WEBHOOK_PATH}"

app = FastAPI()
telegram_app: Application = None

# --- In-memory storage ---
pending_challenges = {}  # chat_id -> {challenger_id, challenged_username, cards}

# --- Helper functions ---
def calculate_hp(card):
    # Determine rarity factor
    rarity_map = {
        "Legendary": 4,
        "Ultra-Rare": 3,
        "Rare": 2,
        "Common": 1
    }
    rarity_factor = rarity_map.get(card.get("rarity"), 1)

    # Lower serial = more exclusive, give bonus
    serial_bonus = max(0, 2000 - int(card.get("serial_number", 1000))) / 500

    # Power & defense stats
    power = int(card.get("power", 10))
    defense = int(card.get("defense", 10))

    hp = (power + defense) * rarity_factor + serial_bonus
    return hp

def determine_winner(card1, card2):
    hp1 = calculate_hp(card1)
    hp2 = calculate_hp(card2)
    if hp1 > hp2:
        return card1
    elif hp2 > hp1:
        return card2
    else:
        # tie-breaker random
        return random.choice([card1, card2])

# --- Handlers ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Welcome to the Card Battle Arena!\n"
        "Use /challenge @username to start a battle."
    )

async def challenge(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("⚠️ Usage: /challenge @username")
        return

    challenged_username = context.args[0].lstrip("@")
    challenger = update.message.from_user
    chat_id = update.effective_chat.id

    pending_challenges[chat_id] = {
        "challenger": {"id": challenger.id, "username": challenger.username},
        "challenged": {"username": challenged_username},
        "cards": {}
    }

    await update.message.reply_text(
        f"⚔️ @{challenger.username} has challenged @{challenged_username}!\n"
        "Both players, please upload your cards to start the battle."
    )

async def upload_card(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if chat_id not in pending_challenges:
        await update.message.reply_text("⚠️ No active challenge. Start with /challenge @username")
        return

    user = update.message.from_user
    if not update.message.photo:
        await update.message.reply_text("⚠️ Please upload an image of your card.")
        return

    # Download the image
    file = await update.message.photo[-1].get_file()
    file_path = f"/tmp/{user.id}_card.jpg"
    await file.download_to_drive(file_path)

    # --- OCR / Metadata Extraction ---
    # Use your existing function from the card generator bot
    try:
        card_stats = extract_card_stats(file_path)
        # Expected output: dict with keys: name, power, defense, rarity, serial_number
        card_stats["username"] = user.username
    except Exception as e:
        await update.message.reply_text(f"⚠️ Failed to extract card stats: {str(e)}")
        return

    # Save card to challenge
    pending_challenges[chat_id]["cards"][user.id] = card_stats
    await update.message.reply_text(f"✅ Card received for @{user.username}")

    # Check if both players uploaded
    challenge = pending_challenges[chat_id]
    if len(challenge["cards"]) == 2:
        card1, card2 = list(challenge["cards"].values())
        winner = determine_winner(card1, card2)
        loser = card1 if winner == card2 else card2

        await context.bot.send_message(
            chat_id=chat_id,
            text=(
                f"🏆 Battle complete!\n"
                f"Winner: @{winner['username']}\n"
                f"@{card1['username']} vs @{card2['username']}\n"
                f"Stats:\n"
                f"{card1['username']}: HP {calculate_hp(card1):.1f}\n"
                f"{card2['username']}: HP {calculate_hp(card2):.1f}"
            )
        )
        del pending_challenges[chat_id]


# --- FastAPI webhook ---
@app.post(WEBHOOK_PATH)
async def webhook(request: Request):
    data = await request.json()
    update = Update.de_json(data, telegram_app.bot)
    await telegram_app.process_update(update)
    return JSONResponse({"ok": True})

# --- Startup / Shutdown ---
@app.on_event("startup")
async def on_startup():
    global telegram_app
    telegram_app = Application.builder().token(BOT_TOKEN).build()
    telegram_app.add_handler(CommandHandler("start", start))
    telegram_app.add_handler(CommandHandler("challenge", challenge))
    telegram_app.add_handler(CommandHandler("upload_card", upload_card))  # alternatively capture photos directly

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
