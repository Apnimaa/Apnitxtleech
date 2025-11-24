# core.py
"""
Final helper module — async download_video, thumbnail generation, upload helper.
- download(url, out_path): async (for PDFs)
- download_video(cmd, out_path, prog_message): async (yt-dlp), streams output and updates prog_message
- send_vid(...): async upload with thumbnail generation and rotation fix
"""
import os
import sys
import time
import re
import subprocess
import logging
import aiohttp
import aiofiles
import asyncio
from typing import Optional, Tuple

try:
    from pyrogram.types import Message
except Exception:
    Message = object

# import progress_bar from utils (safe)
try:
    from utils import progress_bar
except Exception:
    async def progress_bar(current, total, message, start):
        return

log = logging.getLogger(__name__)
_h = logging.StreamHandler(sys.stdout)
_h.setFormatter(logging.Formatter("[%(levelname)s] %(asctime)s - %(message)s"))
log.addHandler(_h)
log.setLevel(logging.INFO)

# ---------- edit locking ----------
_edit_locks = {}
_last_edit_ts = {}
_dead_messages = set()

def _msg_key(msg_obj):
    try:
        return (getattr(msg_obj, "chat", None).id, getattr(msg_obj, "message_id", None))
    except Exception:
        return id(msg_obj)

async def _locked_edit(msg_obj, text):
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
                _last_edit_ts[key] = time.time()
                return True
            except Exception as e:
                try:
                    from pyrogram.errors import MessageIdInvalid, PeerIdInvalid
                except Exception:
                    MessageIdInvalid = Exception
                    PeerIdInvalid = Exception
                if e.__class__.__name__ in ("MessageIdInvalid", "PeerIdInvalid"):
                    _dead_messages.add(key)
                    return False
                # single retry
                try:
                    await asyncio.sleep(0.8)
                    await msg_obj.edit(text)
                    _last_edit_ts[key] = time.time()
                    return True
                except Exception as e2:
                    if e2.__class__.__name__ in ("MessageIdInvalid", "PeerIdInvalid"):
                        _dead_messages.add(key)
                    return False
    except Exception:
        return False

def _schedule_edit(msg_obj, text):
    try:
        asyncio.get_running_loop().create_task(_locked_edit(msg_obj, text))
    except Exception:
        # fallback: try get_event_loop
        try:
            asyncio.get_event_loop().create_task(_locked_edit(msg_obj, text))
        except Exception:
            pass

async def _safe_edit(msg_obj, text):
    _schedule_edit(msg_obj, text)
    await asyncio.sleep(0)

# ---------- progress formatting ----------
def _format_progress_lines(percent: float, processed: str, total: str, speed: str, eta: str) -> str:
    try:
        perc = float(percent)
    except:
        perc = 0.0
    total_blocks = 18
    filled = int((perc / 100.0) * total_blocks)
    bar = "█" * filled + "░" * (total_blocks - filled)
    lines = [
        "`╭─⌯══⟰ 𝐏𝐫𝐨𝐠𝐫𝐞𝐬𝐬 ⟰══⌯──★`",
        f"├⚡ {bar} |﹝{perc:.2f}%﹞",
        f"├🚀 Speed » {speed}",
        f"├📟 Processed » {processed}",
        f"├🧲 Size - ETA » {total} - {eta}",
        "`├𝐁𝐲 » 𝐖𝐃 𝐙𝐎Ν𝐄`",
        "╰─══ ✪ @Opleech_WD ✪ ══─★"
    ]
    text = "\n".join(lines)
    if len(text) > 900:
        text = text[-900:]
    return text

_YT_PERCENT_RE = re.compile(r'(\d{1,3}\.\d+)%')
_YT_SIZE_RE = re.compile(r'of ~\s*([0-9.,A-Za-z]+)')
_YT_SPEED_RE = re.compile(r'at\s+([0-9.,A-Za-z]+/s)')
_YT_ETA_RE = re.compile(r'ETA\s+([0-9:]+)')

