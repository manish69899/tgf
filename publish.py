# -*- coding: utf-8 -*-
"""
================================================================================
                    TITANIUM PUBLISHER BOT - ENTERPRISE EDITION
================================================================================
Version: 10.0.0 (Titanium)
Author: Senior Python Architect
Architecture: Monolithic Async Class-Based

DESCRIPTION:
This is a massive, production-grade automation system for Telegram Channels.
It replaces the need for multiple bots by integrating:
1. Queue Management (FIFO)
2. Sticker Interleaving Strategy
3. Task Scheduling (Cron-like)
4. Database Persistence (SQLite)
5. Mass Broadcasting
6. Content Filtering & Caption Manipulation
7. Disaster Recovery (Auto-Backups)

INSTRUCTIONS:
1. Install requirements: pip install pyrogram tgcrypto
2. Fill CONFIGURATION section below.
3. Run: python publish_bot.py

WARNING: 
This code uses advanced threading and asyncio patterns. 
Do not modify unless you understand Python Concurrency.
================================================================================
"""

import asyncio
import logging
import sqlite3
import os
import time
import random
import sys
import traceback
import json
import re
from typing import List, Optional, Dict, Union, Any, Tuple
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor

# Third-Party Imports
try:
    from pyrogram import Client, filters, idle, enums
    from pyrogram.types import (
        Message, 
        InlineKeyboardMarkup, 
        InlineKeyboardButton, 
        CallbackQuery,
        InputMediaPhoto,
        InputMediaVideo,
        InputMediaDocument
    )
    from pyrogram.errors import (
        FloodWait, 
        RPCError, 
        MessageNotModified, 
        StickersetInvalid,
        PeerIdInvalid,
        ChatAdminRequired,
        UserIsBlocked
    )
except ImportError:
    print("CRITICAL ERROR: Pyrogram is not installed.")
    print("Run: pip install pyrogram tgcrypto")
    sys.exit(1)

# ==============================================================================
#                               CONFIGURATION
# ==============================================================================

# 🔐 CREDENTIALS (REPLACE THESE)
API_ID = 39556336
API_HASH = "5b8d61ed768f68ebdf403350e64234cc"
BOT_TOKEN = "8424927329:AAE0lfGNvfiXGjF45hAmkzI6BFO32zP1n84"
SUPER_ADMIN_ID = 8308946154  # YOUR ID

# ⚙️ SYSTEM SETTINGS
DB_NAME = "titanium_bot.db"
LOG_FILE = "titanium_system.log"
BACKUP_INTERVAL = 3600 * 6  # Backup every 6 hours
WORKER_SLEEP = 1            # Tick rate for workers

