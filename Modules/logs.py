# logs.py
import logging
from logging.handlers import RotatingFileHandler

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s [%(filename)s:%(lineno)d]",
    datefmt="%d-%b-%y %H:%M:%S",
    handlers=[
        RotatingFileHandler("logs.txt", maxBytes=50_000_000, backupCount=5),
        logging.StreamHandler(),
    ],
)
# reduce noise from pyrogram internals
logging.getLogger("pyrogram").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)
