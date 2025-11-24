# main.py
import os
import re
import sys
import json
import time
import asyncio
import requests

import core as helper
from utils import progress_bar
from vars import API_ID, API_HASH, BOT_TOKEN, WEBHOOK, PORT
from aiohttp import ClientSession
from pyrogram import Client, filters
from pyrogram.types import Message
from pyrogram.errors import FloodWait
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from style import Ashu

# optional mongodb vars
try:
    from vars import MONGO_URI
except Exception:
    MONGO_URI = None
try:
    from vars import MONGO_DB_NAME
except Exception:
    MONGO_DB_NAME = None

USE_MONGO = False
mongo_client = None
mongo_db = None
mongo_collection = None

try:
    if MONGO_URI:
        from pymongo import MongoClient
        mongo_client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
        try:
            if MONGO_DB_NAME:
                mongo_db = mongo_client[MONGO_DB_NAME]
            else:
                mongo_db = mongo_client.get_default_database()
        except Exception:
            mongo_db = mongo_client['bot_data']
        if mongo_db is not None:
            mongo_collection = mongo_db.get_collection("targets")
            mongo_client.server_info()
            USE_MONGO = True
            print("[Bot] MongoDB connected.")
except Exception as e:
    print(f"[Bot] MongoDB not available or failed to connect: {e}. Falling back to JSON file storage.")

bot = Client(
    "bot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN
)

USER_DATA_FILE = "user_data.json"
user_targets = {}

DOWNLOADS_DIR = "./downloads"
TEMP_DIR = os.path.join(DOWNLOADS_DIR, "temp")

def ensure_dirs():
    os.makedirs(DOWNLOADS_DIR, exist_ok=True)
    os.makedirs(TEMP_DIR, exist_ok=True)

def cleanup_temp_files():
    if not os.path.exists(TEMP_DIR):
        return
    try:
        for root, dirs, files in os.walk(TEMP_DIR):
            for f in files:
                fp = os.path.join(root, f)
                try:
                    os.remove(fp)
                except:
                    pass
        for d in os.listdir(TEMP_DIR):
            dp = os.path.join(TEMP_DIR, d)
            if os.path.isdir(dp):
                try:
                    os.rmdir(dp)
                except:
                    pass
        print(f"[Bot] Cleaned up temp files in {TEMP_DIR}")
    except Exception as e:
        print(f"[Bot] Error cleaning temp files: {e}")

def save_user_data():
    try:
        with open(USER_DATA_FILE, "w") as f:
            json.dump({str(k): v for k, v in user_targets.items()}, f, indent=4)
    except Exception as e:
        print(f"[Bot] Error saving user data to file: {e}")

    if USE_MONGO and mongo_collection is not None:
        try:
            for uid, tid in user_targets.items():
                mongo_collection.update_one(
                    {"_id": int(uid)},
                    {"$set": {"target": int(tid)}},
                    upsert=True
                )
            print("[Bot] Saved user targets to MongoDB.")
        except Exception as e:
            print(f"[Bot] Error saving to MongoDB: {e}")

def load_user_data():
    global user_targets
    user_targets = {}
    if USE_MONGO and mongo_collection is not None:
        try:
            docs = mongo_collection.find({})
            for doc in docs:
                try:
                    uid = int(doc.get("_id"))
                    tid = int(doc.get("target"))
                    user_targets[uid] = tid
                except Exception:
                    continue
            print("[Bot] Loaded user targets from MongoDB.")
            return
        except Exception as e:
            print(f"[Bot] Error loading from MongoDB: {e}. Falling back to JSON file.")
    try:
        if os.path.exists(USER_DATA_FILE):
            with open(USER_DATA_FILE, "r") as f:
                loaded_data = json.load(f)
                user_targets = {int(k): v for k, v in loaded_data.items()}
                print(f"[Bot] Loaded user data from {USER_DATA_FILE}")
        else:
            user_targets = {}
    except Exception as e:
        print(f"[Bot] Error loading user data: {e}. Starting fresh.")
        user_targets = {}

# web server (optional)
try:
    from aiohttp import web
    routes = web.RouteTableDef()
    @routes.get("/", allow_head=True)
    async def root_route_handler(request):
        return web.json_response("https://github.com/AshutoshGoswami24")
    async def web_server():
        web_app = web.Application(client_max_size=30000000)
        web_app.add_routes(routes)
        return web_app