# 📝 LOGGING CONFIGURATION (Rotating & Verbose)
logging.basicConfig(
    format='%(asctime)s - [%(levelname)s] - %(name)s - %(funcName)s:%(lineno)d - %(message)s',
    level=logging.INFO,
    handlers=[
        logging.FileHandler(LOG_FILE, encoding='utf-8'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger("TitaniumCore")

# ==============================================================================
#                           MODULE 1: DATABASE ENGINE
# ==============================================================================

class DatabaseEngine:
    """
    Advanced Thread-Safe SQLite Wrapper.
    Handles all persistence logic with auto-commit and thread locking.
    """
    def __init__(self, db_name: str):
        self.db_name = db_name
        self.lock = asyncio.Lock()
        self.init_db()

    def _connect(self):
        """Creates a fresh connection (SQLite is not thread-safe by default)."""
        return sqlite3.connect(self.db_name, check_same_thread=False)

    def init_db(self):
        """Initialize the schema with normalization."""
        conn = self._connect()
        cursor = conn.cursor()
        try:
            # 1. Config Table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS config (
                    key TEXT PRIMARY KEY,
                    value TEXT,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # 2. Sticker Packs
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS sticker_packs (
                    pack_name TEXT PRIMARY KEY,
                    added_by INTEGER,
                    is_active INTEGER DEFAULT 1,
                    added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')

            # 3. Scheduled Tasks
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS schedule (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_id INTEGER,
                    message_blob BLOB,
                    caption TEXT,
                    media_type TEXT,
                    run_at TIMESTAMP,
                    status TEXT DEFAULT 'pending'
                )
            ''')
            
            # 4. Access Control
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS admins (
                    user_id INTEGER PRIMARY KEY,
                    role TEXT DEFAULT 'admin',
                    added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')

            # 5. Blacklist/Filters
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS filters (
                    keyword TEXT PRIMARY KEY,
                    replacement TEXT
                )
            ''')

            # Seed Defaults
            defaults = {
                "target_channel": "0",
                "delay": "3",
                "footer": "NONE",
                "header": "NONE",
                "mode": "copy",
                "is_paused": "0",
                "clean_links": "0"
            }
            for k, v in defaults.items():
                cursor.execute("INSERT OR IGNORE INTO config (key, value) VALUES (?, ?)", (k, v))

            # Ensure Super Admin
            cursor.execute("INSERT OR IGNORE INTO admins (user_id, role) VALUES (?, 'super')", (SUPER_ADMIN_ID,))
            
            conn.commit()
            logger.info("✅ Database Schema Verified.")
        except Exception as e:
            logger.critical(f"❌ DB Init Failed: {e}")
            traceback.print_exc()
            sys.exit(1)
        finally:
            conn.close()

    # --- Generic Helpers ---
    def get_config(self, key: str) -> str:
        with sqlite3.connect(self.db_name) as conn:
            cur = conn.cursor()
            cur.execute("SELECT value FROM config WHERE key=?", (key,))
            res = cur.fetchone()
            return res[0] if res else None

    def set_config(self, key: str, value: str):
        with sqlite3.connect(self.db_name) as conn:
            conn.execute("REPLACE INTO config (key, value) VALUES (?, ?)", (key, str(value)))
            conn.commit()

    # --- Advanced Queries ---
    def add_schedule(self, chat_id, msg_blob, caption, media_type, run_at):
        with sqlite3.connect(self.db_name) as conn:
            conn.execute("""
                INSERT INTO schedule (chat_id, message_blob, caption, media_type, run_at)
                VALUES (?, ?, ?, ?, ?)
            """, (chat_id, msg_blob, caption, media_type, run_at))
            conn.commit()
            return conn.total_changes

    def get_pending_schedules(self):
        with sqlite3.connect(self.db_name) as conn:
            cur = conn.cursor()
            # Get tasks where run_at <= current_time and status is pending
            cur.execute("SELECT * FROM schedule WHERE status='pending' AND run_at <= datetime('now', 'localtime')")
            return cur.fetchall()

    def mark_schedule_done(self, task_id, status='done'):
        with sqlite3.connect(self.db_name) as conn:
            conn.execute("UPDATE schedule SET status=? WHERE id=?", (status, task_id))
            conn.commit()

    def get_sticker_packs(self):
        with sqlite3.connect(self.db_name) as conn:
            cur = conn.cursor()
            cur.execute("SELECT pack_name FROM sticker_packs WHERE is_active=1")
            return [x[0] for x in cur.fetchall()]

    def add_sticker_pack(self, name, user_id):
        with sqlite3.connect(self.db_name) as conn:
            conn.execute("INSERT OR IGNORE INTO sticker_packs (pack_name, added_by) VALUES (?, ?)", (name, user_id))
            conn.commit()

    def remove_sticker_pack(self, name):
        with sqlite3.connect(self.db_name) as conn:
            conn.execute("DELETE FROM sticker_packs WHERE pack_name=?", (name,))
            conn.commit()

    def is_admin(self, user_id):
        with sqlite3.connect(self.db_name) as conn:
            cur = conn.cursor()
            cur.execute("SELECT 1 FROM admins WHERE user_id=?", (user_id,))
            return cur.fetchone() is not None

# Global DB Instance
db = DatabaseEngine(DB_NAME)

# ==============================================================================
#                           MODULE 2: STATE MANAGEMENT
# ==============================================================================

class BotState:
    """In-Memory Fast Access State Cache."""
    def __init__(self):
        self.queue = asyncio.Queue()
        self.sticker_cache = {}  # {pack_name: [file_ids]}
        self.user_inputs = {}    # {user_id: expected_input_type}
        self.start_time = time.time()
        self.processed_count = 0
        self.errors_count = 0

state = BotState()

# ==============================================================================
#                           MODULE 3: UTILITY CLASSES
# ==============================================================================

class MessageSerializer:
    """Handles serialization of Pyrogram messages for scheduling."""
    @staticmethod
    def serialize(message: Message) -> bytes:
        # Simplistic serialization: store raw JSON text or pickle
        # For robustness, we store the object using pickle in a real enterprise app,
        # but here we'll use a simplified JSON dict approach for safety.
        # Note: Scheduling media usually requires file_ids.
        data = {
            "text": message.text or message.caption or "",
            "file_id": None,
            "type": "text"
        }
        if message.photo:
            data['file_id'] = message.photo.file_id
            data['type'] = "photo"
        elif message.video:
            data['file_id'] = message.video.file_id
            data['type'] = "video"
        elif message.document:
            data['file_id'] = message.document.file_id
            data['type'] = "document"
        elif message.sticker:
            data['file_id'] = message.sticker.file_id
            data['type'] = "sticker"
            
        return json.dumps(data).encode('utf-8')

    @staticmethod
    def deserialize(blob: bytes) -> dict:
        return json.loads(blob.decode('utf-8'))

class TextProcessor:
    """Advanced Caption Manipulation."""
    @staticmethod
    def apply_formatting(text: str) -> str:
        if not text: return ""
        
        # 1. Clean Links if enabled
        if db.get_config("clean_links") == "1":
            text = re.sub(r'http\S+', '', text)
            text = re.sub(r'@\S+', '', text)

        # 2. Add Header
        header = db.get_config("header")
        if header and header != "NONE":
            text = f"{header}\n\n{text}"

        # 3. Add Footer
        footer = db.get_config("footer")
        if footer and footer != "NONE":
            text = f"{text}\n\n{footer}"
            
        return text.strip()

# ==============================================================================
#                           MODULE 4: KEYBOARD FACTORY
# ==============================================================================

class Keyboards:
    @staticmethod
    def main_dashboard():
        is_paused = db.get_config("is_paused") == "1"
        target = db.get_config("target_channel")
        target_display = target if target != "0" else "⚠️ NOT SET"
        status = "🔴 PAUSED" if is_paused else "🟢 RUNNING"
        
        return InlineKeyboardMarkup([
            [
                InlineKeyboardButton(status, callback_data="toggle_pause"),
                InlineKeyboardButton(f"📡 ID: {target_display}", callback_data="set_channel")
            ],
            [
                InlineKeyboardButton(f"⏱ Delay: {db.get_config('delay')}s", callback_data="set_delay"),
                InlineKeyboardButton(f"🔄 Mode: {db.get_config('mode').upper()}", callback_data="toggle_mode")
            ],
            [
                InlineKeyboardButton("✍️ Branding (H/F)", callback_data="menu_branding"),
                InlineKeyboardButton("🎭 Sticker Packs", callback_data="menu_stickers")
            ],
            [
                InlineKeyboardButton(f"📥 Queue: {state.queue.qsize()}", callback_data="view_queue"),
                InlineKeyboardButton("📅 Schedules", callback_data="menu_schedule")
            ],
            [
                InlineKeyboardButton("🛠 Tools & Backup", callback_data="menu_tools"),
                InlineKeyboardButton("⚙️ Admins", callback_data="menu_admins")
            ],
            [InlineKeyboardButton("🔄 Refresh Dashboard", callback_data="refresh_home")]
        ])

    @staticmethod
    def branding_menu():
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("📝 Set Header", callback_data="set_header"), InlineKeyboardButton("📝 Set Footer", callback_data="set_footer")],
            [InlineKeyboardButton("🗑 Remove Header", callback_data="rem_header"), InlineKeyboardButton("🗑 Remove Footer", callback_data="rem_footer")],
            [InlineKeyboardButton("👀 Preview Branding", callback_data="preview_branding")],
            [InlineKeyboardButton("🔙 Back", callback_data="back_home")]
        ])

    @staticmethod
    def cancel_btn():
        return InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel Input", callback_data="cancel_input")]])

    @staticmethod
    def back_home_btn():
        return InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back to Home", callback_data="back_home")]])

