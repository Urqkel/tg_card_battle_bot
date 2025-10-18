import os
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from io import BytesIO

# ---------------- Config ----------------
BOT_TOKEN = os.getenv("BOT_TOKEN")
RENDER_EXTERNAL_URL = os.getenv("RENDER_EXTERNAL_URL")
PORT = int(os.getenv("PORT", 8000))

if not BOT_TOKEN or not RENDER_EXTERNAL_URL:
    raise RuntimeError("BOT_TOKEN and RENDER_EXTERNAL_URL must be set in environment variables.")

WEBHOOK_PATH = f"/webhook/{BOT_TOKEN}"
WEBHOOK_URL = f"{RENDER_EXTERNAL_URL}{WEBHOOK_PATH}"

# ---------------- FastAPI ----------------
app = FastAPI()
telegram_app: Application = None

# ---------------- Game State ----------------
active_battles = {}  # key: frozenset of user_ids, value: battle info
pending_cards = {}   # user_id -> card data

RARITY_TIERS = {
    "Common": (1000, 1999),
    "Rare": (300, 999),
    "Ultra-Rare": (100, 299),
    "Legendary": (1, 99),
}

# ---------------- Utility Functions ----------------
def extract_card_stats(file_bytes: bytes):
    """
    Extract card stats from image using OCR or metadata.
    Return dict: {'name': str, 'power': int, 'defense': int, 'rarity': str, 'serial': int}
    """
    # TODO: Replace with your OCR/metadata parsing logic
    # Example dummy return
    return {
        "name": "Card_" + str(len(pending_cards) + 1),
        "power": 50,
        "defense": 50,
        "rarity": "Rare",
        "serial": 500
    }

def calculate_hp(card1, card2):
    """
    Calculate temporary HP based on stats, rarity, and serial number.
    Higher tier and lower serial = higher HP
    """
    def card_hp(card):
        rarity_factor = {
            "Common": 1,
            "Rare": 2,
            "Ultra-Rare": 3,
            "Legendary": 4
        }.get(card.get("rarity"), 1)
        serial_factor = max(1, 2000 - card.get("serial", 1000)) / 1000
        stat_factor = card.get("power", 0) + card.get("defense", 0)
        return int((stat_factor * rarity_factor) * serial_factor)

    return card_hp(card1), card_hp(card2)

def generate_battle_gif(name1: str, name2: str) -> BytesIO:
    """
    Generate a simple local GIF animation for the battle.
    Returns BytesIO.
    """
    from PIL import Image, ImageDraw
    import imageio

    frames = []
    for i in range(5):
        img = Image.new("RGB", (300, 150), color=(255, 255, 255))
        d = ImageDraw.Draw(img)
        d.text((10, 50), f"{name1} ⚔ {name2}", fill=(0, 0, 0))
        d.text((10, 80), "Battle ongoing..." + "." * i, fill=(0, 0, 0))
        frames.append(img)

    gif_bytes = BytesIO()
    imageio.mimsave(gif_bytes, frames, format="GIF", duration=0.5)
    gif_bytes.seek(0)
    return gif_bytes

# ---------------- Telegram Handlers ----------------
async def challenge(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args or not context.args[0].startswith("@"):
        await update.message.reply_text("Usage: /challenge @username")
        return

    challenger = update.effective_user
    opponent_username = context.args[0][1:]  # remove @
    active_battles[frozenset({challenger.id, opponent_username})] = {
        "challenger": challenger.id,
        "opponent_username": opponent_username
    }

    await update.message.reply_text(
        f"⚔️ @{challenger.username} has challenged @{opponent_username}! "
        "Both players, please upload your card to start the battle."
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

    await update.message.reply_text(f"✅ @{user.username}'s card received!")

    # Check for any active battle this user is in
    for players_ids, battle_info in list(active_battles.items()):
        if user.id in players_ids:
            # Resolve opponent_id
            if isinstance(players_ids, frozenset):
                # Map usernames to ids if needed
                ids = list(players_ids)
                opponent_id = next((pid for pid in ids if pid != user.id), None)
            else:
                opponent_id = None

            # Both players uploaded their cards?
            if opponent_id in pending_cards and user.id in pending_cards:
                card1 = pending_cards[user.id]
                card2 = pending_cards[opponent_id]

                hp1, hp2 = calculate_hp(card1, card2)
                winner_id = user.id if hp1 >= hp2 else opponent_id
                winner_username = context.bot.get_chat(winner_id).username

                gif_bytes = generate_battle_gif(card1["name"], card2["name"])
                await update.message.reply_document(document=gif_bytes, filename="battle.gif")

                await update.message.reply_text(
                    f"⚔️ Battle complete!\nWinner: @{winner_username}\n"
                    f"{card1['name']} vs {card2['name']}"
                )

                # Clean up
                pending_cards.pop(user.id, None)
                pending_cards.pop(opponent_id, None)
                active_battles.pop(players_ids)
            break

# ---------------- FastAPI Routes ----------------
@app.get("/")
async def root():
    return {"status": "ok", "service": "Card Battle Bot"}

@app.post(WEBHOOK_PATH)
async def webhook(request: Request):
    data = await request.json()
    update = Update.de_json(data, telegram_app.bot)
    await telegram_app.process_update(update)
    return JSONResponse({"ok": True})

# ---------------- Lifecycle ----------------
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
