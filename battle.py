import os
import random
from flask import Flask, request
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

TOKEN = os.getenv("BOT_TOKEN") or "YOUR_BOT_TOKEN_HERE"
RENDER_URL = os.getenv("RENDER_URL") or "https://tg-card-battle-bot.onrender.com"

app = Flask(__name__)
application = Application.builder().token(TOKEN).build()

# --- Storage (replace with your actual card storage from card bot) ---
players_cards = {}
challenges = {}

# --- Rarity multipliers ---
RARITY_MULTIPLIERS = {
    "Common": 1.0,
    "Rare": 1.05,
    "Ultra-Rare": 1.1,
    "Legendary": 1.2
}

# --- Battle Calculation ---
def calculate_score(card, opponent_card):
    rarity_bonus = RARITY_MULTIPLIERS.get(card["rarity"], 1.0) * card["power"]
    attack_score = card["power"] + random.randint(0, 20) + rarity_bonus
    defense_score = card["defense"] + random.randint(0, 10)

    # Special ability effects
    if card["special"] == "Double Strike":
        attack_score += 10
    if card["special"] == "Shield Break":
        defense_score = defense_score / 2  # ignores half of defense
    if card["special"] == "Critical Hit":
        attack_score += random.randint(10, 30)

    final_score = attack_score - (defense_score / 2)
    return final_score

# --- Bot Handlers ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Welcome to Card Battle! Use /battle @username to challenge someone."
    )

async def battle_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args or not update.message.entities:
        await update.message.reply_text(
            "Please mention a user to challenge, e.g., /battle @username"
        )
        return

    challenged_user = update.message.entities[0].user
    challenger_id = update.effective_user.id

    # Check if both players have cards
    if challenger_id not in players_cards:
        await update.message.reply_text("You haven't uploaded a card yet! Upload one with the card bot first.")
        return
    if challenged_user.id not in players_cards:
        await update.message.reply_text(f"{challenged_user.first_name} hasn't uploaded a card yet!")
        return

    challenges[challenged_user.id] = challenger_id

    keyboard = [
        [
            InlineKeyboardButton("Accept", callback_data=f"accept_{challenged_user.id}_{challenger_id}"),
            InlineKeyboardButton("Decline", callback_data=f"decline_{challenged_user.id}_{challenger_id}")
        ]
    ]
    await update.message.reply_text(
        f"{challenged_user.first_name}, you have been challenged by {update.effective_user.first_name}!",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data.split("_")
    action, challenged_id, challenger_id = data[0], int(data[1]), int(data[2])

    challenger_card = players_cards[challenger_id]
    challenged_card = players_cards[challenged_id]

    if action == "accept":
        score1 = calculate_score(challenger_card, challenged_card)
        score2 = calculate_score(challenged_card, challenger_card)

        if score1 > score2:
            result = f"🏆 {context.bot.get_chat(challenger_id).first_name} wins!"
        elif score2 > score1:
            result = f"🏆 {context.bot.get_chat(challenged_id).first_name} wins!"
        else:
            result = "🤝 It's a tie!"

        # Send battle summary with images
        await context.bot.send_photo(
            chat_id=query.message.chat_id,
            photo=challenger_card["image_id"],
            caption=(
                f"{context.bot.get_chat(challenger_id).first_name}'s Card\n"
                f"Rarity: {challenger_card['rarity']}\n"
                f"Power: {challenger_card['power']}\n"
                f"Defense: {challenger_card['defense']}\n"
                f"Special: {challenger_card['special']}\n"
                f"Tagline: {challenger_card['tagline']}"
            )
        )
        await context.bot.send_photo(
            chat_id=query.message.chat_id,
            photo=challenged_card["image_id"],
            caption=(
                f"{context.bot.get_chat(challenged_id).first_name}'s Card\n"
                f"Rarity: {challenged_card['rarity']}\n"
                f"Power: {challenged_card['power']}\n"
                f"Defense: {challenged_card['defense']}\n"
                f"Special: {challenged_card['special']}\n"
                f"Tagline: {challenged_card['tagline']}\n\n"
                f"{result}"
            )
        )
        del challenges[challenged_id]

    else:
        await query.edit_message_text("Challenge declined ❌")
        del challenges[challenged_id]

# --- Register handlers ---
application.add_handler(CommandHandler("start", start))
application.add_handler(CommandHandler("battle", battle_command))
application.add_handler(CallbackQueryHandler(button_callback))

# --- Webhook route ---
@app.route(f"/webhook/{TOKEN}", methods=["POST"])
def webhook():
    update = Update.de_json(request.get_json(force=True), application.bot)
    application.create_task(application.process_update(update))
    return "ok"

# --- Health check ---
@app.route("/", methods=["GET"])
def home():
    return "Bot is running!"

# --- Set webhook ---
async def set_webhook():
    url = f"{RENDER_URL}/webhook/{TOKEN}"
    await application.bot.set_webhook(url)
    print(f"Webhook set to {url}")

if __name__ == "__main__":
    import asyncio
    asyncio.run(set_webhook())
