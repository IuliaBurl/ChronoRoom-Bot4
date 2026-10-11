import asyncio
import time
import unittest

from helpers import HAVE_PTB, FakeBot

if HAVE_PTB:
    from telegram.constants import ChatMemberStatus
    from telegram.error import BadRequest, Forbidden, NetworkError, RetryAfter, TimedOut
    from chronoroom_final import SubscriptionChecker, SubscriptionStatus

S = None if not HAVE_PTB else SubscriptionStatus


@unittest.skipUnless(HAVE_PTB, "python-telegram-bot is not installed")
class SubscriptionCheckerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.bot = FakeBot()
        self.checker = SubscriptionChecker("@channel", ttl=60)

    async def test_member_statuses(self):
        for status in (ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER):
            with self.subTest(status=status):
                self.bot.statuses[1] = status
                self.assertIs(await self.checker.check(self.bot, 1, force=True), S.SUBSCRIBED)

    async def test_not_a_member(self):
        for status in (ChatMemberStatus.LEFT, ChatMemberStatus.BANNED):
            with self.subTest(status=status):
                self.bot.statuses[1] = status
                self.assertIs(await self.checker.check(self.bot, 1, force=True), S.NOT_SUBSCRIBED)

    async def test_restricted_counts_only_if_still_a_member(self):
        self.bot.statuses[1] = ChatMemberStatus.RESTRICTED
        self.bot.is_member[1] = True
        self.assertIs(await self.checker.check(self.bot, 1, force=True), S.SUBSCRIBED)
        self.bot.is_member[1] = False
        self.assertIs(await self.checker.check(self.bot, 1, force=True), S.NOT_SUBSCRIBED)

    async def test_telegram_errors_never_grant_access(self):
        errors = [NetworkError("down"), TimedOut(), BadRequest("Chat not found"),
                  Forbidden("bot is not a member of the channel chat"), RetryAfter(5)]
        for error in errors:
            with self.subTest(error=type(error).__name__):
                self.bot.default_error = error
                with self.assertLogs("chronoroom", level="WARNING"):
                    self.assertIs(await self.checker.check(self.bot, 1), S.UNKNOWN)

    async def test_configuration_errors_are_logged_as_errors_and_outages_as_warnings(self):
        self.bot.default_error = BadRequest("Chat not found")
        with self.assertLogs("chronoroom", level="ERROR") as logs:
            await self.checker.check(self.bot, 1)
        self.assertEqual(logs.records[0].levelname, "ERROR")
        self.bot.default_error = NetworkError("down")
        with self.assertLogs("chronoroom", level="WARNING") as logs:
            await self.checker.check(self.bot, 1)
        self.assertEqual(logs.records[0].levelname, "WARNING")

    async def test_positive_answer_is_cached(self):
        await self.checker.check(self.bot, 1)
        await self.checker.check(self.bot, 1)
        self.assertEqual(self.bot.member_calls, 1)
        await self.checker.check(self.bot, 1, force=True)
        self.assertEqual(self.bot.member_calls, 2)

    async def test_negative_answer_is_not_cached(self):
        self.bot.statuses[1] = ChatMemberStatus.LEFT
        await self.checker.check(self.bot, 1)
        self.bot.statuses[1] = ChatMemberStatus.MEMBER
        self.assertIs(await self.checker.check(self.bot, 1), S.SUBSCRIBED, "subscribing must work at once")

    async def test_error_after_cache_expiry_does_not_keep_the_old_grant(self):
        await self.checker.check(self.bot, 1)
        self.checker._valid_until[1] = time.monotonic() - 1        # the cached answer expired
        self.bot.default_error = NetworkError("down")
        with self.assertLogs("chronoroom", level="WARNING"):
            self.assertIs(await self.checker.check(self.bot, 1), S.UNKNOWN)

    async def test_cache_still_works_while_telegram_is_down(self):
        await self.checker.check(self.bot, 1)
        self.bot.default_error = NetworkError("down")
        self.assertIs(await self.checker.check(self.bot, 1), S.SUBSCRIBED, "a fresh confirmation is a confirmation")

    async def test_empty_channel_disables_the_requirement(self):
        checker = SubscriptionChecker("")
        self.assertFalse(checker.enabled)
        self.bot.default_error = NetworkError("must not be called")
        self.assertIs(await checker.check(self.bot, 1), S.SUBSCRIBED)
        self.assertEqual(self.bot.member_calls, 0)

    async def test_expired_entries_are_pruned_when_the_cache_is_full(self):
        checker = SubscriptionChecker("@channel")
        checker.CACHE_LIMIT = 5
        now = time.monotonic()
        checker._valid_until = {i: now - 1 for i in range(5)}      # all expired
        await checker.check(self.bot, 100)
        self.assertEqual(list(checker._valid_until), [100])


if __name__ == "__main__":
    unittest.main()
