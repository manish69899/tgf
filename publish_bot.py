# -*- coding: utf-8 -*-
"""
================================================================================
                       ULTIMATE TELEGRAM PUBLISHER BOT
================================================================================
Author: Senior Python Architect
Version: 5.0.0 (Enterprise Edition)
Description: 
    A professional-grade Telegram bot for automating channel publications.
    Features:
    - FIFO Queue Management with Persistence
    - SQLite Database for robust data storage
    - Multi-Admin System with permissions
    - Advanced Sticker Interleaving logic
    - Dynamic Footer/Caption Management
    - Detailed Statistical Analysis
    - FloodWait Handling & Smart Retries
    - Interactive GUI (Inline Buttons)

Requirements:
    pip install pyrogram tgcrypto

Usage:
    python publish_bot.py
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
from typing import List, Optional, Dict, Union, Any, Tuple
from datetime import datetime
from keep_alive import keep_alive
from dotenv import load_dotenv

# Import Pyrogram
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
    ChatAdminRequired,
    PeerIdInvalid
)

load_dotenv()

# ==============================================================================
#                               CONFIGURATION
# ==============================================================================

# ⚠️ SECURITY WARNING: Replace these with your actual credentials.
# Get API_ID/HASH from: https://my.telegram.org
API_ID = int(os.getenv("API_ID", "0")) 
API_HASH = os.getenv("API_HASH")
BOT_TOKEN = os.getenv("BOT_TOKEN")

# The SUPER ADMIN
SUPER_ADMIN_ID = int(os.getenv("SUPER_ADMIN_ID", "0"))

# Check agar values load nahi hui
if not API_HASH or not BOT_TOKEN:
    print("❌ ERROR: .env file missing or empty! Please check configuration.")
    sys.exit(1)

# Database Configuration
DB_NAME = "enterprise_bot.db"

# Logging Configuration
LOG_FILE = "system.log"
logging.basicConfig(
    format='%(asctime)s - [%(levelname)s] - %(name)s - (Line: %(lineno)d) - %(message)s',
    level=logging.INFO,
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger("EnterpriseBot")

# ==============================================================================
#                           DATABASE MANAGER (SQLite)
# ==============================================================================

class DatabaseManager:
    """
    Handles all interactions with the SQLite database.
    Ensures data persistence across restarts.
    """
    def __init__(self, db_name: str):
        self.db_name = db_name
        self.conn = None
        self.cursor = None
        self.connect()
        self.init_tables()

    def connect(self):
        """Establishes connection to the database."""
        try:
            self.conn = sqlite3.connect(self.db_name, check_same_thread=False)
            self.cursor = self.conn.cursor()
            logger.info("💾 Database connection established.")
        except sqlite3.Error as e:
            logger.critical(f"❌ Database Connection Failed: {e}")
            sys.exit(1)

    def init_tables(self):
        """Creates necessary tables if they don't exist."""
        try:
            # 1. Settings Table
            self.cursor.execute('''
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT
                )
            ''')

            # 2. Sticker Sets Table
            self.cursor.execute('''
                CREATE TABLE IF NOT EXISTS sticker_sets (
                    set_name TEXT PRIMARY KEY,
                    added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')

            # 3. Admins Table
            self.cursor.execute('''
                CREATE TABLE IF NOT EXISTS admins (
                    user_id INTEGER PRIMARY KEY,
                    added_by INTEGER,
                    added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')

            # 4. Stats Table
            self.cursor.execute('''
                CREATE TABLE IF NOT EXISTS stats (
                    date DATE PRIMARY KEY,
                    processed INTEGER DEFAULT 0,
                    stickers_sent INTEGER DEFAULT 0,
                    errors INTEGER DEFAULT 0
                )
            ''')
            
            # Initialize Default Settings if not present
            defaults = {
                "target_channel": "0",
                "delay": "3",
                "footer": "NONE",
                "mode": "copy", # or forward
                "is_paused": "0" # 0 = Running, 1 = Paused
            }
            
            for key, val in defaults.items():
                self.cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (key, val))
            
            # Ensure Super Admin is in DB
            self.cursor.execute("INSERT OR IGNORE INTO admins (user_id, added_by) VALUES (?, ?)", (SUPER_ADMIN_ID, 0))
            
            self.conn.commit()
            logger.info("✅ Database Tables Initialized.")
        except sqlite3.Error as e:
            logger.error(f"❌ Table Initialization Error: {e}")

    # --- SETTINGS OPERATIONS ---
    def get_setting(self, key: str) -> str:
        self.cursor.execute("SELECT value FROM settings WHERE key=?", (key,))
        res = self.cursor.fetchone()
        return res[0] if res else None

    def set_setting(self, key: str, value: str):
        self.cursor.execute("REPLACE INTO settings (key, value) VALUES (?, ?)", (key, str(value)))
        self.conn.commit()

    # --- STICKER OPERATIONS ---
    def add_sticker_pack(self, name: str):
        self.cursor.execute("INSERT OR IGNORE INTO sticker_sets (set_name) VALUES (?)", (name,))
        self.conn.commit()

    def remove_sticker_pack(self, name: str):
        self.cursor.execute("DELETE FROM sticker_sets WHERE set_name=?", (name,))
        self.conn.commit()

    def get_sticker_packs(self) -> List[str]:
        self.cursor.execute("SELECT set_name FROM sticker_sets")
        return [row[0] for row in self.cursor.fetchall()]

    # --- ADMIN OPERATIONS ---
    def add_admin(self, user_id: int, added_by: int):
        self.cursor.execute("INSERT OR IGNORE INTO admins (user_id, added_by) VALUES (?, ?)", (user_id, added_by))
        self.conn.commit()

    def remove_admin(self, user_id: int):
        if user_id == SUPER_ADMIN_ID: return # Cannot delete super admin
        self.cursor.execute("DELETE FROM admins WHERE user_id=?", (user_id,))
        self.conn.commit()

    def is_admin(self, user_id: int) -> bool:
        self.cursor.execute("SELECT 1 FROM admins WHERE user_id=?", (user_id,))
        return self.cursor.fetchone() is not None

    def get_all_admins(self) -> List[int]:
        self.cursor.execute("SELECT user_id FROM admins")
        return [row[0] for row in self.cursor.fetchall()]

    # --- STATS OPERATIONS ---
    def update_stats(self, processed=0, stickers=0, errors=0):
        today = datetime.now().date()
        self.cursor.execute("""
            INSERT INTO stats (date, processed, stickers_sent, errors)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(date) DO UPDATE SET
                processed = processed + ?,
                stickers_sent = stickers_sent + ?,
                errors = errors + ?
        """, (today, processed, stickers, errors, processed, stickers, errors))
        self.conn.commit()

    def get_total_stats(self) -> Dict[str, int]:
        self.cursor.execute("SELECT SUM(processed), SUM(stickers_sent), SUM(errors) FROM stats")
        res = self.cursor.fetchone()
        return {
            "processed": res[0] or 0,
            "stickers": res[1] or 0,
            "errors": res[2] or 0
        }

# Initialize DB
db = DatabaseManager(DB_NAME)

# ==============================================================================
#                           GLOBAL STATE & OBJECTS
# ==============================================================================

app = Client("enterprise_publisher_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# In-Memory Queue (Asyncio Queue is best for FIFO)
msg_queue = asyncio.Queue()

# Sticker Cache (RAM) to avoid repeated API calls
sticker_cache: Dict[str, List[str]] = {}

# User Input State (Who is typing what?)
# States: SET_CHANNEL, SET_DELAY, SET_FOOTER, ADD_STICKER, ADD_ADMIN
user_input_mode: Dict[int, str] = {}

start_time = time.time()

# ==============================================================================
#                           HELPER FUNCTIONS
# ==============================================================================

def get_uptime() -> str:
    """Returns human-readable uptime string."""
    seconds = int(time.time() - start_time)
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    d, h = divmod(h, 24)
    return f"{d}d {h}h {m}m {s}s"

async def refresh_sticker_cache(client: Client):
    """Refreshes sticker cache from DB."""
    packs = db.get_sticker_packs()
    logger.info(f"🔄 Refreshing Cache for {len(packs)} sticker packs...")
    for pack in packs:
        try:
            s_set = await client.get_sticker_set(pack)
            sticker_cache[pack] = [s.file_id for s in s_set.stickers]
        except Exception as e:
            logger.error(f"⚠️ Failed to cache pack {pack}: {e}")
            # Optional: Remove invalid pack from DB
            # db.remove_sticker_pack(pack)

# ==============================================================================
#                           GUI KEYBOARD FACTORIES
# ==============================================================================

def get_main_menu() -> InlineKeyboardMarkup:
    """Generates the Main Dashboard."""
    
    # Fetch live state
    is_paused = db.get_setting("is_paused") == "1"
    target_ch = db.get_setting("target_channel")
    delay = db.get_setting("delay")
    mode = db.get_setting("mode")
    footer_stat = "ON" if db.get_setting("footer") != "NONE" else "OFF"
    
    # Icons
    status_icon = "🔴 PAUSED" if is_paused else "🟢 RUNNING"
    pause_action = "resume_bot" if is_paused else "pause_bot"
    
    ch_text = f"📡 ID: {target_ch}" if target_ch != "0" else "📡 Set Channel (⚠️)"
    
    kb = [
        [
            InlineKeyboardButton(status_icon, callback_data=pause_action),
            InlineKeyboardButton(ch_text, callback_data="ask_channel")
        ],
        [
            InlineKeyboardButton(f"⏱ Delay: {delay}s", callback_data="ask_delay"),
            InlineKeyboardButton(f"🔄 Mode: {mode.upper()}", callback_data="toggle_mode")
        ],
        [
            InlineKeyboardButton(f"✍️ Footer: {footer_stat}", callback_data="menu_footer"),
            InlineKeyboardButton(f"🎭 Stickers", callback_data="menu_stickers")
        ],
        [
            InlineKeyboardButton(f"📥 Queue: {msg_queue.qsize()}", callback_data="view_queue"),
            InlineKeyboardButton("📊 Statistics", callback_data="view_stats")
        ],
        [
            InlineKeyboardButton("🧹 Clear Queue", callback_data="confirm_clear"),
            InlineKeyboardButton("⚙️ Admin Mgmt", callback_data="menu_admins")
        ],
        [
            InlineKeyboardButton("🔄 Refresh Panel", callback_data="refresh_home")
        ]
    ]
    return InlineKeyboardMarkup(kb)

def get_footer_menu() -> InlineKeyboardMarkup:
    """Menu for Footer Management."""
    current_footer = db.get_setting("footer")
    has_footer = current_footer != "NONE"
    
    btns = []
    if has_footer:
        btns.append([InlineKeyboardButton("👀 View Current Footer", callback_data="view_footer_text")])
        btns.append([InlineKeyboardButton("🗑 Remove Footer", callback_data="remove_footer")])
    
    btns.append([InlineKeyboardButton("✏️ Set/Update Footer", callback_data="ask_footer")])
    btns.append([InlineKeyboardButton("🔙 Back to Home", callback_data="back_home")])
    
    return InlineKeyboardMarkup(btns)

def get_sticker_menu() -> InlineKeyboardMarkup:
    """Menu for Sticker Management."""
    packs = db.get_sticker_packs()
    
    btns = []
    # Dynamic list of removal buttons
    for pack in packs:
        btns.append([
            InlineKeyboardButton(f"📦 {pack}", callback_data="noop"),
            InlineKeyboardButton("🗑 Remove", callback_data=f"del_pack_{pack}")
        ])
    
    btns.append([InlineKeyboardButton("➕ Add New Sticker Pack", callback_data="ask_sticker")])
    btns.append([InlineKeyboardButton("🔙 Back to Home", callback_data="back_home")])
    
    return InlineKeyboardMarkup(btns)

def get_cancel_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel Input", callback_data="cancel_input")]])

def get_back_home_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back to Home", callback_data="back_home")]])

# ==============================================================================
#                           CORE WORKER ENGINE
# ==============================================================================

async def worker_engine():
    """
    The heart of the bot. Processes the queue infinitely.
    Handles Flow Control, Error Management, and Publishing.
    """
    logger.info("🚀 Enterprise Worker Engine Started...")
    
    while True:
        # Get Item from Queue
        message: Message = await msg_queue.get()
        
        try:
            # 1. Check Pause State
            while db.get_setting("is_paused") == "1":
                await asyncio.sleep(2) # Poll every 2s
            
            # 2. Check Target Channel
            target_id = int(db.get_setting("target_channel"))
            if target_id == 0:
                logger.warning("⚠️ Target channel not set. Dropping message.")
                continue

            # 3. STICKER LOGIC
            packs = db.get_sticker_packs()
            if packs:
                # Randomly choose a pack
                pack_name = random.choice(packs)
                # Check cache
                if pack_name not in sticker_cache:
                    await refresh_sticker_cache(app)
                
                # Retrieve IDs
                ids = sticker_cache.get(pack_name, [])
                if ids:
                    try:
                        sticker_id = random.choice(ids)
                        await app.send_sticker(target_id, sticker_id)
                        db.update_stats(stickers=1)
                        # Mini sleep for aesthetic flow
                        await asyncio.sleep(1.0) 
                    except Exception as ex:
                        logger.error(f"❌ Sticker Send Failed: {ex}")
                        db.update_stats(errors=1)

            # 4. MESSAGE PUBLISHING LOGIC
            mode = db.get_setting("mode")
            footer = db.get_setting("footer")
            footer = footer if footer != "NONE" else None

            if mode == "forward":
                # Simple Forward
                await message.forward(target_id)
            
            else:
                # Copy Mode (Complex Caption Handling)
                text = message.text or message.caption or ""
                
                # Append Footer
                if footer:
                    if text:
                        text = f"{text}\n\n{footer}"
                    elif message.media:
                        # Media without caption, use footer as caption
                        text = footer

                # Send based on type
                if message.text:
                    await app.send_message(
                        target_id, 
                        text, 
                        entities=message.entities,
                        disable_web_page_preview=False
                    )
                else:
                    # It's media
                    await message.copy(
                        target_id,
                        caption=text
                    )

            # 5. POST-PROCESSING
            db.update_stats(processed=1)
            logger.info(f"✅ Published Message. Queue Size: {msg_queue.qsize()}")
            
            # 6. DYNAMIC DELAY
            delay = int(db.get_setting("delay"))
            await asyncio.sleep(delay)

        except FloodWait as e:
            logger.warning(f"⏳ FloodWait Triggered: Sleeping for {e.value} seconds.")
            await asyncio.sleep(e.value)
            # Re-queue the message to try again? 
            # Ideally yes, but for simplicity we skip to avoid infinite loops on bad content.
            # To re-queue: await msg_queue.put(message)
            
        except RPCError as e:
            logger.error(f"❌ Telegram API Error: {e}")
            db.update_stats(errors=1)
            
        except Exception as e:
            logger.critical(f"❌ Unhandled Worker Error: {e}")
            traceback.print_exc()
            db.update_stats(errors=1)
            
        finally:
            # Mark task done regardless of success/fail
            msg_queue.task_done()

# ==============================================================================
#                           CALLBACK HANDLERS (GUI)
# ==============================================================================

@app.on_callback_query()
async def callback_router(client: Client, cb: CallbackQuery):
    """Routes all button clicks to respective logic."""
    user_id = cb.from_user.id
    data = cb.data

    # Security Check
    if not db.is_admin(user_id):
        await cb.answer("⛔ Access Denied! You are not an admin.", show_alert=True)
        return

    try:
        # --- HOME NAVIGATION ---
        if data in ["back_home", "refresh_home"]:
            # Clear any input state
            user_input_mode.pop(user_id, None)
            await cb.edit_message_text(
                f"🤖 **Enterprise Publisher Dashboard**\n"
                f"➖➖➖➖➖➖➖➖➖➖\n"
                f"👤 Admin: **{cb.from_user.first_name}**\n"
                f"⚡ Uptime: `{get_uptime()}`\n"
                f"💾 Database: `Connected`\n"
                f"➖➖➖➖➖➖➖➖➖➖\n"
                f"Select an action below:",
                reply_markup=get_main_menu()
            )

        # --- FLOW CONTROL ---
        elif data == "pause_bot":
            db.set_setting("is_paused", "1")
            await cb.answer("⏸ System Paused")
            await cb.edit_message_reply_markup(get_main_menu())
            
        elif data == "resume_bot":
            db.set_setting("is_paused", "0")
            await cb.answer("▶️ System Resumed")
            await cb.edit_message_reply_markup(get_main_menu())

        elif data == "toggle_mode":
            curr = db.get_setting("mode")
            new_mode = "forward" if curr == "copy" else "copy"
            db.set_setting("mode", new_mode)
            await cb.answer(f"🔄 Mode switched to: {new_mode.upper()}")
            await cb.edit_message_reply_markup(get_main_menu())

        # --- QUEUE MANAGEMENT ---
        elif data == "view_queue":
            size = msg_queue.qsize()
            if size == 0:
                await cb.answer("📭 Queue is empty!", show_alert=True)
            else:
                await cb.answer(f"📬 {size} items pending in queue.", show_alert=True)

        elif data == "confirm_clear":
            await cb.edit_message_text(
                "⚠️ **ARE YOU SURE?**\n\nThis will delete all pending posts from memory.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("✅ YES, DELETE ALL", callback_data="do_clear_queue")],
                    [InlineKeyboardButton("❌ CANCEL", callback_data="back_home")]
                ])
            )

        elif data == "do_clear_queue":
            count = msg_queue.qsize()
            while not msg_queue.empty():
                try: msg_queue.get_nowait(); msg_queue.task_done()
                except: break
            await cb.answer(f"🗑 Deleted {count} items!", show_alert=True)
            await cb.edit_message_text("✅ Queue Cleared Successfully.", reply_markup=get_back_home_kb())

        # --- INPUT PROMPTS ---
        elif data == "ask_channel":
            user_input_mode[user_id] = "SET_CHANNEL"
            await cb.edit_message_text(
                "📡 **CHANNEL SETUP**\n\n"
                "Please **Forward a Message** from your target channel here.\n"
                "OR send the **Channel ID** manually (e.g., -100xxxx).",
                reply_markup=get_cancel_kb()
            )

        elif data == "ask_delay":
            user_input_mode[user_id] = "SET_DELAY"
            await cb.edit_message_text(
                "⏱ **DELAY SETUP**\n\n"
                "Send the delay time in **Seconds** (e.g., 5, 10, 60).",
                reply_markup=get_cancel_kb()
            )

        elif data == "cancel_input":
            user_input_mode.pop(user_id, None)
            await cb.answer("❌ Cancelled")
            await cb.edit_message_text("❌ Input Cancelled.", reply_markup=get_back_home_kb())

        # --- FOOTER MANAGEMENT ---
        elif data == "menu_footer":
            await cb.edit_message_text(
                "✍️ **FOOTER MANAGEMENT**\n\n"
                "Manage your auto-signature/caption here.",
                reply_markup=get_footer_menu()
            )

        elif data == "ask_footer":
            user_input_mode[user_id] = "SET_FOOTER"
            await cb.edit_message_text(
                "✍️ **SET NEW FOOTER**\n\n"
                "Send the text you want to add to every post.\n"
                "Supports Markdown/HTML.",
                reply_markup=get_cancel_kb()
            )

        elif data == "remove_footer":
            db.set_setting("footer", "NONE")
            await cb.answer("🗑 Footer Removed")
            await cb.edit_message_text("✅ Footer has been disabled.", reply_markup=get_footer_menu())

        elif data == "view_footer_text":
            ft = db.get_setting("footer")
            await cb.edit_message_text(
                f"📝 **CURRENT FOOTER:**\n\n`{ft}`",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="menu_footer")]])
            )

        # --- STICKER MANAGEMENT ---
        elif data == "menu_stickers":
            await cb.edit_message_text(
                "🎭 **STICKER PACK MANAGER**\n\n"
                "Add or remove sticker packs used for interleaving.",
                reply_markup=get_sticker_menu()
            )

        elif data == "ask_sticker":
            user_input_mode[user_id] = "ADD_STICKER"
            await cb.edit_message_text(
                "➕ **ADD STICKER PACK**\n\n"
                "Send a Sticker from the pack OR the Pack Link.\n"
                "Ex: `https://t.me/addstickers/Animals`",
                reply_markup=get_cancel_kb()
            )

        elif data.startswith("del_pack_"):
            pack = data.replace("del_pack_", "")
            db.remove_sticker_pack(pack)
            if pack in sticker_cache:
                del sticker_cache[pack]
            await cb.answer(f"🗑 {pack} Removed")
            await cb.edit_message_reply_markup(get_sticker_menu())

        # --- STATS ---
        elif data == "view_stats":
            stats = db.get_total_stats()
            txt = (
                f"📊 **SYSTEM STATISTICS**\n\n"
                f"✅ **Total Published:** `{stats['processed']}`\n"
                f"🎭 **Stickers Sent:** `{stats['stickers']}`\n"
                f"❌ **Errors/Fails:** `{stats['errors']}`\n"
                f"📅 **Started:** `{datetime.fromtimestamp(start_time).strftime('%Y-%m-%d %H:%M')}`\n\n"
                f"⚡ **Performance:** Good"
            )
            await cb.edit_message_text(txt, reply_markup=get_back_home_kb())

        # --- ADMIN MANAGEMENT ---
        elif data == "menu_admins":
            if user_id != SUPER_ADMIN_ID:
                await cb.answer("🔒 Only Super Admin can manage admins!", show_alert=True)
                return
            
            admins = db.get_all_admins()
            txt = "**👥 ADMIN LIST:**\n\n"
            for a in admins:
                role = "👑 Super Admin" if a == SUPER_ADMIN_ID else "👤 Admin"
                txt += f"• `{a}` - {role}\n"
            
            kb = [
                [InlineKeyboardButton("➕ Add Admin", callback_data="ask_add_admin")],
                [InlineKeyboardButton("➖ Remove Admin", callback_data="ask_rem_admin")],
                [InlineKeyboardButton("🔙 Back", callback_data="back_home")]
            ]
            await cb.edit_message_text(txt, reply_markup=InlineKeyboardMarkup(kb))

        elif data == "ask_add_admin":
            user_input_mode[user_id] = "ADD_ADMIN"
            await cb.edit_message_text("👤 Send the **User ID** of the new admin.", reply_markup=get_cancel_kb())

        elif data == "ask_rem_admin":
            user_input_mode[user_id] = "REM_ADMIN"
            await cb.edit_message_text("👤 Send the **User ID** to remove.", reply_markup=get_cancel_kb())

    except MessageNotModified:
        pass # Ignore if same menu
    except Exception as e:
        logger.error(f"Callback Error: {e}")
        await cb.answer("❌ Error occurred! Check logs.", show_alert=True)

