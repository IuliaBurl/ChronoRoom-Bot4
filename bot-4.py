from __future__ import annotations

import html
import json
import logging
import math
import os
import random
import re
import sqlite3
import time
import uuid
from enum import Enum, auto
from pathlib import Path

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatMemberStatus, ParseMode
from telegram.error import BadRequest, TelegramError
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    Defaults,
    MessageHandler,
    filters,
)

logger = logging.getLogger("chronoroom")

Context = ContextTypes.DEFAULT_TYPE

DATABASE_FILE = os.getenv("DATABASE_FILE", "chronoroom.db")
TOKEN_FILE = "token.txt"
CHANNEL_USERNAME = os.getenv("CHANNEL_USERNAME", "@ChronoRoom")
CHANNEL_URL = f"https://t.me/{CHANNEL_USERNAME.lstrip('@')}"
ADMIN_IDS = {
    int(item) for item in os.getenv("ADMIN_IDS", "").split(",") if item.strip().isdigit()
}

PROFILES_PER_PAGE = 4
MIN_BIO_LENGTH = 15
MAX_BIO_LENGTH = 400
MAX_MESSAGE_LENGTH = 1000
MAX_GROUP_MEMBERS = 8
SUBSCRIPTION_CACHE_TTL = 60

GROUP_LINK_PATTERN = re.compile(
    r"^(?:https?://)?(?:t|telegram)\.me/[A-Za-z0-9_+\-/]+$|^@[A-Za-z0-9_]{5,32}$"
)


class State(Enum):
    NONE = auto()
    WRITING_BIO = auto()
    IN_CHAT = auto()
    WAITING_GROUP_LINK = auto()
    CHOOSING_GROUP_MESSAGE = auto()
    WAITING_GROUP_MESSAGE = auto()


SUBSCRIPTION_REQUIRED_TEXT = (
    "📢 <b>Please subscribe to the channel first!</b>\n\n"
    "To use the bot, you need to subscribe:\n"
    f"👉 {CHANNEL_URL} 👈\n\n"
    "After subscribing, press the button below:"
)

WELCOME_NEW_USER_TEXT = (
    "🎉 <b>Welcome to ChronoRoom!</b>\n\n"
    "🤔 <b>What is this?</b>\n"
    "A bot for finding friends by interests!\n\n"
    "🚀 <b>How it works:</b>\n"
    "1. Create a profile about yourself\n"
    "2. Find interesting people\n"
    "3. Chat or create groups!\n\n"
    "✏️ <b>Let's start!</b>\n"
    f"Tell us about yourself (from {MIN_BIO_LENGTH} to {MAX_BIO_LENGTH} characters):\n\n"
    "💡 <b>What to write:</b>\n"
    "• Your interests and hobbies\n"
    "• What you do (work/study)\n"
    "• What you're looking for in this bot\n\n"
    "<b>Example:</b>\n"
    "<i>I love movies, traveling, programming. Looking for friends to chat "
    "and work on joint projects. I work in IT.</i>"
)

HELP_TEXT = (
    "🆘 <b>ChronoRoom Help</b>\n\n"
    "📚 <b>Main commands:</b>\n"
    "• /start - Main menu\n"
    "• /help - This help\n"
    "• /profile - Show my profile\n"
    "• /delete - Delete profile\n\n"
    "🚀 <b>Quick start:</b>\n"
    "1. Write about yourself at start\n"
    "2. Press 'Find friends'\n"
    "3. Choose interesting profiles\n"
    "4. Press 'Confirm selection'\n"
    "5. Chat or create groups!\n\n"
    "📖 <b>Detailed instructions:</b>\n"
    "Press 'How to use' in the main menu"
)

NO_PROFILE_TEXT = "❌ <b>You don't have a profile yet!</b>\n\nWrite /start to create one"

EMPTY_SEARCH_TEXT = (
    "😔 <b>Nobody here yet...</b>\n\n"
    "🔍 <b>What to do?</b>\n"
    "• Tell your friends about the bot\n"
    "• Share it in your chats\n"
    "• First users will appear soon!\n\n"
    "💡 <b>In the meantime:</b>\n"
    "Make sure your profile is interesting and detailed"
)

DELETE_CONFIRM_TEXT = (
    "⚠️ <b>Delete confirmation</b>\n\n"
    "Are you sure you want to delete your profile?\n\n"
    "❌ <b>After deletion:</b>\n"
    "• Your profile will disappear from search\n"
    "• You won't receive messages\n"
    "• Group invitations will stop\n\n"
    "✅ <b>You can:</b>\n"
    "• Create a new profile at any time\n"
    "• Start over from scratch"
)

GROUP_LINK_PROMPT = (
    "👥 <b>Creating a group</b>\n\n"
    "📌 <b>What you need to do:</b>\n"
    "1. Create a group in Telegram\n"
    "2. Get an invite link to the group\n"
    "3. Send the link here\n\n"
    "🔗 <b>Link format:</b>\n"
    "• https://t.me/group_name\n"
    "• @group_name\n\n"
    "{recipients}\n\n"
    "✍️ <b>After that, you can send a common message to all invitees</b>\n\n"
    "ℹ️ <b>To cancel, press /start</b>"
)

TUTORIAL_INTRO_TEXT = (
    "📖 <b>ChronoRoom Tutorial</b>\n\n"
    "Here you will learn how to use all bot features.\n\n"
    "Choose a topic:"
)

