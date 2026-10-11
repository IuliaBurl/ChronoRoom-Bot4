import asyncio
import re
import unittest
from unittest import mock

from helpers import (HAVE_PTB, FakeBot, FakeQuery, callbacks, make_context, make_update, urls)

if HAVE_PTB:
    from telegram.constants import ChatMemberStatus
    from telegram.error import BadRequest, NetworkError
    import chronoroom_final as bot_module
    from chronoroom_final import (ConfigurationError, State, SubscriptionChecker,
                                  SubscriptionStatus)
    from database import Database

EVIL = '<a href="http://evil.example">Alice</a> & <b>x'
BIO = "I like testing things properly."


def add_profile(db, uid, name=None, bio=BIO):
    db.upsert_profile(uid, None, name or f"User{uid}", bio)


@unittest.skipUnless(HAVE_PTB, "python-telegram-bot is not installed")
class HandlerTestCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.bot = FakeBot()
        self.db = Database(":memory:")
        self.addCleanup(self.db.close)
        self.checker = SubscriptionChecker("@channel", ttl=0)     # ttl=0: always ask Telegram
        self.ctx = make_context(self.bot, self.db, self.checker)

    def query(self, user_id, data, name="Name"):
        return FakeQuery(self.bot, user_id, data, first_name=name)


class SubscriptionGateTests(HandlerTestCase):
    async def test_not_subscribed_gets_the_subscribe_prompt(self):
        self.bot.statuses[1] = ChatMemberStatus.LEFT
        update = make_update(1)
        self.assertFalse(await bot_module.ensure_subscribed(update, self.ctx))
        text, markup = update.effective_message.replies[-1]
        self.assertIn("subscribe", text.lower())
        self.assertEqual(urls(markup), [bot_module.CHANNEL_URL])   # the configured channel
        self.assertTrue(urls(markup)[0].startswith("https://t.me/"))
        self.assertIn("check_subscription", callbacks(markup))

    async def test_telegram_outage_blocks_access_but_does_not_accuse_the_user(self):
        self.bot.default_error = NetworkError("down")
        update = make_update(1)
        with self.assertLogs("chronoroom", level="WARNING"):
            self.assertFalse(await bot_module.ensure_subscribed(update, self.ctx))
        text, markup = update.effective_message.replies[-1]
        self.assertIn("try again", text.lower())
        self.assertNotIn("subscribe to the channel first", text.lower())
        self.assertEqual(callbacks(markup), ["check_subscription"])
        self.assertEqual(urls(markup), [])

    async def test_subscribed_passes(self):
        update = make_update(1)
        self.assertTrue(await bot_module.ensure_subscribed(update, self.ctx))
        self.assertEqual(update.effective_message.replies, [])

    async def test_start_command_is_gated(self):
        self.bot.default_error = BadRequest("Chat not found")
        update = make_update(1, "/start")
        with self.assertLogs("chronoroom", level="ERROR"):
            await bot_module.start(update, self.ctx)
        self.assertNotIn("welcome", update.effective_message.replies[-1][0].lower())
        self.assertNotEqual(self.ctx.user_data.get("state"), State.WRITING_BIO)

    async def test_text_message_is_gated(self):
        self.bot.default_error = NetworkError("down")
        self.ctx.user_data["state"] = State.WRITING_BIO
        update = make_update(1, BIO)
        with self.assertLogs("chronoroom", level="WARNING"):
            await bot_module.handle_message(update, self.ctx)
        self.assertIsNone(self.db.get_profile(1), "no profile may be created while the check is failing")

    async def test_button_during_outage_keeps_the_screen_and_shows_an_alert(self):
        self.bot.default_error = NetworkError("down")
        query = self.query(1, "main_menu")
        update = make_update(1, query=query)
        with self.assertLogs("chronoroom", level="WARNING"):
            await bot_module.button_callback(update, self.ctx)
        self.assertEqual(query.edits, [], "the current message must not be replaced")
        self.assertEqual(len(query.answers), 1)
        text, show_alert = query.answers[0]
        self.assertTrue(show_alert and "try again" in text.lower())

    async def test_button_for_non_subscriber_shows_the_prompt_and_does_not_run_the_handler(self):
        self.bot.statuses[1] = ChatMemberStatus.LEFT
        query = self.query(1, "find_friends")
        await bot_module.button_callback(make_update(1, query=query), self.ctx)
        self.assertIn("subscribe", query.edits[-1][0].lower())
        self.assertNotIn("selected_ids", self.ctx.user_data)

    async def test_check_button_paths(self):
        self.bot.statuses[1] = ChatMemberStatus.LEFT
        query = self.query(1, "check_subscription")
        await bot_module.button_callback(make_update(1, query=query), self.ctx)
        self.assertIn("not subscribed yet", query.answers[-1][0])

        self.bot.default_error = NetworkError("down")
        query = self.query(1, "check_subscription")
        with self.assertLogs("chronoroom", level="WARNING"):
            await bot_module.button_callback(make_update(1, query=query), self.ctx)
        self.assertIn("try again", query.answers[-1][0].lower())
        self.assertNotIn("not subscribed yet", query.answers[-1][0])

        self.bot.default_error = None
        self.bot.statuses[1] = ChatMemberStatus.MEMBER
        query = self.query(1, "check_subscription")
        await bot_module.button_callback(make_update(1, query=query), self.ctx)
        self.assertIsNone(query.answers[-1][0])
        self.assertEqual(self.ctx.user_data["state"], State.WRITING_BIO, "new subscriber continues with the bio")


