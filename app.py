import os
import io
import re
import uuid
import json
import sqlite3
import logging
import random
from datetime import datetime
from typing import Optional

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles

from telegram import Update, InputFile, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
)

from PIL import Image, ImageDraw, ImageFont
import pytesseract

# ---------- Config ----------
BOT_TOKEN = os.getenv("BOT_TOKEN")
RENDER_EXTERNAL_URL = os.getenv("RENDER_EXTERNAL_URL")
PORT = int(os.getenv("PORT", 10000))

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN missing in environment.")
if not RENDER_EXTERNAL_URL:
    raise RuntimeError("RENDER_EXTERNAL_URL missing in environment.")

WEBHOOK_PATH = f"/webhook/{BOT_TOKEN}"
WEBHOOK_URL = f"{RENDER_EXTERNAL_URL}{WEBHOOK_PATH}"

# ---------- Logging ----------
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("pfp-battle-bot")

# ---------- FastAPI + Templates ----------
app = FastAPI()
templates = Jinja2Templates(directory="templates")
app.mount("/static", StaticFiles(directory="static"), name="static")

os.makedirs("battles", exist_ok=True)
os.makedirs("cards", exist_ok=True)

# ---------- SQLite storage ----------
DB_PATH = "battles.db"

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS battles (
            id TEXT PRIMARY KEY,
            timestamp TEXT,
            challenger_username TEXT,
            challenger_stats TEXT,
            opponent_username TEXT,
            opponent_stats TEXT,
            winner TEXT,
            html_path TEXT
        )
        """
    )
    conn.commit()
    conn.close()

init_db()

# ---------- In-memory state ----------
pending_challenges: dict[int, str] = {}
uploaded_cards: dict[int, dict] = {}

# ---------- OCR / Parsing ----------
RARITY_BONUS = {"common": 0, "rare": 20, "ultrarare": 40, "ultra-rare": 40, "legendary": 60}

def ocr_text_from_bytes(file_bytes: bytes) -> str:
    image = Image.open(io.BytesIO(file_bytes)).convert("RGB")
    text = pytesseract.image_to_string(image)
    return text

def parse_stats_from_text(text: str) -> dict:
    lower = text.lower()
    
    # rarity
    rarity = "Common"
    for key in ["legendary", "ultra-rare", "ultra rare", "ultrarare", "rare", "common"]:
        if key in lower:
            if "ultra" in key:
                rarity = "Ultra-Rare"
            else:
                rarity = key.capitalize()
            break
    
    nums = [int(n) for n in re.findall(r"\b([0-9]{1,4})\b", text)]
    
    # power
    power = None
    m = re.search(r"power[:\s]*([0-9]{1,4})", lower)
    if m:
        power = int(m.group(1))
    else:
        m2 = re.search(r"(attack|atk)[:\s]*([0-9]{1,4})", lower)
        if m2:
            power = int(m2.group(2))
    
    # defense
    defense = None
    m = re.search(r"defen(?:se|c)e?[:\s]*([0-9]{1,4})", lower)
    if m:
        defense = int(m.group(1))
    else:
        m2 = re.search(r"\bdef[:\s]*([0-9]{1,4})\b", lower)
        if m2:
            defense = int(m2.group(1))
    
    # serial
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
    
    if power is None:
        power = nums[0] if len(nums) >= 1 else 50
    if defense is None:
        defense = nums[1] if len(nums) >= 2 else 50
    if serial is None:
        serial = min(nums) if nums else 1000
    
    serial = max(1, min(int(serial), 1999))
    
    return {"power": int(power), "defense": int(defense), "rarity": rarity, "serial": int(serial)}

# ---------- HP calculation ----------
def calculate_hp(card: dict) -> int:
    base = card.get("power", 50) + card.get("defense", 50)
    rarity_key = card.get("rarity", "Common").lower()
    rarity_bonus = RARITY_BONUS.get(rarity_key, 0)
    serial = int(card.get("serial", 1000))
    serial_bonus = (2000 - serial) / 50.0
    hp = int(base + rarity_bonus + serial_bonus)
    return max(1, hp)

# ---------- Battle simulation ----------
def simulate_battle(hp1: int, hp2: int, power1: int, power2: int):
    """Return (final_hp1, final_hp2, battle_log)"""
    battle_log = []
    round_num = 0
    
    while hp1 > 0 and hp2 > 0:
        round_num += 1
        dmg1 = max(1, int(power1 * random.uniform(0.08, 0.16)))
        dmg2 = max(1, int(power2 * random.uniform(0.08, 0.16)))
        
        hp2 -= dmg1
        battle_log.append({
            "round": round_num,
            "attacker": 1,
            "damage": dmg1,
            "hp1": max(0, hp1),
            "hp2": max(0, hp2)
        })
        
        if hp2 <= 0:
            break
        
        hp1 -= dmg2
        battle_log.append({
            "round": round_num,
            "attacker": 2,
            "damage": dmg2,
            "hp1": max(0, hp1),
            "hp2": max(0, hp2)
        })
    
    return max(0, hp1), max(0, hp2), battle_log

# ---------- IMPROVED: Battle HTML with actual visualization ----------
def save_battle_html(battle_id: str, battle_context: dict):
    """
    Generate an animated battle replay HTML page using the battle context data.
    """
    os.makedirs("battles", exist_ok=True)
    
    card1_name = battle_context["card1_name"]
    card2_name = battle_context["card2_name"]
    card1_stats = battle_context["card1_stats"]
    card2_stats = battle_context["card2_stats"]
    hp1_start = battle_context["hp1_start"]
    hp2_start = battle_context["hp2_start"]
    hp1_end = battle_context["hp1_end"]
    hp2_end = battle_context["hp2_end"]
    winner_name = battle_context["winner_name"]
    battle_log = battle_context.get("battle_log", [])
    
    # Generate battle log HTML
    battle_log_html = ""
    for entry in battle_log[:20]:  # Show first 20 rounds
        attacker = card1_name if entry["attacker"] == 1 else card2_name
        battle_log_html += f"""
            <div class="log-entry">
                Round {entry["round"]}: @{attacker} deals {entry["damage"]} damage! 
                (HP: {entry["hp1"]} vs {entry["hp2"]})
            </div>
        """
    
    battle_html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Battle {battle_id}</title>
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <style>
            * {{
                margin: 0;
                padding: 0;
                box-sizing: border-box;
            }}
            
            body {{ 
                background: linear-gradient(135deg, #0a0a0a 0%, #1a0a2e 100%);
                color: white; 
                font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
                padding: 20px;
                min-height: 100vh;
            }}
            
            .container {{
                max-width: 800px;
                margin: 0 auto;
            }}
            
            h1 {{
                text-align: center;
                margin-bottom: 30px;
                font-size: 2em;
                background: linear-gradient(45deg, #ff6b6b, #ffd93d);
                -webkit-background-clip: text;
                -webkit-text-fill-color: transparent;
                text-shadow: 0 0 20px rgba(255, 107, 107, 0.5);
            }}
            
            .battle-arena {{
                background: rgba(255, 255, 255, 0.05);
                border-radius: 20px;
                padding: 30px;
                margin-bottom: 30px;
                box-shadow: 0 8px 32px rgba(0, 0, 0, 0.3);
                backdrop-filter: blur(10px);
            }}
            
            .fighters {{
                display: flex;
                justify-content: space-between;
                align-items: center;
                margin-bottom: 30px;
                gap: 20px;
            }}
            
            .fighter {{
                flex: 1;
                text-align: center;
            }}
            
            .fighter-name {{
                font-size: 1.5em;
                font-weight: bold;
                margin-bottom: 10px;
                color: #ffd93d;
            }}
            
            .stats {{
                background: rgba(0, 0, 0, 0.3);
                padding: 15px;
                border-radius: 10px;
                margin-top: 10px;
            }}
            
            .stat-row {{
                display: flex;
                justify-content: space-between;
                margin: 5px 0;
                font-size: 0.9em;
            }}
            
            .vs {{
                font-size: 3em;
                font-weight: bold;
                color: #ff6b6b;
                text-shadow: 0 0 20px rgba(255, 107, 107, 0.8);
            }}
            
            .hp-bars {{
                margin: 30px 0;
            }}
            
            .hp-bar-container {{
                margin: 15px 0;
            }}
            
            .hp-label {{
                display: flex;
                justify-content: space-between;
                margin-bottom: 5px;
                font-size: 0.9em;
            }}
            
            .hp-bar-bg {{
                width: 100%;
                height: 30px;
                background: rgba(0, 0, 0, 0.5);
                border-radius: 15px;
                overflow: hidden;
                box-shadow: inset 0 2px 4px rgba(0, 0, 0, 0.3);
            }}
            
            .hp-bar {{
                height: 100%;
                background: linear-gradient(90deg, #4CAF50, #8BC34A);
                border-radius: 15px;
                transition: width 2s ease-out;
                box-shadow: 0 0 10px rgba(76, 175, 80, 0.5);
            }}
            
            .winner-announcement {{
                text-align: center;
                padding: 20px;
                margin: 30px 0;
                background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                border-radius: 15px;
                font-size: 1.5em;
                font-weight: bold;
                box-shadow: 0 4px 15px rgba(102, 126, 234, 0.4);
            }}
            
            .battle-log {{
                background: rgba(0, 0, 0, 0.3);
                border-radius: 15px;
                padding: 20px;
                max-height: 300px;
                overflow-y: auto;
            }}
            
            .battle-log h3 {{
                margin-bottom: 15px;
                color: #ffd93d;
            }}
            
            .log-entry {{
                padding: 8px;
                margin: 5px 0;
                background: rgba(255, 255, 255, 0.05);
                border-radius: 5px;
                font-size: 0.9em;
                border-left: 3px solid #ff6b6b;
            }}
            
            @media (max-width: 600px) {{
                .fighters {{
                    flex-direction: column;
                }}
                
                .vs {{
                    transform: rotate(90deg);
                    margin: 20px 0;
                }}
                
                h1 {{
                    font-size: 1.5em;
                }}
            }}
        </style>
    </head>
    <body>
        <div class="container">
            <h1>⚔️ Battle Replay ⚔️</h1>
            
            <div class="battle-arena">
                <div class="fighters">
                    <div class="fighter">
                        <div class="fighter-name">@{card1_name}</div>
                        <div class="stats">
                            <div class="stat-row">
                                <span>⚡ Power:</span>
                                <span>{card1_stats['power']}</span>
                            </div>
                            <div class="stat-row">
                                <span>🛡️ Defense:</span>
                                <span>{card1_stats['defense']}</span>
                            </div>
                            <div class="stat-row">
                                <span>✨ Rarity:</span>
                                <span>{card1_stats['rarity']}</span>
                            </div>
                            <div class="stat-row">
                                <span>🎫 Serial:</span>
                                <span>#{card1_stats['serial']}</span>
                            </div>
                        </div>
                    </div>
                    
                    <div class="vs">VS</div>
                    
                    <div class="fighter">
                        <div class="fighter-name">@{card2_name}</div>
                        <div class="stats">
                            <div class="stat-row">
                                <span>⚡ Power:</span>
                                <span>{card2_stats['power']}</span>
                            </div>
                            <div class="stat-row">
                                <span>🛡️ Defense:</span>
                                <span>{card2_stats['defense']}</span>
                            </div>
                            <div class="stat-row">
                                <span>✨ Rarity:</span>
                                <span>{card2_stats['rarity']}</span>
                            </div>
                            <div class="stat-row">
                                <span>🎫 Serial:</span>
                                <span>#{card2_stats['serial']}</span>
                            </div>
                        </div>
                    </div>
                </div>
                
                <div class="hp-bars">
                    <div class="hp-bar-container">
                        <div class="hp-label">
                            <span>@{card1_name}</span>
                            <span id="hp1-value">{hp1_start} HP</span>
                        </div>
                        <div class="hp-bar-bg">
                            <div class="hp-bar" id="hp1-bar" style="width: 100%"></div>
                        </div>
                    </div>
                    
                    <div class="hp-bar-container">
                        <div class="hp-label">
                            <span>@{card2_name}</span>
                            <span id="hp2-value">{hp2_start} HP</span>
                        </div>
                        <div class="hp-bar-bg">
                            <div class="hp-bar" id="hp2-bar" style="width: 100%"></div>
                        </div>
                    </div>
                </div>
                
                <div class="winner-announcement">
                    {"🏆 Winner: @" + winner_name if winner_name != "Tie" else "🤝 It's a Tie!"}
                </div>
            </div>
            
            <div class="battle-log">
                <h3>📜 Battle Log</h3>
                {battle_log_html if battle_log_html else "<p>No battle log available</p>"}
            </div>
        </div>
        
        <script>
            // Animate HP bars
            setTimeout(() => {{
                const hp1Percent = ({hp1_end} / {hp1_start}) * 100;
                const hp2Percent = ({hp2_end} / {hp2_start}) * 100;
                
                document.getElementById('hp1-bar').style.width = hp1Percent + '%';
                document.getElementById('hp2-bar').style.width = hp2Percent + '%';
                
                // Animate HP values
                animateValue('hp1-value', {hp1_start}, {hp1_end}, 2000);
                animateValue('hp2-value', {hp2_start}, {hp2_end}, 2000);
            }}, 500);
            
            function animateValue(id, start, end, duration) {{
                const element = document.getElementById(id);
                const range = end - start;
                const increment = range / (duration / 16);
                let current = start;
                
                const timer = setInterval(() => {{
                    current += increment;
                    if ((increment > 0 && current >= end) || (increment < 0 && current <= end)) {{
                        current = end;
                        clearInterval(timer);
                    }}
                    element.textContent = Math.round(current) + ' HP';
                }}, 16);
            }}
        </script>
    </body>
    </html>
    """
    
    file_path = f"battles/{battle_id}.html"
    with open(file_path, "w", encoding="utf-8") as f:
        f.write(battle_html)
    
    return file_path

