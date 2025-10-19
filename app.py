# app.py
import os
import io
import re
import random
import logging
import sqlite3
import asyncio
from datetime import datetime, timedelta
from typing import Optional, Tuple
from fastapi import FastAPI, Request, HTTPException
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
import time

# ---------- Config ----------
BOT_TOKEN = os.getenv("BOT_TOKEN")
RENDER_EXTERNAL_URL = os.getenv("RENDER_EXTERNAL_URL")
PORT = int(os.getenv("PORT", 8000))
TESSERACT_CMD = os.getenv("TESSERACT_CMD", "/usr/bin/tesseract")  # Default for Render's Docker image

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN missing in environment.")
if not RENDER_EXTERNAL_URL:
    raise RuntimeError("RENDER_EXTERNAL_URL missing in environment.")

# Set Tesseract path
pytesseract.pytesseract.tesseract_cmd = TESSERACT_CMD

WEBHOOK_PATH = f"/webhook/{BOT_TOKEN}"
WEBHOOK_URL = f"{RENDER_EXTERNAL_URL}{WEBHOOK_PATH}"
DB_PATH = "battle_bot.db"
CARDS_DIR = "cards"
CHALLENGE_TIMEOUT = timedelta(minutes=10)

# ---------- Logging ----------
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("pfpf-battle-bot")

# ---------- FastAPI ----------
app = FastAPI()
telegram_app: Optional[Application] = None
db_lock = asyncio.Lock()

# ---------- Rate Limiting (Simple In-Memory) ----------
request_counts: dict[int, list[float]] = {}  # user_id -> [timestamps]
RATE_LIMIT = 10  # requests per minute

# ---------- Rarity & HP Rules ----------
RARITY_BONUS = {
    "common": 0,
    "rare": 20,
    "ultrarare": 40,
    "ultra-rare": 40,
    "legendary": 60,
}

# ---------- Database Setup ----------
def init_db():
    os.makedirs(CARDS_DIR, exist_ok=True)
    with sqlite3.connect(DB_PATH) as conn:
        c = conn.cursor()
        c.execute("""
            CREATE TABLE IF NOT EXISTS challenges (
                challenger_id INTEGER,
                opponent_username TEXT,
                chat_id INTEGER,
                timestamp TEXT,
                PRIMARY KEY (challenger_id, chat_id)
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS cards (
                user_id INTEGER,
                username TEXT,
                chat_id INTEGER,
                file_path TEXT,
                power INTEGER,
                defense INTEGER,
                rarity TEXT,
                serial INTEGER,
                confirmed INTEGER DEFAULT 0,
                PRIMARY KEY (user_id, chat_id)
            )
        """)
        conn.commit()

def sanitize_filename(username: str) -> str:
    """Sanitize username for safe file paths."""
    return re.sub(r'[^a-zA-Z0-9_-]', '_', username)

# ---------- Utilities: OCR and Parsing ----------
def preprocess_image(image: Image.Image) -> Image.Image:
    """Preprocess image for better OCR accuracy."""
    image = image.convert("RGB")
    image = image.resize((800, 800), Image.Resampling.LANCZOS)
    from PIL import ImageEnhance
    enhancer = ImageEnhance.Contrast(image)
    return enhancer.enhance(1.5)

def ocr_extract_text_from_bytes(file_bytes: bytes, retries: int = 2) -> str:
    """Run pytesseract on image bytes with retries."""
    for attempt in range(retries):
        try:
            image = Image.open(io.BytesIO(file_bytes))
            image = preprocess_image(image)
            text = pytesseract.image_to_string(image)
            return text
        except Exception as e:
            log.warning("OCR attempt %d failed: %s", attempt + 1, e)
            if attempt == retries - 1:
                log.error("OCR failed after %d retries: %s", retries, e)
                raise
            time.sleep(1)  # Wait before retrying