TUTORIAL_TOPICS = {
    "profile": (
        "📝 <b>Creating a profile</b>\n\n"
        "🎯 <b>Goal:</b> Tell about yourself so others want to communicate with you.\n\n"
        "✏️ <b>What to write:</b>\n"
        "• Your interests and hobbies\n"
        "• What you do (work/study)\n"
        "• What you're looking for in this bot\n"
        "• About your character\n\n"
        "📏 <b>Requirements:</b>\n"
        f"• Minimum: {MIN_BIO_LENGTH} characters\n"
        f"• Maximum: {MAX_BIO_LENGTH} characters\n\n"
        "💡 <b>Example of a good profile:</b>\n"
        "<i>I love movies (especially sci-fi), travel, learn Python. "
        "I work in IT, looking for friends to chat and work on joint projects. "
        "Open to new acquaintances!</i>\n\n"
        "✏️ You can change your profile any time with the 'Edit profile' button."
    ),
    "search": (
        "🔍 <b>Finding friends</b>\n\n"
        "🎯 <b>How it works:</b>\n"
        "1. Press 'Find friends'\n"
        f"2. You see {PROFILES_PER_PAGE} profiles per page\n"
        "3. Profiles are <b>shuffled</b> each time you start a new search\n"
        "4. Navigate: [◀️] [1/5] [▶️]\n"
        "5. Click on names to select people ✅\n"
        f"6. You can select up to {MAX_GROUP_MEMBERS} people\n"
        "7. Press '✅ Confirm selection'\n\n"
        "👆 <b>How to select:</b>\n"
        "• Click on a name - a checkmark will appear ✅\n"
        "• Click again - the checkmark will disappear\n"
        "• The number of selected is shown at the bottom\n\n"
        "🚀 <b>After confirmation:</b>\n"
        "• For 1 person: write personally OR create a group\n"
        "• For 2+ people: only create a group"
    ),
    "group": (
        "👥 <b>Creating a group</b>\n\n"
        "🎯 <b>Goal:</b> Unite people with common interests.\n\n"
        "📋 <b>Requirements:</b>\n"
        f"• You need to select from 1 to {MAX_GROUP_MEMBERS} people\n"
        "• You must have created a group in Telegram\n\n"
        "🚀 <b>Step by step:</b>\n"
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
        "⏳ <b>What invitees see:</b>\n"
        "• Your name\n"
        "• Your message (if sent)\n"
        "• 'Join' and 'Decline' buttons\n"
        "• Group link (only after 'Join')"
    ),
    "chat": (
        "💬 <b>Personal communication</b>\n\n"
        "🎯 <b>Goal:</b> Anonymous communication via the bot.\n\n"
        "🔒 <b>How it works:</b>\n"
        "1. You select 1 person\n"
        "2. Confirm selection\n"
        "3. Press 'Write personally'\n"
        "4. Write a message via the bot\n"
        "5. They receive it <b>without your username</b>\n"
        "6. They see a 'Reply' button\n"
        "7. They press 'Reply' and write back\n"
        "8. You receive the reply in the same chat\n\n"
        "🛡️ <b>Security:</b>\n"
        "• Your username is hidden\n"
        "• Only the name from the profile is visible\n"
        "• Messages go through the bot\n"
        "• You can stop communication at any time\n\n"
        "💡 <b>Tip:</b> Be polite and respectful!"
    ),
}


def esc(value) -> str:
    return html.escape(str(value)) if value else ""


def display_name(profile: dict | None, fallback: str = "No name") -> str:
    return (profile or {}).get("first_name") or fallback


def other_participant(chat: dict, user_id: int) -> int:
    return chat["user2_id"] if chat["user1_id"] == user_id else chat["user1_id"]


class Database:
    HAS_BIO = "bio IS NOT NULL AND TRIM(bio) != ''"

    def __init__(self, path: str = DATABASE_FILE) -> None:
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS profiles (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                bio TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS group_requests (
                request_id TEXT PRIMARY KEY,
                creator_id INTEGER NOT NULL,
                selected_ids TEXT NOT NULL,
                group_link TEXT,
                group_message TEXT,
                status TEXT DEFAULT 'pending',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS active_chats (
                chat_id TEXT PRIMARY KEY,
                user1_id INTEGER NOT NULL,
                user2_id INTEGER NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        self._ensure_column("group_requests", "group_message", "TEXT")
        with self.conn:
            self.conn.execute(f"DELETE FROM profiles WHERE NOT ({self.HAS_BIO})")
        logger.info("Database ready: %s", DATABASE_FILE)

    def _ensure_column(self, table: str, column: str, definition: str) -> None:
        existing = {row["name"] for row in self.conn.execute(f"PRAGMA table_info({table})")}
        if column not in existing:
            self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
            self.conn.commit()

    def upsert_profile(
        self, user_id: int, username: str | None, first_name: str | None, bio: str
    ) -> None:
        with self.conn:
            self.conn.execute(
                """
                INSERT INTO profiles (user_id, username, first_name, bio)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    username = excluded.username,
                    first_name = excluded.first_name,
                    bio = excluded.bio
                """,
                (user_id, username, first_name, bio),
            )

    def get_profile(self, user_id: int) -> dict | None:
        row = self.conn.execute(
            f"SELECT * FROM profiles WHERE user_id = ? AND {self.HAS_BIO}", (user_id,)
        ).fetchone()
        return dict(row) if row else None

    def get_profile_ids(self, exclude_user_id: int) -> list[int]:
        rows = self.conn.execute(
            f"SELECT user_id FROM profiles WHERE user_id != ? AND {self.HAS_BIO}",
            (exclude_user_id,),
        ).fetchall()
        return [row["user_id"] for row in rows]

    def get_profiles_by_ids(self, user_ids: list[int]) -> list[dict]:
        if not user_ids:
            return []
        placeholders = ",".join("?" * len(user_ids))
        rows = self.conn.execute(
            f"SELECT * FROM profiles WHERE user_id IN ({placeholders}) AND {self.HAS_BIO}",
            user_ids,
        ).fetchall()
        by_id = {row["user_id"]: dict(row) for row in rows}
        return [by_id[user_id] for user_id in user_ids if user_id in by_id]

    def delete_profile(self, user_id: int) -> bool:
        with self.conn:
            cursor = self.conn.execute("DELETE FROM profiles WHERE user_id = ?", (user_id,))
            self.conn.execute(
                "DELETE FROM active_chats WHERE user1_id = ? OR user2_id = ?",
                (user_id, user_id),
            )
        return cursor.rowcount > 0

    def create_group_request(self, creator_id: int, selected_ids: list[int]) -> str:
        request_id = f"group_{uuid.uuid4().hex[:16]}"
        with self.conn:
            self.conn.execute(
                "INSERT INTO group_requests (request_id, creator_id, selected_ids) VALUES (?, ?, ?)",
                (request_id, creator_id, json.dumps(selected_ids)),
            )
        return request_id

    def get_group_request(self, request_id: str) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM group_requests WHERE request_id = ?", (request_id,)
        ).fetchone()
        if not row:
            return None
        data = dict(row)
        data["selected_ids"] = [int(user_id) for user_id in json.loads(data["selected_ids"])]
        return data

    def update_group_request(
        self, request_id: str, group_link: str, group_message: str | None
    ) -> None:
        with self.conn:
            self.conn.execute(
                """
                UPDATE group_requests
                SET group_link = ?, group_message = ?, status = 'sent'
                WHERE request_id = ?
                """,
                (group_link, group_message, request_id),
            )

    def create_chat(self, user1_id: int, user2_id: int) -> str:
        low, high = sorted((int(user1_id), int(user2_id)))
        chat_id = f"chat_{low}_{high}"
        with self.conn:
            self.conn.execute(
                "INSERT OR IGNORE INTO active_chats (chat_id, user1_id, user2_id) VALUES (?, ?, ?)",
                (chat_id, low, high),
            )
        return chat_id

    def get_chat(self, chat_id: str | None) -> dict | None:
        if not chat_id:
            return None
        row = self.conn.execute(
            "SELECT * FROM active_chats WHERE chat_id = ?", (chat_id,)
        ).fetchone()
        return dict(row) if row else None

    def close(self) -> None:
        self.conn.close()


class SubscriptionChecker:
    def __init__(self, channel: str, ttl: int = SUBSCRIPTION_CACHE_TTL) -> None:
        self.channel = channel
        self.ttl = ttl
        self._valid_until: dict[int, float] = {}

    async def is_subscribed(self, bot, user_id: int, force: bool = False) -> bool:
        now = time.monotonic()
        if not force and self._valid_until.get(user_id, 0.0) > now:
            return True

        try:
            member = await bot.get_chat_member(chat_id=self.channel, user_id=user_id)
        except TelegramError as exc:
            logger.warning("Subscription check failed for %s, allowing access: %s", user_id, exc)
            return True

        subscribed = member.status in (
            ChatMemberStatus.OWNER,
            ChatMemberStatus.ADMINISTRATOR,
            ChatMemberStatus.MEMBER,
        ) or (member.status == ChatMemberStatus.RESTRICTED and member.is_member)

        if subscribed:
            self._valid_until[user_id] = now + self.ttl
        else:
            self._valid_until.pop(user_id, None)
        return subscribed


def _btn(text: str, data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text, callback_data=data)


def main_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [_btn("🔍 Find friends", "find_friends")],
            [_btn("👤 My profile", "my_profile"), _btn("✏️ Edit profile", "edit_profile")],
            [_btn("📖 How to use", "tutorial")],
            [_btn("🗑️ Delete profile", "delete_ask")],
        ]
    )


