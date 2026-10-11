import os
import re
import tempfile
import unittest

from database import Database


def profile(db, uid, name="Name", bio="b" * 20, username=None):
    db.upsert_profile(uid, username, name, bio)


class ProfileTests(unittest.TestCase):
    def setUp(self):
        self.db = Database(":memory:")
        self.addCleanup(self.db.close)

    def test_upsert_updates_existing_profile(self):
        profile(self.db, 1, "Old", "o" * 20)
        profile(self.db, 1, "New", "n" * 20, username="neo")
        row = self.db.get_profile(1)
        self.assertEqual((row["first_name"], row["bio"], row["username"]), ("New", "n" * 20, "neo"))
        self.assertEqual(self.db.conn.execute("SELECT COUNT(*) FROM profiles").fetchone()[0], 1)

    def test_empty_bio_is_not_a_profile(self):
        self.db.conn.execute("INSERT INTO profiles (user_id, first_name, bio) VALUES (9, 'x', '   ')")
        self.assertIsNone(self.db.get_profile(9))
        self.assertNotIn(9, self.db.get_profile_ids(exclude_user_id=1))

    def test_ids_exclude_self_and_by_ids_keeps_order_and_skips_missing(self):
        for uid in (1, 2, 3):
            profile(self.db, uid, f"U{uid}")
        self.assertCountEqual(self.db.get_profile_ids(exclude_user_id=1), [2, 3])
        self.assertEqual([p["user_id"] for p in self.db.get_profiles_by_ids([3, 99, 1, 2])], [3, 1, 2])
        self.assertEqual(self.db.get_profiles_by_ids([]), [])

    def test_sql_injection_in_text_is_stored_literally(self):
        evil = "x'); DROP TABLE profiles;--"
        profile(self.db, 1, evil, evil * 2)
        self.assertEqual(self.db.get_profile(1)["first_name"], evil)

    def test_delete_profile_reports_whether_something_was_deleted(self):
        profile(self.db, 1)
        self.assertTrue(self.db.delete_profile(1))
        self.assertFalse(self.db.delete_profile(1))


class GroupRequestTests(unittest.TestCase):
    def setUp(self):
        self.db = Database(":memory:")
        self.addCleanup(self.db.close)

    def test_roundtrip(self):
        rid = self.db.create_group_request(1, [2, 3])
        request = self.db.get_group_request(rid)
        self.assertEqual((request["creator_id"], request["selected_ids"], request["status"]), (1, [2, 3], "pending"))
        self.db.update_group_request(rid, "https://t.me/g", "hello")
        request = self.db.get_group_request(rid)
        self.assertEqual((request["group_link"], request["group_message"], request["status"]),
                         ("https://t.me/g", "hello", "sent"))
        self.assertIsNone(self.db.get_group_request("group_missing"))

    def test_first_answer_wins(self):
        rid = self.db.create_group_request(1, [2, 3])
        self.assertTrue(self.db.record_group_response(rid, 2, "join"))
        self.assertFalse(self.db.record_group_response(rid, 2, "join"), "double tap")
        self.assertFalse(self.db.record_group_response(rid, 2, "decline"), "decline after join")
        self.assertTrue(self.db.record_group_response(rid, 3, "decline"), "another invitee is independent")
        answers = dict(self.db.conn.execute("SELECT user_id, response FROM group_responses"))
        self.assertEqual(answers, {2: "join", 3: "decline"})

    def test_delete_profile_removes_what_the_user_created_and_answered(self):
        mine = self.db.create_group_request(1, [2])
        self.db.update_group_request(mine, "https://t.me/secret", "private note")
        theirs = self.db.create_group_request(3, [1])
        self.db.record_group_response(mine, 2, "join")
        self.db.record_group_response(theirs, 1, "join")
        self.db.delete_profile(1)
        self.assertIsNone(self.db.get_group_request(mine), "link and message of the organizer are gone")
        self.assertIsNotNone(self.db.get_group_request(theirs), "other people's invitations stay")
        remaining = [tuple(r) for r in self.db.conn.execute("SELECT request_id, user_id FROM group_responses")]
        self.assertEqual(remaining, [], "answers by the user and answers to the user's invitations are gone")