class GroupInvitationTests(HandlerTestCase):
    def setUp(self):
        super().setUp()
        add_profile(self.db, 1, "Organizer")
        for uid in (2, 3, 4, 5):
            add_profile(self.db, uid)

    def request(self, invitees=(2, 3, 4, 5), link="https://t.me/mygroup", message=None):
        rid = self.db.create_group_request(1, list(invitees))
        self.db.update_group_request(rid, link, message)
        return rid

    async def test_everyone_is_invited_when_all_are_subscribed(self):
        rid = self.request()
        result = await bot_module.send_group_invitations(self.ctx, rid)
        self.assertEqual(tuple(result), (4, 4, 0))
        for uid in (2, 3, 4, 5):
            markup = self.bot.sent[[c for c, _, _ in self.bot.sent].index(uid)][2]
            self.assertEqual(callbacks(markup), [f"join_{rid}", f"decline_{rid}"])

    async def test_unsubscribed_unverified_and_deleted_are_not_invited(self):
        rid = self.request()
        self.bot.statuses[3] = ChatMemberStatus.LEFT                 # not subscribed
        self.bot.errors[4] = NetworkError("timeout")                 # could not be verified
        self.db.delete_profile(5)                                    # deleted profile
        with self.assertLogs("chronoroom", level="WARNING"):
            result = await bot_module.send_group_invitations(self.ctx, rid)
        self.assertEqual(tuple(result), (1, 4, 1))
        self.assertEqual({c for c, _, _ in self.bot.sent}, {2}, "only the confirmed subscriber is invited")

    async def test_during_an_outage_nobody_is_invited_and_the_report_says_why(self):
        rid = self.request()
        self.bot.default_error = NetworkError("down")
        self.ctx.user_data["group_draft"] = {"request_id": rid, "link": "https://t.me/mygroup"}
        with self.assertLogs("chronoroom", level="WARNING"):
            report = await bot_module.dispatch_group_invitation(self.ctx, None)
        self.assertEqual(self.bot.sent, [])
        self.assertIn("Successful: 0", report)
        self.assertIn("Failed: 0", report, "unverified people are not counted as failures")
        self.assertIn("Not invited: 4", report)
        self.assertIn("could not confirm", report)

    async def test_report_without_problems_has_no_warning(self):
        rid = self.request(invitees=(2,))
        self.ctx.user_data["group_draft"] = {"request_id": rid, "link": "https://t.me/mygroup"}
        report = await bot_module.dispatch_group_invitation(self.ctx, "hello all")
        self.assertIn("Successful: 1", report)
        self.assertNotIn("Not invited", report)
        self.assertIn("hello all", self.bot.texts_to(2)[0])

    async def test_blocked_user_counts_as_failed_not_unverified(self):
        rid = self.request(invitees=(2, 3))
        self.bot.blocked.add(3)
        with self.assertLogs("chronoroom", level="WARNING"):
            result = await bot_module.send_group_invitations(self.ctx, rid)
        self.assertEqual(tuple(result), (1, 2, 0))

    async def test_group_name_and_message_are_escaped_in_the_invitation(self):
        self.db.upsert_profile(1, None, EVIL, BIO)
        rid = self.request(invitees=(2,), message=EVIL)
        await bot_module.send_group_invitations(self.ctx, rid)
        text = self.bot.texts_to(2)[0]
        self.assertNotIn("<a href", text)
        self.assertIn("&lt;a href", text)

    async def test_join_sends_the_link_and_notifies_once(self):
        rid = self.request(message="welcome")
        query = self.query(2, f"join_{rid}", name="Bob")
        self.assertIsNone(await bot_module.cb_join(query, self.ctx, rid))
        self.assertIn("https://t.me/mygroup", query.edits[-1][0])
        self.assertEqual(len(self.bot.texts_to(1)), 1)

        again = self.query(2, f"join_{rid}")
        alert = await bot_module.cb_join(again, self.ctx, rid)
        self.assertIn("already answered", alert)
        self.assertEqual(again.edits, [], "the link is not sent a second time")
        self.assertEqual(len(self.bot.texts_to(1)), 1, "the organizer is notified once")

    async def test_decline_after_join_and_join_after_decline_are_refused(self):
        rid = self.request()
        await bot_module.cb_join(self.query(2, "x"), self.ctx, rid)
        self.assertIn("already answered", await bot_module.cb_decline(self.query(2, "x"), self.ctx, rid))
        await bot_module.cb_decline(self.query(3, "x"), self.ctx, rid)
        self.assertIn("already answered", await bot_module.cb_join(self.query(3, "x"), self.ctx, rid))
        self.assertEqual(len(self.bot.texts_to(1)), 2, "one notification per invitee")

    async def test_names_in_organizer_notifications_are_escaped(self):
        rid = self.request()
        self.db.upsert_profile(2, None, EVIL, BIO)
        self.db.upsert_profile(3, None, EVIL, BIO)
        await bot_module.cb_join(self.query(2, "x", name=EVIL), self.ctx, rid)
        await bot_module.cb_decline(self.query(3, "x", name=EVIL), self.ctx, rid)
        for text in self.bot.texts_to(1):
            self.assertNotIn("<a href", text, "user-controlled names must never become markup")
            self.assertNotIn("<b>x", text)
            self.assertIn("&lt;a href", text)

    async def test_outsiders_and_incomplete_requests_cannot_join(self):
        rid = self.request()
        self.assertIn("no longer available", await bot_module.cb_join(self.query(99, "x"), self.ctx, rid))
        no_link = self.db.create_group_request(1, [2])
        self.assertIn("no longer available", await bot_module.cb_join(self.query(2, "x"), self.ctx, no_link))
        self.assertIn("no longer available", await bot_module.cb_join(self.query(2, "x"), self.ctx, "group_nope"))
        self.assertEqual(self.bot.sent, [])