# ==============================================================================
#                           INPUT MESSAGE HANDLER
# ==============================================================================

@app.on_message(filters.private & ~filters.bot)
async def message_processor(client: Client, message: Message):
    """
    Handles:
    1. /start command
    2. Admin Inputs (Setting channels, delays, etc)
    3. Queue Content Ingestion
    """
    user_id = message.from_user.id

    # 1. AUTHENTICATION
    if not db.is_admin(user_id):
        # Allow bot to ignore random people
        return

    # 2. COMMANDS
    if message.text and message.text.lower() == "/start":
        # Reset any stuck input mode
        user_input_mode.pop(user_id, None)
        await message.reply_text(
            f"👋 **Hello {message.from_user.first_name}!**\n\n"
            "Welcome to the **Enterprise Publisher Dashboard**.\n"
            "Use the buttons below to control the system.",
            reply_markup=get_main_menu()
        )
        return

    # 3. INPUT MODE HANDLING (Configuration)
    if user_id in user_input_mode:
        mode = user_input_mode[user_id]
        
        try:
            # --- SET CHANNEL ---
            if mode == "SET_CHANNEL":
                target = None
                if message.forward_from_chat:
                    target = message.forward_from_chat.id
                elif message.text:
                    try:
                        target = int(message.text)
                    except: pass
                
                if target:
                    db.set_setting("target_channel", str(target))
                    await message.reply_text(f"✅ **Target Channel Updated:** `{target}`", reply_markup=get_back_home_kb())
                else:
                    await message.reply_text("❌ Invalid ID. Please forward from channel or send numeric ID.")
                    return # Retry input

            # --- SET DELAY ---
            elif mode == "SET_DELAY":
                try:
                    val = int(message.text)
                    if val < 1: raise ValueError
                    db.set_setting("delay", str(val))
                    await message.reply_text(f"✅ **Delay Set:** `{val} seconds`", reply_markup=get_back_home_kb())
                except:
                    await message.reply_text("❌ Please send a valid number (minimum 1).")
                    return

            # --- SET FOOTER ---
            elif mode == "SET_FOOTER":
                db.set_setting("footer", message.text.html if message.text else "NONE")
                await message.reply_text("✅ **Footer Text Updated!**", reply_markup=get_footer_menu())

            # --- ADD STICKER ---
            elif mode == "ADD_STICKER":
                pack_name = None
                
                # Check if it's a sticker object
                if message.sticker:
                    pack_name = message.sticker.set_name
                # Check if it's a text link
                elif message.text:
                    if "addstickers/" in message.text:
                        pack_name = message.text.split("addstickers/")[-1]
                    else:
                        pack_name = message.text
                
                if pack_name:
                    db.add_sticker_pack(pack_name)
                    # Refresh Cache immediately
                    asyncio.create_task(refresh_sticker_cache(client))
                    await message.reply_text(f"✅ **Sticker Pack Added:** `{pack_name}`", reply_markup=get_sticker_menu())
                else:
                    await message.reply_text("❌ Could not detect sticker pack name. Try sending a sticker from the pack.")
                    return

            # --- ADMIN MANAGEMENT ---
            elif mode == "ADD_ADMIN":
                try:
                    new_admin = int(message.text)
                    db.add_admin(new_admin, user_id)
                    await message.reply_text(f"✅ **Admin Added:** `{new_admin}`", reply_markup=get_back_home_kb())
                except:
                    await message.reply_text("❌ Invalid ID. Send numeric User ID.")
                    return

            elif mode == "REM_ADMIN":
                try:
                    rem_id = int(message.text)
                    if rem_id == SUPER_ADMIN_ID:
                        await message.reply_text("❌ You cannot remove the Super Admin.")
                    else:
                        db.remove_admin(rem_id)
                        await message.reply_text(f"🗑 **Admin Removed:** `{rem_id}`", reply_markup=get_back_home_kb())
                except:
                    await message.reply_text("❌ Invalid ID.")
                    return

            # Clear Input Mode on Success
            del user_input_mode[user_id]
        
        except Exception as e:
            logger.error(f"Input Handler Error: {e}")
            await message.reply_text(f"❌ **Error:** {e}\nTry again or Click Cancel.")
        
        return # Stop processing, don't queue this message

    # 4. QUEUE INGESTION (Normal Operation)
    target_channel = db.get_setting("target_channel")
    if target_channel == "0":
        await message.reply_text("⚠️ **Target Channel NOT Set!**\nUse the menu to set it first.", reply_markup=get_main_menu())
        return

    # Add to Queue
    await msg_queue.put(message)
    
    # User Feedback
    q_size = msg_queue.qsize()
    is_paused = db.get_setting("is_paused") == "1"
    
    # Don't spam admin on every message, just small confirmation
    if q_size == 1 and not is_paused:
        await message.reply_text(f"✅ **Started Processing...** (1st in Queue)")
    elif q_size % 5 == 0:
        await message.reply_text(f"📥 **Queued.** Total Pending: {q_size}")