def profiles_keyboard(
    profiles: list[dict], page: int, total_pages: int, selected_ids: list[int]
) -> InlineKeyboardMarkup:
    rows = []
    for profile in profiles:
        mark = "✅ " if profile["user_id"] in selected_ids else ""
        name = display_name(profile)[:20]
        rows.append([_btn(f"{mark}{name}", f"select_{profile['user_id']}")])

    if selected_ids:
        rows.append(
            [_btn(f"✅ Confirm selection ({len(selected_ids)} people)", "confirm_selection")]
        )

    back = _btn("◀️ Back", f"page_{page - 1}") if page > 0 else _btn("⏺️", "no_page")
    forward = (
        _btn("Forward ▶️", f"page_{page + 1}")
        if page < total_pages - 1
        else _btn("⏺️", "no_page")
    )
    rows.append([back, _btn(f"{page + 1}/{total_pages}", "current_page"), forward])
    rows.append([_btn("🏠 Main menu", "main_menu")])
    return InlineKeyboardMarkup(rows)


def selection_keyboard(selected_count: int) -> InlineKeyboardMarkup:
    rows = [
        [_btn(f"📋 Selected: {selected_count} people", "show_selected")],
        [_btn("👥 Create group", "create_group")],
    ]
    if selected_count == 1:
        rows.append([_btn("💌 Write personally", "write_personal")])
    rows.append(
        [_btn("🔍 Continue search", "continue_search"), _btn("🔄 Reset selection", "reset_selection")]
    )
    rows.append([_btn("🏠 Main menu", "main_menu")])
    return InlineKeyboardMarkup(rows)


def group_invite_keyboard(request_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[_btn("✅ Join", f"join_{request_id}"), _btn("❌ Decline", f"decline_{request_id}")]]
    )


def chat_reply_keyboard(chat_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[_btn("💌 Reply", f"reply_{chat_id}"), _btn("❌ Ignore", "ignore_message")]]
    )


def confirm_delete_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[_btn("✅ Yes, delete", "confirm_delete"), _btn("❌ No, keep", "cancel_delete")]]
    )


def tutorial_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [_btn("📝 Creating a profile", "tutor_profile")],
            [_btn("🔍 Finding friends", "tutor_search")],
            [_btn("👥 Creating a group", "tutor_group")],
            [_btn("💬 Communication", "tutor_chat")],
            [_btn("🏠 Main menu", "main_menu")],
        ]
    )


def subscription_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("📢 Subscribe to channel", url=CHANNEL_URL),
                _btn("✅ I subscribed", "check_subscription"),
            ]
        ]
    )


def group_message_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                _btn("✅ Yes, send message", "send_group_message"),
                _btn("❌ No, just invite", "skip_group_message"),
            ]
        ]
    )


def get_db(context: Context) -> Database:
    return context.bot_data["db"]


def get_checker(context: Context) -> SubscriptionChecker:
    return context.bot_data["subscriptions"]


def reset_state(context: Context) -> None:
    context.user_data["state"] = State.NONE
    for key in ("active_chat", "group_draft", "selected_ids", "profile_order", "current_page"):
        context.user_data.pop(key, None)


def get_profile_order(context: Context, user_id: int, reshuffle: bool = False) -> list[int]:
    order = context.user_data.get("profile_order")
    if order is None or reshuffle:
        order = get_db(context).get_profile_ids(exclude_user_id=user_id)
        random.shuffle(order)
        context.user_data["profile_order"] = order
    return order