class AnonymityTests(HandlerTestCase):
    BOARD_IDS = (7000001, 7000002, 7000003, 7000004, 7000005, 7000006)
    ME = 8000001

    def setUp(self):
        super().setUp()
        for uid in self.BOARD_IDS:
            add_profile(self.db, uid, f"Person{uid % 10}")
        add_profile(self.db, self.ME, "Me")

    async def open_board(self):
        query = self.query(self.ME, "find_friends")
        await bot_module.cb_find_friends(query, self.ctx, "")
        return query

    def select_buttons(self, query):
        return [d for d in callbacks(query.edits[-1][1]) if d.startswith("select_")]

    async def test_board_buttons_do_not_contain_telegram_ids(self):
        query = await self.open_board()
        buttons = self.select_buttons(query)
        self.assertEqual(len(buttons), bot_module.PROFILES_PER_PAGE)
        for data in callbacks(query.edits[-1][1]):
            for uid in self.BOARD_IDS + (self.ME,):
                self.assertNotIn(str(uid), data)
            self.assertLessEqual(len(data.encode()), 64)

    async def test_selection_maps_the_position_to_the_right_person(self):
        query = await self.open_board()
        order = self.ctx.user_data["profile_order"]
        for position_in_page, data in enumerate(self.select_buttons(query)):
            _, generation, position = data.split("_")
            self.assertEqual(int(position), position_in_page)                       # first page
        data = self.select_buttons(query)[2]
        select = self.query(self.ME, data)
        self.assertIsNone(await bot_module.cb_select(select, self.ctx, data[len("select_"):]))
        self.assertEqual(self.ctx.user_data["selected_ids"], [order[2]])
        self.assertEqual(select.edits[-1][0].count("✅"), 2, "marked in the text and in the button row")

    async def test_second_page_positions_continue(self):
        await self.open_board()
        query = self.query(self.ME, "page_1")
        await bot_module.cb_page(query, self.ctx, "1")
        positions = sorted(int(d.split("_")[2]) for d in self.select_buttons(query))
        self.assertEqual(positions, [4, 5])

    async def test_a_new_search_invalidates_old_buttons(self):
        old = self.select_buttons(await self.open_board())[0]
        await self.open_board()                                                      # reshuffle
        alert = await bot_module.cb_select(self.query(self.ME, old), self.ctx, old[len("select_"):])
        self.assertIn("outdated", alert)
        self.assertEqual(self.ctx.user_data["selected_ids"], [])

    async def test_broken_or_forged_selection_data_is_rejected(self):
        await self.open_board()
        generation = self.ctx.user_data["order_gen"]
        for arg in ("", "abc", "1", f"{generation}_x", f"{generation}_-1", f"{generation}_999",
                    f"{generation + 1}_0", "7000001"):
            with self.subTest(arg=arg):
                alert = await bot_module.cb_select(self.query(self.ME, "x"), self.ctx, arg)
                self.assertTrue(alert and alert.startswith("❌"))
        self.assertEqual(self.ctx.user_data["selected_ids"], [])

    async def test_a_telegram_id_is_not_accepted_as_a_selection(self):
        await self.open_board()
        for uid in self.BOARD_IDS:
            alert = await bot_module.cb_select(self.query(self.ME, "x"), self.ctx, str(uid))
            self.assertTrue(alert)
        self.assertEqual(self.ctx.user_data["selected_ids"], [])

    async def test_reply_button_does_not_reveal_the_senders_id(self):
        target = self.BOARD_IDS[0]
        self.ctx.user_data["selected_ids"] = [target]
        await bot_module.cb_write_personal(self.query(self.ME, "write_personal"), self.ctx, "")
        update = make_update(self.ME, "hello there", first_name="Me")
        await bot_module.handle_chat_input(update, self.ctx, "hello there")
        text, markup = [(t, m) for c, t, m in self.bot.sent if c == target][-1]
        data = callbacks(markup)
        self.assertEqual(len(data), 2)
        self.assertNotIn(str(self.ME), "".join(data))
        self.assertNotIn(str(target), "".join(data))
        self.assertRegex(data[0], r"^reply_c[0-9a-f]{16}$")
        self.assertIn("hello there", text)
        self.assertNotIn("Username", text)

    async def test_the_recipient_can_reply_through_the_opaque_id(self):
        target = self.BOARD_IDS[0]
        self.ctx.user_data["selected_ids"] = [target]
        await bot_module.cb_write_personal(self.query(self.ME, "write_personal"), self.ctx, "")
        await bot_module.handle_chat_input(make_update(self.ME, "hi"), self.ctx, "hi")
        reply_button = callbacks([m for c, _, m in self.bot.sent if c == target][-1])[0]
        recipient_ctx = make_context(self.bot, self.db, self.checker)
        chat_id = reply_button[len("reply_"):]
        self.assertIsNone(await bot_module.cb_reply(self.query(target, reply_button), recipient_ctx, chat_id))
        self.assertEqual(recipient_ctx.user_data["state"], State.IN_CHAT)
        # somebody who is not part of the chat cannot use the id
        stranger = make_context(self.bot, self.db, self.checker)
        self.assertEqual(await bot_module.cb_reply(self.query(self.BOARD_IDS[1], reply_button), stranger, chat_id),
                         "❌ Chat not found!")

    async def test_recipient_names_in_messages_are_escaped(self):
        target = self.BOARD_IDS[0]
        self.db.upsert_profile(self.ME, None, EVIL, BIO)
        self.ctx.user_data["selected_ids"] = [target]
        await bot_module.cb_write_personal(self.query(self.ME, "write_personal"), self.ctx, "")
        await bot_module.handle_chat_input(make_update(self.ME, EVIL, first_name=EVIL), self.ctx, EVIL)
        text = self.bot.texts_to(target)[-1]
        self.assertNotIn("<a href", text)
        self.assertEqual(text.count("&lt;a href"), 2, "sender name and message text")


