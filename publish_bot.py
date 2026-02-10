# -*- coding: utf-8 -*-
"""
================================================================================
                       ULTIMATE TELEGRAM PUBLISHER BOT
================================================================================
Author: Senior Python Architect
Version: 5.2.0 (Enterprise Edition - Storage Optimized)
Description: 
    A professional-grade Telegram bot for automating channel publications.
    
    🔥 NEW ENTERPRISE FEATURES:
    - 🛡️ Rotating Logs: Prevents storage overflow on Render (Auto-cleans logs).
    - 📂 Smart Directory: Auto-creates 'downloads' folder for easy cleanup.
    - ♻️ Resilience: Prepared for Auto-Restore mechanics.
    - 🚀 FIFO Queue with Priority Injection support.

Requirements:
    pip install pyrogram tgcrypto flask python-dotenv

Usage:
    python publish_bot.py
================================================================================
"""

import asyncio
import logging
from logging.handlers import RotatingFileHandler # <-- NEW: Auto-deletes old logs
import sqlite3
import os
import shutil # <-- NEW: For deleting folders/files easily
import time
import random
import sys
import traceback
from datetime import datetime
from typing import List, Optional, Dict, Union, Any, Tuple

# --- Third Party Imports ---
from dotenv import load_dotenv
from pyrogram import Client, filters, idle, enums
from pyrogram.raw import functions, types
from pyrogram.types import (
    Message, 
    InlineKeyboardMarkup, 
    InlineKeyboardButton, 
    CallbackQuery,
    InputMediaPhoto,
    InputMediaVideo,
    InputMediaDocument,
    BotCommand  # <--- YE ADD KIYA HAI
)
from pyrogram.errors import (
    FloodWait, 
    RPCError, 
    MessageNotModified, 
    ChatAdminRequired,
    PeerIdInvalid
)

# --- Local Imports ---
from keep_alive import keep_alive

# 1. Load Environment Variables (Sabse Pehle)
load_dotenv()

# ==============================================================================
#                               CONFIGURATION
# ==============================================================================

# ⚠️ SECURITY WARNING: Replace these with your actual credentials in .env file
try:
    API_ID = int(os.getenv("API_ID", "0")) 
    API_HASH = os.getenv("API_HASH")
    BOT_TOKEN = os.getenv("BOT_TOKEN")
    SUPER_ADMIN_ID = int(os.getenv("SUPER_ADMIN_ID", "0"))
except ValueError:
    print("❌ ERROR: API_ID or SUPER_ADMIN_ID must be integers in .env file.")
    sys.exit(1)

# Critical Check
if not API_HASH or not BOT_TOKEN or API_ID == 0:
    print("❌ CRITICAL ERROR: .env file is missing or variables are empty!")
    sys.exit(1)

# --- 📂 STORAGE MANAGEMENT (Render Free Tier Optimization) ---
DB_NAME = "enterprise_bot.db"
LOG_FILE = "system.log"
DOWNLOAD_PATH = "downloads/" # <-- NEW: Dedicated folder for temp files

# Create downloads folder if not exists
if not os.path.exists(DOWNLOAD_PATH):
    os.makedirs(DOWNLOAD_PATH)