def build_profile_text(profile: dict) -> str:
    created = (profile.get("created_at") or "")[:10] or "Unknown"
    lines = [
        "👤 <b>Your profile</b>",
        "",
        f"👋 <b>Name:</b> {esc(display_name(profile))}",
    ]
    if profile.get("username"):
        lines.append(f"🔗 <b>Username:</b> @{esc(profile['username'])}")
    lines += [
        f"📅 <b>Created:</b> {created}",
        "",
        "📝 <b>About me:</b>",
        esc(profile["bio"]),
        "",
        "📊 <b>Statistics:</b>",
        f"• Characters: {len(profile['bio'])}/{MAX_BIO_LENGTH}",
        "• Use 'Edit profile' to change it",
    ]
    return "\n".join(lines)


async def reply(update: Update, text: str, markup: InlineKeyboardMarkup | None = None) -> None:
    await update.effective_message.reply_text(text, reply_markup=markup)


async def safe_edit(query, text: str, markup: InlineKeyboardMarkup | None = None) -> None:
    try:
        await query.edit_message_text(text=text, reply_markup=markup)
    except BadRequest as exc:
        if "message is not modified" in str(exc).lower():
            return
        logger.warning("Could not edit message, sending a new one: %s", exc)
        await query.get_bot().send_message(
            chat_id=query.from_user.id, text=text, reply_markup=markup
        )


async def notify_user(context: Context, user_id: int, text: str) -> None:
    try:
        await context.bot.send_message(chat_id=user_id, text=text)
    except TelegramError as exc:
        logger.warning("Could not notify user %s: %s", user_id, exc)


async def ensure_subscribed(update: Update, context: Context) -> bool:
    if await get_checker(context).is_subscribed(context.bot, update.effective_user.id):
        return True
    if update.callback_query:
        await safe_edit(update.callback_query, SUBSCRIPTION_REQUIRED_TEXT, subscription_keyboard())
    else:
        await reply(update, SUBSCRIPTION_REQUIRED_TEXT, subscription_keyboard())
    return False


async def send_group_invitations(context: Context, request_id: str) -> tuple[int, int]:
    db = get_db(context)
    checker = get_checker(context)
    request = db.get_group_request(request_id)
    organizer = display_name(db.get_profile(request["creator_id"]), "Organizer")

    lines = [
        "👥 <b>You are invited to a group!</b>",
        "",
        f"👤 <b>Organizer:</b> {esc(organizer)}",
    ]
    if request.get("group_message"):
        lines += ["", "📝 <b>Message from organizer:</b>", esc(request["group_message"])]
    lines += [
        "",
        "⚡ <b>What to do?</b>",
        "• ✅ Join - get the group link",
        "• ❌ Decline - politely refuse",
        "",
        "<i>The link will appear only after pressing 'Join'</i>",
    ]
    invite_text = "\n".join(lines)

    sent = 0
    for user_id in request["selected_ids"]:
        if not db.get_profile(user_id):
            continue
        if not await checker.is_subscribed(context.bot, user_id):
            continue
        try:
            await context.bot.send_message(
                chat_id=user_id,
                text=invite_text,
                reply_markup=group_invite_keyboard(request_id),
            )
            sent += 1
        except TelegramError as exc:
            logger.warning("Failed to send invitation to %s: %s", user_id, exc)
    return sent, len(request["selected_ids"])


async def dispatch_group_invitation(context: Context, group_message: str | None) -> str:
    draft = context.user_data.pop("group_draft")
    context.user_data.pop("selected_ids", None)
    context.user_data["state"] = State.NONE

    get_db(context).update_group_request(draft["request_id"], draft["link"], group_message)
    sent, total = await send_group_invitations(context, draft["request_id"])

    return (
        "✅ <b>Done! Invitations sent.</b>\n\n"
        "📊 <b>Statistics:</b>\n"
        f"• Successful: {sent} people\n"
        f"• Failed: {total - sent} people\n\n"
        "⏳ <b>What's next?</b>\n"
        "1. People will receive your invitation\n"
        "2. They will press 'Join' or 'Decline'\n"
        "3. You will receive notifications about their decision\n"
        "4. Those who agree will see your link"
    )


def current_group_draft(context: Context) -> dict | None:
    draft = context.user_data.get("group_draft")
    return draft if draft and draft.get("link") else None


async def start(update: Update, context: Context) -> None:
    user = update.effective_user
    reset_state(context)

    if not await ensure_subscribed(update, context):
        return

    if get_db(context).get_profile(user.id):
        await reply(
            update,
            "🎉 <b>Welcome to ChronoRoom!</b>\n\n"
            f"👋 Hi, {esc(user.first_name)}!\n\n"
            "✨ <b>Available features:</b>\n"
            "• 🔍 Find friends - browse other people's profiles\n"
            "• 👤 My profile - view and edit\n"
            f"• 👥 Create group - unite up to {MAX_GROUP_MEMBERS} people\n"
            "• 💬 Personal communication - write anonymously via the bot\n\n"
            "📖 <b>New here?</b> Press 'How to use'",
            main_keyboard(),
        )
        return

    context.user_data["state"] = State.WRITING_BIO
    await reply(update, WELCOME_NEW_USER_TEXT)


async def help_command(update: Update, context: Context) -> None:
    reset_state(context)
    await reply(update, HELP_TEXT)


async def profile_command(update: Update, context: Context) -> None:
    reset_state(context)
    profile = get_db(context).get_profile(update.effective_user.id)
    if not profile:
        await reply(update, NO_PROFILE_TEXT, main_keyboard())
        return
    await reply(update, build_profile_text(profile), main_keyboard())


async def delete_command(update: Update, context: Context) -> None:
    reset_state(context)
    await reply(update, DELETE_CONFIRM_TEXT, confirm_delete_keyboard())


async def admin_command(update: Update, context: Context) -> None:
    user = update.effective_user
    if user.id not in ADMIN_IDS:
        await reply(update, "⛔ You don't have access to this command.")
        return

    db = get_db(context)
    candidate_ids = db.get_profile_ids(exclude_user_id=user.id)
    random.shuffle(candidate_ids)
    profiles = db.get_profiles_by_ids(candidate_ids[:MAX_GROUP_MEMBERS])
    if not profiles:
        await reply(update, "😔 <b>There are no profiles in the database yet.</b>")
        return

    selected_ids = [profile["user_id"] for profile in profiles]
    reset_state(context)
    context.user_data["selected_ids"] = selected_ids
    context.user_data["current_page"] = 0
    context.user_data["group_draft"] = {
        "request_id": db.create_group_request(user.id, selected_ids),
        "link": None,
    }
    context.user_data["state"] = State.WAITING_GROUP_LINK

    names = "\n".join(
        f"{index}. {esc(display_name(profile))}" for index, profile in enumerate(profiles, 1)
    )
    recipients = (
        "👑 <b>Admin mode</b>\n"
        f"Picked {len(profiles)} of {len(candidate_ids)} available profiles:\n\n{names}"
    )
    await reply(update, GROUP_LINK_PROMPT.format(recipients=recipients))