def persist_battle_record(battle_id: str, challenger_username: str, challenger_stats: dict,
                          opponent_username: str, opponent_stats: dict, winner: Optional[str], html_path: str):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        "INSERT INTO battles (id, timestamp, challenger_username, challenger_stats, opponent_username, opponent_stats, winner, html_path) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            battle_id,
            datetime.utcnow().isoformat(),
            challenger_username,
            json.dumps(challenger_stats),
            opponent_username,
            json.dumps(opponent_stats),
            winner or "",
            html_path,
        ),
    )
    conn.commit()
    conn.close()

# ---------- Telegram handlers ----------
async def cmd_battle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "⚔️ PFP Battle Bot\n\n"
        "Commands:\n"
        "/challenge @username - Challenge someone to battle\n"
        "/mystats - View your uploaded card stats\n"
        "/leaderboard - View top battlers\n\n"
        "After challenging, both players upload their PFP battle card (photo or file)."
    )

async def cmd_challenge(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args or not context.args[0].startswith("@"):
        await update.message.reply_text("Usage: /challenge @username")
        return
    
    challenger = update.effective_user
    opponent_username = context.args[0].lstrip("@").strip()
    
    # Check if challenger is challenging themselves
    if challenger.username and challenger.username.lower() == opponent_username.lower():
        await update.message.reply_text("❌ You can't challenge yourself!")
        return
    
    pending_challenges[challenger.id] = opponent_username
    log.info("Challenge: @%s -> @%s", challenger.username, opponent_username)
    
    await update.message.reply_text(
        f"⚔️ @{challenger.username} has challenged @{opponent_username}!\n\n"
        "📤 Both players: upload your battle card image in this chat.\n"
        "Uploads can be in any order."
    )

async def cmd_mystats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show user's currently uploaded card stats"""
    user_id = update.effective_user.id
    
    if user_id not in uploaded_cards:
        await update.message.reply_text(
            "❌ You haven't uploaded a card yet.\n"
            "Upload your PFP battle card to see your stats!"
        )
        return
    
    card = uploaded_cards[user_id]
    hp = calculate_hp(card)
    
    stats_text = (
        f"📊 Your Card Stats:\n\n"
        f"⚡ Power: {card['power']}\n"
        f"🛡️ Defense: {card['defense']}\n"
        f"✨ Rarity: {card['rarity']}\n"
        f"🎫 Serial: #{card['serial']}\n"
        f"❤️ HP: {hp}\n\n"
        f"Ready to battle!"
    )
    
    await update.message.reply_text(stats_text)

async def handler_card_upload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    username = (user.username or f"user{user.id}").lower()
    user_id = user.id
    
    # Get file bytes
    file_obj = None
    if update.message.photo:
        file_obj = await update.message.photo[-1].get_file()
    elif update.message.document:
        file_obj = await update.message.document.get_file()
    else:
        await update.message.reply_text("Please upload an image (photo or file).")
        return
    
    file_bytes = await file_obj.download_as_bytearray()
    
    # Save image
    os.makedirs("cards", exist_ok=True)
    save_path = f"cards/{username}.png"
    try:
        with open(save_path, "wb") as f:
            f.write(file_bytes)
    except Exception:
        log.exception("Failed saving card to %s", save_path)
    
    # OCR + parse
    try:
        ocr_text = ocr_text_from_bytes(file_bytes)
        parsed = parse_stats_from_text(ocr_text)
    except Exception as e:
        log.exception("OCR failure for @%s: %s", username, e)
        parsed = {"power": 50, "defense": 50, "rarity": "Common", "serial": 1000}
    
    card = {
        "username": username,
        "user_id": user_id,
        "path": save_path,
        "power": int(parsed["power"]),
        "defense": int(parsed["defense"]),
        "rarity": parsed["rarity"],
        "serial": int(parsed["serial"]),
    }
    
    uploaded_cards[user_id] = card
    hp = calculate_hp(card)
    
    await update.message.reply_text(
        f"✅ @{username}'s card received!\n"
        f"⚡ Power: {card['power']} | 🛡️ Defense: {card['defense']}\n"
        f"✨ {card['rarity']} | 🎫 Serial #{card['serial']}\n"
        f"❤️ HP: {hp}"
    )
    
    # Check for battle trigger
    triggered_pair = None
    
    # Check if uploader is challenger
    if user_id in pending_challenges:
        opponent_username = pending_challenges[user_id].lower()
        opponent_id = next(
            (uid for uid, c in uploaded_cards.items() if c["username"].lower() == opponent_username),
            None,
        )
        if opponent_id:
            triggered_pair = (user_id, opponent_id)
    
    # Check if uploader is opponent
    if not triggered_pair:
        for challenger_id, opponent_username in pending_challenges.items():
            if username == opponent_username.lower():
                if challenger_id in uploaded_cards:
                    triggered_pair = (challenger_id, user_id)
                    break
    
    # Run battle if both ready
    if triggered_pair:
        challenger_id, opponent_id = triggered_pair
        card1 = uploaded_cards.get(challenger_id)
        card2 = uploaded_cards.get(opponent_id)
        
        if not card1 or not card2:
            log.warning("Missing card data for battle")
            return
        
        # Compute HPs
        hp1_start = calculate_hp(card1)
        hp2_start = calculate_hp(card2)
        
        # Simulate battle with log
        hp1_end, hp2_end, battle_log = simulate_battle(
            hp1_start, hp2_start, card1["power"], card2["power"]
        )
        
        # Determine winner
        if hp1_end > hp2_end:
            winner = card1["username"]
        elif hp2_end > hp1_end:
            winner = card2["username"]
        else:
            winner = None
        
        # Create battle replay
        battle_id = str(uuid.uuid4())
        battle_context = {
            "card1_name": card1["username"],
            "card2_name": card2["username"],
            "card1_stats": {
                "power": card1["power"],
                "defense": card1["defense"],
                "rarity": card1["rarity"],
                "serial": card1["serial"]
            },
            "card2_stats": {
                "power": card2["power"],
                "defense": card2["defense"],
                "rarity": card2["rarity"],
                "serial": card2["serial"]
            },
            "hp1_start": hp1_start,
            "hp2_start": hp2_start,
            "hp1_end": hp1_end,
            "hp2_end": hp2_end,
            "winner_name": winner or "Tie",
            "battle_id": battle_id,
            "battle_log": battle_log
        }
        
        html_path = save_battle_html(battle_id, battle_context)
        
        # Save to database
        persist_battle_record(
            battle_id,
            card1["username"],
            battle_context["card1_stats"],
            card2["username"],
            battle_context["card2_stats"],
            winner,
            html_path,
        )
        
        # Send results
        replay_url = f"{RENDER_EXTERNAL_URL}/battle/{battle_id}"
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🎬 View Battle Replay", url=replay_url)]
        ])
        
        summary_text = f"⚔️ Battle Complete!\n\n"
        if winner:
            summary_text += f"🏆 Winner: @{winner}!\n\n"
        else:
            summary_text += "🤝 It's a Tie!\n\n"
        summary_text += (
            f"@{card1['username']}: {hp1_end}/{hp1_start} HP\n"
            f"@{card2['username']}: {hp2_end}/{hp2_start} HP\n\n"
            f"Battle lasted {len(battle_log)} rounds!"
        )
        
        await update.message.reply_text(summary_text, reply_markup=keyboard)
        
        # Cleanup
        uploaded_cards.pop(challenger_id, None)
        uploaded_cards.pop(opponent_id, None)
        pending_challenges.pop(challenger_id, None)
    
    else:
        # FIXED: Improved waiting message logic
        waiting_for = None
        if user_id in pending_challenges:
            # Uploader is challenger waiting for opponent
            waiting_for = f"@{pending_challenges[user_id]}"
        else:
            # Check if uploader is the opponent in someone's challenge
            for challenger_id, opponent_username in pending_challenges.items():
                if username == opponent_username.lower():
                    # Found the challenge, now get challenger's username
                    challenger_card = uploaded_cards.get(challenger_id)
                    if challenger_card:
                        waiting_for = f"@{challenger_card['username']}"
                    else:
                        waiting_for = "your challenger"
                    break
        
        if waiting_for:
            await update.message.reply_text(
                f"⏳ Card ready! Waiting for {waiting_for} to upload theirs..."
            )
        else:
            await update.message.reply_text(
                "✅ Card uploaded! Use /challenge @username to start a battle."
            )