except Exception:
    web_server = None

@bot.on_message(filters.command(["start"]))
async def account_login(bot: Client, m: Message):
    await m.reply_text(
       Ashu.START_TEXT, reply_markup=InlineKeyboardMarkup(
            [
                    [
                    InlineKeyboardButton("✜ ᴀsʜᴜᴛᴏsʜ ɢᴏsᴡᴀᴍɪ 𝟸𝟺 ✜" ,url="https://t.me/AshutoshGoswami24") ],
                    [
                    InlineKeyboardButton("🦋 𝐅𝐨𝐥𝐥𝐨𝐰 𝐌𝐞 🦋" ,url="https://t.me/AshuSupport") ]                               
            ]))

@bot.on_message(filters.command("stop"))
async def restart_handler(_, m):
    await m.reply_text("♦ Stopped ♦", True)
    os.execl(sys.executable, sys.executable, *sys.argv)

@bot.on_message(filters.command(["set"]))
async def set_target_handler(bot: Client, m: Message):
    if len(m.command) < 2:
        await m.reply_text(
            "**Usage:** `/set <channel_id_or_username>`\n\n"
            "**Example:** `/set -100123456789` or `/set @my_channel`\n\n"
            "The bot must be an **Admin** in the target channel to post messages."
        )
        return
    
    target_str = m.command[1]
    try:
        chat = await bot.get_chat(target_str)
        user_targets[m.from_user.id] = chat.id
        save_user_data()
        await m.reply_text(f"✅ **Target channel set!**\n\n**Name:** {chat.title}\n**ID:** `{chat.id}`")
    except Exception as e:
        await m.reply_text(f"❌ **Error setting target:** `{e}`")

