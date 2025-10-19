FROM python:3.11-slim
RUN apt-get update && apt-get install -y tesseract-ocr
RUN pip install fastapi python-telegram-bot pytesseract Pillow imageio uvicorn
COPY . /app
WORKDIR /app
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "$PORT"]
