# utils.py
"""
Safe progress utilities for the bot.

Replaces the previous progress_bar with a robust version that:
- Uses per-message locks to prevent concurrent edits and socket races.
- Catches Pyrogram RPC errors including MessageIdInvalid and stops editing safely.
- Throttles edits and limits message size to avoid flooding.
- Keeps a familiar progress text layout.

Signature:
    async def progress_bar(current, total, message, start)
Where `message` is the reply/prog Message object that should be edited.
"""

import asyncio
import time
import math
import logging

log = logging.getLogger(__name__)
_h = logging.StreamHandler()
_h.setFormatter(logging.Formatter("[%(levelname)s] %(asctime)s - %(message)s"))
log.addHandler(_h)
log.setLevel(logging.INFO)

# Per-message locks and last-edit timestamps
_edit_locks = {}
_last_edit_time = {}
# If a message hits a fatal edit error, we mark it so we stop attempting edits
_dead_messages = set()

def _msg_key(msg_obj):
    try:
        return (getattr(msg_obj, "chat", None).id, getattr(msg_obj, "message_id", None))
    except Exception:
        return id(msg_obj)

async def _locked_edit(msg_obj, text):
    """
    Safely edit a message with per-message lock and guarded retries.
    If the message becomes invalid (MessageIdInvalid or similar), stop trying.
    """
    if msg_obj is None:
        return False

    key = _msg_key(msg_obj)
    if key in _dead_messages:
        return False

    lock = _edit_locks.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _edit_locks[key] = lock

    try:
        async with lock:
            try:
                await msg_obj.edit(text)
                _last_edit_time[key] = time.time()
                return True
            except Exception as e:
                # Import here to avoid hard dependency if pyrogram is unavailable during linting
                try:
                    from pyrogram.errors import MessageIdInvalid, PeerIdInvalid, RPCError
                except Exception:
                    MessageIdInvalid = Exception
                    PeerIdInvalid = Exception
                    RPCError = Exception

                # If it's MessageIdInvalid or PeerIdInvalid, mark dead and stop
                err_name = e.__class__.__name__
                if err_name in ("MessageIdInvalid", "PeerIdInvalid"):
                    log.debug(f"Progress edit disabled for message {key}: {e}")
                    _dead_messages.add(key)
                    return False

                # For other RPC errors (rate limit, socket errors), try a single retry after delay
                try:
                    await asyncio.sleep(0.8)
                    await msg_obj.edit(text)
                    _last_edit_time[key] = time.time()
                    return True
                except Exception as e2:
                    log.debug(f"Progress edit failed twice for {key}: {e2}")
                    # If it's MessageIdInvalid on retry, mark dead
                    if e2.__class__.__name__ in ("MessageIdInvalid", "PeerIdInvalid"):
                        _dead_messages.add(key)
                    return False
    except Exception as e:
        log.debug(f"_locked_edit unexpected: {e}")
        return False

def _schedule_edit(msg_obj, text):
    """
    Schedule a non-blocking locked edit task.
    """
    try:
        asyncio.get_event_loop().create_task(_locked_edit(msg_obj, text))
    except Exception as e:
        log.debug(f"_schedule_edit could not schedule: {e}")

def _format_size(bytes_size: float) -> str:
    if bytes_size < 1024:
        return f"{bytes_size:.0f} B"
    units = ["B", "KiB", "MiB", "GiB", "TiB"]
    i = int(math.floor(math.log(bytes_size, 1024)))
    p = math.pow(1024, i)
    s = bytes_size / p
    return f"{s:.2f} {units[i]}"

def _format_eta(start_time: float, processed: int, total: int) -> str:
    if processed <= 0:
        return "Unknown"
    elapsed = max(1e-6, time.time() - start_time)
    speed = processed / elapsed
    remaining = max(0, total - processed)
    if speed <= 0:
        return "Unknown"
    eta_seconds = int(remaining / speed)
    m, s = divmod(eta_seconds, 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h}h {m}m {s}s"
    if m:
        return f"{m}m {s}s"
    return f"{s}s"

def _progress_bar_str(perc: float) -> str:
    # simple visual bar
    total_blocks = 18
    filled = int((perc / 100.0) * total_blocks)
    return "█" * filled + "░" * (total_blocks - filled)

async def progress_bar(current, total, message, start):
    """
    Main progress callback used by Pyrogram send_* functions.
    - current: bytes processed so far
    - total: total bytes (0 if unknown)
    - message: the pyrogram Message object to edit
    - start: start_time timestamp
    """
    # defensive guards
    try:
        if message is None:
            return
        key = _msg_key(message)
        if key in _dead_messages:
            return
    except Exception:
        # if something about message fails, bail silently
        return

    # compute stats
    try:
        cur = int(current)
    except:
        cur = 0
    try:
        tot = int(total)
    except:
        tot = 0

    perc = 0.0
    try:
        if tot > 0:
            perc = (cur / float(tot)) * 100.0
    except Exception:
        perc = 0.0

    try:
        sp = "0B/s"
        if start and cur > 0:
            elapsed = max(1e-6, time.time() - start)
            speed = cur / elapsed
            sp = _format_size(speed) + "/s"
        processed = _format_size(cur)
        total_s = _format_size(tot) if tot else "Unknown"
        eta = _format_eta(start, cur, tot) if tot else "Unknown"
        bar = _progress_bar_str(perc)
    except Exception:
        # fallback in rare cases
        bar = ""
        sp = ""
        processed = str(cur)
        total_s = str(tot)
        eta = "Unknown"

    # build message text (mirror the old layout as much as possible)
    text_lines = [
        "`╭─⌯══⟰ 𝐔𝐩𝐥𝐨𝐝𝐢𝐧𝐠 ⟰══⌯──★`",
        f"├⚡ {bar} |﹝{perc:.2f}%﹞",
        f"├🚀 Speed » {sp}",
        f"├📟 Processed » {processed}",
        f"├🧲 Size - ETA » {total_s} - {eta}",
        "`├𝐁𝐲 » 𝐖𝐃 𝐙𝐎Ν𝐄`",
        "╰─══ ✪ @Opleech_WD ✪ ══─★"
    ]
    full_text = "\n".join(text_lines)

    # throttle and limit length
    try:
        last_ts = _last_edit_time.get(key, 0)
        now = time.time()
        # At most once every 2.5 seconds
        if (now - last_ts) < 2.5:
            return
        # cap message length
        if len(full_text) > 800:
            full_text = full_text[-800:]
        # schedule edit and record timestamp
        _schedule_edit(message, full_text)
        _last_edit_time[key] = now
    except Exception as e:
        # never raise from progress bar; just log and return
        log.debug(f"progress_bar scheduling failed: {e}")
        return