def _parse_yt_line(line: str) -> Tuple[float, str, str, str, str]:
    percent = 0.0
    processed = ""
    total = ""
    speed = ""
    eta = ""
    try:
        m = _YT_PERCENT_RE.search(line)
        if m:
            percent = float(m.group(1))
    except:
        percent = 0.0
    try:
        m = _YT_SIZE_RE.search(line)
        if m:
            total = m.group(1)
    except:
        total = ""
    try:
        m = _YT_SPEED_RE.search(line)
        if m:
            speed = m.group(1)
    except:
        speed = ""
    try:
        m = _YT_ETA_RE.search(line)
        if m:
            eta = m.group(1)
    except:
        eta = ""
    if percent and total:
        processed = f"{percent:.2f}%"
    return percent, processed, total, speed, eta

# ---------- async download for simple files ----------
async def download(url: str, out_path: str) -> str:
    out_path = os.path.abspath(out_path)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    timeout = aiohttp.ClientTimeout(total=0)
    headers = {"User-Agent": "Mozilla/5.0 (compatible; Bot/1.0)"}
    async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
        async with session.get(url, allow_redirects=True) as resp:
            if resp.status != 200:
                raise Exception(f"HTTP {resp.status} for {url}")
            try:
                async with aiofiles.open(out_path, "wb") as f:
                    async for chunk in resp.content.iter_chunked(64 * 1024):
                        await f.write(chunk)
                return out_path
            except Exception:
                try:
                    if os.path.exists(out_path):
                        os.remove(out_path)
                except:
                    pass
                raise