async def handle_bio_input(update: Update, context: Context, text: str) -> None:
    user = update.effective_user

    if len(text) < MIN_BIO_LENGTH:
        await reply(
            update,
            "📏 <b>Need more information!</b>\n\n"
            f"Minimum: {MIN_BIO_LENGTH} characters\n"
            f"Currently: {len(text)} characters\n\n"
            "💡 <b>Add details:</b>\n"
            "<i>For example, tell about your hobbies, work, interests, "
            "and what you're looking for in this bot.</i>",
        )
        return

    if len(text) > MAX_BIO_LENGTH:
        await reply(
            update,
            "📏 <b>Too much text!</b>\n\n"
            f"Maximum: {MAX_BIO_LENGTH} characters\n"
            f"Currently: {len(text)} characters\n\n"
            "✂️ <b>Shorten the text:</b>\n"
            "Keep only the most important and interesting",
        )
        return

    get_db(context).upsert_profile(user.id, user.username, user.first_name, text)
    context.user_data["state"] = State.NONE

    await reply(
        update,
        "🎉 <b>Great! Your profile is saved!</b>\n\n"
        "✅ <b>Now you can:</b>\n"
        "1. 🔍 <b>Find friends</b> - browse other profiles\n"
        "2. ✅ <b>Choose people</b> - click on names\n"
        "3. 📋 <b>Confirm selection</b> - press the button\n"
        "4. 💬 <b>Communicate</b> - personally or in a group\n\n"
        "💡 <b>Tip:</b> Profiles are shuffled each time you start a search!\n\n"
        "🚀 <b>Start right now:</b>",
        main_keyboard(),
    )


async def handle_group_link_input(update: Update, context: Context, text: str) -> None:
    draft = context.user_data.get("group_draft")
    if not draft:
        reset_state(context)
        await reply(update, "❌ An error occurred. Start over.", main_keyboard())
        return

    if not GROUP_LINK_PATTERN.match(text):
        await reply(
            update,
            "❌ <b>This doesn't look like a Telegram link!</b>\n\n"
            "📌 <b>Just create a group in Telegram and send the link here</b>\n"
            "<b>Link format:</b>\n"
            "• https://t.me/group_name\n"
            "• @group_name",
        )
        return

    draft["link"] = text
    context.user_data["state"] = State.CHOOSING_GROUP_MESSAGE
    await reply(
        update,
        "🔗 <b>Link accepted!</b>\n\n"
        "✉️ <b>Do you want to send a common message to all invitees?</b>\n\n"
        "For example:\n"
        "• Say hello\n"
        "• Tell about the group topic\n"
        "• Suggest something to discuss\n\n"
        "If not - just press 'No, just invite'",
        group_message_keyboard(),
    )


async def handle_group_message_choice(update: Update, context: Context, text: str) -> None:
    await reply(
        update,
        "👆 <b>Please use the buttons:</b> send a message to the invitees or just invite them.",
        group_message_keyboard(),
    )


async def handle_group_message_input(update: Update, context: Context, text: str) -> None:
    if len(text) > MAX_MESSAGE_LENGTH:
        await reply(
            update,
            "📏 <b>Too much text!</b>\n\n"
            f"Maximum: {MAX_MESSAGE_LENGTH} characters\n"
            f"Currently: {len(text)} characters",
        )
        return

    if not current_group_draft(context):
        reset_state(context)
        await reply(update, "❌ An error occurred. Start over.", main_keyboard())
        return

    report = await dispatch_group_invitation(context, text)
    await reply(update, report, main_keyboard())


async def handle_chat_input(update: Update, context: Context, text: str) -> None:
    user = update.effective_user
    db = get_db(context)
    chat = db.get_chat(context.user_data.get("active_chat"))

    if not chat or user.id not in (chat["user1_id"], chat["user2_id"]):
        reset_state(context)
        await reply(update, "❌ Chat not found or was closed", main_keyboard())
        return

    if len(text) > MAX_MESSAGE_LENGTH:
        await reply(
            update,
            "📏 <b>Too much text!</b>\n\n"
            f"Maximum: {MAX_MESSAGE_LENGTH} characters\n"
            f"Currently: {len(text)} characters",
        )
        return

    target_id = other_participant(chat, user.id)

    if not await get_checker(context).is_subscribed(context.bot, target_id):
        reset_state(context)
        await reply(
            update,
            "❌ <b>Failed to send message</b>\n\n"
            "This user is not subscribed to the ChronoRoom channel",
            main_keyboard(),
        )
        return

    sender_name = display_name(db.get_profile(user.id), user.first_name or "Someone")

    try:
        await context.bot.send_message(
            chat_id=target_id,
            text=(
                "💌 <b>You have a new message!</b>\n\n"
                f"👤 <b>From:</b> {esc(sender_name)}\n"
                f"📝 <b>Text:</b>\n{esc(text)}\n\n"
                "<i>Press 'Reply' to write back</i>"
            ),
            reply_markup=chat_reply_keyboard(chat["chat_id"]),
        )
    except TelegramError as exc:
        logger.warning("Failed to deliver message from %s to %s: %s", user.id, target_id, exc)
        reset_state(context)
        await reply(
            update,
            "❌ <b>Failed to send message</b>\n\n"
            "Possible reasons:\n"
            "• The user blocked the bot\n"
            "• Technical issues",
            main_keyboard(),
        )
        return

    await reply(
        update,
        "✅ <b>Message sent!</b>\n\n"
        "⏳ <b>What's next?</b>\n"
        "• The person will receive your message\n"
        "• They can press 'Reply'\n"
        "• You will receive their reply here",
    )


async def handle_idle_input(update: Update, context: Context, text: str) -> None:
    tips = [
        "💡 Use the buttons below for navigation",
        "💡 Press '🔍 Find friends' to start searching",
        "💡 Want to learn more? Press '📖 How to use'",
    ]
    await reply(update, random.choice(tips), main_keyboard())


