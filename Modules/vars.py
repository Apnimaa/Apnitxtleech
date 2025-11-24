import os

API_ID    = os.environ.get("API_ID", "27433400")
API_HASH  = os.environ.get("API_HASH", "1a286620de5ffe0a7d9b57e604293555")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "") 

WEBHOOK = True  # Don't change this
PORT = int(os.environ.get("PORT", 8870))  # Default to 8000 if not set
MONGO_URI = ""
MONGO_DB_NAME = ""
