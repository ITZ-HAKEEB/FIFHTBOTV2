FROM python:3.11.9-slim

# Set working directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    git \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements
COPY requirements.txt .

# Upgrade pip and install dependencies
RUN python -m pip install --no-cache-dir --upgrade pip setuptools wheel && \
    python -m pip install --no-cache-dir -r requirements.txt

# Verify PyTgCalls and PyrogramMod installation
RUN python -c "from pytgcalls import PyTgCalls, idle; import pyrogram; print('PyTgCalls and Pyrogram integration OK')"

# Copy project files
COPY . .

# Start bot
CMD ["python", "bot.py"]
