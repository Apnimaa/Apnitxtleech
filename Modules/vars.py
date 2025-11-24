import os

API_ID    = os.environ.get("API_ID", "")
API_HASH  = os.environ.get("API_HASH", "")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "") 

WEBHOOK = True  # Don't change this
PORT = int(os.environ.get("PORT", 8870))  # Default to 8000 if not set
MONGO_URI = ""
MONGO_DB_NAME = "mydbname"
