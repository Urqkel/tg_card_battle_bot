import os
import io
import re
import uuid
import json
import sqlite3
import logging
import random
import base64
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

from PIL import Image
import anthropic

# ---------- Config ----------
BOT_TOKEN = os.getenv("BOT_TOKEN")
RENDER_EXTERNAL_URL = os.getenv("RENDER_EXTERNAL_URL")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")  # Add this to Render env vars
PORT = int(os.getenv("PORT", 10000))

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN missing in environment.")
if not RENDER_EXTERNAL_URL:
    raise RuntimeError("RENDER_EXTERNAL_URL missing in environment.")
if not ANTHROPIC_API_KEY:
    raise RuntimeError("ANTHROPIC_API_KEY missing in environment. Get one at https://console.anthropic.com")

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

# ---------- Claude Vision OCR ----------
RARITY_BONUS = {"common": 0, "rare": 20, "ultrarare": 40, "ultra-rare": 40, "legendary": 60}

# Initialize Anthropic client
claude_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

def analyze_card_with_claude(file_bytes: bytes) -> dict:
    """
    Use Claude's vision API to extract card stats.
    Much more accurate than traditional OCR for styled cards!
    """
    try:
        # Convert to base64
        base64_image = base64.standard_b64encode(file_bytes).decode("utf-8")
        
        # Determine image type
        image = Image.open(io.BytesIO(file_bytes))
        image_format = image.format.lower() if image.format else "jpeg"
        media_type = f"image/{image_format}" if image_format in ["jpeg", "png", "gif", "webp"] else "image/jpeg"
        
        # Ask Claude to extract the stats
        message = claude_client.messages.create(
            model="claude-3-5-sonnet-20241022",
            max_tokens=1024,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": media_type,
                                "data": base64_image,
                            },
                        },
                        {
                            "type": "text",
                            "text": """This is a PFP battle card. Please extract the following stats from the card:

1. Power (attack stat, usually a number between 1-200)
2. Defense (defense stat, usually a number between 1-200)
3. Rarity (Common, Rare, Ultra-Rare, or Legendary)
4. Serial Number (usually marked as Serial, S/N, or #number, typically 1-1999)

Return ONLY a JSON object in this exact format with no other text:
{
  "power": <number>,
  "defense": <number>,
  "rarity": "<Common|Rare|Ultra-Rare|Legendary>",
  "serial": <number>
}

If you cannot find a stat clearly, use these defaults:
- Power: 50
- Defense: 50
- Rarity: "Common"
- Serial: 1000"""
                        }
                    ],
                }
            ],
        )
        
        # Extract response
        response_text = message.content[0].text.strip()
        
        # Parse JSON (handle potential markdown code blocks)
        json_text = response_text
        if "```json" in response_text:
            json_text = response_text.split("```json")[1].split("```")[0].strip()
        elif "```" in response_text:
            json_text = response_text.split("```")[1].split("```")[0].strip()
        
        stats = json.loads(json_text)
        
        # Validate and normalize
        power = max(1, min(int(stats.get("power", 50)), 200))
        defense = max(1, min(int(stats.get("defense", 50)), 200))
        rarity = stats.get("rarity", "Common")
        serial = max(1, min(int(stats.get("serial", 1000)), 1999))
        
        log.info(f"Claude extracted stats: power={power}, defense={defense}, rarity={rarity}, serial={serial}")
        
        return {
            "power": power,
            "defense": defense,
            "rarity": rarity,
            "serial": serial
        }
        
    except Exception as e:
        log.exception(f"Claude Vision API error: {e}")
        # Fallback to defaults
        return {
            "power": 50,
            "defense": 50,
            "rarity": "Common",
            "serial": 1000
        }

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

# ---------- Battle HTML generation ----------
def save_battle_html(battle_id: str, battle_context: dict):
    """Generate an animated battle replay HTML page."""
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
    for entry in battle_log[:20]:
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
            setTimeout(() => {{
                const hp1Percent = ({hp1_end} / {hp1_start}) * 100;
                const hp2Percent = ({hp2_end} / {hp2_start}) * 100;
                
                document.getElementById('hp1-bar').style.width = hp1Percent + '%';
                document.getElementById('hp2-bar').style.width = hp2Percent + '%';
                
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
        "/mystats - View your uploaded card stats\n\n"
        "After challenging, both players upload their PFP battle card (photo or file).\n"
        "🤖 Powered by Claude AI for accurate card reading!"
    )

async def cmd_challenge(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args or not context.args[0].st