# ---------- FastAPI routes ----------
@app.get("/")
async def root():
    return {"status": "ok", "service": "PFP Battle Bot"}

@app.get("/battle/{battle_id}", response_class=HTMLResponse)
async def battle_page(request: Request, battle_id: str):
    battle_file = f"battles/{battle_id}.html"
    if os.path.exists(battle_file):
        return FileResponse(battle_file, media_type="text/html")
    
    # Battle not found
    return HTMLResponse(content="""
        <!DOCTYPE html>
        <html>
        <head>
            <title>Battle Not Found</title>
            <style>
                body {
                    background-color: #0d0d0d;
                    color: white;
                    text-align: center;
                    font-family: Arial, sans-serif;
                    padding: 50px;
                }
                h1 { color: #ff6b6b; }
            </style>
        </head>
        <body>
            <h1>⚔️ Battle Not Found</h1>
            <p>This battle doesn't exist or hasn't been completed yet.</p>
        </body>
        </html>
    """, status_code=404)

@app.post(WEBHOOK_PATH)
async def webhook(request: Request):
    data = await request.json()
    update = Update.de_json(data, telegram_app.bot)
    await telegram_app.process_update(update)
    return JSONResponse({"ok": True})

# ---------- Startup / Shutdown ----------
telegram_app: Optional[Application] = None

@app.on_event("startup")
async def on_startup():
    global telegram_app
    log.info("Starting Telegram Application...")
    
    telegram_app = Application.builder().token(BOT_TOKEN).build()
    
    telegram_app.add_handler(CommandHandler("battle", cmd_battle))
    telegram_app.add_handler(CommandHandler("start", cmd_battle))
    telegram_app.add_handler(CommandHandler("challenge", cmd_challenge))
    telegram_app.add_handler(CommandHandler("mystats", cmd_mystats))
    telegram_app.add_handler(MessageHandler(filters.PHOTO | filters.Document.ALL, handler_card_upload))
    
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

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=PORT, reload=False)