@bot.on_message(filters.command(["upload"]))
async def account_login(bot: Client, m: Message):
    editable = await m.reply_text('Send me your `.txt` file ⏍')
    input: Message = await bot.listen(editable.chat.id)

    if not input.document:
        await editable.edit("That is not a file. Process cancelled. Please send `/upload` again and send a file.")
        try:
            await input.delete(True)
        except:
            pass
        return
    
    if not input.document.file_name.endswith(".txt"):
        await editable.edit(f"This is not a `.txt` file (`{input.document.file_name}`). Process cancelled.")
        try:
            await input.delete(True)
        except:
            pass
        return

    ensure_dirs()

    ts = int(time.time())
    safe_orig = re.sub(r'[^\w\-. ]', '_', input.document.file_name)
    temp_txt_path = os.path.join(TEMP_DIR, f"{m.from_user.id}_{ts}_{safe_orig}")

    try:
        x = await input.download(file_name=temp_txt_path)
        await input.delete(True)
    except Exception as e:
        await editable.edit(f"Failed to download the .txt file: {e}")
        return

    try:
       with open(x, "r") as f:
           content = f.read()
       content = content.splitlines()
       links = []
       for i in content:
           if "://" in i:
               parts = i.split("://", 1)
               links.append(parts)
       try:
           os.remove(x)
       except:
           pass
    except Exception as e:
           await m.reply_text(f"∝ Invalid file input. Error: {e}")
           try:
               os.remove(x)
           except:
               pass
           return
    
    await editable.edit(f"**Total links found in txt file:** 🔗 **{len(links)}**\n\nSend the number from where you want to start downloading. (Initial is `1`)")
    input0: Message = await bot.listen(editable.chat.id)
    raw_text = input0.text
    await input0.delete(True)

    await editable.edit("∝ Now Please Send Me Your Batch Name")
    input1: Message = await bot.listen(editable.chat.id)
    raw_text0 = input1.text
    await input1.delete(True)
    
    await editable.edit(Ashu.Q1_TEXT)
    input2: Message = await bot.listen(editable.chat.id)
    raw_text2 = input2.text
    await input2.delete(True)
    try:
        if raw_text2 == "144":
            res = "256x144"
        elif raw_text2 == "240":
            res = "426x240"
        elif raw_text2 == "360":
            res = "640x360"
        elif raw_text2 == "480":
            res = "854x480"
        elif raw_text2 == "720":
            res = "1280x720"
        elif raw_text2 == "1080":
            res = "1920x1080" 
        else: 
            res = "UN"
    except Exception:
            res = "UN"
    
    await editable.edit("Do you want to add a custom caption (like 'Robin')?\n\nSend `yes` to add, or `no` to skip.")
    input_choice: Message = await bot.listen(editable.chat.id)
    choice = input_choice.text.lower().strip()
    await input_choice.delete(True)

    MR = ""
    if choice in ('yes','y'):
        await editable.edit(Ashu.C1_TEXT)
        input3: Message = await bot.listen(editable.chat.id)
        raw_text3 = input3.text
        await input3.delete(True)
        highlighter  = f"️ ⁪⁬⁮⁮⁮"
        MR = highlighter if raw_text3 == 'Robin' else raw_text3

    await editable.edit(Ashu.T1_TEXT)
    input6 = message = await bot.listen(editable.chat.id)
    raw_text6 = input6.text
    await input6.delete(True)
    await editable.delete()

    thumb = input6.text
    if thumb.startswith("http://") or thumb.startswith("https://"):
        os.system(f"wget '{thumb}' -O 'thumb.jpg'")
        thumb = "thumb.jpg"
    else:
        thumb = "no"

    if len(links) == 1:
        count = 1
    else:
        try:
            count = int(raw_text)
        except:
            await m.reply_text("Invalid start number. Defaulting to 1.")
            count = 1

    target_chat_id = user_targets.get(m.from_user.id)
    if not target_chat_id:
        await m.reply_text("⚠️ **No target channel set.**\nI will send files to this chat.\n\nUse `/set <channel_id>` to set a target channel for future uploads.")
        target_chat_id = m.chat.id
    else:
        try:
            target_chat_id = int(target_chat_id) 
            chat = await bot.get_chat(target_chat_id) 
            await m.reply_text(f"✅ **Target channel found!**\n**Name:** {chat.title}\n**ID:** `{chat.id}`")
        except Exception as e:
            await m.reply_text(f"❌ **Error accessing target channel:** `{e}`\nDefaulting to this chat for now.")
            target_chat_id = m.chat.id

    try:
        for idx in range(count - 1, len(links)):
            V = links[idx][1].replace("file/d/","uc?export=download&id=").replace("www.youtube-nocookie.com/embed", "youtu.be").replace("?modestbranding=1", "").replace("/view?usp=sharing","")
            url = "https://" + V

            if "visionias" in url:
                async with ClientSession() as session:
                    async with session.get(url, headers={'User-Agent': 'Mozilla/5.0'}) as resp:
                        text = await resp.text()
                        mobj = re.search(r"(https://.*?playlist.m3u8.*?)\"", text)
                        if mobj:
                            url = mobj.group(1)

            elif 'videos.classplusapp' in url:
                try:
                    url = requests.get(f'https://api.classplusapp.com/cams/uploader/video/jw-signed-url?url={url}').json().get('url')
                except:
                    pass

            elif '/master.mpd' in url:
                idd = url.split("/")[-2]
                url = "https://d26g5bnklkwsh4.cloudfront.net/" + idd + "/master.m3u8"

            name1 = links[idx][0].replace("\t", "").strip()
            name_for_file = name1.replace(":", "").replace("/", "").replace("+", "").replace("#", "").replace("|", "").replace("@", "").replace("*", "").replace(".", "").replace("https", "").replace("http", "").strip()
            name = f'{str(idx+1).zfill(3)}) {name_for_file[:60]}'

            if "youtu" in url:
                ytf = f"b[height<={raw_text2}][ext=mp4]/bv[height<={raw_text2}][ext=mp4]+ba[ext=m4a]/b[ext=mp4]"
            else:
                ytf = f"b[height<={raw_text2}]/bv[height<={raw_text2}]+ba/b/bv+ba"

            safe_name = re.sub(r'[\\/<>:"|?*]', '_', name)
            pdf_out = os.path.join(TEMP_DIR, f"{safe_name}.pdf")
            base_out = os.path.join(TEMP_DIR, safe_name)

            if "jw-prod" in url:
                cmd = f'yt-dlp -o "{os.path.join(TEMP_DIR, safe_name)}.mp4" "{url}"'
            else:
                cmd = f'yt-dlp -f "{ytf}" "{url}" -o "{os.path.join(TEMP_DIR, safe_name)}.mp4"'

            try:
                cc = f'**[ 🎥 ] Vid_ID:** {str(idx+1).zfill(3)}. {name1}{MR}.mkv\n✉️ 𝐁𝐚ᴛᴄʜ » **{raw_text0}**'
                cc1 = f'**[ 📁 ] Pdf_ID:** {str(idx+1).zfill(3)}. {name1}{MR}.pdf \n✉️ 𝐁𝐚ᴛᴄʜ » **{raw_text0}**'

                if "drive" in url or ".pdf" in url:
                    prog = await m.reply_text(f"❊⟱ 𝐃𝐨𝐰𝐧𝐥𝐨𝐚𝐝𝐢𝐧𝐠 ⟱❊ » `{safe_name}.pdf`")
                    try:
                        if ".pdf" in url:
                            res_file = await helper.download(url, pdf_out)
                            if not res_file:
                                await prog.edit("Failed to download PDF.")
                                continue
                        else:
                            # call async download_video and await
                            res_file = await helper.download_video(cmd, pdf_out, prog)
                            if res_file is False:
                                await prog.edit("Failed to download PDF via downloader.")
                                continue
                        await prog.delete(True)
                        await bot.send_document(chat_id=target_chat_id, document=res_file, caption=cc1)
                        try:
                            os.remove(res_file)
                        except:
                            pass
                    except FloodWait as e:
                        await m.reply_text(str(e))
                        time.sleep(e.x)
                        continue
                    except Exception as e:
                        await prog.edit(f"Error: {e}")
                        continue

                else:
                    prog = await m.reply_text(f"❊⟱ 𝐃𝐨𝐰𝐧𝐥𝐨𝐚𝐝𝐢𝐧𝐠 ⟱❊ » `{safe_name}`\n🔗 {url}")
                    # await async download_video
                    res_file = await helper.download_video(cmd, base_out, prog)
                    if res_file is False:
                        try:
                            await prog.edit(f"⌘ 𝐃𝐨𝐰𝐧𝐥𝐨𝐚𝐝𝐢𝐧𝐠 𝐈𝐧𝐭𝐞𝐫𝐮𝐩𝐭𝐄𝐃\n⌘ 𝐍𝐚ᴍᴇ » {safe_name}\n⌘ 𝐋𝐢ɴᴋ » `{url}`")
                        except:
                            pass
                        # cleanup partials
                        try:
                            for f in os.listdir(TEMP_DIR):
                                if f.startswith(safe_name):
                                    try:
                                        os.remove(os.path.join(TEMP_DIR, f))
                                    except:
                                        pass
                        except:
                            pass
                        continue

                    if not os.path.isabs(res_file):
                        res_file = os.path.abspath(res_file)

                    await helper.send_vid(bot, m, cc, res_file, thumb, name, prog, target_chat_id)
                    await asyncio.sleep(1)

            except Exception as e:
                await m.reply_text(f"Error processing {name}: {e}")
                try:
                    for f in os.listdir(TEMP_DIR):
                        if f.startswith(safe_name):
                            try:
                                os.remove(os.path.join(TEMP_DIR, f))
                            except:
                                pass
                except:
                    pass
                continue

    except Exception as e:
        await m.reply_text(str(e))
    await m.reply_text("✅ Successfully Done")

async def start_web_server_if_needed():
    if WEBHOOK and web_server:
        try:
            app = await web_server()
            runner = web.AppRunner(app)
            await runner.setup()
            site = web.TCPSite(runner, "0.0.0.0", PORT)
            await site.start()
            print(f"Web server started on port {PORT}")
        except Exception as e:
            print(f"[Bot] Web server failed to start: {e}")

if __name__ == "__main__":
    ensure_dirs()
    cleanup_temp_files()
    load_user_data()
    try:
        async def on_start(client):
            asyncio.create_task(start_web_server_if_needed())
        try:
            bot._on_startup = on_start
        except:
            pass
        bot.run()
    except KeyboardInterrupt:
        pass
    except Exception as e:
        print(f"[Bot] Fatal error in main: {e}")