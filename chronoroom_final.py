from __future__ import annotations

import html
import logging
import math
import os
import random
import re
import time
from enum import Enum, auto
from pathlib import Path
from typing import NamedTuple

from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatMemberStatus, ParseMode
from telegram.error import BadRequest, Forbidden, TelegramError
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    Defaults,
    MessageHandler,
    filters,
)

from database import Database

load_dotenv()  # reads a local .env (git-ignored) before the settings below are evaluated

logger = logging.getLogger("chronoroom")

Context = ContextTypes.DEFAULT_TYPE

DATABASE_FILE = os.getenv("DATABASE_FILE", "chronoroom.db")
TOKEN_FILE = "token.txt"
# An empty CHANNEL_USERNAME switches the subscription requirement off.
CHANNEL_USERNAME = os.getenv("CHANNEL_USERNAME", "@ChronoRoom").strip()
CHANNEL_URL = f"https://t.me/{CHANNEL_USERNAME.lstrip('@')}" if CHANNEL_USERNAME else ""
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
    f"👉 {html.escape(CHANNEL_URL)} 👈\n\n"
    "After subscribing, press the button below:"
)

# Shown when Telegram could not tell whether the user is subscribed. This is NOT the same
# as "not subscribed", so the user is not asked to subscribe again.
SUBSCRIPTION_UNKNOWN_TEXT = (
    "⚠️ <b>Couldn't verify your subscription right now</b>\n\n"
    "Telegram did not answer. This is temporary - please try again in a minute."
)
SUBSCRIPTION_UNKNOWN_ALERT = (
    "⚠️ Couldn't verify your subscription right now. Please try again in a minute."
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


class SubscriptionStatus(Enum):
    SUBSCRIBED = auto()
    NOT_SUBSCRIBED = auto()
    UNKNOWN = auto()  # Telegram could not tell us (outage, bot is not a channel admin, ...)


class ConfigurationError(RuntimeError):
    pass


class SubscriptionChecker:
    """Checks the membership of the required channel.

    It fails CLOSED: when Telegram cannot confirm the membership the answer is UNKNOWN and the
    caller must not grant access. UNKNOWN is deliberately a separate status, so the user is
    told "try again later" instead of being wrongly accused of not being subscribed.
    """

    CACHE_LIMIT = 10_000

    def __init__(self, channel: str, ttl: int = SUBSCRIPTION_CACHE_TTL) -> None:
        self.channel = (channel or "").strip()
        self.ttl = ttl
        self._valid_until: dict[int, float] = {}

    @property
    def enabled(self) -> bool:
        return bool(self.channel)

    async def check(self, bot, user_id: int, force: bool = False) -> SubscriptionStatus:
        if not self.enabled:
            return SubscriptionStatus.SUBSCRIBED

        now = time.monotonic()
        if not force and self._valid_until.get(user_id, 0.0) > now:
            return SubscriptionStatus.SUBSCRIBED

        try:
            member = await bot.get_chat_member(chat_id=self.channel, user_id=user_id)
        except TelegramError as exc:
            # BadRequest / Forbidden mean a configuration problem (wrong channel, the bot
            # is not an admin there), the rest is usually a temporary network problem.
            level = logging.ERROR if isinstance(exc, (BadRequest, Forbidden)) else logging.WARNING
            logger.log(level, "Subscription check failed for %s, access NOT granted: %s", user_id, exc)
            return SubscriptionStatus.UNKNOWN

        subscribed = member.status in (
            ChatMemberStatus.OWNER,
            ChatMemberStatus.ADMINISTRATOR,
            ChatMemberStatus.MEMBER,
        ) or (member.status == ChatMemberStatus.RESTRICTED and member.is_member)

        if not subscribed:
            self._valid_until.pop(user_id, None)
            return SubscriptionStatus.NOT_SUBSCRIBED

        if len(self._valid_until) >= self.CACHE_LIMIT:
            self._valid_until = {uid: t for uid, t in self._valid_until.items() if t > now}
        self._valid_until[user_id] = now + self.ttl
        return SubscriptionStatus.SUBSCRIBED


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
    profiles: list[dict],
    page: int,
    total_pages: int,
    selected_ids: list[int],
    positions: dict[int, int],
    order_gen: int,
) -> InlineKeyboardMarkup:
    # The buttons carry the position in this user's own shuffled list, never a Telegram user
    # id: callback_data is readable by any client and the board is meant to be anonymous.
    rows = []
    for profile in profiles:
        mark = "✅ " if profile["user_id"] in selected_ids else ""
        name = display_name(profile)[:20]
        rows.append([_btn(f"{mark}{name}", f"select_{order_gen}_{positions[profile['user_id']]}")])

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


