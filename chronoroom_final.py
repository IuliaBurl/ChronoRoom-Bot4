import os
import sqlite3
import json
import random
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters
from telegram.error import BadRequest

# ==================== SETTINGS ====================
DATABASE_FILE = "chronoroom.db"
TOKEN_FILE = "token.txt"
PROFILES_PER_PAGE = 4
MIN_BIO_LENGTH = 15
MAX_BIO_LENGTH = 400
MAX_GROUP_MEMBERS = 8
CHANNEL_USERNAME = "@ChronoRoom"  # Channel for mandatory subscription
ADMIN_USERNAME = "alex_blackthorn"  # Username (without @) allowed to use /admin

# User states
STATE_NONE = "none"
STATE_WRITING_BIO = "writing_bio"
STATE_IN_CHAT = "in_chat"
STATE_WAITING_GROUP_LINK = "waiting_group_link"
STATE_WAITING_GROUP_MESSAGE = "waiting_group_message"

# ==================== TOKEN LOADING ====================
def load_token():
    """Load token from file"""
    try:
        with open(TOKEN_FILE, 'r') as f:
            token = f.read().strip()

        if not token or token == "YOUR_TOKEN_HERE":
            print("\n" + "="*60)
            print("❌ Token not found or not set!")
            print("="*60)
            print("\n📝 Instructions:")
            print("1. Get a token from @BotFather in Telegram")
            print("2. Open the token.txt file:")
            print(f"   nano {TOKEN_FILE}")
            print("3. Replace YOUR_TOKEN_HERE with your token")
            print("4. Save the file (Ctrl+X, then Y, then Enter)")
            print("5. Run the bot again")
            print("="*60)
            return None

        print(f"✅ Token loaded: {token[:15]}...")
        return token

    except FileNotFoundError:
        print(f"❌ File {TOKEN_FILE} not found!")
        print(f"📝 Create the file: echo 'YOUR_TOKEN_HERE' > {TOKEN_FILE}")
        return None