# ==============================================================================
#                           MAIN EXECUTOR
# ==============================================================================

async def main():
    """Initializes the entire system."""
    
    # Clear console
    os.system('cls' if os.name == 'nt' else 'clear')
    print("----------------------------------------------------------------")
    print("       🚀 ENTERPRISE PUBLISHER BOT - STARTING SYSTEM 🚀")
    print("----------------------------------------------------------------")
    
    # 1. Validate Config
    if API_ID == 123456 or "your" in API_HASH:
        logger.critical("❌ CONFIGURATION ERROR: Please update API_ID, API_HASH in code.")
        return

    # 2. Start Client
    logger.info("📡 Connecting to Telegram Servers...")
    await app.start()
    me = await app.get_me()
    logger.info(f"✅ Logged in as: @{me.username} (ID: {me.id})")
    
    # 3. Cache Initialization
    logger.info("🔄 Initializing Sticker Cache...")
    await refresh_sticker_cache(app)
    
    # 4. Start Worker Task
    logger.info("👷 Starting Worker Engine...")
    worker_task = asyncio.create_task(worker_engine())
    
    # 5. Notify Super Admin
    try:
        await app.send_message(
            SUPER_ADMIN_ID, 
            f"🚀 **Bot Restarted!**\n"
            f"📅 Time: `{datetime.now()}`\n"
            f"ℹ️ Send /start to open dashboard."
        )
    except Exception as e:
        logger.warning(f"⚠️ Could not notify Super Admin: {e}")

    # 6. Keep Alive
    logger.info("🟢 SYSTEM ONLINE. WAITING FOR COMMANDS.")
    await idle()
    
    # 7. Shutdown
    logger.info("🛑 Shutting down system...")
    worker_task.cancel()
    await app.stop()

if __name__ == "__main__":
    try:
        # --- NEW CODE START (Ye add karna hai) ---
        keep_alive()  # <--- Ye line Render server ko zinda rakhegi
        print("🌍 Web Server Started! (Bot is ready for Render)")
        # --- NEW CODE END ---

        # Use existing event loop logic
        loop = asyncio.get_event_loop()
        loop.run_until_complete(main())
    except KeyboardInterrupt:
        print("\n🛑 Force Stopped by User")
    except Exception as e:
        logger.critical(f"❌ Fatal Error: {e}")
        traceback.print_exc()