class ChatTests(unittest.TestCase):
    def setUp(self):
        self.db = Database(":memory:")
        self.addCleanup(self.db.close)

    def test_same_pair_gives_same_chat_in_both_directions(self):
        a = self.db.create_chat(111, 222)
        self.assertEqual(self.db.create_chat(222, 111), a)
        self.assertEqual(self.db.conn.execute("SELECT COUNT(*) FROM active_chats").fetchone()[0], 1)
        chat = self.db.get_chat(a)
        self.assertEqual((chat["user1_id"], chat["user2_id"]), (111, 222))

    def test_chat_id_is_opaque(self):
        chats = {}
        for a in range(1000000, 1000010):
            for b in range(2000000, 2000010):
                chats[(a, b)] = self.db.create_chat(a, b)
        self.assertEqual(len(set(chats.values())), 100, "every pair gets its own id")
        for (a, b), chat_id in chats.items():
            self.assertRegex(chat_id, r"^c[0-9a-f]{16}$")
            self.assertNotIn(str(a), chat_id, "the id must not contain a user id")
            self.assertNotIn(str(b), chat_id, "the id must not contain a user id")

    def test_unknown_or_empty_chat(self):
        self.assertIsNone(self.db.get_chat(None))
        self.assertIsNone(self.db.get_chat(""))
        self.assertIsNone(self.db.get_chat("cdeadbeefdeadbeef"))
        self.assertIsNone(self.db.get_chat("chat_1_2"))

    def test_one_chat_per_pair_is_enforced(self):
        self.db.create_chat(1, 2)
        import sqlite3
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.conn.execute("INSERT INTO active_chats (chat_id, user1_id, user2_id) VALUES ('x', 1, 2)")


class LegacyChatMigrationTests(unittest.TestCase):
    """Old databases contain ids like chat_<low>_<high>, which expose user ids in button data."""

    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.path = os.path.join(self._dir.name, "old.db")
        db = Database(self.path)
        db.conn.execute("INSERT INTO active_chats (chat_id, user1_id, user2_id) VALUES ('chat_5_9', 5, 9)")
        db.conn.execute("INSERT INTO active_chats (chat_id, user1_id, user2_id) VALUES ('chat_7_8', 7, 8)")
        db.conn.commit()
        db.close()

    def test_legacy_ids_are_replaced_and_old_buttons_still_work(self):
        db = Database(self.path)
        self.addCleanup(db.close)
        ids = [r[0] for r in db.conn.execute("SELECT chat_id FROM active_chats")]
        self.assertEqual(len(ids), 2)
        for chat_id in ids:
            self.assertRegex(chat_id, r"^c[0-9a-f]{16}$")
        # a "Reply" button from an old message still resolves
        chat = db.get_chat("chat_5_9")
        self.assertEqual((chat["user1_id"], chat["user2_id"]), (5, 9))
        self.assertEqual(db.get_chat("chat_9_5")["user1_id"], 5)
        # and new messages of that pair use the new id
        self.assertEqual(db.create_chat(9, 5), chat["chat_id"])
        self.assertNotRegex(chat["chat_id"], r"chat_")

    def test_migration_is_idempotent(self):
        first = Database(self.path); ids1 = sorted(r[0] for r in first.conn.execute("SELECT chat_id FROM active_chats")); first.close()
        second = Database(self.path); ids2 = sorted(r[0] for r in second.conn.execute("SELECT chat_id FROM active_chats")); second.close()
        self.assertEqual(ids1, ids2)

    def test_legacy_id_of_a_pair_without_chat_is_unknown(self):
        db = Database(self.path)
        self.addCleanup(db.close)
        self.assertIsNone(db.get_chat("chat_100_200"))


if __name__ == "__main__":
    unittest.main()