def subscription_retry_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[_btn("🔄 Try again", "check_subscription")]])


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
        # buttons of an older list must not point into this one
        context.user_data["order_gen"] = context.user_data.get("order_gen", 0) + 1
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
    """Gate for text messages and commands (buttons are gated in button_callback)."""
    status = await get_checker(context).check(context.bot, update.effective_user.id)
    if status is SubscriptionStatus.SUBSCRIBED:
        return True
    if status is SubscriptionStatus.UNKNOWN:
        await reply(update, SUBSCRIPTION_UNKNOWN_TEXT, subscription_retry_keyboard())
    else:
        await reply(update, SUBSCRIPTION_REQUIRED_TEXT, subscription_keyboard())
    return False


class InviteResult(NamedTuple):
    sent: int
    total: int
    unverified: int  # not invited: Telegram could not confirm their subscription


async def send_group_invitations(context: Context, request_id: str) -> InviteResult:
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
    unverified = 0
    for user_id in request["selected_ids"]:
        if not db.get_profile(user_id):
            continue
        status = await checker.check(context.bot, user_id)
        if status is SubscriptionStatus.UNKNOWN:
            unverified += 1  # fail closed: no invitation without a confirmed subscription
            continue
        if status is SubscriptionStatus.NOT_SUBSCRIBED:
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
    return InviteResult(sent, len(request["selected_ids"]), unverified)


async def dispatch_group_invitation(context: Context, group_message: str | None) -> str:
    draft = context.user_data.pop("group_draft")
    context.user_data.pop("selected_ids", None)
    context.user_data["state"] = State.NONE

    get_db(context).update_group_request(draft["request_id"], draft["link"], group_message)
    result = await send_group_invitations(context, draft["request_id"])

    lines = [
        "✅ <b>Done! Invitations sent.</b>",
        "",
        "📊 <b>Statistics:</b>",
        f"• Successful: {result.sent} people",
        f"• Failed: {result.total - result.sent - result.unverified} people",
    ]
    if result.unverified:
        lines.append(
            f"• ⚠️ Not invited: {result.unverified} people - Telegram could not confirm "
            "their channel subscription right now. Create the group invitation again later."
        )
    lines += [
        "",
        "⏳ <b>What's next?</b>",
        "1. People will receive your invitation",
        "2. They will press 'Join' or 'Decline'",
        "3. You will receive notifications about their decision",
        "4. Those who agree will see your link",
    ]
    return "\n".join(lines)


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

    target_status = await get_checker(context).check(context.bot, target_id)
    if target_status is SubscriptionStatus.UNKNOWN:
        # keep the chat open: the user can simply send the message again in a minute
        await reply(
            update,
            "⚠️ <b>Couldn't send the message right now</b>\n\n"
            "Telegram could not confirm the recipient's channel subscription. "
            "Please send your message again in a minute.",
        )
        return
    if target_status is SubscriptionStatus.NOT_SUBSCRIBED:
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
    positions = {user_id: index for index, user_id in enumerate(order)}

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
        profiles_keyboard(
            profiles, page, total_pages, selected_ids, positions, context.user_data["order_gen"]
        ),
    )


async def cb_noop(query, context: Context, arg: str) -> str | None:
    return None