class ChatSubscriptionTests(HandlerTestCase):
    ME, TARGET = 10, 20

    def setUp(self):
        super().setUp()
        add_profile(self.db, self.ME, "Me")
        add_profile(self.db, self.TARGET, "Target")
        self.ctx.user_data["selected_ids"] = [self.TARGET]

    async def start_chat(self):
        await bot_module.cb_write_personal(self.query(self.ME, "write_personal"), self.ctx, "")
        self.assertEqual(self.ctx.user_data["state"], State.IN_CHAT)

    async def test_unverifiable_recipient_does_not_receive_anything_and_the_chat_stays_open(self):
        await self.start_chat()
        self.bot.errors[self.TARGET] = NetworkError("timeout")
        update = make_update(self.ME, "hello")
        with self.assertLogs("chronoroom", level="WARNING"):
            await bot_module.handle_chat_input(update, self.ctx, "hello")
        self.assertEqual(self.bot.texts_to(self.TARGET), [])
        self.assertIn("send your message again", update.effective_message.replies[-1][0])
        self.assertEqual(self.ctx.user_data["state"], State.IN_CHAT, "the user can simply resend")
        # ... and once Telegram answers again, the same chat works
        del self.bot.errors[self.TARGET]
        await bot_module.handle_chat_input(make_update(self.ME, "hello"), self.ctx, "hello")
        self.assertEqual(len(self.bot.texts_to(self.TARGET)), 1)

    async def test_unsubscribed_recipient_is_still_refused_and_the_chat_is_closed(self):
        await self.start_chat()
        self.bot.statuses[self.TARGET] = ChatMemberStatus.LEFT
        update = make_update(self.ME, "hello")
        await bot_module.handle_chat_input(update, self.ctx, "hello")
        self.assertEqual(self.bot.texts_to(self.TARGET), [])
        self.assertIn("not subscribed", update.effective_message.replies[-1][0])
        self.assertEqual(self.ctx.user_data["state"], State.NONE)