def parse_card_stats_from_text(text: str) -> dict:
    """Parse Power, Defense, Rarity, Serial from OCR text with validation."""
    lower = text.lower()
    rarity = None
    for key in ["legendary", "ultra-rare", "ultra rare", "ultrarare", "rare", "common"]:
        if key in lower:
            rarity = "Ultra-Rare" if "ultra" in key else key.capitalize()
            break
    if not rarity:
        rarity = "Common"

    power = None
    m = re.search(r"power[:\s]*([0-9]{1,3})", lower)
    if m:
        power = int(m.group(1))
    else:
        m2 = re.search(r"(attack|atk)[:\s]*([0-9]{1,3})", lower)
        if m2:
            power = int(m2.group(2))
    if power is None:
        nums = re.findall(r"\b([0-9]{1,3})\b", lower)
        power = int(nums[0]) if nums else 50
    power = max(1, min(power, 999))

    defense = None
    m = re.search(r"defen(?:se|c)e?[:\s]*([0-9]{1,3})", lower)
    if m:
        defense = int(m.group(1))
    else:
        m2 = re.search(r"\bdef[:\s]*([0-9]{1,3})\b", lower)
        if m2:
            defense = int(m2.group(1))
    if defense is None:
        nums = re.findall(r"\b([0-9]{1,3})\b", lower)
        if len(nums) >= 2:
            defense = int(nums[1])
        else:
            defense = 50
    defense = max(1, min(defense, 999))

    serial = None
    m = re.search(r"serial[:\s#]*([0-9]{1,4})", lower)
    if m:
        serial = int(m.group(1))
    else:
        m2 = re.search(r"#\s*([0-9]{1,4})", text)
        if m2:
            serial = int(m2.group(1))
        else:
            m3 = re.search(r"s\/n[:\s]*([0-9]{1,4})", lower)
            if m3:
                serial = int(m3.group(1))
    if serial is None:
        nums = [int(n) for n in re.findall(r"\b([0-9]{1,4})\b", lower)]
        serial = min(nums) if nums else 1000
    serial = max(1, min(serial, 1999))

    return {
        "power": power,
        "defense": defense,
        "rarity": rarity,
        "serial": serial,
    }