MESSAGE_HANDLERS = {
    State.WRITING_BIO: handle_bio_input,
    State.WAITING_GROUP_LINK: handle_group_link_input,
    State.CHOOSING_GROUP_MESSAGE: handle_group_message_choice,
    State.WAITING_GROUP_MESSAGE: handle_group_message_input,
    State.IN_CHAT: handle_chat_input,
}


async def handle_message(update: Update, context: Context) -> None:
    if not await ensure_subscribed(update, context):
        return

    text = (update.effective_message.text or "").strip()
    state = context.user_data.get("state", State.NONE)
    handler = MESSAGE_HANDLERS.get(state, handle_idle_input)
    await handler(update, context, text)


async def show_profiles_page(query, context: Context, page: int) -> None:
    order = get_profile_order(context, query.from_user.id)
    total_pages = max(1, math.ceil(len(order) / PROFILES_PER_PAGE))
    page = min(max(page, 0), total_pages - 1)
    context.user_data["current_page"] = page

    offset = page * PROFILES_PER_PAGE
    profiles = get_db(context).get_profiles_by_ids(order[offset : offset + PROFILES_PER_PAGE])
    if not profiles:
        await safe_edit(query, EMPTY_SEARCH_TEXT, main_keyboard())
        return

    selected_ids = context.user_data.setdefault("selected_ids", [])

    lines = [f"👥 <b>Found profiles</b> (page {page + 1}/{total_pages}):", ""]
    for index, profile in enumerate(profiles, 1):
        mark = "✅ " if profile["user_id"] in selected_ids else ""
        preview = " ".join(profile["bio"].split())
        if len(preview) > 80:
            preview = preview[:77] + "..."
        lines.append(f"{index}. {mark}<b>{esc(display_name(profile))}</b>")
        lines.append(f"   <i>{esc(preview)}</i>")
        lines.append("")

    lines.append("💡 <b>How to select:</b> Click on a person's name")
    if selected_ids:
        lines.append(f"✅ <b>Selected:</b> {len(selected_ids)}/{MAX_GROUP_MEMBERS} people")
        lines.append("📋 <b>What's next:</b> Press 'Confirm selection'")
    else:
        lines.append("📝 <b>Tip:</b> Choose people who interest you")

    await safe_edit(
        query,
        "\n".join(lines),
        profiles_keyboard(profiles, page, total_pages, selected_ids),
    )


async def cb_noop(query, context: Context, arg: str) -> str | None:
    return None


async def cb_check_subscription(query, context: Context, arg: str) -> str | None:
    user = query.from_user
    if not await get_checker(context).is_subscribed(context.bot, user.id, force=True):
        return "❌ You are not subscribed yet. Subscribe and press the button again."

    if get_db(context).get_profile(user.id):
        reset_state(context)
        await safe_edit(
            query,
            "✅ <b>Great! You are subscribed!</b>\n\nNow you can use all bot features.",
            main_keyboard(),
        )
    else:
        reset_state(context)
        context.user_data["state"] = State.WRITING_BIO
        await safe_edit(
            query, "✅ <b>Great! You are subscribed!</b>\n\n" + WELCOME_NEW_USER_TEXT
        )
    return None


async def cb_main_menu(query, context: Context, arg: str) -> str | None:
    reset_state(context)
    await safe_edit(query, "🏠 <b>Main menu</b>\n\nChoose an action:", main_keyboard())
    return None


async def cb_my_profile(query, context: Context, arg: str) -> str | None:
    context.user_data["state"] = State.NONE
    profile = get_db(context).get_profile(query.from_user.id)
    if not profile:
        await safe_edit(query, NO_PROFILE_TEXT, main_keyboard())
        return None
    await safe_edit(query, build_profile_text(profile), main_keyboard())
    return None


async def cb_edit_profile(query, context: Context, arg: str) -> str | None:
    reset_state(context)
    context.user_data["state"] = State.WRITING_BIO
    await safe_edit(
        query,
        "✏️ <b>Edit your profile</b>\n\n"
        f"Send a new description ({MIN_BIO_LENGTH}-{MAX_BIO_LENGTH} characters). "
        "It will replace the current one.\n\n"
        "ℹ️ <b>To cancel, press /start</b>",
    )
    return None


async def cb_find_friends(query, context: Context, arg: str) -> str | None:
    user = query.from_user
    if not get_db(context).get_profile(user.id):
        return "❌ Create a profile first: send /start"

    reset_state(context)
    get_profile_order(context, user.id, reshuffle=True)
    context.user_data["selected_ids"] = []
    await show_profiles_page(query, context, 0)
    return None


async def cb_continue_search(query, context: Context, arg: str) -> str | None:
    context.user_data["state"] = State.NONE
    await show_profiles_page(query, context, context.user_data.get("current_page", 0))
    return None


async def cb_page(query, context: Context, arg: str) -> str | None:
    try:
        page = int(arg)
    except ValueError:
        return "❌ Pagination error"
    context.user_data["state"] = State.NONE
    await show_profiles_page(query, context, page)
    return None


async def cb_select(query, context: Context, arg: str) -> str | None:
    try:
        target_id = int(arg)
    except ValueError:
        return "❌ Selection error"

    context.user_data["state"] = State.NONE
    selected_ids = context.user_data.setdefault("selected_ids", [])

    if target_id in selected_ids:
        selected_ids.remove(target_id)
    elif len(selected_ids) >= MAX_GROUP_MEMBERS:
        return f"❌ You can select no more than {MAX_GROUP_MEMBERS} people!"
    else:
        selected_ids.append(target_id)

    await show_profiles_page(query, context, context.user_data.get("current_page", 0))
    return None


