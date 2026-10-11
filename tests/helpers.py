"""Fakes shared by the tests that need python-telegram-bot (no network is used)."""
import asyncio
import importlib.util
from types import SimpleNamespace as NS

HAVE_PTB = importlib.util.find_spec("telegram") is not None


class FakeBot:
    id = 4242

    def __init__(self):
        self.sent = []               # (chat_id, text, reply_markup)
        self.statuses = {}           # user_id -> ChatMemberStatus (default: member)
        self.is_member = {}          # for "restricted"
        self.errors = {}             # user_id -> exception raised by get_chat_member
        self.default_error = None
        self.blocked = set()         # chat ids that refuse messages
        self.member_calls = 0

    async def get_chat_member(self, chat_id, user_id):
        from telegram.constants import ChatMemberStatus
        self.member_calls += 1
        error = self.errors.get(user_id) or self.default_error
        if error:
            raise error
        return NS(status=self.statuses.get(user_id, ChatMemberStatus.MEMBER),
                  is_member=self.is_member.get(user_id, True))

    async def send_message(self, chat_id, text, reply_markup=None, **kwargs):
        if chat_id in self.blocked:
            from telegram.error import Forbidden
            raise Forbidden("bot was blocked by the user")
        self.sent.append((chat_id, text, reply_markup))

    def texts_to(self, chat_id):
        return [text for chat, text, _ in self.sent if chat == chat_id]


class FakeQuery:
    def __init__(self, bot, user_id, data, first_name="Name"):
        self.bot = bot
        self.from_user = NS(id=user_id, first_name=first_name, username=None)
        self.data = data
        self.edits = []              # (text, reply_markup)
        self.answers = []            # (text, show_alert)

    async def edit_message_text(self, text, reply_markup=None, **kwargs):
        self.edits.append((text, reply_markup))

    async def answer(self, text=None, show_alert=False):
        self.answers.append((text, show_alert))

    def get_bot(self):
        return self.bot


class FakeMessage:
    def __init__(self, text=""):
        self.text = text
        self.replies = []            # (text, reply_markup)

    async def reply_text(self, text, reply_markup=None, **kwargs):
        self.replies.append((text, reply_markup))


def make_context(bot, db, checker):
    return NS(bot=bot, bot_data={"db": db, "subscriptions": checker}, user_data={})


def make_update(user_id, text="", first_name="Name", query=None):
    message = FakeMessage(text)
    return NS(
        effective_user=NS(id=user_id, first_name=first_name, username=None),
        effective_message=message,
        callback_query=query,
    )


def callbacks(markup):
    return [b.callback_data for row in markup.inline_keyboard for b in row if b.callback_data]


def urls(markup):
    return [b.url for row in markup.inline_keyboard for b in row if b.url]


def run(coro):
    return asyncio.run(coro)