# ---------- robust async subprocess streamer ----------
async def download_video(cmd: str, out_path: str, prog_message: Optional[Message] = None, throttle: float = 1.2):
    """
    Async downloader: runs cmd with asyncio.create_subprocess_shell, streams stdout in chunks,
    parses yt-dlp-like lines and edits prog_message periodically.
    Returns absolute filepath on success or False on failure.
    """
    out_path = os.path.abspath(out_path)
    base, ext = os.path.splitext(out_path)
    candidates = []
    if ext:
        candidates.append(out_path)
    candidates.extend([f"{base}.mp4", f"{base}.mkv", f"{base}.webm", f"{base}.m4a", f"{base}.mp3", f"{base}.pdf"])
    candidates.insert(0, base)

    log.info(f"download_video: {cmd}")
    try:
        proc = await asyncio.create_subprocess_shell(cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
    except Exception:
        log.exception("Failed to start async downloader")
        return False

    buf = ""
    tail_lines = []
    last_edit = 0.0
    key = _msg_key(prog_message) if prog_message is not None else None

    try:
        while True:
            chunk = await proc.stdout.read(4096)
            if not chunk:
                break
            try:
                text = chunk.decode(errors="ignore")
            except:
                text = str(chunk)
            buf += text
            if "\n" in buf:
                parts = buf.split("\n")
                complete = parts[:-1]
                buf = parts[-1]
                tail_lines.extend(complete)
                if len(tail_lines) > 80:
                    tail_lines = tail_lines[-80:]
            else:
                # no newline yet, trim buf
                if len(buf) > 20000:
                    buf = buf[-20000:]

            now = time.time()
            send = False
            # find last line with progress markers
            last_line = None
            for t in reversed(tail_lines):
                tl = t.lower()
                if "[download]" in tl or "%" in tl or "eta" in tl or "frag" in tl or "downloading" in tl:
                    last_line = t
                    send = True
                    break
            if not last_line and buf and ("%" in buf or "eta" in buf):
                last_line = buf
                send = True
            if (now - last_edit) >= throttle:
                send = True

            if send and prog_message is not None and last_line:
                percent, processed, total, speed, eta = _parse_yt_line(last_line)
                text = _format_progress_lines(percent, processed, total, speed, eta)
                _schedule_edit(prog_message, text)
                last_edit = now

        rc = await proc.wait()
    except Exception:
        log.exception("Error streaming async downloader")
        try:
            proc.kill()
        except:
            pass
        return False

    # final update
    if prog_message is not None and (tail_lines or buf):
        last_line = None
        for t in reversed(tail_lines):
            if "%" in t or "eta" in t or "frag" in t:
                last_line = t
                break
        if not last_line and buf:
            last_line = buf
        if last_line:
            percent, processed, total, speed, eta = _parse_yt_line(last_line)
            final_text = _format_progress_lines(percent, processed, total, speed, eta)
            _schedule_edit(prog_message, final_text)

    if rc != 0:
        log.warning(f"Downloader exited with code {rc}")
        return False

    for c in candidates:
        if os.path.isfile(c):
            return os.path.abspath(c)

    # fallback scan
    directory = os.path.dirname(base) or "."
    prefix = os.path.basename(base)
    try:
        if os.path.isdir(directory):
            for f in os.listdir(directory):
                if f.startswith(prefix):
                    cand = os.path.join(directory, f)
                    if os.path.isfile(cand):
                        return os.path.abspath(cand)
    except Exception:
        pass

    return False

# ---------- thumbnail generation ----------
def generate_thumbnail_from_video(video_path: str, thumb_path: Optional[str] = None, time_offset: int = 5) -> Optional[str]:
    try:
        video_path = os.path.abspath(video_path)
        if thumb_path is None:
            base = os.path.splitext(video_path)[0]
            thumb_path = f"{base}.thumb.jpg"
        thumb_path = os.path.abspath(thumb_path)
        vf = (
            "scale='if(gt(a,1280/720),1280,-2)':'if(gt(a,1280/720),-2,720)',"
            "pad=1280:720:(ow-iw)/2:(oh-ih)/2,setsar=1"
        )
        cmd = f'ffmpeg -y -ss {time_offset} -i "{video_path}" -vframes 1 -q:v 2 -vf "{vf}" "{thumb_path}"'
        rc = subprocess.run(cmd, shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if rc.returncode == 0 and os.path.isfile(thumb_path):
            return thumb_path
    except Exception:
        pass
    return None

# ---------- rotation fix ----------
def _fix_rotation(infile: str) -> Optional[str]:
    try:
        p = subprocess.run(
            f'ffprobe -v error -select_streams v:0 -show_entries stream_tags=rotate -of default=noprint_wrappers=1:nokey=1 "{infile}"',
            shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, timeout=8
        )
        rot = p.stdout.strip()
        if not rot:
            return None
        base = os.path.splitext(infile)[0]
        fixed = f"{base}.fixed.mp4"
        cmd = f'ffmpeg -y -i "{infile}" -c copy -map 0 -metadata:s:v:0 rotate=0 "{fixed}"'
        rc = subprocess.run(cmd, shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if rc.returncode == 0 and os.path.isfile(fixed):
            return fixed
    except Exception:
        pass
    return None

# ---------- probe metadata ----------
def _probe_video_metadata(path: str) -> Tuple[int,int,Optional[int]]:
    width = 0; height = 0; rotate = None
    try:
        p_w = subprocess.run(f'ffprobe -v error -select_streams v:0 -show_entries stream=width -of csv=p=0 "{path}"', shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, timeout=6)
        p_h = subprocess.run(f'ffprobe -v error -select_streams v:0 -show_entries stream=height -of csv=p=0 "{path}"', shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, timeout=6)
        try:
            width = int(p_w.stdout.strip())
        except:
            width = 0
        try:
            height = int(p_h.stdout.strip())
        except:
            height = 0
        p_rot = subprocess.run(f'ffprobe -v error -select_streams v:0 -show_entries stream_tags=rotate -of default=noprint_wrappers=1:nokey=1 "{path}"', shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, timeout=6)
        rot = p_rot.stdout.strip()
        if rot:
            try:
                rotate = int(rot)
            except:
                rotate = None
    except Exception:
        pass
    return width, height, rotate

# ---------- duration ----------
def duration(filename: str) -> int:
    try:
        filename = os.path.abspath(filename)
        cmd = f'ffprobe -v error -show_entries format=duration -of default=noprint_wrappers=1:nokey=1 "{filename}"'
        proc = subprocess.run(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, timeout=10)
        out = proc.stdout.strip()
        if out:
            try:
                return int(float(out))
            except:
                return 0
    except Exception:
        pass
    return 0

# ---------- send_vid (upload) ----------
async def send_vid(bot, m, cc: str, filename: str, thumb, name: str, prog: Optional[Message], target_chat_id):
    try:
        if prog:
            try:
                await prog.delete(True)
            except:
                pass
    except:
        pass

    filename = os.path.abspath(filename)
    if not os.path.isfile(filename):
        try:
            await m.reply_text(f"Upload failed: file not found `{filename}`")
        except:
            pass
        return False

    reply_msg = None
    try:
        reply_msg = await m.reply_text(f"**⥣ Uploading ...** » `{name}`")
    except:
        reply_msg = None

    thumb_path = None
    generated_thumb = None
    try:
        if thumb and thumb != "no" and isinstance(thumb, str) and os.path.isfile(thumb):
            thumb_path = thumb
        else:
            gen = generate_thumbnail_from_video(filename)
            if gen:
                thumb_path = gen
                generated_thumb = gen
    except:
        thumb_path = None
        generated_thumb = None

    fixed_file = None
    try:
        fixed = _fix_rotation(filename)
        if fixed:
            fixed_file = fixed
            use_file = fixed_file
        else:
            use_file = filename
    except:
        use_file = filename

    width, height, _ = _probe_video_metadata(use_file)

    async def try_upload(as_video=True, attempts=2):
        last_err = None
        for attempt in range(attempts):
            try:
                if as_video:
                    dur = duration(use_file)
                    await bot.send_video(
                        chat_id=target_chat_id,
                        video=use_file,
                        caption=cc,
                        supports_streaming=True,
                        thumb=thumb_path if thumb_path else None,
                        duration=dur,
                        width=width if width else None,
                        height=height if height else None,
                        progress=progress_bar,
                        progress_args=(reply_msg if reply_msg is not None else m, time.time())
                    )
                else:
                    await bot.send_document(
                        chat_id=target_chat_id,
                        document=use_file,
                        caption=cc,
                        thumb=thumb_path if thumb_path else None,
                        progress=progress_bar,
                        progress_args=(reply_msg if reply_msg is not None else m, time.time())
                    )
                return True
            except Exception as e:
                last_err = e
                try:
                    from pyrogram.errors import FloodWait
                    if isinstance(e, FloodWait):
                        await asyncio.sleep(int(e.x) + 1)
                        continue
                except:
                    pass
                await asyncio.sleep(1)
        try:
            await m.reply_text(f"Upload failed: {last_err}")
        except:
            pass
        return False

    sent_ok = await try_upload(as_video=True, attempts=2)
    if not sent_ok:
        sent_ok = await try_upload(as_video=False, attempts=1)

    try:
        if fixed_file and os.path.isfile(fixed_file):
            try:
                os.remove(fixed_file)
            except:
                pass
    except:
        pass
    try:
        if generated_thumb and os.path.isfile(generated_thumb):
            try:
                os.remove(generated_thumb)
            except:
                pass
    except:
        pass
    try:
        if os.path.isfile(filename):
            try:
                os.remove(filename)
            except:
                pass
    except:
        pass

    try:
        if reply_msg:
            await reply_msg.delete(True)
    except:
        pass

    return sent_ok

# ---------- safe remove ----------
def safe_remove(path: str):
    try:
        if os.path.isfile(path):
            os.remove(path)
    except Exception:
        pass