# --- 📝 LOGGING CONFIGURATION (Space Saver) ---
# RotatingFileHandler:
# - maxBytes=5MB: File 5MB se badi nahi hogi.
# - backupCount=1: Sirf 1 purani file rakhega, baaki delete kar dega.
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - [%(levelname)s] - %(name)s - %(message)s',
    handlers=[
        RotatingFileHandler(LOG_FILE, maxBytes=5*1024*1024, backupCount=1), 
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger("EnterpriseBot")

# Suppress noisy logs from Pyrogram (Clean Console)
logging.getLogger("pyrogram").setLevel(logging.WARNING)


# ==============================================================================
#                           DATABASE MANAGER (SQLite - Enterprise)
# ==============================================================================

class DatabaseManager:
    """
    Handles all interactions with the SQLite database.
    Features: WAL Mode (High Speed), Smart Defaults, and Thread Safety.
    """
    def __init__(self, db_name: str):
        self.db_name = db_name
        self.conn = None
        self.cursor = None
        self.connect()
        self.init_tables()

    def connect(self):
        """
        Establishes a high-performance connection.
        Enables Write-Ahead Logging (WAL) for concurrency.
        """
        try:
            self.conn = sqlite3.connect(
                self.db_name, 
                check_same_thread=False, 
                timeout=30.0
            )
            # Row Factory allows accessing columns by name (More professional)
            self.conn.row_factory = sqlite3.Row 
            self.cursor = self.conn.cursor()
            
            # 🔥 PERFORMANCE HACK: Enable WAL Mode (Faster, No Locking)
            self.cursor.execute("PRAGMA journal_mode=WAL;")
            self.cursor.execute("PRAGMA synchronous=NORMAL;")
            
            logger.info("💾 Database Connected (WAL Mode Enabled).")
        except sqlite3.Error as e:
            logger.critical(f"❌ Critical Database Connection Failed: {e}")
            sys.exit(1)

    def init_tables(self):
        """Creates necessary tables with NEW Smart Settings."""
        try:
            # 1. Settings Table
            self.cursor.execute('''
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT
                )
            ''')

            # 2. Sticker Sets (For Random Mode)
            self.cursor.execute('''
                CREATE TABLE IF NOT EXISTS sticker_sets (
                    set_name TEXT PRIMARY KEY,
                    added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')

            # 3. Admins (For Permission)
            self.cursor.execute('''
                CREATE TABLE IF NOT EXISTS admins (
                    user_id INTEGER PRIMARY KEY,
                    added_by INTEGER,
                    added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')

            # 4. Stats (Analytics)
            self.cursor.execute('''
                CREATE TABLE IF NOT EXISTS stats (
                    date DATE PRIMARY KEY,
                    processed INTEGER DEFAULT 0,
                    stickers_sent INTEGER DEFAULT 0,
                    errors INTEGER DEFAULT 0
                )
            ''')
            
            # --- 💉 INJECTING NEW SMART DEFAULTS ---
            defaults = {
                # Basic
                "target_channel": "0",
                "delay": "30",              # Safe delay
                "footer": "NONE",
                "mode": "copy",             # copy (No Tag) / forward (With Tag)
                "is_paused": "0",
                
                # 🎭 Advanced Sticker Controls
                "sticker_state": "ON",      # ON / OFF
                "sticker_mode": "RANDOM",   # RANDOM / SINGLE
                "single_sticker_id": "",    # File ID for single mode
                "sticker_pack_link": "",    # Backup link
                
                # 🧹 Smart Features
                "caption_cleaner": "OFF",   # Removes links/@ from captions
            }
            
            for key, val in defaults.items():
                self.cursor.execute(
                    "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", 
                    (key, val)
                )
            
            # Ensure Super Admin Access
            self.cursor.execute(
                "INSERT OR IGNORE INTO admins (user_id, added_by) VALUES (?, ?)", 
                (SUPER_ADMIN_ID, 0)
            )
            
            self.conn.commit()
            logger.info("✅ Database Tables & Smart Settings Ready.")
            
        except sqlite3.Error as e:
            logger.critical(f"❌ Table Initialization Error: {e}")
            sys.exit(1)

    # ========================== SETTINGS OPERATIONS ==========================

    def get_setting(self, key: str, default: str = None) -> str:
        try:
            self.cursor.execute("SELECT value FROM settings WHERE key=?", (key,))
            res = self.cursor.fetchone()
            return res['value'] if res else default
        except sqlite3.Error:
            return default

    def set_setting(self, key: str, value: str):
        try:
            self.cursor.execute(
                "REPLACE INTO settings (key, value) VALUES (?, ?)", 
                (key, str(value))
            )
            self.conn.commit()
        except sqlite3.Error as e:
            logger.error(f"⚠️ Setting Save Error: {e}")

    # ========================== STICKER OPERATIONS ==========================

    def add_sticker_pack(self, name: str):
        self.cursor.execute("INSERT OR IGNORE INTO sticker_sets (set_name) VALUES (?)", (name,))
        self.conn.commit()

    def remove_sticker_pack(self, name: str):
        self.cursor.execute("DELETE FROM sticker_sets WHERE set_name=?", (name,))
        self.conn.commit()

    def get_sticker_packs(self) -> List[str]:
        self.cursor.execute("SELECT set_name FROM sticker_sets")
        return [row['set_name'] for row in self.cursor.fetchall()]

    # ========================== ADMIN OPERATIONS ==========================

    def add_admin(self, user_id: int, added_by: int):
        self.cursor.execute(
            "INSERT OR IGNORE INTO admins (user_id, added_by) VALUES (?, ?)", 
            (user_id, added_by)
        )
        self.conn.commit()

    def remove_admin(self, user_id: int):
        # 🛡️ SECURITY: Prevent removing Super Admin via Code
        if user_id == SUPER_ADMIN_ID:
            logger.warning("⚠️ Attempted to remove Super Admin. Blocked.")
            return 
        self.cursor.execute("DELETE FROM admins WHERE user_id=?", (user_id,))
        self.conn.commit()

    def is_admin(self, user_id: int) -> bool:
        if user_id == SUPER_ADMIN_ID: return True
        self.cursor.execute("SELECT 1 FROM admins WHERE user_id=?", (user_id,))
        return self.cursor.fetchone() is not None

    def get_all_admins(self) -> List[int]:
        self.cursor.execute("SELECT user_id FROM admins")
        return [row['user_id'] for row in self.cursor.fetchall()]

    # ========================== STATS OPERATIONS ==========================

    def update_stats(self, processed=0, stickers=0, errors=0):
        try:
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
        except sqlite3.Error:
            pass

    def get_total_stats(self) -> Dict[str, int]:
        try:
            self.cursor.execute("SELECT SUM(processed), SUM(stickers_sent), SUM(errors) FROM stats")
            res = self.cursor.fetchone()
            return {
                "processed": res[0] or 0,
                "stickers": res[1] or 0,
                "errors": res[2] or 0
            }
        except:
            return {"processed": 0, "stickers": 0, "errors": 0}



# ========================== SETTINGS OPERATIONS ==========================

    def get_setting(self, key: str, default: str = None) -> str:
        """
        Retrieves a setting safely. 
        Returns 'default' if key not found or error occurs.
        """
        try:
            self.cursor.execute("SELECT value FROM settings WHERE key=?", (key,))
            res = self.cursor.fetchone()
            # Handle both Tuple and Row objects safely
            if res:
                return res[0] if isinstance(res, tuple) else res['value']
            return default
        except sqlite3.Error as e:
            # logger.error(f"⚠️ DB Read Error (get_setting): {e}")
            return default

    def set_setting(self, key: str, value: str):
        """Updates or Inserts a setting immediately."""
        try:
            self.cursor.execute(
                "REPLACE INTO settings (key, value) VALUES (?, ?)", 
                (key, str(value))
            )
            self.conn.commit()
        except sqlite3.Error as e:
            logger.error(f"⚠️ DB Write Error (set_setting): {e}")

    # ========================== STICKER OPERATIONS ==========================

    def add_sticker_pack(self, name: str):
        """Adds a sticker pack link to the rotation list."""
        try:
            self.cursor.execute("INSERT OR IGNORE INTO sticker_sets (set_name) VALUES (?)", (name,))
            self.conn.commit()
        except sqlite3.Error:
            pass

    def remove_sticker_pack(self, name: str):
        """Removes a sticker pack from rotation."""
        try:
            self.cursor.execute("DELETE FROM sticker_sets WHERE set_name=?", (name,))
            self.conn.commit()
        except sqlite3.Error:
            pass

    def get_sticker_packs(self) -> List[str]:
        """Returns a list of all saved sticker pack names/links."""
        try:
            self.cursor.execute("SELECT set_name FROM sticker_sets")
            rows = self.cursor.fetchall()
            # Handle both Tuple and Row objects
            if rows and isinstance(rows[0], tuple):
                return [row[0] for row in rows]
            return [row['set_name'] for row in rows]
        except sqlite3.Error:
            return []

    # ========================== ADMIN OPERATIONS ==========================

    def add_admin(self, user_id: int, added_by: int):
        """Authorizes a new user as an admin."""
        try:
            self.cursor.execute(
                "INSERT OR IGNORE INTO admins (user_id, added_by) VALUES (?, ?)", 
                (user_id, added_by)
            )
            self.conn.commit()
        except sqlite3.Error as e:
            logger.error(f"❌ Add Admin Error: {e}")

    def remove_admin(self, user_id: int):
        """Revokes admin access (Super Admin is protected)."""
        if user_id == SUPER_ADMIN_ID:
            logger.warning("🛡️ Security Alert: Attempt to remove Super Admin blocked.")
            return 
        try:
            self.cursor.execute("DELETE FROM admins WHERE user_id=?", (user_id,))
            self.conn.commit()
        except sqlite3.Error as e:
            logger.error(f"❌ Remove Admin Error: {e}")

    def is_admin(self, user_id: int) -> bool:
        """Checks if a user is an admin or super admin."""
        if user_id == SUPER_ADMIN_ID:
            return True
        try:
            self.cursor.execute("SELECT 1 FROM admins WHERE user_id=?", (user_id,))
            return self.cursor.fetchone() is not None
        except sqlite3.Error:
            return False

    def get_all_admins(self) -> List[int]:
        """Returns list of all admin IDs."""
        try:
            self.cursor.execute("SELECT user_id FROM admins")
            rows = self.cursor.fetchall()
            if rows and isinstance(rows[0], tuple):
                return [row[0] for row in rows]
            return [row['user_id'] for row in rows]
        except sqlite3.Error:
            return []

    # ========================== STATS OPERATIONS ==========================

    def update_stats(self, processed=0, stickers=0, errors=0):
        """
        Updates daily statistics safely using UPSERT logic.
        """
        try:
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
        except sqlite3.Error:
            # Stats failures are non-critical, so we ignore to keep bot running
            pass

    def get_total_stats(self) -> Dict[str, int]:
        """Aggregates all-time stats."""
        try:
            self.cursor.execute("SELECT SUM(processed), SUM(stickers_sent), SUM(errors) FROM stats")
            res = self.cursor.fetchone()
            
            # Safe unpacking
            proc = res[0] if res and res[0] else 0
            stik = res[1] if res and res[1] else 0
            errs = res[2] if res and res[2] else 0
            
            return {
                "processed": proc,
                "stickers": stik,
                "errors": errs
            }
        except sqlite3.Error:
            return {"processed": 0, "stickers": 0, "errors": 0}

# Initialize DB (Global Instance)
db = DatabaseManager(DB_NAME)

# ==============================================================================
#                           GLOBAL STATE & OBJECTS
# ==============================================================================

# 1. Telegram Client Initialization
# Using 'enterprise_publisher_bot' session file
# This creates a persistent session, so login is only needed once.
app = Client(
    "enterprise_publisher_bot", 
    api_id=API_ID, 
    api_hash=API_HASH, 
    bot_token=BOT_TOKEN
)

# 2. In-Memory Message Queues (Smart Priority System)
# ---------------------------------------------------
# 🔥 VIP Queue: Urgent/Breaking News (Processed FIRST)
vip_queue = asyncio.Queue()

# 📥 Normal Queue: Standard Posts (Processed FIFO)
msg_queue = asyncio.Queue()

# 3. User Input State (RAM)
# Track karta hai ki kaun user abhi kya set kar raha hai.
# Example: {12345: "SET_CHANNEL", 67890: "ADD_STICKER"}
user_input_mode: Dict[int, str] = {}

# 4. System Start Time (For Uptime Calculation)
# Used in the Dashboard to show how long the bot has been running.
start_time = time.time()

# 5. Smart Album Tracking (For Sticker Logic)
# Stores the 'media_group_id' of the last processed message.
# Used to prevent spamming stickers in the middle of an album.
last_processed_album_id = None 

# Note: Sticker Cache removed to save RAM on Render Free Tier.
# We fetch stickers dynamically using Raw API.

# ==============================================================================
#                           HELPER FUNCTIONS
# ==============================================================================

def get_uptime() -> str:
    """
    Returns a human-readable uptime string (e.g., '2d 4h 30m 15s').
    Includes safety check for start_time variable.
    """
    try:
        # Calculate total seconds
        # 'start_time' must be defined in Global State
        if 'start_time' not in globals():
            return "0d 0h 0m 0s"
            
        seconds = int(time.time() - start_time)
        
        # Calculate Days, Hours, Minutes, Seconds
        m, s = divmod(seconds, 60)
        h, m = divmod(m, 60)
        d, h = divmod(h, 24)
        
        # Return formatted string
        return f"{d}d {h}h {m}m {s}s"
    except Exception as e:
        logger.error(f"⚠️ Uptime Error: {e}")
        return "0d 0h 0m 0s"
        
# ==============================================================================
#                           GUI KEYBOARD FACTORIES
# ==============================================================================

def get_main_menu() -> InlineKeyboardMarkup:
    """
    Generates the Main Dashboard with live status.
    Reflects the new 'Smart Mode' and 'Sticker Status'.
    """
    try:
        # 1. Fetch live state from DB
        is_paused = db.get_setting("is_paused", "0") == "1"
        target_ch = db.get_setting("target_channel", "0")
        delay = db.get_setting("delay", "30")
        
        # Mode Logic: Forward (Tag) vs Copy (No Tag)
        mode = db.get_setting("mode", "copy") 
        mode_display = "⏩ Forward (Tag)" if mode == "forward" else "©️ Copy (No Tag)"
        
        # Sticker Status
        st_state = db.get_setting("sticker_state", "ON")
        st_icon = "🟢" if st_state == "ON" else "🔴"
        
        footer_val = db.get_setting("footer", "NONE")
        footer_status = "✅ ON" if footer_val != "NONE" else "❌ OFF"

        # 2. Logic for Buttons
        status_text = "🔴 SYSTEM PAUSED" if is_paused else "🟢 SYSTEM RUNNING"
        status_callback = "resume_bot" if is_paused else "pause_bot"
        
        # Channel Status
        ch_text = "⚠️ Set Target Channel" if target_ch == "0" else f"📡 ID: {target_ch}"

        # 3. Construct Keyboard (Professional Layout)
        keyboard = [
            [
                InlineKeyboardButton(status_text, callback_data=status_callback),
            ],
            [
                InlineKeyboardButton(ch_text, callback_data="ask_channel")
            ],
            [
                InlineKeyboardButton(f"⏱ Delay: {delay}s", callback_data="ask_delay"),
                InlineKeyboardButton(f"🔄 {mode_display}", callback_data="toggle_mode")
            ],
            [
                InlineKeyboardButton(f"✍️ Footer: {footer_status}", callback_data="menu_footer"),
                InlineKeyboardButton(f"🎭 Stickers: {st_state} {st_icon}", callback_data="menu_stickers")
            ],
            [
                InlineKeyboardButton(f"📥 Queue: {msg_queue.qsize()}", callback_data="view_queue"),
                InlineKeyboardButton("📊 Analytics", callback_data="view_stats")
            ],
            [
                InlineKeyboardButton("⚙️ Admin Panel", callback_data="menu_admins"),
                InlineKeyboardButton("🔄 Refresh Dashboard", callback_data="refresh_home")
            ]
        ]
        return InlineKeyboardMarkup(keyboard)
        
    except Exception as e:
        logger.error(f"❌ Menu Generation Error: {e}")
        return InlineKeyboardMarkup([[InlineKeyboardButton("🔄 Error! Click to Refresh", callback_data="refresh_home")]])

def get_sticker_menu() -> InlineKeyboardMarkup:
    """
    Advanced Sticker Control Panel.
    Allows toggling ON/OFF and switching between Random/Single modes.
    """
    # Fetch Settings
    state = db.get_setting("sticker_state", "ON")     # ON / OFF
    mode = db.get_setting("sticker_mode", "RANDOM")   # RANDOM / SINGLE
    
    # State Button Logic
    btn_state_text = "🔴 Turn OFF" if state == "ON" else "🟢 Turn ON"
    btn_state_cb = "toggle_sticker_off" if state == "ON" else "toggle_sticker_on"
    
    # Mode Button Logic
    btn_mode_text = "🎲 Mode: Random Pack" if mode == "RANDOM" else "🎯 Mode: Fixed Sticker"
    btn_mode_cb = "set_mode_single" if mode == "RANDOM" else "set_mode_random"
    
    btns = [
        [
            InlineKeyboardButton(btn_state_text, callback_data=btn_state_cb),
            InlineKeyboardButton(btn_mode_text, callback_data=btn_mode_cb)
        ],
        [
            InlineKeyboardButton("➕ Add Pack (Random)", callback_data="ask_sticker"),
            InlineKeyboardButton("🎯 Set Single Sticker", callback_data="ask_single_sticker")
        ]
    ]

    # Show Packs list only if Random Mode is Active or for management
    packs = db.get_sticker_packs()
    if packs:
        btns.append([InlineKeyboardButton(f"--- 📦 Manage {len(packs)} Packs ---", callback_data="noop")])
        for i, pack in enumerate(packs):
            if i >= 5: # Limit list to 5 to keep UI clean
                btns.append([InlineKeyboardButton(f"➕ And {len(packs)-5} more...", callback_data="noop")])
                break
            
            # Smart Name Truncation
            name = pack.split('/')[-1]
            if len(name) > 15: name = name[:12] + "..."
            
            btns.append([
                InlineKeyboardButton(f"📦 {name}", callback_data="noop"),
                InlineKeyboardButton("🗑 Del", callback_data=f"del_pack_{pack}")
            ])
            
    btns.append([InlineKeyboardButton("🔙 Back to Dashboard", callback_data="back_home")])
    return InlineKeyboardMarkup(btns)

def get_footer_menu() -> InlineKeyboardMarkup:
    """Sub-menu for Footer Management."""
    current_footer = db.get_setting("footer", "NONE")
    has_footer = current_footer != "NONE"
    
    btns = []
    if has_footer:
        btns.append([InlineKeyboardButton("👀 View Current Footer", callback_data="view_footer_text")])
        btns.append([InlineKeyboardButton("🗑 Remove Footer", callback_data="remove_footer")])
    
    btns.append([InlineKeyboardButton("✏️ Set/Update Footer", callback_data="ask_footer")])
    btns.append([InlineKeyboardButton("🔙 Back to Home", callback_data="back_home")])
    
    return InlineKeyboardMarkup(btns)

def get_upload_success_kb() -> InlineKeyboardMarkup:
    """
    🔥 NEW: Dikhata hai jab file queue me lag jati hai.
    User choose kar sakta hai aur file bhejna hai ya wapis menu jana hai.
    """
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📤 Send More Files", callback_data="noop")], # 'noop' means no operation (just stays in chat)
        [InlineKeyboardButton("🔙 Return to Main Menu", callback_data="refresh_home")]
    ])

def get_cancel_kb() -> InlineKeyboardMarkup:
    """Standard Cancel Button."""
    return InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel Operation", callback_data="cancel_input")]])

def get_back_home_kb() -> InlineKeyboardMarkup:
    """Standard Back Button."""
    return InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back to Dashboard", callback_data="back_home")]])




# ==============================================================================
#                           CORE WORKER ENGINE
# ==============================================================================



# ==============================================================================
#                           AUTO-BACKUP SYSTEM (With Storage Cleaner)
# ==============================================================================
async def auto_backup_task(app):
    """
    1. Sends Database Backup to Super Admin every 1 Hour.
    2. Cleans up 'downloads' folder to keep Render Free Tier alive.
    """
    logger.info("💾 Auto-Backup & Cleanup System Started...")
    
    while True:
        try:
            # ⏳ 1 Hour Interval (3600 Seconds)
            await asyncio.sleep(3600)
            
            # --- TASK 1: DATABASE BACKUP ---
            if os.path.exists(DB_NAME) and os.path.getsize(DB_NAME) > 0:
                caption = (
                    f"🗄 **System Backup (Hourly)**\n"
                    f"📅 Date: `{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}`\n\n"
                    f"ℹ️ **Restore:** Reply to this file with `/restore` command.\n"
                    f"🧹 **Status:** Cache Cleared."
                )
                
                await app.send_document(
                    chat_id=SUPER_ADMIN_ID,
                    document=DB_NAME,
                    caption=caption
                )
                logger.info("✅ Database Backup sent to Super Admin.")
            else:
                logger.warning("⚠️ Database file not found or empty!")

            # --- TASK 2: STORAGE CLEANUP (Injecting for Render Free Tier) ---
            # Har ghante hum 'downloads' folder aur purane logs saaf karenge
            # Taaki Render ki 512MB storage kabhi full na ho.
            
            # A. Clean Downloads Folder
            if os.path.exists("downloads"):
                for filename in os.listdir("downloads"):
                    file_path = os.path.join("downloads", filename)
                    try:
                        if os.path.isfile(file_path) or os.path.islink(file_path):
                            os.unlink(file_path)
                        elif os.path.isdir(file_path):
                            shutil.rmtree(file_path)
                    except Exception as e:
                        logger.error(f"⚠️ Cleanup Error: {e}")
                logger.info("🧹 Downloads folder wiped to save space.")

            # B. Truncate Logs if too big (Safety Net)
            if os.path.exists(LOG_FILE) and os.path.getsize(LOG_FILE) > 5 * 1024 * 1024:
                # Agar log 5MB se bada ho gaya, to usko khali kar do
                with open(LOG_FILE, "w") as f:
                    f.truncate(0)
                logger.info("🧹 System Log truncated (Size Limit).")

        except Exception as e:
            logger.error(f"❌ Backup/Cleanup Failed: {e}")
            await asyncio.sleep(60) # Error aaya toh 1 min wait karke retry



# ==============================================================================
#                        SMART STICKER SENDER (LOGIC INJECTED)
# ==============================================================================
from pyrogram.raw import functions, types
import random

async def send_smart_sticker(client, chat_id):
    """
    Decides whether to send a Fixed Sticker or a Random one from packs.
    Handles 'SINGLE' vs 'RANDOM' logic dynamically.
    """
    try:
        # 1. Check if Stickers are Globally Enabled
        state = db.get_setting("sticker_state", "ON")
        if state == "OFF":
            return # Chupchap wapis jao

        # 2. Check Mode: RANDOM or SINGLE?
        mode = db.get_setting("sticker_mode", "RANDOM") # RANDOM / SINGLE
        
        # --- MODE A: SINGLE FIXED STICKER ---
        if mode == "SINGLE":
            # User ne koi specific sticker set kiya hai
            file_id = db.get_setting("single_sticker_id")
            if file_id:
                # Direct send via Pyrogram (Faster for single file_id)
                await client.send_sticker(chat_id, file_id)
                db.update_stats(stickers=1)
                await asyncio.sleep(1.0) # Thoda gap
                return
            else:
                # Agar Single Mode hai par ID nahi hai, to Random par fall back karo
                pass 

        # --- MODE B: RANDOM FROM PACKS (Raw API) ---
        # DB se saare packs ki list nikalo
        packs = db.get_sticker_packs()
        
        if not packs: 
            return # Koi pack nahi hai to kuch mat karo

        # Random Pack Choose karo
        pack_name = random.choice(packs)
        
        # Link se short name nikalo (e.g., t.me/addstickers/MyPack -> MyPack)
        short_name = pack_name.split('/')[-1]

        # Raw API se Stickers Mangwao (Ye error nahi dega)
        pack_data = await client.invoke(
            functions.messages.GetStickerSet(
                stickerset=types.InputStickerSetShortName(short_name=short_name),
                hash=0
            )
        )

        # Random Sticker Choose karke Bhejo
        if pack_data and pack_data.documents:
            sticker = random.choice(pack_data.documents)
            
            # Raw method se bhejo (SendMedia) - Most Robust Way
            await client.invoke(
                functions.messages.SendMedia(
                    peer=await client.resolve_peer(chat_id),
                    media=types.InputMediaDocument(
                        id=types.InputDocument(
                            id=sticker.id,
                            access_hash=sticker.access_hash,
                            file_reference=sticker.file_reference
                        )
                    ),
                    message="",
                    random_id=client.rnd_id()
                )
            )
            
            db.update_stats(stickers=1)
            logger.info(f"🤡 Sticker sent ({mode} mode): {short_name}")
            await asyncio.sleep(1.0) # Thoda gap taaki caption mix na ho

    except Exception as e:
        # Agar sticker fail ho, to ignore karo aur main post bhejo
        # logger.error(f"⚠️ Sticker Skip: {e}")
        pass


# ==============================================================================
#                           WORKER ENGINE (FINAL - ENTERPRISE LOGIC)
# ==============================================================================
import re # Caption cleaning ke liye

async def worker_engine():
    """
    The Brain of the System 🧠
    Handles:
    1. Priority Queues (VIP first)
    2. Smart Album Detection (No Sticker Spam)
    3. Caption Cleaning (Regex)
    4. Dynamic Publishing (Copy vs Forward)
    """
    logger.info("🚀 Enterprise Worker Engine Started...")
    
    # Track last album to prevent sticker spam
    # Hum local variable use kar rahe hain taaki global state clutter na ho
    last_processed_album_id = None 
    
    while True:
        # -------------------------------------------------------
        # [STEP 1] PRIORITY QUEUE FETCHING
        # -------------------------------------------------------
        # Pehle check karo VIP queue me kuch hai?
        if not vip_queue.empty():
            message = await vip_queue.get()
            logger.info("⚡ Processing VIP Message...")
            is_vip = True
        else:
            # Agar VIP khali hai, to Normal queue dekho
            message = await msg_queue.get()
            is_vip = False
        
        try:
            # 2. Check Pause State (Loop until resumed)
            # Super Admin can pause the bot anytime
            while db.get_setting("is_paused") == "1":
                await asyncio.sleep(5) 
            
            # 3. Check Target Channel
            target_raw = db.get_setting("target_channel", "0")
            if target_raw == "0":
                logger.warning("⚠️ Target channel not set. Dropping message.")
                if is_vip: vip_queue.task_done()
                else: msg_queue.task_done()
                continue
            
            target_id = int(target_raw)

            # -------------------------------------------------------
            # [STEP 4] SMART ALBUM & STICKER LOGIC 🧠
            # -------------------------------------------------------
            # Logic: 
            # 1. Agar ye message Album ka hissa hai (media_group_id hai).
            # 2. Aur ye ID pichle message jaisi SAME hai.
            # 3. To iska matlab ye Album ki 2nd, 3rd photo hai -> Sticker mat bhejo.
            
            current_group_id = message.media_group_id
            should_send_sticker = True
            
            if current_group_id is not None:
                if current_group_id == last_processed_album_id:
                    should_send_sticker = False # Same album, skip sticker
                    logger.info("Skipping sticker (Album continuation)")
                else:
                    last_processed_album_id = current_group_id # New album started
            else:
                last_processed_album_id = None # Not an album, reset

            # Call the Helper Function we made in Part 6
            if should_send_sticker:
                await send_smart_sticker(app, target_id)

            # -------------------------------------------------------
            # [STEP 5] CONTENT PREPARATION (Caption Cleaner)
            # -------------------------------------------------------
            mode = db.get_setting("mode", "copy")
            footer = db.get_setting("footer", "NONE")
            cleaner_mode = db.get_setting("caption_cleaner", "OFF")
            
            # Extract Text
            original_text = message.text or message.caption or ""
            
            # 🧹 Auto-Cleaner Logic (Regex)
            if cleaner_mode == "ON" and original_text:
                # Remove links (http/https/www)
                original_text = re.sub(r'http\S+', '', original_text)
                # Remove usernames (@username)
                original_text = re.sub(r'@\w+', '', original_text)
                # Remove extra spaces
                original_text = original_text.strip()

            # Merge Footer
            if footer != "NONE" and footer:
                if original_text:
                    final_text = f"{original_text}\n\n{footer}"
                else:
                    final_text = footer
            else:
                final_text = original_text

            # -------------------------------------------------------
            # [STEP 6] PUBLISHING
            # -------------------------------------------------------
            if mode == "forward":
                # Forward with Tag
                await message.forward(target_id)
            
            else:
                # Copy (No Tag) - This is what you called "Forward without tag"
                # .copy() method automatically handles Photo, Video, Document, Text
                await message.copy(
                    chat_id=target_id,
                    caption=final_text
                )

            # 7. Success & Stats
            db.update_stats(processed=1)
            
            # Log Queue Size
            q_total = msg_queue.qsize() + vip_queue.qsize()
            logger.info(f"✅ Published. Queue Remaining: {q_total}")
            
            # 8. Dynamic Delay
            delay = int(db.get_setting("delay", "30"))
            await asyncio.sleep(delay)

        except FloodWait as e:
            logger.warning(f"⏳ FloodWait: Sleeping for {e.value} seconds.")
            await asyncio.sleep(e.value)
            # Retry logic could be added here, but for now we skip to avoid loops
            
        except RPCError as e:
            logger.error(f"❌ Telegram API Error: {e}")
            db.update_stats(errors=1)
            
        except Exception as e:
            logger.critical(f"❌ Worker Error: {e}")
            traceback.print_exc()
            db.update_stats(errors=1)
            
        finally:
            # Task Done Mark karna zaroori hai
            if is_vip:
                vip_queue.task_done()
            else:
                msg_queue.task_done()

# ==============================================================================
#                           CALLBACK HANDLERS (GUI ENGINE)
# ==============================================================================

@app.on_callback_query()
async def callback_router(client: Client, cb: CallbackQuery):
    """
    Handles all Button Interactions.
    Includes Smart Toggles for Stickers & Advanced Navigation.
    """
    user_id = cb.from_user.id
    data = cb.data

    # 1. Security Barrier 🛡️ (Admins Only)
    if not db.is_admin(user_id):
        await cb.answer("🚫 ACCESS DENIED: Authorized Personnel Only.", show_alert=True)
        return

    try:
        # --- 🏠 DASHBOARD & NAVIGATION ---
        if data in ["back_home", "refresh_home"]:
            if user_id in user_input_mode: del user_input_mode[user_id]
            
            paused = db.get_setting("is_paused") == "1"
            status_icon = "🔴 SYSTEM PAUSED" if paused else "🟢 SYSTEM ONLINE"
            
            # Premium Dashboard Text
            dash_text = (
                f"🎛 **ENTERPRISE CONTROL HUB**\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"👋 **Welcome:** `{cb.from_user.first_name}`\n"
                f"🛡️ **Access Level:** `{'Super Admin' if user_id == SUPER_ADMIN_ID else 'Admin'}`\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"📊 **LIVE TELEMETRY**\n"
                f"➤ **Status:** `{status_icon}`\n"
                f"➤ **Uptime:** `{get_uptime()}`\n"
                f"➤ **Queue Depth:** `{msg_queue.qsize()} Normal` + `{vip_queue.qsize()} VIP`\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"👇 *Select a module to configure:* "
            )
            await cb.edit_message_text(text=dash_text, reply_markup=get_main_menu())

        # --- ⏯️ SYSTEM CONTROL (Super Admin Only) ---
        elif data in ["pause_bot", "resume_bot"]:
            if user_id != SUPER_ADMIN_ID:
                await cb.answer("⛔ Only the Owner can Pause/Resume the system!", show_alert=True)
                return

            if data == "pause_bot":
                db.set_setting("is_paused", "1")
                await cb.answer("⚠️ System Halted!")
            else:
                db.set_setting("is_paused", "0")
                await cb.answer("🚀 System Resumed!")
            
            # Refresh Dashboard to show new status
            await callback_router(client, cb)

        # --- 🔄 MODE SWITCHING ---
        elif data == "toggle_mode":
            curr = db.get_setting("mode", "copy")
            new_mode = "forward" if curr == "copy" else "copy"
            db.set_setting("mode", new_mode)
            
            txt = "⏩ Forward (Tag)" if new_mode == "forward" else "©️ Copy (No Tag)"
            await cb.answer(f"Mode Switched to: {txt}")
            await callback_router(client, cb) # Refresh UI

        # --- 🎭 STICKER CONTROLS (NEW INJECTIONS) ---
        elif data == "menu_stickers":
            await cb.edit_message_text(
                "🎭 **STICKER STUDIO PRO**\n"
                "━━━━━━━━━━━━━━━━━━\n"
                "Configure automated engagement stickers.\n"
                "• **Random:** Picks from your added packs.\n"
                "• **Single:** Uses one specific sticker always.\n"
                "• **Smart Album:** Prevents spam in photo albums.",
                reply_markup=get_sticker_menu()
            )

        # Toggle ON/OFF
        elif data == "toggle_sticker_on":
            db.set_setting("sticker_state", "ON")
            await cb.answer("✅ Stickers Enabled")
            await cb.edit_message_reply_markup(get_sticker_menu())
            
        elif data == "toggle_sticker_off":
            db.set_setting("sticker_state", "OFF")
            await cb.answer("🚫 Stickers Disabled")
            await cb.edit_message_reply_markup(get_sticker_menu())

        # Toggle Random/Single
        elif data == "set_mode_random":
            db.set_setting("sticker_mode", "RANDOM")
            await cb.answer("🎲 Mode: Random Packs")
            await cb.edit_message_reply_markup(get_sticker_menu())

        elif data == "set_mode_single":
            db.set_setting("sticker_mode", "SINGLE")
            await cb.answer("🎯 Mode: Single Fixed Sticker")
            await cb.edit_message_reply_markup(get_sticker_menu())

        elif data == "ask_single_sticker":
            user_input_mode[user_id] = "SET_SINGLE_STICKER"
            await cb.edit_message_text(
                "🎯 **SET FIXED STICKER**\n\n"
                "👉 Please send the **One Sticker** you want to use.",
                reply_markup=get_cancel_kb()
            )

        elif data == "ask_sticker":
            user_input_mode[user_id] = "ADD_STICKER"
            await cb.edit_message_text(
                "➕ **ADD STICKER PACK**\n\n"
                "👉 Send a **Sticker** from the pack OR the **Link**.\n"
                "Ex: `https://t.me/addstickers/Animals`",
                reply_markup=get_cancel_kb()
            )

        elif data.startswith("del_pack_"):
            pack = data.replace("del_pack_", "")
            db.remove_sticker_pack(pack)
            await cb.answer("🗑 Pack Removed")
            await cb.edit_message_reply_markup(get_sticker_menu())

        # --- 📥 QUEUE OPS ---
        elif data == "view_queue":
            q_msg = f"🔥 Pending: {msg_queue.qsize()} | ⚡ VIP: {vip_queue.qsize()}"
            await cb.answer(q_msg, show_alert=True)
            
        elif data == "noop":
            await cb.answer() # Do nothing (Visual Button)

        # --- 📡 INPUT HANDLERS (Standard) ---
        elif data == "ask_channel":
            user_input_mode[user_id] = "SET_CHANNEL"
            await cb.edit_message_text(
                "📡 **CHANNEL CONFIGURATION**\n"
                "━━━━━━━━━━━━━━━━━━\n"
                "1️⃣ **Forward** a message from target channel.\n"
                "2️⃣ **Send** Channel ID manually (e.g., -100...).",
                reply_markup=get_cancel_kb()
            )

        elif data == "ask_delay":
            user_input_mode[user_id] = "SET_DELAY"
            await cb.edit_message_text("⏱ **SET DELAY (Seconds)**\n\n👉 Send a number (Min 5).", reply_markup=get_cancel_kb())

        elif data == "cancel_input":
            if user_id in user_input_mode: del user_input_mode[user_id]
            await cb.answer("🚫 Cancelled")
            await callback_router(client, cb) # Go back to home

        # --- ✍️ FOOTER ---
        elif data == "menu_footer":
            await cb.edit_message_text("✍️ **BRANDING SUITE**\nManage your auto-signature.", reply_markup=get_footer_menu())

        elif data == "ask_footer":
            user_input_mode[user_id] = "SET_FOOTER"
            await cb.edit_message_text("✍️ **SEND NEW FOOTER**\nSupports HTML/Markdown.", reply_markup=get_cancel_kb())

        elif data == "remove_footer":
            db.set_setting("footer", "NONE")
            await cb.answer("🗑 Footer Deleted")
            await cb.edit_message_reply_markup(get_footer_menu())
            
        elif data == "view_footer_text":
            ft = db.get_setting("footer", "NONE")
            await cb.edit_message_text(f"📝 **PREVIEW:**\n\n{ft}", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="menu_footer")]]))

        # --- ⚙️ ADMINS ---
        elif data == "menu_admins":
            if user_id != SUPER_ADMIN_ID:
                await cb.answer("⛔ Super Admin Only!", show_alert=True)
                return
            admins = db.get_all_admins()
            txt = "**👥 ADMIN TEAM:**\n\n" + "\n".join([f"• `{a}`" for a in admins])
            kb = [[InlineKeyboardButton("➕ Add", callback_data="ask_add_admin"), InlineKeyboardButton("➖ Remove", callback_data="ask_rem_admin")], [InlineKeyboardButton("🔙 Back", callback_data="back_home")]]
            await cb.edit_message_text(txt, reply_markup=InlineKeyboardMarkup(kb))

        elif data == "ask_add_admin":
            user_input_mode[user_id] = "ADD_ADMIN"
            await cb.edit_message_text("👤 Send **User ID** to Hire.", reply_markup=get_cancel_kb())

        elif data == "ask_rem_admin":
            user_input_mode[user_id] = "REM_ADMIN"
            await cb.edit_message_text("👤 Send **User ID** to Fire.", reply_markup=get_cancel_kb())
            
        # --- 📊 STATS ---
        elif data == "view_stats":
            stats = db.get_total_stats()
            txt = f"📊 **STATS**\n✅ Sent: `{stats['processed']}`\n🎭 Stickers: `{stats['stickers']}`\n❌ Errors: `{stats['errors']}`"
            await cb.edit_message_text(txt, reply_markup=get_back_home_kb())

    except MessageNotModified:
        pass 
    except Exception as e:
        logger.error(f"Callback Error: {e}")
        await cb.answer("❌ Error!", show_alert=True)

