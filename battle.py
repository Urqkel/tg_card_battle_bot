import os
import random
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, ContextTypes, ConversationHandler

# --- Card & Battle Logic (same as before) ---
class Card:
    def __init__(self, name, power, defense, rarity, tagline, special_ability=None):
        self.name = name
        self.power = power
        self.defense = defense
        self.rarity = rarity
        self.tagline = tagline
        self.special_ability = special_ability
        self.hp = 100  # starting HP

RARITY_MULTIPLIER = {"Common":1, "Rare":1.2, "Ultra-Rare":1.5, "Legendary":2}

def apply_special(card, opponent):
    if not card.special_ability:
        return ""
    msg = ""
    if card.special_ability == "Double Strike":
        extra_damage = max(1, int(card.power * RARITY_MULTIPLIER[card.rarity] - opponent.defense + random.randint(1,10)))
        opponent.hp -= extra_damage
        msg = f"{card.name} activates Double Strike! Deals extra {extra_damage} damage!"
    elif card.special_ability == "Shield Wall":
        card.hp += 20
        msg = f"{card.name} uses Shield Wall! Gains 20 temporary HP!"
    elif card.special_ability == "Heal":
        heal_amount = 15
        card.hp += heal_amount
        msg = f"{card.name} heals for {heal_amount} HP!"
    return msg

def battle(card1, card2):
    log = []
    log.append(f"{card1.name} – \"{card1.tagline}\"")
    log.append(f"{card2.name} – \"{card2.tagline}\"")
    log.append("Battle Start!\n")

    turn = 0
    while card1.hp > 0 and card2.hp > 0:
        attacker = card1 if turn % 2 == 0 else card2
        defender = card2 if turn % 2 == 0 else card1
        damage = max(1, int(attacker.power * RARITY_MULTIPLIER[attacker.rarity] - defender.defense + random.randint(1,10)))
        defender.hp -= damage
        log.append(f"{attacker.name} attacks {defender.name}! Damage: {damage}. {defender.name} HP: {max(defender.hp,0)}")
        special_msg = apply_special(attacker, defender)
        if special_msg:
            log.append(special_msg)
            defender.hp = max(defender.hp, 0)
        turn += 1
        if turn > 20:  # safety limit
            break

    if card1.hp > card2.hp:
        winner = card1.name
    elif card2.hp > card1.hp:
        winner = card2.name
    else:
        winner = "Draw"
    log.append(f"\nBattle Over! Winner: {winner}")
    return "\n".join(log)

# --- Sample User Collections ---
user_cards = {
    123456: [Card("SHMOO", 25, 10, "Ultra-Rare", "The unstoppable wall of fury!", "Double Strike")],
    654321: [Card("ZORBLAX", 28, 12, "Rare", "Master of shadows", "Shield Wall")]
}

# --- Conversation State ---
SELECT_CARD, = range(1)
challenges = {}
selected_cards = {}

# --- Bot Handlers ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Welcome! Use /battle @username to challenge someone.")

async def battle_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Please mention a user to challenge, e.g., /battle @username")
        return
    challenged_user = update.message.entities[0].user
    challenges[challenged_user.id] = update.effective_user.id
    await update.message.reply_text(f"{challenged_user.first_name}, you have been challenged by {update.effective_user.first_name}!")
    keyboard = [
        [InlineKeyboardButton("Accept", callback_data=f"accept_{challenged_user.id}_{update.effective_user.id}")],
        [InlineKeyboardButton("Decline", callback_data=f"decline_{challenged_user.id}_{update.effective_user.id}")]
    ]
    await