async def extract_card_stats_from_bytes(file_bytes: bytes, username: str, user_id: int, chat_id: int) -> dict:
    """Run OCR, parse stats, save image, and store in DB."""
    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    save_path = f"{CARDS_DIR}/{sanitize_filename(username)}_{user_id}_{timestamp}.png"
    try:
        with open(save_path, "wb") as f:
            f.write(file_bytes)
    except Exception as e:
        log.exception("Failed to save card at %s: %s", save_path, e)
        raise

    try:
        text = ocr_extract_text_from_bytes(file_bytes)
        parsed = parse_card_stats_from_text(text)
    except Exception as e:
        log.exception("OCR extraction failed for @%s: %s", username, e)
        parsed = {"power": 50, "defense": 50, "rarity": "Common", "serial": 1000}
        await notify_ocr_failure(user_id, chat_id, str(e))

    card = {
        "username": username,
        "user_id": user_id,
        "chat_id": chat_id,
        "file_path": save_path,
        "power": parsed["power"],
        "defense": parsed["defense"],
        "rarity": parsed["rarity"],
        "serial": parsed["serial"],
        "confirmed": 0,
    }
    async with db_lock:
        with sqlite3.connect(DB_PATH) as conn:
            c = conn.cursor()
            c.execute("""
                INSERT OR REPLACE INTO cards (user_id, username, chat_id, file_path, power, defense, rarity, serial, confirmed)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                user_id, username, chat_id, save_path,
                card["power"], card["defense"], card["rarity"], card["serial"], 0
            ))
            conn.commit()
    log.info("Parsed card for @%s: %s", username, parsed)
    return card

async def notify_ocr_failure(user_id: int, chat_id: int, error: str):
    """Notify user of OCR failure with Render-specific guidance."""
    error_msg = "⚠️ Couldn't read card stats due to an OCR error.\n"
    if "tesseract is not installed" in error.lower() or "not in your PATH" in error.lower():
        error_msg += (
            "Tesseract OCR is not installed or misconfigured in the Render environment.\n"
            "Admin: Ensure `tesseract-ocr` is installed in the Dockerfile and TESSERACT_CMD is set to /usr/bin/tesseract.\n"
        )
    else:
        error_msg += f"Error: {error}\nPlease upload a clearer image with readable text (high contrast, clear font).\n"
    error_msg += "Using defaults (Power: 50, Defense: 50, Rarity: Common, Serial: 1000).\nUse /confirm to verify or edit stats."
    await telegram_app.bot.send_message(chat_id=chat_id, text=error_msg)

# ---------- HP Calculation ----------
def calculate_card_hp(card: dict) -> int:
    """Calculate HP: base (power + defense) + rarity bonus + serial bonus."""
    base = card.get("power", 50) + card.get("defense", 50)
    rarity_key = card.get("rarity", "Common").lower()
    rarity_bonus = RARITY_BONUS.get(rarity_key, 0)
    serial = int(card.get("serial", 1000))
    serial_bonus = (2000 - serial) / 50.0
    hp = int(base + rarity_bonus + serial_bonus)
    return max(1, hp)

# ---------- Battle Simulation ----------
def simulate_battle(hp1: int, hp2: int, atk1: int, def1: int, atk2: int, def2: int) -> Tuple[int, int]:
    """Simulate turn-based battle with defense and critical hits."""
    while hp1 > 0 and hp2 > 0:
        crit1 = random.random() < 0.1
        dmg1 = max(1, int(atk1 * random.uniform(0.08, 0.18) * (2 if crit1 else 1)))
        dmg1 = max(1, dmg1 - def2 // 10)
        hp2 -= dmg1
        if hp2 <= 0:
            break
        crit2 = random.random() < 0.1
        dmg2 = max(1, int(atk2 * random.uniform(0.08, 0.18) * (2 if crit2 else 1)))
        dmg2 = max(1, dmg2 - def1 // 10)
        hp1 -= dmg2
    return max(0, hp1), max(0, hp2)

# ---------- GIF Generation ----------
def generate_battle_gif_bytes(card1: dict, card2: dict, hp1_start: int, hp2_start: int, hp1_end: int, hp2_end: int) -> io.BytesIO:
    """Create a GIF showing cards with decreasing HP bars."""
    frames = []
    try:
        img1 = Image.open(card1["file_path"]).convert("RGBA")
    except Exception:
        img1 = Image.new("RGBA", (240, 320), (120, 120, 200))
    try:
        img2 = Image.open(card2["file_path"]).convert("RGBA")
    except Exception:
        img2 = Image.new("RGBA", (240, 320), (200, 120, 120))

    w_card, h_card = 240, 320
    img1 = img1.resize((w_card, h_card), Image.Resampling.LANCZOS)
    img2 = img2.resize((w_card, h_card), Image.Resampling.LANCZOS)

    canvas_w = 600
    canvas_h = 400
    try:
        font = ImageFont.truetype("arial.ttf", 18)
    except Exception:
        log.warning("arial.ttf not found; using default font")
        font = ImageFont.load_default()

    steps = 12
    for step in range(steps):
        frame = Image.new("RGB", (canvas_w, canvas_h), (18, 18, 24))
        draw = ImageDraw.Draw(frame)

        x1, x2, y = 30, canvas_w - w_card - 30, 30
        frame.paste(img1, (x1, y), img1)
        frame.paste(img2, (x2, y), img2)

        t = step / (steps - 1)
        cur_hp1 = int(hp1_start + (hp1_end - hp1_start) * t)
        cur_hp2 = int(hp2_start + (hp2_end - hp2_start) * t)

        bar_w, bar_h = w_card, 16
        draw.rectangle((x1, y + h_card + 10, x1 + bar_w, y + h_card + 10 + bar_h), fill=(70, 70, 70))
        fill_w1 = int(bar_w * max(0, cur_hp1) / max(1, hp1_start))
        draw.rectangle((x1, y + h_card + 10, x1 + fill_w1, y + h_card + 10 + bar_h), fill=(200, 50, 50))
        draw.text((x1, y + h_card + 30), f"@{card1['username']} HP: {cur_hp1}", font=font, fill=(230, 230, 230))

        draw.rectangle((x2, y + h_card + 10, x2 + bar_w, y + h_card + 10 + bar_h), fill=(70, 70, 70))
        fill_w2 = int(bar_w * max(0, cur_hp2) / max(1, hp2_start))
        draw.rectangle((x2, y + h_card + 10, x2 + fill_w2, y + h_card + 10 + bar_h), fill=(50, 150, 200))
        draw.text((x2, y + h_card + 30), f"@{card2['username']} HP: {cur_hp2}", font=font, fill=(230, 230, 230))

        if step % 3 == 0:
            draw.text((canvas_w // 2 - 60, 10), "⚔️ Battle!", font=font, fill=(255, 215, 0))

        frames.append(frame)

    final = frames[-1].copy()
    draw = ImageDraw.Draw(final)
    winner = card1["username"] if hp1_end > hp2_end else card2["username"] if hp2_end > hp1_end else None
    text = f"🏆 Winner: @{winner}" if winner else "🤝 Tie!"
    draw.text((canvas_w // 2 - 110, canvas_h - 40), text, font=font, fill=(255, 215, 0))
    frames.append(final)

    gif_bytes = io.BytesIO()
    imageio.mimsave(gif_bytes, frames, format="GIF", duration=0.12)
    gif_bytes.seek(0)
    return gif_bytes

# ---------- Telegram Handlers ----------
async def cmd_battle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Explain how to start a battle."""
    await update.message.reply_text(
        "⚔️ PFP Battle System\n"
        "1. Start a match: /challenge @username\n"
        "2. Both players upload card images (photo or document).\n"
        "3. Confirm stats with /confirm if needed.\n"
        "4. View your card: /stats\n"
        "5. Cancel a challenge: /cancel\n"
        "Note: Ensure your card image is clear for accurate OCR."
    )

async def cmd_challenge(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start a challenge: /challenge @username"""
    if not context.args or not context.args[0].startswith("@"):
        await update.message.reply_text("Usage: /challenge @username")
        return

    challenger = update.effective_user
    chat_id = update.effective_chat.id
    opponent_username = context.args[0].lstrip("@").strip()

    async with db_lock:
        with sqlite3.connect(DB_PATH) as conn:
            c = conn.cursor()
            c.execute("INSERT OR REPLACE INTO challenges (challenger_id, opponent_username, chat_id, timestamp) VALUES (?, ?, ?, ?)",
                      (challenger.id, opponent_username, chat_id, datetime.utcnow().isoformat()))
            conn.commit()

    log.info("Challenge: @%s -> @%s in chat %s", challenger.username, opponent_username, chat_id)
    await update.message.reply_text(
        f"⚔️ @{challenger.username} has challenged @{opponent_username}!\n"
        f"Both players: upload your card image in this chat. Use /confirm to verify stats."
    )

async def cmd_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Allow user to confirm or edit card stats."""
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    async with db_lock:
        with sqlite3.connect(DB_PATH) as conn:
            c = conn.cursor()
            c.execute("SELECT username, power, defense, rarity, serial, confirmed FROM cards WHERE user_id = ? AND chat_id = ?",
                      (user_id, chat_id))
            card = c.fetchone()
            if not card:
                await update.message.reply_text("No card uploaded. Please upload an image first.")
                return
            username, power, defense, rarity, serial, confirmed = card
            if confirmed:
                await update.message.reply_text("Card stats already confirmed.")
                return

    if not context.args:
        await update.message.reply_text(
            f"Current stats for @{username}:\n"
            f"Power: {power}, Defense: {defense}, Rarity: {rarity}, Serial: {serial}\n"
            f"To edit, use: /confirm <power> <defense> <rarity> <serial>\n"
            f"E.g., /confirm 100 80 Rare 500\n"
            f"To confirm current stats, use: /confirm ok"
        )
        return

    if context.args[0].lower() == "ok":
        async with db_lock:
            with sqlite3.connect(DB_PATH) as conn:
                c = conn.cursor()
                c.execute("UPDATE cards SET confirmed = 1 WHERE user_id = ? AND chat_id = ?", (user_id, chat_id))
                conn.commit()
        await update.message.reply_text("✅ Card stats confirmed!")
        await check_battle_ready(user_id, chat_id)
        return

    if len(context.args) != 4:
        await update.message.reply_text("Usage: /confirm <power> <defense> <rarity> <serial> or /confirm ok")
        return

    try:
        power = int(context.args[0])
        defense = int(context.args[1])
        rarity = context.args[2].capitalize()
        serial = int(context.args[3])
        if not (1 <= power <= 999 and 1 <= defense <= 999):
            raise ValueError("Power and Defense must be between 1 and 999.")
        if rarity not in ["Common", "Rare", "Ultra-Rare", "Legendary"]:
            raise ValueError("Rarity must be Common, Rare, Ultra-Rare, or Legendary.")
        if not (1 <= serial <= 1999):
            raise ValueError("Serial must be between 1 and 1999.")
    except Exception as e:
        await update.message.reply_text(f"Invalid input: {e}")
        return

    async with db_lock:
        with sqlite3.connect(DB_PATH) as conn:
            c = conn.cursor()
            c.execute("UPDATE cards SET power = ?, defense = ?, rarity = ?, serial = ?, confirmed = 1 WHERE user_id = ? AND chat_id = ?",
                      (power, defense, rarity, serial, user_id, chat_id))
            conn.commit()

    await update.message.reply_text("✅ Card stats updated and confirmed!")
    await check_battle_ready(user_id, chat_id)

async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show user's uploaded card stats."""
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    async with db_lock:
        with sqlite3.connect(DB_PATH) as conn:
            c = conn.cursor()
            c.execute("SELECT username, power, defense, rarity, serial, confirmed FROM cards WHERE user_id = ? AND chat_id = ?",
                      (user_id, chat_id))
            card = c.fetchone()
            if not card:
                await update.message.reply_text("No card uploaded in this chat.")
                return
            username, power, defense, rarity, serial, confirmed = card
            status = "Confirmed" if confirmed else "Unconfirmed (use /confirm)"
            await update.message.reply_text(
                f"Card for @{username}:\n"
                f"Power: {power}\nDefense: {defense}\nRarity: {rarity}\nSerial: {serial}\nStatus: {status}"
            )

async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancel a pending challenge."""
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    async with db_lock:
        with sqlite3.connect(DB_PATH) as conn:
            c = conn.cursor()
            c.execute("DELETE FROM challenges WHERE challenger_id = ? AND chat_id = ?", (user_id, chat_id))
            c.execute("SELECT file_path FROM cards WHERE user_id = ? AND chat_id = ?", (user_id, chat_id))
            card = c.fetchone()
            if card:
                try:
                    os.remove(card[0])
                except Exception:
                    log.warning("Failed to delete card file: %s", card[0])
                c.execute("DELETE FROM cards WHERE user_id = ? AND chat_id = ?", (user_id, chat_id))
            conn.commit()

    await update.message.reply_text("✅ Challenge canceled and card removed if uploaded.")

async def handler_card_upload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle uploaded card image."""
    user = update.effective_user
    chat_id = update.effective_chat.id
    username = user.username or f"user{user.id}"

    # Rate limiting
    now = time.time()
    request_counts.setdefault(user.id, [])
    request_counts[user.id] = [t for t in request_counts[user.id] if now - t < 60]
    if len(request_counts[user.id]) >= RATE_LIMIT:
        await update.message.reply_text("⏳ Too many requests. Please wait a minute.")
        return
    request_counts[user.id].append(now)

    file_obj = None
    if update.message.photo:
        file_obj = await update.message.photo[-1].get_file()
    elif update.message.document:
        file_obj = await update.message.document.get_file()
    else:
        await update.message.reply_text("Please upload an image (photo or document).")
        return

    file_bytes = await file_obj.download_as_bytearray()
  