async def cb_confirm_selection(query, context: Context, arg: str) -> str | None:
    context.user_data["state"] = State.NONE
    selected_ids = context.user_data.get("selected_ids", [])
    if not selected_ids:
        return "❌ You haven't selected anyone!"

    profiles = get_db(context).get_profiles_by_ids(selected_ids)
    if not profiles:
        context.user_data["selected_ids"] = []
        await show_profiles_page(query, context, 0)
        return "❌ The selected users deleted their profiles!"

    context.user_data["selected_ids"] = [profile["user_id"] for profile in profiles]

    lines = [f"✅ <b>You selected {len(profiles)} people:</b>", ""]
    lines += [
        f"{index}. <b>{esc(display_name(profile))}</b>"
        for index, profile in enumerate(profiles, 1)
    ]
    lines += ["", "📋 <b>What's next?</b>", "• 👥 Create group - create a group"]
    if len(profiles) == 1:
        lines.append("• 💌 Write personally - send a personal message")
    lines += [
        "",
        "⚡ <b>Or you can:</b>",
        "• 🔍 Continue search",
        "• 🔄 Reset selection and start over",
    ]

    await safe_edit(query, "\n".join(lines), selection_keyboard(len(profiles)))
    return None


async def cb_reset_selection(query, context: Context, arg: str) -> str | None:
    context.user_data["state"] = State.NONE
    context.user_data["selected_ids"] = []
    await show_profiles_page(query, context, 0)
    return None


async def cb_write_personal(query, context: Context, arg: str) -> str | None:
    db = get_db(context)
    selected_ids = context.user_data.get("selected_ids", [])
    if len(selected_ids) != 1:
        return "❌ Select only one person!"

    target = db.get_profile(selected_ids[0])
    if not target:
        context.user_data["selected_ids"] = []
        context.user_data["state"] = State.NONE
        await show_profiles_page(query, context, 0)
        return "❌ This user deleted their profile!"

    context.user_data["active_chat"] = db.create_chat(query.from_user.id, target["user_id"])
    context.user_data["state"] = State.IN_CHAT

    await safe_edit(
        query,
        f"✍️ <b>Write a message for {esc(display_name(target, 'user'))}</b>\n\n"
        "📝 <b>The message will be sent anonymously:</b>\n"
        "• The recipient will see your message\n"
        "• But won't see your username\n"
        "• They can reply via the 'Reply' button\n\n"
        "💡 <b>What to write:</b>\n"
        "• Introduce yourself\n"
        "• Tell why you wrote\n"
        "• Suggest a topic for conversation\n\n"
        "ℹ️ <b>To exit chat mode, press /start</b>",
    )
    return None


async def cb_reply(query, context: Context, chat_id: str) -> str | None:
    user = query.from_user
    db = get_db(context)
    chat = db.get_chat(chat_id)
    if not chat or user.id not in (chat["user1_id"], chat["user2_id"]):
        return "❌ Chat not found!"

    target = db.get_profile(other_participant(chat, user.id))
    if not target:
        return "❌ User deleted their profile!"

    context.user_data["active_chat"] = chat_id
    context.user_data["state"] = State.IN_CHAT

    await context.bot.send_message(
        chat_id=user.id,
        text=(
            f"✍️ <b>Write a reply for {esc(display_name(target, 'user'))}</b>\n\n"
            "📝 <b>The message will be sent anonymously:</b>\n"
            "• The recipient will see your reply\n"
            "• But won't see your username\n"
            "• They can reply via the 'Reply' button\n\n"
            "💡 <b>What to write:</b>\n"
            "• Reply to the previous message\n"
            "• Ask a question\n"
            "• Suggest continuing the conversation\n\n"
            "ℹ️ <b>To exit chat mode, press /start</b>"
        ),
    )
    return None


async def cb_ignore_message(query, context: Context, arg: str) -> str | None:
    reset_state(context)
    await safe_edit(query, "❌ Message ignored", main_keyboard())
    return None


async def cb_create_group(query, context: Context, arg: str) -> str | None:
    selected_ids = context.user_data.get("selected_ids", [])
    if not selected_ids:
        return "❌ Select at least 1 person!"
    if len(selected_ids) > MAX_GROUP_MEMBERS:
        return f"❌ Maximum {MAX_GROUP_MEMBERS} people!"

    request_id = get_db(context).create_group_request(query.from_user.id, selected_ids)
    context.user_data["group_draft"] = {"request_id": request_id, "link": None}
    context.user_data["state"] = State.WAITING_GROUP_LINK

    recipients = f"👤 <b>Who will receive the invitation:</b>\n{len(selected_ids)} people you selected"
    await safe_edit(query, GROUP_LINK_PROMPT.format(recipients=recipients))
    return None


async def cb_send_group_message(query, context: Context, arg: str) -> str | None:
    if not current_group_draft(context):
        reset_state(context)
        await safe_edit(query, "❌ An error occurred. Start over.", main_keyboard())
        return "❌ Data error!"

    context.user_data["state"] = State.WAITING_GROUP_MESSAGE
    await safe_edit(
        query,
        "✍️ <b>Great! Now write a message for the invitees:</b>\n\n"
        "💡 <b>What you can write:</b>\n"
        "• Say hello\n"
        "• Tell about the group topic\n"
        "• Suggest something to discuss\n"
        "• Explain why you created the group\n\n"
        "<b>Example:</b>\n"
        "<i>Hi everyone! I created this group to discuss Python programming. "
        "Let's share experience, ask questions, and help each other!</i>\n\n"
        "ℹ️ <b>To cancel, press /start</b>",
    )
    return None


async def cb_skip_group_message(query, context: Context, arg: str) -> str | None:
    if not current_group_draft(context):
        reset_state(context)
        await safe_edit(query, "❌ An error occurred. Start over.", main_keyboard())
        return "❌ Data error!"

    report = await dispatch_group_invitation(context, None)
    await safe_edit(query, report, main_keyboard())
    return None


async def cb_join(query, context: Context, request_id: str) -> str | None:
    user = query.from_user
    db = get_db(context)
    request = db.get_group_request(request_id)
    if not request or not request["group_link"] or user.id not in request["selected_ids"]:
        return "❌ This invitation is no longer available"

    organizer = display_name(db.get_profile(request["creator_id"]), "Organizer")

    lines = [
        "✅ <b>You joined the group!</b>",
        "",
        "🔗 <b>Group link:</b>",
        esc(request["group_link"]),
        "",
    ]
    if request.get("group_message"):
        lines += ["📝 <b>Message from organizer:</b>", esc(request["group_message"]), ""]
    lines += [
        "📌 <b>What to do next:</b>",
        "1. Follow the link",
        "2. Introduce yourself in the group",
        "3. Tell about your interests",
        "4. Chat and find common ground!",
        "",
        f"👤 <b>Organizer:</b> {esc(organizer)}",
    ]
    await safe_edit(query, "\n".join(lines), main_keyboard())

    joiner = display_name(db.get_profile(user.id), user.first_name or "User")
    await notify_user(
        context, request["creator_id"], f"✅ {joiner} accepted your group invitation!"
    )
    context.user_data["state"] = State.NONE
    return None