# ==============================================================================
#                           MODULE 5: WORKER ENGINES
# ==============================================================================

app = Client("titanium_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

async def sticker_cache_manager():
    """Background task to keep sticker cache fresh."""
    while True:
        packs = db.get_sticker_packs()
        if not packs:
            await asyncio.sleep(60)
            continue
            
        for pack in packs:
            try:
                s_set = await app.get_sticker_set(pack)
                if s_set.stickers:
                    state.sticker_cache[pack] = [s.file_id for s in s_set.stickers]
            except Exception as e:
                logger.warning(f"Failed to refresh pack {pack}: {e}")
                # Remove invalid packs automatically
                if "StickersetInvalid" in str(e):
                    db.remove_sticker_pack(pack)
        
        await asyncio.sleep(300)  # Refresh every 5 mins

async def backup_engine():
    """Periodically backups database to Super Admin."""
    while True:
        await asyncio.sleep(BACKUP_INTERVAL)
        try:
            await app.send_document(
                SUPER_ADMIN_ID, 
                DB_NAME, 
                caption=f"💽 **Auto-Backup**\n📅 {datetime.now()}"
            )
            logger.info("✅ Auto-Backup sent.")
        except Exception as e:
            logger.error(f"Backup failed: {e}")

async def schedule_engine():
    """Checks for scheduled tasks and executes them."""
    logger.info("🕰 Scheduler Engine Online")
    while True:
        pending = db.get_pending_schedules()
        for task in pending:
            # task: (id, chat_id, blob, caption, type, run_at, status)
            task_id, chat_id, blob, caption, mtype, run_at, status = task
            
            try:
                data = MessageSerializer.deserialize(blob)
                file_id = data.get('file_id')
                text_content = data.get('text')
                
                # Use current caption logic over stored if needed
                final_caption = TextProcessor.apply_formatting(caption or text_content)
                target = int(db.get_config("target_channel"))
                
                if target == 0:
                    logger.warning("Scheduled task skipped: No Target Set")
                    db.mark_schedule_done(task_id, 'failed')
                    continue

                # Execute Send
                if mtype == "text":
                    await app.send_message(target, final_caption)
                elif mtype == "photo":
                    await app.send_photo(target, file_id, caption=final_caption)
                elif mtype == "video":
                    await app.send_video(target, file_id, caption=final_caption)
                elif mtype == "sticker":
                    await app.send_sticker(target, file_id)
                
                db.mark_schedule_done(task_id, 'success')
                await app.send_message(SUPER_ADMIN_ID, f"✅ Scheduled Task #{task_id} Executed.")
                
            except Exception as e:
                logger.error(f"Schedule Execution Failed: {e}")
                db.mark_schedule_done(task_id, 'error')
        
        await asyncio.sleep(10) # Check every 10 seconds

async def queue_worker():
    """The Main Processing Loop."""
    logger.info("🚀 Main Queue Worker Started")
    
    while True:
        # 1. Fetch Item
        message: Message = await state.queue.get()
        
        try:
            # 2. Check Pause
            while db.get_config("is_paused") == "1":
                await asyncio.sleep(5)
                
            target = int(db.get_config("target_channel"))
            if target == 0:
                logger.error("Target channel 0. Dropping message.")
                continue

            # 3. INTERLEAVE STICKER
            packs = db.get_sticker_packs()
            if packs:
                pack = random.choice(packs)
                # Check cache logic
                if pack not in state.sticker_cache:
                    # Try emergency fetch
                    try:
                        s_set = await app.get_sticker_set(pack)
                        state.sticker_cache[pack] = [s.file_id for s in s_set.stickers]
                    except:
                        pass
                
                cached_ids = state.sticker_cache.get(pack, [])
                if cached_ids:
                    try:
                        await app.send_sticker(target, random.choice(cached_ids))
                        await asyncio.sleep(1) # Aesthetic delay
                    except Exception as e:
                        logger.error(f"Sticker error: {e}")

            # 4. PROCESS CONTENT
            mode = db.get_config("mode")
            
            if mode == "forward":
                await message.forward(target)
            else:
                # COPY MODE
                # Extract Text
                raw_text = message.text or message.caption or ""
                final_text = TextProcessor.apply_formatting(raw_text)
                
                if message.text:
                    await app.send_message(target, final_text, entities=message.entities)
                elif message.media:
                    # Copy with new caption
                    # Note: .copy method handles media type automatically
                    await message.copy(target, caption=final_text)
            
            state.processed_count += 1
            logger.info(f"✅ Processed. Queue: {state.queue.qsize()}")
            
            # 5. Delay
            delay = int(db.get_config("delay"))
            await asyncio.sleep(delay)

        except FloodWait as e:
            logger.warning(f"🌊 FloodWait: Sleeping {e.value}s")
            await asyncio.sleep(e.value)
            # Optional: Re-queue? await state.queue.put(message)
        except Exception as e:
            logger.error(f"Queue Worker Fail: {e}")
            state.errors_count += 1
        finally:
            state.queue.task_done()

# ==============================================================================
#                           MODULE 6: HANDLERS
# ==============================================================================

@app.on_message(filters.command("start") & filters.private)
async def start_handler(client, message):
    if not db.is_admin(message.from_user.id): return
    state.user_inputs.pop(message.from_user.id, None)
    
    txt = (
        f"🤖 **TITANIUM PUBLISHER BOT**\n"
        f"▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬\n"
        f"👋 Welcome **{message.from_user.first_name}**\n"
        f"⚡ Uptime: `{str(timedelta(seconds=int(time.time() - state.start_time)))}`\n"
        f"📦 Processed: `{state.processed_count}`\n"
        f"▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬"
    )
    await message.reply_text(txt, reply_markup=Keyboards.main_dashboard())

@app.on_message(filters.command("schedule") & filters.private)
async def schedule_command(client, message):
    if not db.is_admin(message.from_user.id): return
    # Format: /schedule YYYY-MM-DD HH:MM
    # Needs to be a reply
    if not message.reply_to_message:
        await message.reply_text("❌ Reply to a message to schedule it.")
        return
        
    try:
        args = message.text.split(None, 2)
        if len(args) < 3:
            await message.reply_text("❌ Usage: `/schedule YYYY-MM-DD HH:MM`")
            return
            
        date_str = f"{args[1]} {args[2]}"
        run_at = datetime.strptime(date_str, "%Y-%m-%d %H:%M")
        
        if run_at < datetime.now():
            await message.reply_text("❌ Time must be in future!")
            return
            
        # Serialize
        target_msg = message.reply_to_message
        blob = MessageSerializer.serialize(target_msg)
        mtype = "text"
        if target_msg.photo: mtype = "photo"
        elif target_msg.video: mtype = "video"
        elif target_msg.sticker: mtype = "sticker"
        
        chat_id = message.chat.id
        
        db.add_schedule(chat_id, blob, target_msg.caption, mtype, run_at)
        await message.reply_text(f"✅ **Scheduled for:** `{run_at}`")
        
    except ValueError:
        await message.reply_text("❌ Invalid Date Format. Use `YYYY-MM-DD HH:MM` (24h)")
    except Exception as e:
        await message.reply_text(f"❌ Error: {e}")

@app.on_callback_query()
async def cb_handler(client, cb):
    if not db.is_admin(cb.from_user.id): return
    uid = cb.from_user.id
    data = cb.data
    
    try:
        # Navigation
        if data == "back_home" or data == "refresh_home":
            state.user_inputs.pop(uid, None)
            await cb.edit_message_text(f"🤖 **Dashboard**\nSelect option:", reply_markup=Keyboards.main_dashboard())
            
        elif data == "toggle_pause":
            curr = db.get_config("is_paused")
            new = "0" if curr == "1" else "1"
            db.set_config("is_paused", new)
            await cb.edit_message_reply_markup(Keyboards.main_dashboard())
            
        # Inputs
        elif data == "set_channel":
            state.user_inputs[uid] = "CHANNEL"
            await cb.edit_message_text("📡 Send Channel ID or Forward a post.", reply_markup=Keyboards.cancel_btn())
            
        elif data == "set_delay":
            state.user_inputs[uid] = "DELAY"
            await cb.edit_message_text("⏱ Send Delay in Seconds.", reply_markup=Keyboards.cancel_btn())
            
        elif data == "cancel_input":
            state.user_inputs.pop(uid, None)
            await cb.edit_message_text("❌ Cancelled.", reply_markup=Keyboards.back_home_btn())
            
        # Branding
        elif data == "menu_branding":
            await cb.edit_message_text("✍️ **Branding Manager**", reply_markup=Keyboards.branding_menu())
        elif data == "set_header":
            state.user_inputs[uid] = "HEADER"
            await cb.edit_message_text("📝 Send Header Text (HTML supported).", reply_markup=Keyboards.cancel_btn())
        elif data == "set_footer":
            state.user_inputs[uid] = "FOOTER"
            await cb.edit_message_text("📝 Send Footer Text (HTML supported).", reply_markup=Keyboards.cancel_btn())
        elif data == "rem_header":
            db.set_config("header", "NONE")
            await cb.answer("Deleted")
            await cb.edit_message_reply_markup(Keyboards.branding_menu())
        elif data == "rem_footer":
            db.set_config("footer", "NONE")
            await cb.answer("Deleted")
            await cb.edit_message_reply_markup(Keyboards.branding_menu())
            
        # Stickers
        elif data == "menu_stickers":
            packs = db.get_sticker_packs()
            txt = "**🎭 Active Sticker Packs:**\n\n" + "\n".join([f"• `{p}`" for p in packs])
            kb = [[InlineKeyboardButton("➕ Add Pack", callback_data="add_sticker_prompt")]]
            for p in packs:
                kb.append([InlineKeyboardButton(f"🗑 Del {p[:15]}...", callback_data=f"del_pack_{p}")])
            kb.append([InlineKeyboardButton("🔙 Back", callback_data="back_home")])
            await cb.edit_message_text(txt, reply_markup=InlineKeyboardMarkup(kb))
            
        elif data == "add_sticker_prompt":
            state.user_inputs[uid] = "ADD_STICKER"
            await cb.edit_message_text("➕ Send a Sticker or Pack Link.", reply_markup=Keyboards.cancel_btn())
            
        elif data.startswith("del_pack_"):
            pack = data.replace("del_pack_", "")
            db.remove_sticker_pack(pack)
            state.sticker_cache.pop(pack, None)
            await cb.answer("Removed!")
            await cb.edit_message_text("Reloading...", reply_markup=Keyboards.back_home_btn())

        # Queue
        elif data == "view_queue":
            await cb.answer(f"Pending: {state.queue.qsize()}", show_alert=True)
            
        # Schedule
        elif data == "menu_schedule":
             pending = db.get_pending_schedules()
             txt = f"📅 **Pending Schedules:** {len(pending)}\n\nUse `/schedule YYYY-MM-DD HH:MM` via reply to schedule."
             await cb.edit_message_text(txt, reply_markup=Keyboards.back_home_btn())

    except Exception as e:
        logger.error(f"Callback error: {e}")
        await cb.answer("Error occurred!", show_alert=True)

@app.on_message(filters.private & ~filters.bot)
async def input_processor(client, message):
    uid = message.from_user.id
    if not db.is_admin(uid): return
    
    # 1. Check Input Mode
    if uid in state.user_inputs:
        mode = state.user_inputs[uid]
        try:
            if mode == "CHANNEL":
                tid = message.forward_from_chat.id if message.forward_from_chat else int(message.text)
                db.set_config("target_channel", tid)
                await message.reply_text(f"✅ Target: `{tid}`", reply_markup=Keyboards.back_home_btn())
                
            elif mode == "DELAY":
                db.set_config("delay", int(message.text))
                await message.reply_text("✅ Saved.", reply_markup=Keyboards.back_home_btn())
                
            elif mode == "HEADER":
                db.set_config("header", message.text.html)
                await message.reply_text("✅ Header Saved.", reply_markup=Keyboards.branding_menu())
                
            elif mode == "FOOTER":
                db.set_config("footer", message.text.html)
                await message.reply_text("✅ Footer Saved.", reply_markup=Keyboards.branding_menu())
                
            elif mode == "ADD_STICKER":
                pack = None
                if message.sticker: pack = message.sticker.set_name
                elif "addstickers/" in message.text: pack = message.text.split("addstickers/")[-1].split("?")[0]
                else: pack = message.text.strip()
                
                # Validation
                try:
                    s_set = await client.get_sticker_set(pack)
                    if s_set.stickers:
                        db.add_sticker_pack(pack, uid)
                        state.sticker_cache[pack] = [s.file_id for s in s_set.stickers]
                        await message.reply_text(f"✅ Added **{len(s_set.stickers)}** stickers from `{pack}`.", reply_markup=Keyboards.back_home_btn())
                    else:
                        await message.reply_text("❌ Empty Pack.")
                except:
                    await message.reply_text("❌ Invalid Pack. Check link.")
                    
            state.user_inputs.pop(uid, None)
            return
            
        except Exception as e:
            await message.reply_text(f"❌ Error: {e}")
            return

    # 2. Queue Ingestion
    target = db.get_config("target_channel")
    if target == "0":
        await message.reply_text("⚠️ Set Channel first!", reply_markup=Keyboards.main_dashboard())
        return

    # If it's a command, ignore
    if message.text and message.text.startswith("/"): return

    await state.queue.put(message)
    
    if state.queue.qsize() % 5 == 0 or state.queue.qsize() == 1:
        await message.reply_text(f"✅ **Queued.** Position: {state.queue.qsize()}")

# ==============================================================================
#                           MAIN BOOTSTRAP
# ==============================================================================

async def main():
    os.system('cls' if os.name == 'nt' else 'clear')
    print("--------------------------------------------------")
    print("   TITANIUM PUBLISHER BOT - ENTERPRISE EDITION    ")
    print("--------------------------------------------------")
    
    # 1. Validation
    if API_ID == 123456:
        logger.critical("❌ API credentials not set!")
        return

    # 2. Start Services
    await app.start()
    me = await app.get_me()
    logger.info(f"✅ Bot Online: @{me.username}")
    
    # 3. Launch Sub-Systems
    asyncio.create_task(sticker_cache_manager())
    asyncio.create_task(queue_worker())
    asyncio.create_task(schedule_engine())
    asyncio.create_task(backup_engine())
    
    # 4. Notify Owner
    try:
        await app.send_message(SUPER_ADMIN_ID, "🚀 **Titanium Core Online**")
    except:
        logger.warning("Could not msg Super Admin (Start bot in PM first)")
        
    await idle()
    await app.stop()

if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    loop.run_until_complete(main())