@unittest.skipUnless(HAVE_PTB, "python-telegram-bot is not installed")
class StartupTests(unittest.IsolatedAsyncioTestCase):
    def app(self, bot, channel="@channel"):
        from types import SimpleNamespace as NS
        return NS(bot=bot, bot_data={"subscriptions": SubscriptionChecker(channel)})

    async def test_wrong_channel_or_missing_rights_stop_the_bot_with_a_clear_message(self):
        for error in (BadRequest("Chat not found"),):
            bot = FakeBot(); bot.default_error = error
            with self.assertRaises(ConfigurationError) as caught:
                await bot_module.verify_channel_access(self.app(bot))
            self.assertIn("administrator", str(caught.exception))
            self.assertIn("@channel", str(caught.exception))

    async def test_forbidden_is_also_a_configuration_error(self):
        from telegram.error import Forbidden
        bot = FakeBot(); bot.default_error = Forbidden("bot is not a member of the channel chat")
        with self.assertRaises(ConfigurationError):
            await bot_module.verify_channel_access(self.app(bot))

    async def test_a_temporary_problem_at_startup_does_not_prevent_starting(self):
        bot = FakeBot(); bot.default_error = NetworkError("down")
        with self.assertLogs("chronoroom", level="WARNING"):
            await bot_module.verify_channel_access(self.app(bot))

    async def test_non_admin_bot_gets_a_warning(self):
        bot = FakeBot(); bot.statuses[bot.id] = ChatMemberStatus.MEMBER
        with self.assertLogs("chronoroom", level="WARNING") as logs:
            await bot_module.verify_channel_access(self.app(bot))
        self.assertIn("not an administrator", logs.output[0])

    async def test_admin_bot_and_disabled_requirement(self):
        bot = FakeBot(); bot.statuses[bot.id] = ChatMemberStatus.ADMINISTRATOR
        await bot_module.verify_channel_access(self.app(bot))
        self.assertEqual(bot.member_calls, 1)
        silent = FakeBot()
        with self.assertLogs("chronoroom", level="WARNING") as logs:
            await bot_module.verify_channel_access(self.app(silent, channel=""))
        self.assertIn("DISABLED", logs.output[0])
        self.assertEqual(silent.member_calls, 0)


if __name__ == "__main__":
    unittest.main()
