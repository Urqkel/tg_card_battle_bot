# app.py
import os
import io
import re
import random
import logging
from typing import Optional
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from telegram import Update, InputFile
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)
from PIL import Image, ImageDraw, ImageFont
import pytesseract
import imageio

# ---------- Config ----------
BOT_TOKEN = os.getenv("BOT_TOKEN")
RENDER_EXTERNAL_URL = os.getenv("RENDER_EXTERNAL_URL")
PORT = int(os.getenv("PORT", 8000))

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN missing in environment.")
if not RENDER_EXTERNAL_URL:
    raise RuntimeError("RENDER_EXTERNAL_URL missing in environment.")

WEBHOOK_PATH = f"/webhook/{BOT_TOKEN}"
WEBHOOK_URL = f"{RENDER_EXTERNAL_URL}{WEBHOOK_PATH}"

# ---------- Logging ----------
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("pfpf-battle-bot")

# ---------- FastAPI ----------
app = FastAPI()
telegram_app: Optional[Application] = None

# ---------- In-memory state ----------
# pending_challenges: challenger_user_id -> opponent_username (string without @)
pending_challenges: dict[int, str] = {}
# uploaded_cards: user_id -> dict (includes 'username', 'path', parsed stats)
uploaded_cards: dict[int, dict] = {}

# ---------- Rarity & HP rules (per your confirmation) ----------
RARITY_BONUS = {
    "common": 0,
    "rare": 20,
    "ultrarare": 40,
    "ultra-rare": 40,
    "legendary": 60,
}

# ---------- Utilities: OCR and parsing ----------
def ocr_extract_text_from_bytes(file_bytes: bytes) -> str:
    """Run pytesseract on image bytes and return extracted text."""
    try:
        image = Image.open(io.BytesIO(file_bytes)).convert("RGB")
    except Exception as e:
        log.exception("Failed to open image for OCR: %s", e)
        raise
    try:
        text = pytesseract.image_to_string(image)
        return text
    except Exception as e:
        log.exception("pytesseract failed: %s", e)
        raise

def parse_card_stats_from_text(text: str) -> dict:
    """
    Parse Power, Defense, Rarity, Serial from OCR text.
    Fallbacks are provided when patterns aren't found.
    """
    lower = text.lower()
    # Rarity detection
    rarity = None
    for key in ["legendary", "ultra-rare", "ultra rare", "ultrarare", "rare", "common"]:
        if key in lower:
            # normalize
            if "ultra" in key:
                rarity = "Ultra-Rare"
            else:
                rarity = key.capitalize()
            break
    if not rarity:
        rarity = "Common"

    # Power
    power = None
    m = re.search(r"power[:\s]*([0-9]{1,4})", lower)
    if m:
        power = int(m.group(1))
    else:
        # fallback: find a number tagged nearby "atk" or "attack"
        m2 = re.search(r"(attack|atk)[:\s]*([0-9]{1,4})", lower)
        if m2:
            power = int(m2.group(2))
    if power is None:
        # fallback: pick first sensible number 1-999
        nums = re.findall(r"\b([0-9]{1,4})\b", lower)
        power = int(nums[0]) if nums else 50

    # Defense
    defense = None
    m = re.search(r"defen(?:se|c)e?[:\s]*([0-9]{1,4})", lower)
    if m:
        defense = int(m.group(1))
    else:
        # fallback: search for "def"
        m2 = re.search(r"\bdef[:\s]*([0-9]{1,4})\b", lower)
        if m2:
            defense = int(m2.group(1))
    if defense is None:
        # attempt to pick a second number if exists or fallback default
        nums = re.findall(r"\b([0-9]{1,4})\b", lower)
        if len(nums) >= 2:
            defense = int(nums[1])
        else:
            defense = 50

    # Serial number (prefer explicit 'serial' or '#' patterns)
    serial = None
    m = re.search(r"serial[:\s#]*([0-9]{1,4})", lower)
    if m:
        serial = int(m.group(1))
    else:
        # look for patterns like "#123" or "s/n 123"
        m2 = re.search(r"#\s*([0-9]{1,4})", text)
        if m2:
            serial = int(m2.group(1))
        else:
            m3 = re.search(r"s\/n[:\s]*([0-9]{1,4})", lower)
            if m3:
                serial = int(m3.group(1))

    if serial is None:
        # fallback: choose a number plausible for serial (if numbers exist pick smallest to favor exclusivity)
        nums = [int(n) for n in re.findall(r"\b([0-9]{1,4})\b", lower)]
        if nums:
            serial = min(nums)
        else:
            serial = 1000

    # sanitize ranges (make sure serial within 1-1999)
    serial = max(1, min(serial, 1999))

    return {
        "power": int(power),
        "defense": int(defense),
        "rarity": rarity,
        "serial": int(serial),
    }