# ==================== SQLite DATABASE ====================
class Database:
    def __init__(self):
        self.conn = sqlite3.connect(DATABASE_FILE, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.cursor = self.conn.cursor()
        self.init_db()

    def init_db(self):
        """Create tables with existing column checks"""
        # Profiles table
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS profiles (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                bio TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # Check and add has_subscribed column if missing
        self.cursor.execute("PRAGMA table_info(profiles)")
        columns = [column[1] for column in self.cursor.fetchall()]

        if 'has_subscribed' not in columns:
            print("📊 Adding has_subscribed column to profiles table...")
            try:
                self.cursor.execute('ALTER TABLE profiles ADD COLUMN has_subscribed INTEGER DEFAULT 0')
            except:
                print("⚠️ Column already exists or error adding it")

        # Group requests table
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS group_requests (
                request_id TEXT PRIMARY KEY,
                creator_id INTEGER,
                selected_ids TEXT,
                group_link TEXT,
                status TEXT DEFAULT 'pending',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # Check and add group_message column if missing
        self.cursor.execute("PRAGMA table_info(group_requests)")
        columns = [column[1] for column in self.cursor.fetchall()]

        if 'group_message' not in columns:
            print("📊 Adding group_message column to group_requests table...")
            try:
                self.cursor.execute('ALTER TABLE group_requests ADD COLUMN group_message TEXT')
            except:
                print("⚠️ Column already exists or error adding it")

        # Active chats table
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS active_chats (
                chat_id TEXT PRIMARY KEY,
                user1_id INTEGER,
                user2_id INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        self.conn.commit()
        print("✅ Database initialized")

    def add_profile(self, user_id, username, first_name, bio):
        """Add profile"""
        self.cursor.execute('''
            INSERT OR REPLACE INTO profiles (user_id, username, first_name, bio)
            VALUES (?, ?, ?, ?)
        ''', (user_id, username, first_name, bio))
        self.conn.commit()
        return True

    def get_profile(self, user_id):
        """Get profile"""
        self.cursor.execute('SELECT * FROM profiles WHERE user_id = ?', (user_id,))
        row = self.cursor.fetchone()
        return dict(row) if row else None

    def update_subscription_status(self, user_id, has_subscribed):
        """Update subscription status"""
        try:
            # First make sure the profile exists
            profile = self.get_profile(user_id)
            if not profile:
                # Create a minimal profile
                self.cursor.execute('''
                    INSERT OR IGNORE INTO profiles (user_id, has_subscribed)
                    VALUES (?, ?)
                ''', (user_id, 1 if has_subscribed else 0))
            else:
                self.cursor.execute('''
                    UPDATE profiles SET has_subscribed = ? WHERE user_id = ?
                ''', (1 if has_subscribed else 0, user_id))

            self.conn.commit()
            return True
        except Exception as e:
            print(f"⚠️ Error updating subscription status: {e}")
            return False

    def check_subscription(self, user_id):
        """Check user subscription"""
        try:
            self.cursor.execute('SELECT has_subscribed FROM profiles WHERE user_id = ?', (user_id,))
            result = self.cursor.fetchone()
            if result:
                # Check if has_subscribed column exists in the result
                if 'has_subscribed' in result.keys():
                    return result['has_subscribed']
            return 0
        except:
            return 0

    def get_all_profiles(self, exclude_user_id=None):
        """Get all profiles with shuffling"""
        try:
            if exclude_user_id:
                self.cursor.execute('SELECT * FROM profiles WHERE user_id != ?', (exclude_user_id,))
            else:
                self.cursor.execute('SELECT * FROM profiles')

            profiles = [dict(row) for row in self.cursor.fetchall()]
            random.shuffle(profiles)
            return profiles
        except:
            return []

    def get_paginated_profiles(self, page=0, exclude_user_id=None):
        """Paginate profiles"""
        profiles = self.get_all_profiles(exclude_user_id)
        start_idx = page * PROFILES_PER_PAGE
        end_idx = start_idx + PROFILES_PER_PAGE
        return profiles[start_idx:end_idx]

    def get_total_pages(self, exclude_user_id=None):
        """Get total number of pages"""
        try:
            if exclude_user_id:
                self.cursor.execute('SELECT COUNT(*) FROM profiles WHERE user_id != ?', (exclude_user_id,))
            else:
                self.cursor.execute('SELECT COUNT(*) FROM profiles')

            total = self.cursor.fetchone()[0]
            return max(1, (total + PROFILES_PER_PAGE - 1) // PROFILES_PER_PAGE)
        except:
            return 1

    def delete_profile(self, user_id):
        """Delete profile"""
        self.cursor.execute('DELETE FROM profiles WHERE user_id = ?', (user_id,))
        self.conn.commit()
        return self.cursor.rowcount > 0

    def create_group_request(self, creator_id, selected_ids, group_link=None, group_message=None):
        """Create group request"""
        request_id = f"group_{datetime.now().timestamp()}_{random.randint(1000, 9999)}"
        selected_ids_json = json.dumps(selected_ids)

        self.cursor.execute('''
            INSERT INTO group_requests (request_id, creator_id, selected_ids, group_link, group_message)
            VALUES (?, ?, ?, ?, ?)
        ''', (request_id, creator_id, selected_ids_json, group_link, group_message))

        self.conn.commit()
        return request_id

    def get_group_request(self, request_id):
        """Get group request"""
        self.cursor.execute('SELECT * FROM group_requests WHERE request_id = ?', (request_id,))
        row = self.cursor.fetchone()

        if row:
            data = dict(row)
            data['selected_ids'] = json.loads(data['selected_ids'])
            return data
        return None

    def update_group_request(self, request_id, group_link, group_message=None):
        """Update group link"""
        self.cursor.execute('''
            UPDATE group_requests SET group_link = ?, group_message = ? WHERE request_id = ?
        ''', (group_link, group_message, request_id))
        self.conn.commit()
        return self.cursor.rowcount > 0

    def create_chat(self, user1_id, user2_id):
        """Create chat between two users"""
        try:
            user1_id_int = int(user1_id)
            user2_id_int = int(user2_id)
            chat_id = f"chat_{min(user1_id_int, user2_id_int)}_{max(user1_id_int, user2_id_int)}"

            self.cursor.execute('''
                INSERT OR REPLACE INTO active_chats (chat_id, user1_id, user2_id)
                VALUES (?, ?, ?)
            ''', (chat_id, user1_id_int, user2_id_int))

            self.conn.commit()
            return chat_id
        except Exception as e:
            print(f"⚠️ Error creating chat: {e}")
            return f"chat_{user1_id}_{user2_id}"

    def get_chat(self, user1_id, user2_id):
        """Get chat between users"""
        try:
            user1_id_int = int(user1_id)
            user2_id_int = int(user2_id)
            chat_id = f"chat_{min(user1_id_int, user2_id_int)}_{max(user1_id_int, user2_id_int)}"

            self.cursor.execute('SELECT * FROM active_chats WHERE chat_id = ?', (chat_id,))
            row = self.cursor.fetchone()
            return dict(row) if row else None
        except:
            return None

    def get_chat_by_id(self, chat_id):
        """Get chat by ID"""
        self.cursor.execute('SELECT * FROM active_chats WHERE chat_id = ?', (chat_id,))
        row = self.cursor.fetchone()
        return dict(row) if row else None

    def close(self):
        """Close connection"""
        self.conn.close()

# ==================== KEYBOARDS ====================
def get_main_keyboard():
    """Main keyboard"""
    keyboard = [
        [InlineKeyboardButton("🔍 Find friends", callback_data='find_friends')],
        [InlineKeyboardButton("👤 My profile", callback_data='my_profile')],
        [InlineKeyboardButton("📖 How to use", callback_data='tutorial')],
        [InlineKeyboardButton("🗑️ Delete profile", callback_data='delete_ask')]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_profiles_keyboard(profiles, page, total_pages, selected_ids=None):
    """Keyboard with profiles and proper pagination"""
    if selected_ids is None:
        selected_ids = []

    keyboard = []

    # 1. Profiles (maximum 4 per page)
    for profile in profiles:
        is_selected = str(profile['user_id']) in selected_ids
        emoji = "✅ " if is_selected else ""
        name = profile['first_name'][:20] if profile['first_name'] else "No name"
        button_text = f"{emoji}{name}"
        callback_data = f"select_{profile['user_id']}"
        keyboard.append([InlineKeyboardButton(button_text, callback_data=callback_data)])

    # 2. Confirmation button (if there are selected)
    if selected_ids:
        keyboard.append([
            InlineKeyboardButton(
                f"✅ Confirm selection ({len(selected_ids)} people)",
                callback_data='confirm_selection'
            )
        ])

    # 3. Pagination: [ ◀️ ] [ 1/5 ] [ ▶️ ]
    nav_row = []

    # Back button
    if page > 0:
        nav_row.append(InlineKeyboardButton("◀️ Back", callback_data=f'page_{page-1}'))
    else:
        nav_row.append(InlineKeyboardButton("⏺️", callback_data='no_page'))

    # Page number
    nav_row.append(InlineKeyboardButton(f"{page+1}/{total_pages}", callback_data='current_page'))

    # Forward button
    if page < total_pages - 1:
        nav_row.append(InlineKeyboardButton("Forward ▶️", callback_data=f'page_{page+1}'))
    else:
        nav_row.append(InlineKeyboardButton("⏺️", callback_data='no_page'))

    keyboard.append(nav_row)

    # 4. Main menu
    keyboard.append([InlineKeyboardButton("🏠 Main menu", callback_data='main_menu')])

    return InlineKeyboardMarkup(keyboard)

def get_selection_confirmation_keyboard(selected_ids):
    """Keyboard after selection confirmation"""
    keyboard = []

    # 1. Selection info
    keyboard.append([
        InlineKeyboardButton(f"📋 Selected: {len(selected_ids)} people", callback_data='show_selected')
    ])

    # 2. Actions with selected
    # Group can be created from 1 person
    if len(selected_ids) >= 1:
        keyboard.append([
            InlineKeyboardButton("👥 Create group", callback_data='create_group')
        ])

    # Personal message only to 1 person
    if len(selected_ids) == 1:
        keyboard.append([
            InlineKeyboardButton("💌 Write personally", callback_data='write_personal')
        ])

    # 3. Return to search
    keyboard.append([
        InlineKeyboardButton("🔍 Continue search", callback_data='find_friends'),
        InlineKeyboardButton("🔄 Reset selection", callback_data='reset_selection')
    ])

    # 4. Main menu
    keyboard.append([InlineKeyboardButton("🏠 Main menu", callback_data='main_menu')])

    return InlineKeyboardMarkup(keyboard)

def get_group_invite_keyboard(request_id):
    """Keyboard for group invitation"""
    keyboard = [
        [
            InlineKeyboardButton("✅ Join", callback_data=f'join_{request_id}'),
            InlineKeyboardButton("❌ Decline", callback_data=f'decline_{request_id}')
        ]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_reply_keyboard(chat_id):
    """Keyboard for replying to a message"""
    keyboard = [
        [
            InlineKeyboardButton("💌 Reply", callback_data=f'reply_{chat_id}'),
            InlineKeyboardButton("❌ Ignore", callback_data='ignore_message')
        ]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_confirmation_keyboard():
    """Delete confirmation keyboard"""
    keyboard = [
        [
            InlineKeyboardButton("✅ Yes, delete", callback_data='confirm_delete'),
            InlineKeyboardButton("❌ No, keep", callback_data='cancel_delete')
        ]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_tutorial_keyboard():
    """Tutorial keyboard"""
    keyboard = [
        [InlineKeyboardButton("📝 Creating a profile", callback_data='tutor_profile')],
        [InlineKeyboardButton("🔍 Finding friends", callback_data='tutor_search')],
        [InlineKeyboardButton("👥 Creating a group", callback_data='tutor_group')],
        [InlineKeyboardButton("💬 Communication", callback_data='tutor_chat')],
        [InlineKeyboardButton("🏠 Main menu", callback_data='main_menu')]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_subscription_keyboard():
    """Keyboard for subscription check"""
    keyboard = [
        [
            InlineKeyboardButton("📢 Subscribe to channel", url="https://t.me/ChronoRoom"),
            InlineKeyboardButton("✅ I subscribed", callback_data='check_subscription')
        ]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_group_message_keyboard():
    """Keyboard for sending a message to the group"""
    keyboard = [
        [
            InlineKeyboardButton("✅ Yes, send message", callback_data='send_group_message'),
            InlineKeyboardButton("❌ No, just invite", callback_data='skip_group_message')
        ]
    ]
    return InlineKeyboardMarkup(keyboard)

# ==================== TEXT HELPERS ====================
def escape_md(text):
    """Escape Telegram legacy-Markdown special characters in user-provided text
    so that a stray _ * ` [ inside a name/bio/message doesn't break parse_mode='Markdown'."""
    if not text:
        return text
    text = str(text)
    for ch in ('_', '*', '`', '['):
        text = text.replace(ch, '\\' + ch)
    return text

# ==================== CHECK FUNCTIONS ====================
async def check_subscription(bot, user_id):
    """Check if the user is subscribed to the channel"""
    try:
        chat_member = await bot.get_chat_member(chat_id=CHANNEL_USERNAME, user_id=user_id)
        return chat_member.status in ['member', 'administrator', 'creator']
    except Exception as e:
        print(f"⚠️ Subscription check error (ID {user_id}): {e}")
        # Temporarily allow access on check errors
        return True

# ==================== HANDLERS ====================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler for the /start command"""
    user = update.effective_user
    db = context.bot_data['db']

    # Reset user state
    context.user_data['state'] = STATE_NONE
    context.user_data.pop('active_chat', None)
    context.user_data.pop('waiting_for_group_link', None)
    context.user_data.pop('waiting_for_group_message', None)
    context.user_data.pop('temp_request_id', None)
    context.user_data.pop('temp_group_link', None)

    # Check subscription
    try:
        is_subscribed = await check_subscription(context.bot, user.id)
        if not is_subscribed:
            db.update_subscription_status(user.id, False)
            await update.message.reply_text(
                "📢 *Welcome to ChronoRoom!*\n\n"
                "To use the bot, you need to subscribe to our channel:\n"
                "👉 https://t.me/ChronoRoom 👈\n\n"
                "After subscribing, press the button below:",
                reply_markup=get_subscription_keyboard(),
                parse_mode='Markdown'
            )
            return
    except Exception as e:
        print(f"⚠️ Subscription check error in /start: {e}")
        # Continue working even on check error

    # Update subscription status
    db.update_subscription_status(user.id, True)

    # Check profile
    profile = db.get_profile(user.id)

    if profile and profile.get('bio'):
        welcome_text = (
            "🎉 *Welcome to ChronoRoom!*\n\n"
            f"👋 Hi, {user.first_name}!\n\n"
            "✨ *Available features:*\n"
            "• 🔍 Find friends - browse other people's profiles\n"
            "• 👤 My profile - view and edit\n"
            "• 👥 Create group - unite up to 8 people\n"
            "• 💬 Personal communication - write anonymously via the bot\n\n"
            "📖 *New here?* Press 'How to use'"
        )

        await update.message.reply_text(
            welcome_text,
            reply_markup=get_main_keyboard(),
            parse_mode='Markdown'
        )
    else:
        # Set state to "filling profile"
        context.user_data['state'] = STATE_WRITING_BIO

        welcome_text = (
            "🎉 *Welcome to ChronoRoom!*\n\n"
            "🤔 *What is this?*\n"
            "A bot for finding friends by interests!\n\n"
            "🚀 *How it works:*\n"
            "1. Create a profile about yourself\n"
            "2. Find interesting people\n"
            "3. Chat or create groups!\n\n"
            "✏️ *Let's start!*\n"
            "Tell us about yourself (from 15 to 400 characters):\n\n"
            "💡 *What to write:*\n"
            "• Your interests and hobbies\n"
            "• What you do (work/study)\n"
            "• What you're looking for in this bot\n\n"
            "*Example:*\n"
            "_I love movies, traveling, programming. Looking for friends to chat and work on joint projects. I work in IT._"
        )

        await update.message.reply_text(
            welcome_text,
            parse_mode='Markdown'
        )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler for the /help command"""
    # Reset state
    context.user_data['state'] = STATE_NONE
    context.user_data.pop('active_chat', None)

    help_text = (
        "🆘 *ChronoRoom Help*\n\n"
        "📚 *Main commands:*\n"
        "• /start - Main menu\n"
        "• /help - This help\n"
        "• /profile - Show my profile\n"
        "• /delete - Delete profile\n\n"
        "🚀 *Quick start:*\n"
        "1. Write about yourself at start\n"
        "2. Press 'Find friends'\n"
        "3. Choose interesting profiles\n"
        "4. Press 'Confirm selection'\n"
        "5. Chat or create groups!\n\n"
        "📖 *Detailed instructions:*\n"
        "Press 'How to use' in the main menu"
    )

    await update.message.reply_text(
        help_text,
        parse_mode='Markdown'
    )

async def profile_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler for the /profile command (shows the profile as a normal message)"""
    user = update.effective_user
    db = context.bot_data['db']
    context.user_data['state'] = STATE_NONE

    profile = db.get_profile(user.id)
    if not profile or not profile.get('bio'):
        await update.message.reply_text(
            "❌ *You don't have a profile yet!*\n\nWrite /start to create one",
            reply_markup=get_main_keyboard(),
            parse_mode='Markdown'
        )
        return

    profile_text = (
        f"👤 *Your profile*\n\n"
        f"👋 *Name:* {escape_md(profile['first_name'])}\n"
    )

    if profile.get('username'):
        profile_text += f"🔗 *Username:* @{profile['username']}\n"

    # Safe date retrieval
    created_at = profile.get('created_at', 'Unknown')
    if isinstance(created_at, str) and len(created_at) >= 10:
        profile_text += f"📅 *Created:* {created_at[:10]}\n\n"
    else:
        profile_text += f"📅 *Created:* Unknown\n\n"

    # Safe bio check (bio can be NULL, not just missing)
    bio = profile.get('bio') or ''
    if bio:
        profile_text += f"📝 *About me:*\n{escape_md(bio)}\n\n"
        profile_text += f"📊 *Statistics:*\n"
        profile_text += f"• Characters: {len(bio)}/{MAX_BIO_LENGTH}\n"
        profile_text += f"• Can be edited via /start"
    else:
        profile_text += "📝 *About me:* Profile not filled\n\n"
        profile_text += "📊 *Statistics:*\n"
        profile_text += f"• Characters: 0/{MAX_BIO_LENGTH}\n"
        profile_text += f"• Can be created via /start"

    await update.message.reply_text(
        profile_text,
        reply_markup=get_main_keyboard(),
        parse_mode='Markdown'
    )

async def delete_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler for the /delete command (asks for confirmation as a normal message)"""
    context.user_data['state'] = STATE_NONE

    await update.message.reply_text(
        "⚠️ *Delete confirmation*\n\n"
        "Are you sure you want to delete your profile?\n\n"
        "❌ *After deletion:*\n"
        "• Your profile will disappear from search\n"
        "• You won't receive messages\n"
        "• Group invitations will stop\n\n"
        "✅ *You can:*\n"
        "• Create a new profile at any time\n"
        "• Start over from scratch",
        reply_markup=get_confirmation_keyboard(),
        parse_mode='Markdown'
    )

async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler for the /admin command.
    Restricted to ADMIN_USERNAME. Grabs every profile currently in the DB
    (up to MAX_GROUP_MEMBERS) and jumps straight to the 'create group' flow,
    skipping manual selection in the search. Useful when there are only a
    couple of test profiles in the pool instead of a full 8."""
    user = update.effective_user
    db = context.bot_data['db']

    if not user.username or user.username.lower() != ADMIN_USERNAME.lower():
        await update.message.reply_text("⛔ У вас нет доступа к этой команде.")
        return

    profiles = db.get_all_profiles(exclude_user_id=user.id)
    if not profiles:
        await update.message.reply_text(
            "😔 *В базе пока нет ни одного профиля.*\n\nСоздавать группу не с кем.",
            parse_mode='Markdown'
        )
        return

    selected = profiles[:MAX_GROUP_MEMBERS]
    selected_ids = [str(p['user_id']) for p in selected]

    # Reset any stray state and pre-fill selection, same as after manual "Confirm selection"
    context.user_data['selected_ids'] = selected_ids
    context.user_data['current_page'] = 0

    request_id = db.create_group_request(user.id, selected_ids)
    context.user_data['waiting_for_group_link'] = request_id
    context.user_data['state'] = STATE_WAITING_GROUP_LINK

    names = "\n".join(
        f"{i}. {escape_md(p.get('first_name') or 'No name')}"
        for i, p in enumerate(selected, 1)
    )

    await update.message.reply_text(
        "👑 *Админ-режим*\n\n"
        f"Взято {len(selected_ids)} человек из базы (всего доступно: {len(profiles)}):\n\n"
        f"{names}\n\n"
        "👥 *Создание группы*\n\n"
        "📌 *Что нужно сделать:*\n"
        "1. Создайте группу в Telegram\n"
        "2. Получите ссылку-приглашение\n"
        "3. Пришлите ссылку сюда\n\n"
        "🔗 *Формат ссылки:*\n"
        "• https://t.me/group_name\n"
        "• @group_name\n\n"
        "✍️ *После этого можно будет отправить общее сообщение всем приглашённым*\n\n"
        "ℹ️ *Чтобы отменить, нажмите /start*",
        parse_mode='Markdown'
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler for text messages"""
    user = update.effective_user
    db = context.bot_data['db']
    text = update.message.text.strip()

    # Get current user state
    user_state = context.user_data.get('state', STATE_NONE)

    # Check subscription before any action
    try:
        is_subscribed = await check_subscription(context.bot, user.id)
        if not is_subscribed:
            db.update_subscription_status(user.id, False)
            await update.message.reply_text(
                "❌ *Please subscribe to the channel first!*\n\n"
                "To use the bot, you need to subscribe:\n"
                "👉 https://t.me/ChronoRoom 👈\n\n"
                "After subscribing, press the button:",
                reply_markup=get_subscription_keyboard(),
                parse_mode='Markdown'
            )
            return
    except Exception as e:
        print(f"⚠️ Subscription check error: {e}")
        # Continue working even on check error

    db.update_subscription_status(user.id, True)

    # 1. Check if we're waiting for a group link
    if user_state == STATE_WAITING_GROUP_LINK:
        request_id = context.user_data.pop('waiting_for_group_link', None)
        context.user_data['state'] = STATE_NONE

        if request_id:
            # Check that it's a link
            if not ('t.me/' in text or 'telegram.me/' in text or text.startswith('https://') or text.startswith('@')):
                await update.message.reply_text(
                    "❌ *This doesn't look like a Telegram link!*\n\n"
                    "📌 *Just create a group in Telegram and send the link here*\n"
                    "*Link format:*\n"
                    "• https://t.me/group_name\n"
                    "• @group_name",
                    parse_mode='Markdown'
                )
                context.user_data['state'] = STATE_WAITING_GROUP_LINK
                context.user_data['waiting_for_group_link'] = request_id
                return

            # Save the link and ask about the message
            context.user_data['temp_group_link'] = text
            context.user_data['temp_request_id'] = request_id
            context.user_data['state'] = STATE_WAITING_GROUP_MESSAGE

            await update.message.reply_text(
                "🔗 *Link accepted!*\n\n"
                "✉️ *Do you want to send a common message to all invitees?*\n\n"
                "For example:\n"
                "• Say hello\n"
                "• Tell about the group topic\n"
                "• Suggest something to discuss\n\n"
                "If not - just press 'No, just invite'",
                reply_markup=get_group_message_keyboard(),
                parse_mode='Markdown'
            )
        return

    # 2. If waiting for a group message
    elif user_state == STATE_WAITING_GROUP_MESSAGE:
        request_id = context.user_data.pop('waiting_for_group_message', None)
        group_link = context.user_data.pop('temp_group_link', '')
        context.user_data['state'] = STATE_NONE

        if request_id:
            # Update the request in the database
            db.update_group_request(request_id, group_link, text)
            request = db.get_group_request(request_id)

            # Send invitations
            creator_profile = db.get_profile(request['creator_id'])
            success_count = 0

            for user_id in request['selected_ids']:
                try:
                    target_profile = db.get_profile(user_id)
                    if not target_profile:
                        continue

                    # Check invitee's subscription
                    try:
                        is_target_subscribed = await check_subscription(context.bot, int(user_id))
                        if not is_target_subscribed:
                            continue
                    except:
                        # If check error - still send
                        pass

                    invite_text = (
                        "👥 *You are invited to a group!*\n\n"
                        f"👤 *Organizer:* {escape_md(creator_profile['first_name'])}\n"
                        f"📝 *Message from organizer:*\n{escape_md(text)}\n\n"
                        "⚡ *What to do?*\n"
                        "• ✅ Join - get the group link\n"
                        "• ❌ Decline - politely refuse\n\n"
                        "_The link will appear only after pressing 'Join'_"
                    )

                    await context.bot.send_message(
                        chat_id=int(user_id),
                        text=invite_text,
                        reply_markup=get_group_invite_keyboard(request_id),
                        parse_mode='Markdown'
                    )
                    success_count += 1

                except Exception as e:
                    print(f"Failed to send to {user_id}: {e}")

            # Report to creator
            await update.message.reply_text(
                f"✅ *Done! Invitations sent.*\n\n"
                f"📊 *Statistics:*\n"
                f"• Successful: {success_count} people\n"
                f"• Failed: {len(request['selected_ids']) - success_count} people\n\n"
                f"⏳ *What's next?*\n"
                f"1. People will receive your invitation\n"
                f"2. They will press 'Join' or 'Decline'\n"
                f"3. You will receive notifications about their decision\n"
                f"4. Those who agree will see your link",
                reply_markup=get_main_keyboard(),
                parse_mode='Markdown'
            )
        return

    # 3. If it's a personal message in an active chat
    elif user_state == STATE_IN_CHAT:
        chat_id = context.user_data.get('active_chat')
        chat = db.get_chat_by_id(chat_id)

        if not chat:
            await update.message.reply_text(
                "❌ Chat not found or was closed",
                reply_markup=get_main_keyboard()
            )
            context.user_data['state'] = STATE_NONE
            context.user_data.pop('active_chat', None)
            return

        # Determine who to send the message to
        target_id = chat['user1_id'] if chat['user1_id'] != user.id else chat['user2_id']

        try:
            # Check recipient's subscription
            try:
                is_target_subscribed = await check_subscription(context.bot, target_id)
                if not is_target_subscribed:
                    await update.message.reply_text(
                        "❌ *Failed to send message*\n\n"
                        "This user is not subscribed to the ChronoRoom channel",
                        reply_markup=get_main_keyboard(),
                        parse_mode='Markdown'
                    )
                    context.user_data['state'] = STATE_NONE
                    context.user_data.pop('active_chat', None)
                    return
            except:
                # If check error - still send
                pass

            # Send the message to the recipient
            await context.bot.send_message(
                chat_id=target_id,
                text=(
                    "💌 *You have a new message!*\n\n"
                    f"👤 *From:* {escape_md(user.first_name)}\n"
                    f"📝 *Text:*\n{escape_md(text)}\n\n"
                    "_Press 'Reply' to write back_"
                ),
                reply_markup=get_reply_keyboard(chat_id),
                parse_mode='Markdown'
            )

            # Confirmation to sender
            await update.message.reply_text(
                "✅ *Message sent!*\n\n"
                "⏳ *What's next?*\n"
                "• The person will receive your message\n"
                "• They can press 'Reply'\n"
                "• You will receive their reply here",
                parse_mode='Markdown'
            )

        except BadRequest:
            await update.message.reply_text(
                "❌ *Failed to send message*\n\n"
                "Possible reasons:\n"
                "• The user blocked the bot\n"
                "• They deleted their profile\n"
                "• Technical issues",
                reply_markup=get_main_keyboard(),
                parse_mode='Markdown'
            )
            context.user_data['state'] = STATE_NONE
            context.user_data.pop('active_chat', None)
        return

    # 4. If filling out a profile
    elif user_state == STATE_WRITING_BIO:
        profile = db.get_profile(user.id)

        # If profile already exists with bio, reset state
        if profile and profile.get('bio'):
            context.user_data['state'] = STATE_NONE
            await update.message.reply_text(
                "ℹ️ You already have a profile. Use the menu for navigation.",
                reply_markup=get_main_keyboard()
            )
            return

        # Check text length
        if len(text) < MIN_BIO_LENGTH:
            await update.message.reply_text(
                f"📏 *Need more information!*\n\n"
                f"Minimum: {MIN_BIO_LENGTH} characters\n"
                f"Currently: {len(text)} characters\n\n"
                "💡 *Add details:*\n"
                "_For example, tell about your hobbies, work, interests, "
                "and what you're looking for in this bot._",
                parse_mode='Markdown'
            )
            return

        if len(text) > MAX_BIO_LENGTH:
            await update.message.reply_text(
                f"📏 *Too much text!*\n\n"
                f"Maximum: {MAX_BIO_LENGTH} characters\n"
                f"Currently: {len(text)} characters\n\n"
                "✂️ *Shorten the text:*\n"
                "Keep only the most important and interesting",
                parse_mode='Markdown'
            )
            return

        # Save profile
        db.add_profile(
            user_id=user.id,
            username=user.username,
            first_name=user.first_name,
            bio=text
        )

        # Reset state
        context.user_data['state'] = STATE_NONE

        await update.message.reply_text(
            "🎉 *Great! Your profile is created!*\n\n"
            "✅ *Now you can:*\n"
            "1. 🔍 *Find friends* - browse other profiles\n"
            "2. ✅ *Choose people* - click on names\n"
            "3. 📋 *Confirm selection* - press the button\n"
            "4. 💬 *Communicate* - personally or in a group\n\n"
            "💡 *Tip:* Pages are shuffled each time you open them!\n\n"
            "🚀 *Start right now:*",
            reply_markup=get_main_keyboard(),
            parse_mode='Markdown'
        )
        return

    # 5. General messages (no active state)
    else:
        tips = [
            "💡 Use the buttons below for navigation",
            "💡 Press '🔍 Find friends' to start searching",
            "💡 Want to learn more? Press '📖 How to use'"
        ]

        await update.message.reply_text(
            random.choice(tips),
            reply_markup=get_main_keyboard()
        )

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler for button presses"""
    query = update.callback_query

    # Ignore empty callback_data
    if not query.data:
        return

    # Try to answer the request
    try:
        await query.answer()
    except:
        pass  # Ignore answer errors

    user = query.from_user
    db = context.bot_data['db']
    data = query.data

    try:
        # Check subscription before any action
        is_subscribed = await check_subscription(context.bot, user.id)
        if not is_subscribed:
            db.update_subscription_status(user.id, False)
            await safe_edit_message(query,
                text="❌ *Please subscribe to the channel first!*\n\n"
                     "To use the bot, you need to subscribe:\n"
                     "👉 https://t.me/ChronoRoom 👈\n\n"
                     "After subscribing, press the button:",
                reply_markup=get_subscription_keyboard(),
                parse_mode='Markdown'
            )
            return
    except Exception as e:
        print(f"⚠️ Subscription check error in callback: {e}")
        # Continue working even on check error

    db.update_subscription_status(user.id, True)

    # Subscription check
    if data == 'check_subscription':
        try:
            is_subscribed = await check_subscription(context.bot, user.id)
            if is_subscribed:
                db.update_subscription_status(user.id, True)
                await safe_edit_message(query,
                    text="✅ *Great! You are subscribed!*\n\n"
                         "Now you can use all bot features.",
                    reply_markup=get_main_keyboard(),
                    parse_mode='Markdown'
                )
                context.user_data['state'] = STATE_NONE
            else:
                await safe_edit_message(query,
                    text="❌ *You are not subscribed yet!*\n\n"
                         "Subscribe to the channel via the link:\n"
                         "👉 https://t.me/ChronoRoom 👈\n\n"
                         "After subscribing, press the button below:",
                    reply_markup=get_subscription_keyboard(),
                    parse_mode='Markdown'
                )
        except Exception as e:
            print(f"⚠️ Subscription check error: {e}")
            # On error still allow access
            db.update_subscription_status(user.id, True)
            await safe_edit_message(query,
                text="✅ *Check completed!*\n\n"
                     "Now you can use all bot features.",
                reply_markup=get_main_keyboard(),
                parse_mode='Markdown'
            )
            context.user_data['state'] = STATE_NONE
        return

    # Main menu - reset all states
    if data == 'main_menu':
        context.user_data['state'] = STATE_NONE
        context.user_data.pop('selected_ids', None)
        context.user_data.pop('active_chat', None)
        context.user_data.pop('waiting_for_group_link', None)
        context.user_data.pop('waiting_for_group_message', None)
        context.user_data.pop('temp_request_id', None)
        context.user_data.pop('temp_group_link', None)

        await safe_edit_message(query,
            text="🏠 *Main menu*\n\nChoose an action:",
            reply_markup=get_main_keyboard(),
            parse_mode='Markdown'
        )

    # My profile
    elif data == 'my_profile':
        context.user_data['state'] = STATE_NONE
        profile = db.get_profile(user.id)
        if not profile or not profile.get('bio'):
            await safe_edit_message(query,
                text="❌ *You don't have a profile yet!*\n\nWrite /start to create one",
                reply_markup=get_main_keyboard(),
                parse_mode='Markdown'
            )
            return

        profile_text = (
            f"👤 *Your profile*\n\n"
            f"👋 *Name:* {escape_md(profile['first_name'])}\n"
        )

        if profile.get('username'):
            profile_text += f"🔗 *Username:* @{profile['username']}\n"

        # Safe date retrieval
        created_at = profile.get('created_at', 'Unknown')
        if isinstance(created_at, str) and len(created_at) >= 10:
            profile_text += f"📅 *Created:* {created_at[:10]}\n\n"
        else:
            profile_text += f"📅 *Created:* Unknown\n\n"

        # Safe bio check (bio can be NULL, not just missing)
        bio = profile.get('bio') or ''
        if bio:
            profile_text += f"📝 *About me:*\n{escape_md(bio)}\n\n"
            profile_text += f"📊 *Statistics:*\n"
            profile_text += f"• Characters: {len(bio)}/{MAX_BIO_LENGTH}\n"
            profile_text += f"• Can be edited via /start"
        else:
            profile_text += "📝 *About me:* Profile not filled\n\n"
            profile_text += "📊 *Statistics:*\n"
            profile_text += f"• Characters: 0/{MAX_BIO_LENGTH}\n"
            profile_text += f"• Can be created via /start"

        await safe_edit_message(query,
            text=profile_text,
            reply_markup=get_main_keyboard(),
            parse_mode='Markdown'
        )

    # Find friends
    elif data == 'find_friends':
        context.user_data['state'] = STATE_NONE
        # Reset selection each time it's opened
        context.user_data['selected_ids'] = []
        context.user_data['current_page'] = 0
        await show_profiles_page(query, context, page=0)

    # Pagination
    elif data.startswith('page_'):
        context.user_data['state'] = STATE_NONE
        try:
            page = int(data.split('_')[1])
            await show_profiles_page(query, context, page)
        except:
            await query.answer("❌ Pagination error", show_alert=True)

    # Clicking on page number (do nothing)
    elif data in ['no_page', 'current_page']:
        try:
            await query.answer()
        except:
            pass
        return

    # Selecting a profile
    elif data.startswith('select_'):
        context.user_data['state'] = STATE_NONE
        try:
            selected_id = data.split('_')[1]

            # Initialize selected list
            if 'selected_ids' not in context.user_data:
                context.user_data['selected_ids'] = []

            selected_ids = context.user_data['selected_ids']

            # Add or remove from selected
            if selected_id in selected_ids:
                selected_ids.remove(selected_id)
            else:
                # Check limit
                if len(selected_ids) >= MAX_GROUP_MEMBERS:
                    await query.answer(
                        f"❌ You can select no more than {MAX_GROUP_MEMBERS} people!",
                        show_alert=True
                    )
                    return
                selected_ids.append(selected_id)

            # Save updated list
            context.user_data['selected_ids'] = selected_ids

            # Update current page
            current_page = context.user_data.get('current_page', 0)
            await show_profiles_page(query, context, current_page)
        except:
            await query.answer("❌ Selection error", show_alert=True)

    # Confirmation of selection
    elif data == 'confirm_selection':
        context.user_data['state'] = STATE_NONE
        selected_ids = context.user_data.get('selected_ids', [])

        if not selected_ids:
            await query.answer("❌ You haven't selected anyone!", show_alert=True)
            return

        # Show selection confirmation
        text = f"✅ *You selected {len(selected_ids)} people:*\n\n"

        for i, user_id in enumerate(selected_ids, 1):
            profile = db.get_profile(user_id)
            if profile:
                name = profile.get('first_name', 'No name')
                text += f"{i}. *{name}*\n"

        text += "\n📋 *What's next?*\n"

        # Group can be created from 1 person
        if len(selected_ids) >= 1:
            text += "• 👥 Create group - create a group\n"

        # Personal message only to 1 person
        if len(selected_ids) == 1:
            text += "• 💌 Write personally - send a personal message\n"

        text += "\n⚡ *Or you can:*\n"
        text += "• 🔍 Continue search\n"
        text += "• 🔄 Reset selection and start over"

        await safe_edit_message(query,
            text=text,
            reply_markup=get_selection_confirmation_keyboard(selected_ids),
            parse_mode='Markdown'
        )

    # Reset selection
    elif data == 'reset_selection':
        context.user_data['state'] = STATE_NONE
        context.user_data['selected_ids'] = []
        context.user_data['current_page'] = 0
        await show_profiles_page(query, context, page=0)

    # Write personally (1 person)
    elif data == 'write_personal':
        selected_ids = context.user_data.get('selected_ids', [])

        if len(selected_ids) != 1:
            await query.answer("❌ Select only one person!", show_alert=True)
            return

        target_id = selected_ids[0]
        target_profile = db.get_profile(target_id)

        if not target_profile:
            await query.answer("❌ This user deleted their profile!", show_alert=True)
            context.user_data['selected_ids'] = []
            context.user_data['state'] = STATE_NONE
            await show_profiles_page(query, context, 0)
            return

        # Create chat between users
        chat_id = db.create_chat(user.id, target_id)

        # Save active chat and set state
        context.user_data['active_chat'] = chat_id
        context.user_data['state'] = STATE_IN_CHAT

        await safe_edit_message(query,
            text=(
                f"✍️ *Write a message for {escape_md(target_profile.get('first_name', 'user'))}*\n\n"
                f"📝 *The message will be sent anonymously:*\n"
                f"• The recipient will see your message\n"
                f"• But won't see your username\n"
                f"• They can reply via the 'Reply' button\n\n"
                "💡 *What to write:*\n"
                "• Introduce yourself\n"
                "• Tell why you wrote\n"
                "• Suggest a topic for conversation\n\n"
                "ℹ️ *To exit chat mode, press /start*"
            ),
            parse_mode='Markdown'
        )

    # Reply to a message
    elif data.startswith('reply_'):
        try:
            chat_id = data.split('reply_')[1]
            chat = db.get_chat_by_id(chat_id)

            if not chat:
                await query.answer("❌ Chat not found!", show_alert=True)
                return

            # Determine who the second participant is
            target_id = chat['user1_id'] if chat['user1_id'] != user.id else chat['user2_id']
            target_profile = db.get_profile(target_id)

            if not target_profile:
                await query.answer("❌ User deleted their profile!", show_alert=True)
                return

            # Activate chat for reply and set state
            context.user_data['active_chat'] = chat_id
            context.user_data['state'] = STATE_IN_CHAT

            await safe_edit_message(query,
                text=(
                    f"✍️ *Write a reply for {escape_md(target_profile.get('first_name', 'user'))}*\n\n"
                    f"📝 *The message will be sent anonymously:*\n"
                    f"• The recipient will see your reply\n"
                    f"• But won't see your username\n"
                    f"• They can reply via the 'Reply' button\n\n"
                    "💡 *What to write:*\n"
                    "• Reply to the previous message\n"
                    "• Ask a question\n"
                    "• Suggest continuing the conversation\n\n"
                    "ℹ️ *To exit chat mode, press /start*"
                ),
                parse_mode='Markdown'
            )
        except:
            await query.answer("❌ Reply error", show_alert=True)

    # Ignore message
    elif data == 'ignore_message':
        context.user_data['state'] = STATE_NONE
        context.user_data.pop('active_chat', None)
        await safe_edit_message(query,
            text="❌ Message ignored",
            reply_markup=get_main_keyboard()
        )

    # Create group (1+ people)
    elif data == 'create_group':
        selected_ids = context.user_data.get('selected_ids', [])

        if len(selected_ids) < 1:
            await query.answer("❌ Select at least 1 person!", show_alert=True)
            return

        if len(selected_ids) > MAX_GROUP_MEMBERS:
            await query.answer(f"❌ Maximum {MAX_GROUP_MEMBERS} people!", show_alert=True)
            return

        # Create group request
        request_id = db.create_group_request(user.id, selected_ids)

        # Save request ID and set state to waiting for link
        context.user_data['waiting_for_group_link'] = request_id
        context.user_data['state'] = STATE_WAITING_GROUP_LINK

        await safe_edit_message(query,
            text=(
                "👥 *Creating a group*\n\n"
                "📌 *What you need to do:*\n"
                "1. Create a group in Telegram\n"
                "2. Get an invite link to the group\n"
                "3. Send the link here\n\n"
                "🔗 *Link format:*\n"
                "• https://t.me/group_name\n"
                "• @group_name\n\n"
                f"👤 *Who will receive the invitation:*\n"
                f"{len(selected_ids)} people you selected\n\n"
                "✍️ *After that, you can send a common message to all invitees*\n\n"
                "ℹ️ *To cancel group creation, press /start*"
            ),
            parse_mode='Markdown'
        )

    # Send group message
    elif data == 'send_group_message':
        request_id = context.user_data.get('temp_request_id')
        group_link = context.user_data.get('temp_group_link')

        if not request_id or not group_link:
            await query.answer("❌ Data error!", show_alert=True)
            await safe_edit_message(query,
                text="❌ An error occurred. Start over.",
                reply_markup=get_main_keyboard()
            )
            context.user_data['state'] = STATE_NONE
            return

        context.user_data['waiting_for_group_message'] = request_id
        context.user_data['state'] = STATE_WAITING_GROUP_MESSAGE

        await safe_edit_message(query,
            text=(
                "✍️ *Great! Now write a message for the invitees:*\n\n"
                "💡 *What you can write:*\n"
                "• Say hello\n"
                "• Tell about the group topic\n"
                "• Suggest something to discuss\n"
                "• Explain why you created the group\n\n"
                "*Example:*\n"
                "_Hi everyone! I created this group to discuss Python programming. "
                "Let's share experience, ask questions, and help each other!_\n\n"
                "ℹ️ *To cancel, press /start*"
            ),
            parse_mode='Markdown'
        )

    # Skip group message
    elif data == 'skip_group_message':
        request_id = context.user_data.get('temp_request_id')
        group_link = context.user_data.get('temp_group_link')

        if not request_id or not group_link:
            await query.answer("❌ Data error!", show_alert=True)
            await safe_edit_message(query,
                text="❌ An error occurred. Start over.",
                reply_markup=get_main_keyboard()
            )
            context.user_data['state'] = STATE_NONE
            return

        # Update request in database
        db.update_group_request(request_id, group_link, None)
        request = db.get_group_request(request_id)

        # Send invitations
        creator_profile = db.get_profile(request['creator_id'])
        success_count = 0

        for user_id in request['selected_ids']:
            try:
                target_profile = db.get_profile(user_id)
                if not target_profile:
                    continue

                # Check invitee's subscription
                try:
                    is_target_subscribed = await check_subscription(context.bot, int(user_id))
                    if not is_target_subscribed:
                        continue
                except:
                    # If check error - still send
                    pass

                invite_text = (
                    "👥 *You are invited to a group!*\n\n"
                    f"👤 *Organizer:* {escape_md(creator_profile.get('first_name', 'User'))}\n\n"
                    "⚡ *What to do?*\n"
                    "• ✅ Join - get the group link\n"
                    "• ❌ Decline - politely refuse\n\n"
                    "_The link will appear only after pressing 'Join'_"
                )

                await context.bot.send_message(
                    chat_id=int(user_id),
                    text=invite_text,
                    reply_markup=get_group_invite_keyboard(request_id),
                    parse_mode='Markdown'
                )
                success_count += 1

            except Exception as e:
                print(f"Failed to send to {user_id}: {e}")

        # Report to creator
        await safe_edit_message(query,
            text=(
                f"✅ *Done! Invitations sent.*\n\n"
                f"📊 *Statistics:*\n"
                f"• Successful: {success_count} people\n"
                f"• Failed: {len(request['selected_ids']) - success_count} people\n\n"
                f"⏳ *What's next?*\n"
                f"1. People will receive your invitation\n"
                f"2. They will press 'Join' or 'Decline'\n"
                f"3. You will receive notifications about their decision\n"
                f"4. Those who agree will see your link"
            ),
            reply_markup=get_main_keyboard(),
            parse_mode='Markdown'
        )

        # Clear temporary data and reset state
        context.user_data.pop('temp_request_id', None)
        context.user_data.pop('temp_group_link', None)
        context.user_data['state'] = STATE_NONE

    # Join group
    elif data.startswith('join_'):
        try:
            request_id = data.split('join_')[1]
            request = db.get_group_request(request_id)

            if request and request['group_link']:
                # Show link
                message_text = f"✅ *You joined the group!*\n\n🔗 *Group link:*\n{escape_md(request['group_link'])}\n\n"

                if request.get('group_message'):
                    message_text += f"📝 *Message from organizer:*\n{escape_md(request['group_message'])}\n\n"

                creator_profile = db.get_profile(request['creator_id'])
                creator_name = escape_md(creator_profile.get('first_name', 'Organizer') if creator_profile else 'Organizer')

                message_text += (
                    "📌 *What to do next:*\n"
                    "1. Follow the link\n"
                    "2. Introduce yourself in the group\n"
                    "3. Tell about your interests\n"
                    "4. Chat and find common ground!\n\n"
                    f"👤 *Organizer:* {creator_name}"
                )

                # IMPORTANT: Use reply_text for a new message
                await query.message.reply_text(
                    text=message_text,
                    parse_mode='Markdown'
                )

                # Notify creator
                try:
                    user_profile = db.get_profile(user.id)
                    user_name = user_profile.get('first_name', 'User') if user_profile else 'User'
                    await context.bot.send_message(
                        chat_id=int(request['creator_id']),
                        text=f"✅ {user_name} accepted your group invitation!"
                    )
                except:
                    pass

                context.user_data['state'] = STATE_NONE
        except Exception as e:
            print(f"Error joining group: {e}")
            await query.answer("❌ Error joining", show_alert=True)

    # Decline group
    elif data.startswith('decline_'):
        try:
            request_id = data.split('decline_')[1]

            await safe_edit_message(query,
                text=(
                    "❌ *You declined the invitation*\n\n"
                    "It's okay! Everyone has the right to choose.\n\n"
                    "🔍 *You can continue searching for other people*"
                ),
                reply_markup=get_main_keyboard(),
                parse_mode='Markdown'
            )

            # Notify creator
            try:
                request = db.get_group_request(request_id)
                user_profile = db.get_profile(user.id)
                user_name = user_profile.get('first_name', 'User') if user_profile else 'User'
                await context.bot.send_message(
                    chat_id=int(request['creator_id']),
                    text=f"❌ {user_name} declined your group invitation."
                )
            except:
                pass

            context.user_data['state'] = STATE_NONE
        except:
            await query.answer("❌ Decline error", show_alert=True)

    # Delete profile
    elif data == 'delete_ask':
        context.user_data['state'] = STATE_NONE
        await safe_edit_message(query,
            text=(
                "⚠️ *Delete confirmation*\n\n"
                "Are you sure you want to delete your profile?\n\n"
                "❌ *After deletion:*\n"
                "• Your profile will disappear from search\n"
                "• You won't receive messages\n"
                "• Group invitations will stop\n\n"
                "✅ *You can:*\n"
                "• Create a new profile at any time\n"
                "• Start over from scratch"
            ),
            reply_markup=get_confirmation_keyboard(),
            parse_mode='Markdown'
        )

    elif data == 'confirm_delete':
        db.delete_profile(user.id)
        context.user_data['state'] = STATE_NONE
        await safe_edit_message(query,
            text=(
                "🗑️ *Profile deleted*\n\n"
                "Your profile has been successfully deleted from the system.\n\n"
                "🔁 *Want to create a new one?*\n"
                "Write /start to start over"
            ),
            parse_mode='Markdown'
        )

    elif data == 'cancel_delete':
        context.user_data['state'] = STATE_NONE
        await safe_edit_message(query,
            text="✅ Deletion cancelled. Your profile is saved.",
            reply_markup=get_main_keyboard()
        )

    # Tutorial
    elif data == 'tutorial':
        context.user_data['state'] = STATE_NONE
        tutorial_text = (
            "📖 *ChronoRoom Tutorial*\n\n"
            "Here you will learn how to use all bot features.\n\n"
            "Choose a topic:"
        )

        await safe_edit_message(query,
            text=tutorial_text,
            reply_markup=get_tutorial_keyboard(),
            parse_mode='Markdown'
        )

    elif data == 'tutor_profile':
        context.user_data['state'] = STATE_NONE
        await safe_edit_message(query,
            text=(
                "📝 *Creating a profile*\n\n"
                "🎯 *Goal:* Tell about yourself so others want to communicate with you.\n\n"
                "✏️ *What to write:*\n"
                "• Your interests and hobbies\n"
                "• What you do (work/study)\n"
                "• What you're looking for in this bot\n"
                "• About your character\n\n"
                "📏 *Requirements:*\n"
                f"• Minimum: {MIN_BIO_LENGTH} characters\n"
                f"• Maximum: {MAX_BIO_LENGTH} characters\n\n"
                "💡 *Example of a good profile:*\n"
                "_I love movies (especially sci-fi), travel, learn Python. "
                "I work in IT, looking for friends to chat and work on joint projects. "
                "Open to new acquaintances!_"
            ),
            reply_markup=get_tutorial_keyboard(),
            parse_mode='Markdown'
        )

    elif data == 'tutor_search':
        context.user_data['state'] = STATE_NONE
        await safe_edit_message(query,
            text=(
                "🔍 *Finding friends*\n\n"
                "🎯 *How it works:*\n"
                "1. Press 'Find friends'\n"
                f"2. You see {PROFILES_PER_PAGE} profiles per page\n"
                "3. Pages are *SHUFFLED* each time you open them\n"
                "4. Navigate: [◀️] [1/5] [▶️]\n"
                "5. Click on names to select people ✅\n"
                f"6. You can select up to {MAX_GROUP_MEMBERS} people\n"
                "7. Press '✅ Confirm selection'\n\n"
                "👆 *How to select:*\n"
                "• Click on a name - a checkmark will appear ✅\n"
                "• Click again - the checkmark will disappear\n"
                "• The number of selected is shown at the top\n\n"
                "🚀 *After confirmation:*\n"
                "• For 1 person: write personally OR create a group\n"
                "• For 2+ people: only create a group"
            ),
            reply_markup=get_tutorial_keyboard(),
            parse_mode='Markdown'
        )

    elif data == 'tutor_group':
        context.user_data['state'] = STATE_NONE
        await safe_edit_message(query,
            text=(
                "👥 *Creating a group*\n\n"
                "🎯 *Goal:* Unite people with common interests.\n\n"
                "📋 *Requirements:*\n"
                f"• You need to select from 1 to {MAX_GROUP_MEMBERS} people\n"
                "• You must have created a group in Telegram\n\n"
                "🚀 *Step by step:*\n"
                "1. Select people in search\n"
                "2. Press 'Confirm selection'\n"
                "3. Press 'Create group'\n"
                "4. Create a group in Telegram\n"
                "5. Get a link to the group\n"
                "6. Send the link to the bot\n"
                "7. The bot will ask if you want to send a message\n"
                "8. Write a message or skip\n"
                "9. The bot will send invitations\n"
                "10. People press 'Join' or 'Decline'\n"
                "11. Those who agree get your link\n\n"
                "⏳ *What invitees see:*\n"
                "• Your name and profile\n"
                "• Your message (if sent)\n"
                "• 'Join' and 'Decline' buttons\n"
                "• Group link (only after 'Join')"
            ),
            reply_markup=get_tutorial_keyboard(),
            parse_mode='Markdown'
        )

    elif data == 'tutor_chat':
        context.user_data['state'] = STATE_NONE
        await safe_edit_message(query,
            text=(
                "💬 *Personal communication*\n\n"
                "🎯 *Goal:* Anonymous communication via the bot.\n\n"
                "🔒 *How it works:*\n"
                "1. You select 1 person\n"
                "2. Confirm selection\n"
                "3. Press 'Write personally'\n"
                "4. Write a message via the bot\n"
                "5. They receive it *without your username*\n"
                "6. They see a 'Reply' button\n"
                "7. They press 'Reply' and write back\n"
                "8. You receive the reply in the same chat\n\n"
                "🛡️ *Security:*\n"
                "• Your username is hidden\n"
                "• Only the name from the profile is visible\n"
                "• Messages go through the bot\n"
                "• You can stop communication at any time\n\n"
                "💡 *Tip:* Be polite and respectful!"
            ),
            reply_markup=get_tutorial_keyboard(),
            parse_mode='Markdown'
        )

async def show_profiles_page(query, context, page):
    """Show a page with profiles"""
    db = context.bot_data['db']

    # Save current page
    context.user_data['current_page'] = page

    # Get profiles for this page (automatically shuffled!)
    profiles = db.get_paginated_profiles(page=page, exclude_user_id=query.from_user.id)
    total_pages = db.get_total_pages(exclude_user_id=query.from_user.id)

    if not profiles:
        await safe_edit_message(query,
            text=(
                "😔 *Nobody here yet...*\n\n"
                "🔍 *What to do?*\n"
                "• Tell your friends about the bot\n"
                "• Share it in your chats\n"
                "• First users will appear soon!\n\n"
                "💡 *In the meantime:*\n"
                "Make sure your profile is interesting and detailed"
            ),
            reply_markup=get_main_keyboard(),
            parse_mode='Markdown'
        )
        return

    # Compose text with profiles
    text = f"👥 *Found profiles* (page {page+1}/{total_pages}):\n\n"

    selected_ids = context.user_data.get('selected_ids', [])

    for i, profile in enumerate(profiles, 1):
        # Determine if the person is selected
        is_selected = str(profile['user_id']) in selected_ids
        check_mark = "✅ " if is_selected else ""

        # Truncate bio for preview (bio can be NULL in the DB, not just missing)
        bio_preview = profile.get('bio') or 'No description'
        if len(bio_preview) > 80:
            bio_preview = bio_preview[:77] + "..."
        bio_preview = escape_md(bio_preview)

        name = escape_md(profile.get('first_name') or 'No name')
        text += f"{i}. {check_mark}*{name}*\n"
        text += f"   _{bio_preview}_\n\n"

    text += "💡 *How to select:* Click on a person's name\n"

    if selected_ids:
        text += f"✅ *Selected:* {len(selected_ids)}/{MAX_GROUP_MEMBERS} people\n"
        text += "📋 *What's next:* Press 'Confirm selection'"
    else:
        text += "📝 *Tip:* Choose people who interest you"

    keyboard = get_profiles_keyboard(profiles, page, total_pages, selected_ids)

    await safe_edit_message(query,
        text=text,
        reply_markup=keyboard,
        parse_mode='Markdown'
    )

async def safe_edit_message(query, text, reply_markup=None, parse_mode=None):
    """Safely edit a message with error handling"""
    try:
        # Try to edit the message
        await query.edit_message_text(
            text=text,
            reply_markup=reply_markup,
            parse_mode=parse_mode
        )
    except BadRequest as e:
        if "Message is not modified" in str(e):
            # Ignore "message not modified" error
            pass
        elif "Message can't be edited" in str(e):
            # If the message can't be edited, send a new one
            try:
                await query.message.reply_text(
                    text=text,
                    reply_markup=reply_markup,
                    parse_mode=parse_mode
                )
            except:
                pass
        else:
            # Other errors - log them
            print(f"⚠️ Error editing message: {e}")
    except Exception as e:
        print(f"⚠️ Error editing message: {e}")

# ==================== MAIN FUNCTION ====================
def main():
    """Main bot launch function"""

    print("="*60)
    print("🤖 STARTING CHRONOROOM BOT")
    print("="*60)

    # Load token
    token = load_token()
    if not token:
        return

    # Check token
    if ':' not in token:
        print(f"\n❌ ERROR: Invalid token format!")
        print(f"Token must contain ':', for example: 1234567890:ABCdefGHI...")
        print(f"Your token: {token[:30]}...")
        print("\n📝 Get a new token from @BotFather with /newbot")
        return

    # Initialize database
    try:
        db = Database()
    except Exception as e:
        print(f"❌ Database error: {e}")
        return

    print(f"✅ Database: {DATABASE_FILE}")
    print(f"📁 Token from file: {TOKEN_FILE}")
    print(f"📢 Subscription channel: {CHANNEL_USERNAME}")
    print("="*60)

    # Create the application
    try:
        app = Application.builder().token(token).build()
        app.bot_data['db'] = db
    except Exception as e:
        print(f"❌ Error creating bot: {e}")
        return

    # Register handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("profile", profile_command))
    app.add_handler(CommandHandler("delete", delete_command))
    app.add_handler(CommandHandler("admin", admin_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(CallbackQueryHandler(button_callback))

    # Start the bot
    print("\n🚀 Bot is running!")
    print("📱 Open Telegram and find your bot")
    print("💬 Send /start to begin")
    print("="*60)
    print("🛑 Press Ctrl+C to stop")
    print("="*60)

    try:
        app.run_polling()
    except KeyboardInterrupt:
        print("\n🛑 Bot stopped by user")
    except Exception as e:
        print(f"\n❌ Error: {e}")
    finally:
        db.close()

if __name__ == '__main__':
    main()