# ==============================================================================
#                           INPUT MESSAGE HANDLER (ENTERPRISE)
# ==============================================================================

@app.on_message(filters.private & ~filters.bot & ~filters.command(["restore", "logs"]))
async def message_processor(client: Client, message: Message):
    """
    The Gatekeeper 🛡️
    Handles:
    1. /start & Navigation
    2. Configuration Inputs (Channel, Sticker, etc.)
    3. Content Ingestion (Queue + Feedback)
    """
    user_id = message.from_user.id

    # 1. SECURITY & AUTHENTICATION
    if not db.is_admin(user_id):
        return # Ignore unauthorized users silently

    # 2. COMMANDS (/start)
    if message.text and message.text.lower() == "/start":
        # Reset stuck states
        if user_id in user_input_mode: del user_input_mode[user_id]
        
        await message.reply_text(
            f"👋 **Hello Boss, {message.from_user.first_name}!**\n\n"
            "🚀 **Enterprise Publisher System Online**\n"
            "Ready to manage your channel content.",
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
                    title = message.forward_from_chat.title
                elif message.text:
                    try: target = int(message.text); title = "Manual ID"
                    except: pass
                
                if target:
                    db.set_setting("target_channel", str(target))
                    await message.reply_text(
                        f"✅ **Target Channel Locked!**\nID: `{target}`\nName: `{title}`", 
                        reply_markup=get_back_home_kb()
                    )
                else:
                    await message.reply_text("❌ Invalid Input. Forward a message or send ID.")
                    return

            # --- SET DELAY ---
            elif mode == "SET_DELAY":
                try:
                    val = int(message.text)
                    if val < 5: raise ValueError
                    db.set_setting("delay", str(val))
                    await message.reply_text(f"⏱ **Delay Updated:** `{val} seconds`", reply_markup=get_back_home_kb())
                except:
                    await message.reply_text("❌ Invalid! Minimum delay is 5 seconds.")
                    return

            # --- SET FOOTER ---
            elif mode == "SET_FOOTER":
                text = message.text.html if message.text else "NONE"
                db.set_setting("footer", text)
                await message.reply_text("✍️ **Branding Footer Updated!**", reply_markup=get_footer_menu())

            # --- ADD STICKER PACK (Random Mode) ---
            elif mode == "ADD_STICKER":
                pack_name = None
                if message.sticker:
                    pack_name = message.sticker.set_name
                elif message.text:
                    if "addstickers/" in message.text:
                        pack_name = message.text.split("addstickers/")[-1].split()[0]
                    else:
                        pack_name = message.text.strip()
                
                if pack_name:
                    db.add_sticker_pack(pack_name)
                    # NOTE: Removed 'refresh_cache' call as we use Raw API now (Crash Fix)
                    await message.reply_text(f"✅ **Pack Added:** `{pack_name}`", reply_markup=get_sticker_menu())
                else:
                    await message.reply_text("❌ Error. Send a sticker or a valid link.")
                    return

            # --- [NEW] SET SINGLE STICKER (Fixed Mode) ---
            elif mode == "SET_SINGLE_STICKER":
                if message.sticker:
                    file_id = message.sticker.file_id
                    db.set_setting("single_sticker_id", file_id)
                    db.set_setting("sticker_mode", "SINGLE") # Auto-switch to single mode
                    await message.reply_text("🎯 **Fixed Sticker Set!**\nSystem is now in 'Single Sticker' mode.", reply_markup=get_sticker_menu())
                else:
                    await message.reply_text("❌ Please send a Sticker.")
                    return

            # --- ADMIN MANAGEMENT ---
            elif mode == "ADD_ADMIN":
                try:
                    new_id = int(message.text)
                    db.add_admin(new_id, user_id)
                    await message.reply_text(f"👤 **Admin Hired:** `{new_id}`", reply_markup=get_back_home_kb())
                except:
                    await message.reply_text("❌ Send Numeric User ID.")
                    return

            elif mode == "REM_ADMIN":
                try:
                    rem_id = int(message.text)
                    if rem_id == SUPER_ADMIN_ID:
                        await message.reply_text("🛡️ **Security Alert:** Cannot remove Super Admin.")
                    else:
                        db.remove_admin(rem_id)
                        await message.reply_text(f"🗑 **Admin Fired:** `{rem_id}`", reply_markup=get_back_home_kb())
                except:
                    await message.reply_text("❌ Invalid ID.")
                    return

            # Cleanup Input Mode
            del user_input_mode[user_id]
        
        except Exception as e:
            logger.error(f"Input Handler Error: {e}")
            await message.reply_text(f"❌ Error: {e}", reply_markup=get_back_home_kb())
        
        return

    # 4. QUEUE INGESTION (File Uploads)
    target_channel = db.get_setting("target_channel")
    if target_channel == "0":
        await message.reply_text("⚠️ **Setup Required!**\nSet a Target Channel first.", reply_markup=get_main_menu())
        return

    # --- ⚡ PRIORITY CHECK (VIP Logic) ---
    # Agar caption me #urgent ya #vip hai, to VIP Queue me daalo
    is_vip = False
    caption = message.caption or ""
    if "#urgent" in caption.lower() or "#vip" in caption.lower():
        is_vip = True
        await vip_queue.put(message)
    else:
        await msg_queue.put(message)

    # --- 📊 LIVE FEEDBACK & BUTTONS ---
    # User ko immediately pata chalna chahiye ki kaam ho gaya
    
    # Calculate Position
    pos = vip_queue.qsize() if is_vip else msg_queue.qsize()
    queue_type = "⚡ VIP Queue" if is_vip else "📥 Normal Queue"
    
    # Send Interactive Confirmation
    # (Agar album hai, to shuru me spam na kare, isliye hum 'reply' use karte hain
    # jo user ko notification dega)
    try:
        await message.reply_text(
            f"✅ **Added to {queue_type}**\n"
            f"🔢 Position: `{pos}`\n"
            f"⏳ Processing in background...",
            quote=True,
            reply_markup=get_upload_success_kb() # <-- "Add More" & "Back" Buttons here
        )
    except Exception as e:
        logger.error(f"Feedback Error: {e}")

# ==============================================================================
#                           COMMAND HANDLERS
# ==============================================================================

@app.on_message(filters.command("start") & filters.private)
async def start_handler(client: Client, message: Message):
    """Shows the Main Dashboard."""
    if not db.is_admin(message.from_user.id):
        return
    
    # Clear any stuck input modes
    if message.from_user.id in user_input_mode:
        del user_input_mode[message.from_user.id]

    await message.reply(
        f"🤖 **Enterprise Publisher Dashboard**\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"👋 Welcome, Boss `{message.from_user.first_name}`!\n"
        f"⚡ System is **Online** and ready.\n"
        f"━━━━━━━━━━━━━━━━━━",
        reply_markup=get_main_menu()
    )

@app.on_message(filters.command("logs") & filters.private)
async def logs_handler(client: Client, message: Message):
    """Sends the system.log file to Super Admin for debugging."""
    if message.from_user.id != SUPER_ADMIN_ID: return
    
    if os.path.exists(LOG_FILE):
        await message.reply_document(
            LOG_FILE, 
            caption="📜 **System Logs** (Last 5MB)"
        )
    else:
        await message.reply("⚠️ Log file is empty or missing.")

@app.on_message(filters.command("restore") & filters.private)
async def restore_handler(client: Client, message: Message):
    """Restores the database from a backup file."""
    if message.from_user.id != SUPER_ADMIN_ID: return

    if not message.reply_to_message or not message.reply_to_message.document:
        await message.reply("⚠️ **Usage:** Reply to a `.db` backup file with `/restore`.")
        return

    try:
        status = await message.reply("⏳ **Restoring Database...**")
        # Download and overwrite existing DB
        await message.reply_to_message.download(file_name=DB_NAME)
        
        # Re-initialize DB Connection
        db.connect() 
        
        await status.edit("✅ **Restore Complete!**\nSystem updated with backup data.")
    except Exception as e:
        await message.reply(f"❌ Restore Failed: {e}")

# ==============================================================================
#                           MAIN INPUT PROCESSOR
# ==============================================================================

@app.on_message(filters.private & ~filters.bot)
async def message_processor(client: Client, message: Message):
    """
    The Master Input Handler 🛡️
    Handles: Config Inputs AND Content Queuing with Feedback.
    """
    user_id = message.from_user.id
    if not db.is_admin(user_id): return

    # --- A. CONFIGURATION MODE ---
    if user_id in user_input_mode:
        mode = user_input_mode[user_id]
        try:
            # 1. Channel Setup
            if mode == "SET_CHANNEL":
                target = None
                if message.forward_from_chat: target = message.forward_from_chat.id
                elif message.text: 
                    try: target = int(message.text)
                    except: pass
                
                if target:
                    db.set_setting("target_channel", str(target))
                    await message.reply(f"✅ **Target Channel Set:** `{target}`", reply_markup=get_back_home_kb())
                else:
                    await message.reply("❌ Invalid ID. Forward from channel or send ID.")
                    return

            # 2. Delay Setup
            elif mode == "SET_DELAY":
                try:
                    val = int(message.text)
                    if val < 5: raise ValueError
                    db.set_setting("delay", str(val))
                    await message.reply(f"⏱ **Delay Updated:** `{val}s`", reply_markup=get_back_home_kb())
                except:
                    await message.reply("❌ Number must be > 5.")
                    return

            # 3. Footer Setup
            elif mode == "SET_FOOTER":
                txt = message.text.html if message.text else "NONE"
                db.set_setting("footer", txt)
                await message.reply("✍️ **Footer Updated!**", reply_markup=get_footer_menu())

            # 4. Sticker Pack (Random)
            elif mode == "ADD_STICKER":
                pack = None
                if message.sticker: pack = message.sticker.set_name
                elif message.text: pack = message.text.split('/')[-1] if '/' in message.text else message.text
                
                if pack:
                    db.add_sticker_pack(pack)
                    await message.reply(f"✅ **Pack Added:** `{pack}`", reply_markup=get_sticker_menu())
                else:
                    await message.reply("❌ Invalid Sticker/Link.")
                    return

            # 5. Single Sticker (Fixed)
            elif mode == "SET_SINGLE_STICKER":
                if message.sticker:
                    db.set_setting("single_sticker_id", message.sticker.file_id)
                    db.set_setting("sticker_mode", "SINGLE")
                    await message.reply("🎯 **Single Sticker Mode Activated!**", reply_markup=get_sticker_menu())
                else:
                    await message.reply("❌ Please send a sticker.")
                    return

            # 6. Admin Mgmt
            elif mode == "ADD_ADMIN":
                try:
                    db.add_admin(int(message.text), user_id)
                    await message.reply("👤 **Admin Added.**", reply_markup=get_back_home_kb())
                except: await message.reply("❌ Invalid ID.")
                return

            elif mode == "REM_ADMIN":
                try:
                    db.remove_admin(int(message.text))
                    await message.reply("🗑 **Admin Removed.**", reply_markup=get_back_home_kb())
                except: await message.reply("❌ Invalid ID.")
                return

            # Cleanup
            del user_input_mode[user_id]
        
        except Exception as e:
            logger.error(f"Input Error: {e}")
            await message.reply("❌ Error occurred.", reply_markup=get_back_home_kb())
        return

    # --- B. CONTENT QUEUEING (The Smart Part) ---
    
    # 1. Check Pre-requisites
    if db.get_setting("target_channel") == "0":
        await message.reply("⚠️ **Target Channel Missing!** Set it in dashboard.", reply_markup=get_main_menu())
        return

    # 2. Visual Feedback (Immediate)
    # User ko lagega bot fast hai
    status_msg = await message.reply("⏳ **Processing...**", quote=True)

    # 3. Priority Logic (VIP)
    is_vip = False
    caption = message.caption or ""
    if "#urgent" in caption.lower() or "#vip" in caption.lower():
        is_vip = True
        await vip_queue.put(message)
        q_type = "⚡ VIP Queue"
    else:
        await msg_queue.put(message)
        q_type = "📥 Normal Queue"

    # 4. Calculate Position
    pos = vip_queue.qsize() if is_vip else msg_queue.qsize()

    # 5. Final Confirmation (Edit the Loading Message)
    # Buttons add kiye hain taaki user wahin se decide kare
    await status_msg.edit(
        f"✅ **Added to {q_type}**\n"
        f"🔢 Position: `{pos}`\n"
        f"🚀 Uploading in background...",
        reply_markup=get_upload_success_kb()
    )

# ==============================================================================
#                           MAIN EXECUTOR
# ==============================================================================

async def main():
    """Starts the Enterprise Bot System."""
    # 1. Clear Console
    os.system('cls' if os.name == 'nt' else 'clear')
    print("----------------------------------------------------------------")
    print("       🚀 ENTERPRISE PUBLISHER BOT - STARTING SYSTEM 🚀")
    print("----------------------------------------------------------------")
    
    # 2. Start Client
    logger.info("📡 Connecting to Telegram Servers...")
    await app.start()
    
    # 3. Set Bot Commands (Auto-Menu)
    # 3. Set Bot Commands (Auto-Menu)
    commands = [
        BotCommand("start", "🏠 Dashboard"),
        BotCommand("logs", "📜 View Error Logs"),
        BotCommand("restore", "♻️ Restore Backup")
    ]
    await app.set_bot_commands(commands)
    logger.info("✅ Bot Commands Menu Updated.")
    
    # 4. Notify Super Admin
    me = await app.get_me()
    logger.info(f"✅ Logged in as: @{me.username}")
    
    try:
        await app.send_message(
            SUPER_ADMIN_ID, 
            f"🚀 **Bot Restarted Successfully!**\n"
            f"📅 `{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}`\n"
            f"ℹ️ Send /start to open control panel."
        )
    except:
        logger.warning("⚠️ Could not DM Super Admin.")

    # 5. Start Background Workers
    worker_task = asyncio.create_task(worker_engine())
    backup_task = asyncio.create_task(auto_backup_task(app))
    
    # 6. Keep Alive
    logger.info("🟢 SYSTEM ONLINE. WAITING FOR COMMANDS.")
    await idle()
    
    # 7. Shutdown
    worker_task.cancel()
    backup_task.cancel()
    await app.stop()

if __name__ == "__main__":
    try:
        # Keep Render Alive (Web Server)
        keep_alive()  
        
        loop = asyncio.get_event_loop()
        loop.run_until_complete(main())
    except KeyboardInterrupt:
        print("\n🛑 Stopped by User")
    except Exception as e:
        logger.critical(f"❌ Fatal Error: {e}")
        traceback.print_exc()

