FROM python:3.11-slim

# Install Tesseract OCR and dependencies
RUN apt-get update && apt-get install -y \
    tesseract-ocr \
    libtesseract-dev \
    && rm -rf /var/lib/apt/lists/*

# Upgrade pip and install Python dependencies
RUN pip install --upgrade pip
COPY requirements.txt .
RUN pip install -r requirements.txt

# Copy application files
COPY . /app
WORKDIR /app

# Command to run the application
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "$PORT"]