async def extract_card_stats_from_bytes(file_bytes: bytes, username: str, save_path: str) -> dict:
    """Run OCR and parse stats; save image to save_path; return card dict."""
    # Save file bytes
    try:
        with open(save_path, "wb") as f:
            f.write(file_bytes)
    except Exception:
        log.exception("Failed to write uploaded card to disk at %s", save_path)

    text = ocr_extract_text_from_bytes(file_bytes)
    parsed = parse_card_stats_from_text(text)
    card = {
        "username": username,
        "user_id": None,  # will be assigned by caller
        "path": save_path,
        "power": parsed["power"],
        "defense": parsed["defense"],
        "rarity": parsed["rarity"],
        "serial": parsed["serial"],
    }
    log.info("Parsed card for @%s: %s", username, parsed)
    return card

# ---------- HP calculation ----------
def calculate_card_hp(card: dict) -> int:
    """
    HP = base + rarity_bonus + serial_bonus
    base = power + defense
    rarity bonuses per spec: Common 0, Rare 20, Ultra-Rare 40, Legendary 60
    serial bonus = (2000 - serial) / 50
    """
    base = card.get("power", 50) + card.get("defense", 50)
    rarity_key = card.get("rarity", "Common").lower()
    rarity_bonus = RARITY_BONUS.get(rarity_key, 0)
    serial = int(card.get("serial", 1000))
    serial_bonus = (2000 - serial) / 50.0
    hp = int(base + rarity_bonus + serial_bonus)
    return max(1, hp)

# ---------- Battle simulation ----------
def simulate_battle(hp1: int, hp2: int, atk1: int, atk2: int):
    """Simulate simple turn-based battle. Return remaining HPs (hp1, hp2)."""
    # attacker damage per turn proportional to power (some randomness)
    while hp1 > 0 and hp2 > 0:
        dmg1 = max(1, int(atk1 * random.uniform(0.08, 0.18)))
        dmg2 = max(1, int(atk2 * random.uniform(0.08, 0.18)))
        hp2 -= dmg1
        if hp2 <= 0:
            break
        hp1 -= dmg2
    return max(0, hp1), max(0, hp2)

