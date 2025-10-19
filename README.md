# PFP Card Battle Bot

A Telegram bot for card-based battles using image uploads and OCR, deployed on Render.

## Prerequisites
- A Render account (https://render.com).
- A Telegram bot token from BotFather.
- (Optional) Arial font (arial.ttf) in the project directory for GIF generation (falls back to default font if absent).

## Project Structure
- `app.py`: Main application script.
- `requirements.txt`: Python dependencies.
- `Dockerfile`: Render build configuration.
- `arial.ttf`: Font file for GIF generation (optional).

## Setup
1. Clone the repository.
2. Create `requirements.txt`:
   ```plaintext
   fastapi
   python-telegram-bot>=20.0
   pytesseract
   Pillow
   imageio
   uvicorn