async def cb_check_subscription(query, context: Context, arg: str) -> str | None:
    user = query.from_user
    status = await get_checker(context).check(context.bot, user.id, force=True)
    if status is SubscriptionStatus.UNKNOWN:
        return SUBSCRIPTION_UNKNOWN_ALERT
    if status is SubscriptionStatus.NOT_SUBSCRIBED:
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
        generation_text, position_text = arg.split("_", 1)
        generation, position = int(generation_text), int(position_text)
    except ValueError:
        return "❌ Selection error"

    order = context.user_data.get("profile_order")
    if (
        order is None
        or generation != context.user_data.get("order_gen")
        or not 0 <= position < len(order)
    ):
        return "❌ This list is outdated. Press 'Find friends' again."
    target_id = order[position]

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
    if not db.record_group_response(request_id, user.id, "join"):
        return "ℹ️ You have already answered this invitation"

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
        context, request["creator_id"], f"✅ {esc(joiner)} accepted your group invitation!"
    )
    context.user_data["state"] = State.NONE
    return None


async def cb_decline(query, context: Context, request_id: str) -> str | None:
    user = query.from_user
    db = get_db(context)
    request = db.get_group_request(request_id)
    if not request or user.id not in request["selected_ids"]:
        return "❌ This invitation is no longer available"
    if not db.record_group_response(request_id, user.id, "decline"):
        return "ℹ️ You have already answered this invitation"

    await safe_edit(
        query,
        "❌ <b>You declined the invitation</b>\n\n"
        "It's okay! Everyone has the right to choose.\n\n"
        "🔍 <b>You can continue searching for other people</b>",
        main_keyboard(),
    )

    decliner = display_name(db.get_profile(user.id), user.first_name or "User")
    await notify_user(
        context, request["creator_id"], f"❌ {esc(decliner)} declined your group invitation."
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
        else:
            status = await get_checker(context).check(context.bot, query.from_user.id)
            if status is SubscriptionStatus.SUBSCRIBED:
                handler, arg = resolve_callback(query.data or "")
                if handler:
                    alert = await handler(query, context, arg)
                else:
                    logger.warning("Unknown callback data: %r", query.data)
            elif status is SubscriptionStatus.UNKNOWN:
                # a temporary problem: do not tear down what the user is looking at
                alert = SUBSCRIPTION_UNKNOWN_ALERT
            else:
                await safe_edit(query, SUBSCRIPTION_REQUIRED_TEXT, subscription_keyboard())
    finally:
        try:
            await query.answer(alert, show_alert=bool(alert))
        except TelegramError as exc:
            logger.debug("Could not answer callback query: %s", exc)


async def verify_channel_access(app: Application) -> None:
    """Fail fast on a wrong channel / missing admin rights.

    The subscription check fails closed, so such a misconfiguration would otherwise lock
    every user out without any obvious reason.
    """
    checker: SubscriptionChecker = app.bot_data["subscriptions"]
    if not checker.enabled:
        logger.warning("CHANNEL_USERNAME is empty: the subscription requirement is DISABLED")
        return

    try:
        me = await app.bot.get_chat_member(chat_id=checker.channel, user_id=app.bot.id)
    except (BadRequest, Forbidden) as exc:
        raise ConfigurationError(
            f"The bot cannot read the members of {checker.channel}: {exc}. "
            "Add the bot as an administrator of the channel, fix CHANNEL_USERNAME, "
            "or leave CHANNEL_USERNAME empty to disable the subscription requirement."
        ) from exc
    except TelegramError as exc:
        logger.warning("Could not verify access to %s at startup (checked again on demand): %s",
                       checker.channel, exc)
        return

    if me.status not in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER):
        logger.warning("The bot is not an administrator of %s: membership checks may fail",
                       checker.channel)


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

    db = Database(DATABASE_FILE)

    app = (
        Application.builder()
        .token(token)
        .defaults(Defaults(parse_mode=ParseMode.HTML))
        .post_init(verify_channel_access)
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

    logger.info("ChronoRoom is running (channel: %s)", CHANNEL_USERNAME or "none")
    try:
        app.run_polling(allowed_updates=[Update.MESSAGE, Update.CALLBACK_QUERY])
    except ConfigurationError as exc:
        logger.error("%s", exc)
        raise SystemExit(1) from exc
    finally:
        db.close()


if __name__ == "__main__":
    main()
