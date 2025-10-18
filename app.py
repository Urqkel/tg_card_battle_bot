import io
import os
import random
import re
from PIL import Image, ImageDraw, ImageFont
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from telegram import Update, InputFile
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters
import pytesseract

# =====================
# Config
# =====================
BOT_TOKEN = os.getenv("BOT_TOKEN")
RENDER_EXTERNAL_URL = os.getenv("RENDER_EXTERNAL_URL")
PORT = int(os.getenv("PORT", 8000))

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN not found in environment.")
if not RENDER_EXTERNAL_URL:
    raise RuntimeError("RENDER_EXTERNAL_URL not found in environment.")

WEBHOOK_PATH = f"/webhook/{BOT_TOKEN}"
WEBHOOK_URL = f"{RENDER_EXTERNAL_URL}{WEBHOOK_PATH}"

app = FastAPI()
telegram_app: Application = None

# =====================
# Storage
# =====================
pending_battles = {}
active_battles = {}

# =====================
# OCR + Stat Extraction
# =====================
def extract_card_stats(image_path):
    """Extract power, defense, rarity, and serial number from a card"""
    text = pytesseract.image_to_string(Image.open(image_path)).lower()

    power = defense = 50
    rarity = "common"
    serial = 1500

    power_match = re.search(r"power[:\s]*([0-9]+)", text)
    defense_match = re.search(r"defense[:\s]*([0-9]+)", text)
    rarity_match = re.search(r"(common|rare|ultra[-\s]?rare|legendary)", text)
    serial_match = re.search(r"#?(\d{1,4})", text)

    if power_match:
        power = int(power_match.group(1))
    if defense_match:
        defense = int(defense_match.group(1))
    if rarity_match:
        rarity = rarity_match.group(1).replace("-", "").replace(" ", "")
    if serial_match:
        serial = int(serial_match.group(1))

    return {"power": power, "defense": defense, "rarity": rarity, "serial": serial}


# =====================
# HP Calculation
# =====================
def calculate_hp(stats):
    rarity_hp = {
        "common": 100,
        "rare": 200,
        "ultrarare": 300,
        "legendary": 400
    }
    base_hp = rarity_hp.get(stats["rarity"], 100)
    serial_bonus = max(0, 2000 - stats["serial"]) / 10
    total_hp = base_hp + serial_bonus + (stats["power"] * 0.5) + (stats["defense"] * 0.3)
    return int(total_hp)


# =====================
# Battle Animation
# =====================
def generate_battle_gif(card1_path, card2_path, winner_name):
    """Create a short local battle animation"""
    card1 = Image.open(card1_path).resize((350, 500))
    card2 = Image.open(card2_path).resize((350, 500))
    width, height = 900, 550
    bg_color = (25, 25, 40)
    frames = []

    for i in range(10):
        frame = Image.new("RGB", (width, height), bg_color)
        x1 = 50 + i * 15
        x2 = width - 400 - i * 15
        frame.paste(card1, (x1, 25))
        frame.paste(card2, (x2, 25))
        frames.append(frame)

    flash = Image.new("RGB", (width, height), (255, 255, 255))
    frames.append(flash)

    final = frames[-1].copy()
    draw = ImageDraw.Draw(final)
    try:
        font = ImageFont.truetype("arial.ttf", 40)
    except:
        font = ImageFont.load_default()
    draw.text(
        (width // 2 - 180, height - 80),
        f"🏆 {winner_name} Wins!",
        fill=(255, 215, 0),
        font=font,
    )
    frames.append(final)

    gif_bytes = io.BytesIO()
    frames[0].save(
        gif_bytes,
        format="GIF",
        save_all=True,
        append_images=frames[1:],
        duration=120,
        loop=0,
    )
    gif_bytes.seek(0)
    return gif_bytes


# =====================
# Telegram Commands
# =====================
async def battle_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "⚔️ *Battle System Online!*\n\n"
        "To challenge another player, use:\n`/challenge @username`",
        parse_mode="Markdown"
    )


async def challenge_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args or not context.args[0].startswith("@"):
        await update.message.reply_text("Usage: /challenge @username")
        return

    opponent = context.args[0].lower()
    challenger = update.effective_user
    pending_battles[opponent] = challenger.username

    await update.message.reply_text(
        f"⚔️ @{challenger.username} has challenged {opponent} to a battle!\n\n"
        f"{opponent}, reply with your PFP trading card image to accept!"
    )


async def image_upload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user.username
    photo = update.message.photo[-1]
    file = await photo.get_file()

    os.makedirs("cards", exist_ok=True)
    image_path = f"cards/{user}.jpg"
    await file.download_to_drive(image_path)

    # Match challenger & opponent
    for opponent, challenger in pending_battles.items():
        if user == opponent.strip("@"):
            active_battles[user] = {"path": image_path}
            challenger_path = f"cards/{challenger}.jpg"
            if os.path.exists(challenger_path):
                await run_battle(update, challenger, opponent)
            else:
                await update.message.reply_text(f"Card received from @{user}. Waiting for @{challenger}'s card...")
            return

        if user == challenger:
            active_battles[user] = {"path": image_path}
            await update.message.reply_text(f"Card received from @{user}. Waiting for the opponent to respond...")
            return

    await update.message.reply_text("Card received, but no active battle found.")


async def run_battle(update, challenger_username, opponent_username):
    challenger_path = f"cards/{challenger_username}.jpg"
    opponent_path = f"cards/{opponent_username}.jpg"

    challenger_stats = extract_card_stats(challenger_path)
    opponent_stats = extract_card_stats(opponent_path)

    challenger_hp = calculate_hp(challenger_stats)
    opponent_hp = calculate_hp(opponent_stats)

    while challenger_hp > 0 and opponent_hp > 0:
        challenger_hp -= max(5, random.randint(5, 15))
        opponent_hp -= max(5, random.randint(5, 15))

    winner = (
        f"@{challenger_username}" if challenger_hp > opponent_hp else f"@{opponent_username}"
    )

    gif = generate_battle_gif(challenger_path, opponent_path, winner)

    await update.message.reply_animation(
        animation=gif,
        caption=(
            f"⚔️ *Battle Complete!*\n"
            f"🏆 Winner: {winner}\n\n"
            f"@{challenger_username} HP: {int(challenger_hp)}\n"
            f"@{opponent_username} HP: {int(opponent_hp)}"
        ),
        parse_mode="Markdown",
    )


# =====================
# FastAPI Routes
# =====================
@app.get("/")
async def root():
    return {"status": "ok", "service": "PFPF Battle Bot"}


@app.post(WEBHOOK_PATH)
async def webhook(request: Request):
    data = await request.json()
    update = Update.de_json(data, telegram_app.bot)
    await telegram_app.process_update(update)
    return JSONResponse({"ok": True})


# =====================
# Lifecycle Events
# =====================
@app.on_event("startup")
async def on_startup():
    global telegram_app
    telegram_app = Application.builder().token(BOT_TOKEN).build()
    telegram_app.add_handler(CommandHandler("battle", battle_command))
    telegram_app.add_handler(CommandHandler("challenge", challenge_command))
    telegram_app.add_handler(MessageHandler(filters.PHOTO, image_upload))
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