async def cb_decline(query, context: Context, request_id: str) -> str | None:
    user = query.from_user
    db = get_db(context)
    request = db.get_group_request(request_id)
    if not request or user.id not in request["selected_ids"]:
        return "❌ This invitation is no longer available"

    await safe_edit(
        query,
        "❌ <b>You declined the invitation</b>\n\n"
        "It's okay! Everyone has the right to choose.\n\n"
        "🔍 <b>You can continue searching for other people</b>",
        main_keyboard(),
    )

    decliner = display_name(db.get_profile(user.id), user.first_name or "User")
    await notify_user(
        context, request["creator_id"], f"❌ {decliner} declined your group invitation."
    )
    context.user_data["state"] = State.NONE
    return None


async def cb_delete_ask(query, context: Context, arg: str) -> str | None:
    context.user_data["state"] = State.NONE
    await safe_edit(query, DELETE_CONFIRM_TEXT, confirm_delete_keyboard())
    return None


async def cb_confirm_delete(query, context: Context, arg: str) -> str | None:
    get_db(context).delete_profile(query.from_user.id)
    reset_state(context)
    await safe_edit(
        query,
        "🗑️ <b>Profile deleted</b>\n\n"
        "Your profile has been successfully deleted from the system.\n\n"
        "🔁 <b>Want to create a new one?</b>\n"
        "Write /start to start over",
    )
    return None


async def cb_cancel_delete(query, context: Context, arg: str) -> str | None:
    context.user_data["state"] = State.NONE
    await safe_edit(query, "✅ Deletion cancelled. Your profile is saved.", main_keyboard())
    return None


async def cb_tutorial(query, context: Context, arg: str) -> str | None:
    context.user_data["state"] = State.NONE
    await safe_edit(query, TUTORIAL_INTRO_TEXT, tutorial_keyboard())
    return None


async def cb_tutorial_topic(query, context: Context, topic: str) -> str | None:
    text = TUTORIAL_TOPICS.get(topic)
    if text is None:
        return "❌ Unknown topic"
    context.user_data["state"] = State.NONE
    await safe_edit(query, text, tutorial_keyboard())
    return None


EXACT_CALLBACKS = {
    "no_page": cb_noop,
    "current_page": cb_noop,
    "show_selected": cb_noop,
    "main_menu": cb_main_menu,
    "my_profile": cb_my_profile,
    "edit_profile": cb_edit_profile,
    "find_friends": cb_find_friends,
    "continue_search": cb_continue_search,
    "confirm_selection": cb_confirm_selection,
    "reset_selection": cb_reset_selection,
    "write_personal": cb_write_personal,
    "ignore_message": cb_ignore_message,
    "create_group": cb_create_group,
    "send_group_message": cb_send_group_message,
    "skip_group_message": cb_skip_group_message,
    "delete_ask": cb_delete_ask,
    "confirm_delete": cb_confirm_delete,
    "cancel_delete": cb_cancel_delete,
    "tutorial": cb_tutorial,
}

PREFIX_CALLBACKS = {
    "page_": cb_page,
    "select_": cb_select,
    "reply_": cb_reply,
    "join_": cb_join,
    "decline_": cb_decline,
    "tutor_": cb_tutorial_topic,
}


def resolve_callback(data: str):
    if data in EXACT_CALLBACKS:
        return EXACT_CALLBACKS[data], ""
    for prefix, handler in PREFIX_CALLBACKS.items():
        if data.startswith(prefix):
            return handler, data[len(prefix) :]
    return None, ""


async def button_callback(update: Update, context: Context) -> None:
    query = update.callback_query
    alert: str | None = None
    try:
        if query.data == "check_subscription":
            alert = await cb_check_subscription(query, context, "")
        elif await ensure_subscribed(update, context):
            handler, arg = resolve_callback(query.data or "")
            if handler:
                alert = await handler(query, context, arg)
            else:
                logger.warning("Unknown callback data: %r", query.data)
    finally:
        try:
            await query.answer(alert, show_alert=bool(alert))
        except TelegramError as exc:
            logger.debug("Could not answer callback query: %s", exc)


async def error_handler(update: object, context: Context) -> None:
    logger.error("Unhandled exception while processing an update", exc_info=context.error)


def load_token() -> str | None:
    token = os.getenv("BOT_TOKEN", "").strip()
    if not token and Path(TOKEN_FILE).is_file():
        token = Path(TOKEN_FILE).read_text(encoding="utf-8").strip()
    if not token or token == "YOUR_TOKEN_HERE" or ":" not in token:
        return None
    return token


def main() -> None:
    logging.basicConfig(
        format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
        level=logging.INFO,
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)

    token = load_token()
    if token is None:
        logger.error(
            "Bot token not found or invalid. Set the BOT_TOKEN environment variable "
            "or put the token from @BotFather into %s",
            TOKEN_FILE,
        )
        raise SystemExit(1)

    db = Database()

    app = (
        Application.builder()
        .token(token)
        .defaults(Defaults(parse_mode=ParseMode.HTML))
        .build()
    )
    app.bot_data["db"] = db
    app.bot_data["subscriptions"] = SubscriptionChecker(CHANNEL_USERNAME)

    private = filters.ChatType.PRIVATE
    app.add_handler(CommandHandler("start", start, filters=private))
    app.add_handler(CommandHandler("help", help_command, filters=private))
    app.add_handler(CommandHandler("profile", profile_command, filters=private))
    app.add_handler(CommandHandler("delete", delete_command, filters=private))
    app.add_handler(CommandHandler("admin", admin_command, filters=private))
    app.add_handler(MessageHandler(private & filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(CallbackQueryHandler(button_callback))
    app.add_error_handler(error_handler)

    logger.info("ChronoRoom is running (channel: %s)", CHANNEL_USERNAME)
    try:
        app.run_polling(allowed_updates=[Update.MESSAGE, Update.CALLBACK_QUERY])
    finally:
        db.close()


if __name__ == "__main__":
    main()