# ---------- GIF generation ----------
def generate_battle_gif_bytes(card1: dict, card2: dict, hp1_start: int, hp2_start: int, hp1_end: int, hp2_end: int) -> io.BytesIO:
    """
    Create a GIF showing both card images side-by-side with HP bars decreasing.
    Returns BytesIO containing the GIF.
    """
    frames = []
    # load images; if loading fails create placeholder
    try:
        img1 = Image.open(card1["path"]).convert("RGBA")
    except Exception:
        img1 = Image.new("RGBA", (240, 320), (120, 120, 200))
    try:
        img2 = Image.open(card2["path"]).convert("RGBA")
    except Exception:
        img2 = Image.new("RGBA", (240, 320), (200, 120, 120))

    # resize cards to consistent size
    w_card, h_card = 240, 320
    img1 = img1.resize((w_card, h_card))
    img2 = img2.resize((w_card, h_card))

    canvas_w = 600
    canvas_h = 400
    font = None
    try:
        font = ImageFont.truetype("arial.ttf", 18)
    except Exception:
        font = ImageFont.load_default()

    # make animation frames: interpolate HP from start to end
    steps = 12
    for step in range(steps):
        frame = Image.new("RGB", (canvas_w, canvas_h), (18, 18, 24))
        draw = ImageDraw.Draw(frame)

        # Positions
        x1 = 30
        x2 = canvas_w - w_card - 30
        y = 30

        frame.paste(img1, (x1, y), img1)
        frame.paste(img2, (x2, y), img2)

        # Interpolate HP values
        t = step / (steps - 1)
        cur_hp1 = int(hp1_start + (hp1_end - hp1_start) * t)
        cur_hp2 = int(hp2_start + (hp2_end - hp2_start) * t)

        # Draw HP bars under each card
        bar_w = w_card
        bar_h = 16
        # card1
        draw.rectangle((x1, y + h_card + 10, x1 + bar_w, y + h_card + 10 + bar_h), fill=(70,70,70))
        if hp1_start > 0:
            fill_w1 = int(bar_w * max(0, cur_hp1) / max(1, hp1_start))
        else:
            fill_w1 = 0
        draw.rectangle((x1, y + h_card + 10, x1 + fill_w1, y + h_card + 10 + bar_h), fill=(200,50,50))
        draw.text((x1, y + h_card + 30), f"@{card1['username']} HP: {cur_hp1}", font=font, fill=(230,230,230))

        # card2
        draw.rectangle((x2, y + h_card + 10, x2 + bar_w, y + h_card + 10 + bar_h), fill=(70,70,70))
        if hp2_start > 0:
            fill_w2 = int(bar_w * max(0, cur_hp2) / max(1, hp2_start))
        else:
            fill_w2 = 0
        draw.rectangle((x2, y + h_card + 10, x2 + fill_w2, y + h_card + 10 + bar_h), fill=(50,150,200))
        draw.text((x2, y + h_card + 30), f"@{card2['username']} HP: {cur_hp2}", font=font, fill=(230,230,230))

        # small center text showing action
        if step % 3 == 0:
            draw.text((canvas_w//2 - 60, 10), "⚔️ Battle!", font=font, fill=(255,215,0))

        frames.append(frame)

    # final winner frame highlight
    final = frames[-1].copy()
    draw = ImageDraw.Draw(final)
    winner = card1["username"] if hp1_end > hp2_end else card2["username"]
    draw.text((canvas_w//2 - 110, canvas_h - 40), f"🏆 Winner: @{winner}", font=font, fill=(255,215,0))
    frames.append(final)

    # write to BytesIO
    gif_bytes = io.BytesIO()
    imageio.mimsave(gif_bytes, frames, format="GIF", duration=0.12)
    gif_bytes.seek(0)
    return gif_bytes

# ---------- Telegram handlers ----------
async def cmd_battle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Wake/help command: explain how to start a battle."""
    await update.message.reply_text(
        "⚔️ PFP Battle System\n"
        "Start a match with: /challenge @username\n"
        "Then both players upload their card images (photo or document) in this chat."
    )

async def cmd_challenge(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start a challenge: /challenge @username"""
    if not context.args or not context.args[0].startswith("@"):
        await update.message.reply_text("Usage: /challenge @username")
        return

    challenger = update.effective_user
    opponent_username = context.args[0].lstrip("@").strip()
    pending_challenges[challenger.id] = opponent_username
    log.info("Challenge: @%s -> @%s", challenger.username, opponent_username)

    await update.message.reply_text(
        f"⚔️ @{challenger.username} has challenged @{opponent_username}!\n"
        f"Both players: please upload your card image (photo or document) in this chat. Uploads can be in any order."
    )

async def handler_card_upload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle uploaded card (photo or document)."""
    user = update.effective_user
    username = user.username or f"user{user.id}"

    # get file bytes from photo or document
    file_obj = None
    if update.message.photo:
        file_obj = await update.message.photo[-1].get_file()
    elif update.message.document:
        file_obj = await update.message.document.get_file()
    else:
        await update.message.reply_text("Please upload an image (photo or document).")
        return

    file_bytes = await file_obj.download_as_bytearray()

    # save file locally
    os.makedirs("cards", exist_ok=True)
    save_path = f"cards/{username}.png"
    try:
        with open(save_path, "wb") as f:
            f.write(file_bytes)
    except Exception:
        log.exception("Failed to save uploaded card to disk.")

    # run OCR and extract stats
    try:
        card = await extract_card_stats_from_bytes(file_bytes, username, save_path)
    except Exception as e:
        log.exception("OCR extraction failed for @%s: %s", username, e)
        # fallback defaults
        card = {
            "username": username,
            "user_id": user.id,
            "path": save_path,
            "power": 50,
            "defense": 50,
            "rarity": "Common",
            "serial": 1000,
        }

    card["user_id"] = user.id
    uploaded_cards[user.id] = card

    await update.message.reply_text(f"✅ Card received for @{username}")

    # find whether this upload belongs to any pending challenge
    # Cases:
    # - uploader is challenger (they started the challenge) -> we need opponent's upload
    # - uploader is the challenged user -> we need challenger's upload
    # We'll iterate pending_challenges and see if this applies
    triggered_pair = None  # (challenger_id, opponent_id)
    for challenger_id, opponent_username in list(pending_challenges.items()):
        if user.id == challenger_id:
            # challenger uploaded their card; check if opponent already uploaded
            # find opponent user id by matching uploaded_cards username
            opponent_id = next((uid for uid, c in uploaded_cards.items() if c["username"].lower() == opponent_username.lower()), None)
            if opponent_id:
                triggered_pair = (challenger_id, opponent_id)
                break
        else:
            # check if uploader is the challenged username
            if username.lower() == opponent_username.lower():
                # challenger_id is challenger, uploader is opponent
                challenger_id_found = challenger_id
                opponent_id = user.id
                # check if challenger already uploaded
                if challenger_id_found in uploaded_cards:
                    triggered_pair = (challenger_id_found, opponent_id)
                    break

    # if a pair is ready, run battle
    if triggered_pair:
        c_id, o_id = triggered_pair
        card1 = uploaded_cards.get(c_id)
        card2 = uploaded_cards.get(o_id)
        if card1 and card2:
            # compute starting HPs
            hp1 = calculate_card_hp(card1)
            hp2 = calculate_card_hp(card2)
            # simulate battle to get final HPs
            hp1_end, hp2_end = simulate_battle(hp1, hp2, card1["power"], card2["power"])
            # generate gif
            gif_bytes = generate_battle_gif_bytes(card1, card2, hp1, hp2, hp1_end, hp2_end)
            # send gif
            try:
                await update.message.reply_document(document=InputFile(gif_bytes, filename="battle.gif"))
            except Exception:
                # fallback: send message if send fails
                log.exception("Failed to send battle GIF.")
                await update.message.reply_text("Battle finished (GIF send failed).")

            # announce winner
            if hp1_end > hp2_end:
                winner = card1["username"]
            elif hp2_end > hp1_end:
                winner = card2["username"]
            else:
                winner = None

            caption = f"⚔️ Battle complete!\n"
            if winner:
                caption += f"🏆 Winner: @{winner}\n"
            else:
                caption += "🤝 It's a tie!\n"
            caption += f"@{card1['username']} HP: {hp1_end} vs @{card2['username']} HP: {hp2_end}"

            await update.message.reply_text(caption)

            # cleanup state
            uploaded_cards.pop(c_id, None)
            uploaded_cards.pop(o_id, None)
            pending_challenges.pop(c_id, None)

# ---------- FastAPI Webhook ----------
@app.post(WEBHOOK_PATH)
async def webhook(request: Request):
    data = await request.json()
    update = Update.de_json(data, telegram_app.bot)
    await telegram_app.process_update(update)
    return JSONResponse({"ok": True})

# ---------- Startup / shutdown ----------
@app.on_event("startup")
async def on_startup():
    global telegram_app
    log.info("Starting Telegram application and registering handlers...")
    telegram_app = Application.builder().token(BOT_TOKEN).build()

    # register handlers BEFORE initialize()
    telegram_app.add_handler(CommandHandler("battle", cmd_battle))
    telegram_app.add_handler(CommandHandler("challenge", cmd_challenge))
    telegram_app.add_handler(MessageHandler(filters.PHOTO | filters.Document.ALL, handler_card_upload))

    # initialize & set webhook
    await telegram_app.initialize()
    await telegram_app.bot.delete_webhook(drop_pending_updates=True)
    await telegram_app.bot.set_webhook(WEBHOOK_URL)
    log.info("Webhook set to %s", WEBHOOK_URL)

@app.on_event("shutdown")
async def on_shutdown():
    if telegram_app:
        await telegram_app.bot.delete_webhook()
        await telegram_app.shutdown()
        await telegram_app.stop()
    log.info("Bot stopped cleanly.")

# ---------- Health check ----------
@app.get("/health")
async def health():
    return {"status": "ok", "service": "PFPF Battle Bot"}

# ---------- End of file ----------
