from __future__ import annotations

import unittest

from p2p.contacts import (
    create_contact,
    join_contact,
    list_contacts,
    migrate_vault,
    select_contact,
    upsert_contact,
)
from p2p.invite import encode_invite


def _flat_smtp() -> dict:
    return {
        "mode": "smtp",
        "my_email": "alice@example.com",
        "peer_email": "bob@example.com",
        "imap_host": "imap.example.com",
        "imap_password": "secret",
        "smtp_host": "smtp.example.com",
        "smtp_port": 465,
        "smtp_starttls": False,
        "psk_b64": "cHNr",
        "folder": "INBOX",
        "signal_timeout": 600.0,
        "replay_window": 600.0,
    }


class ContactsTests(unittest.TestCase):
    def test_migrate_flat_smtp(self) -> None:
        data = migrate_vault(_flat_smtp())
        self.assertEqual(data["version"], 2)
        self.assertEqual(data["identity"]["my_email"], "alice@example.com")
        self.assertNotIn("peer_email", data["identity"])
        self.assertIn("bob@example.com", data["contacts"])
        key, contact = select_contact(data, "BOB@example.com")
        self.assertEqual(key, "bob@example.com")
        self.assertEqual(contact["psk_b64"], "cHNr")
        self.assertEqual(contact["mode"], "smtp")
        self.assertEqual(contact["role"], "initiator")

    def test_migrate_flat_imap(self) -> None:
        data = migrate_vault(
            {
                "mode": "imap",
                "my_email": "shared@example.com",
                "imap_host": "imap.example.com",
                "imap_password": "secret",
                "folder": "p2p-test",
                "psk_b64": "cHNr",
            }
        )
        self.assertEqual(data["identity"]["mode"], "imap")
        key, contact = select_contact(data)
        self.assertEqual(key, "peer")
        self.assertEqual(contact["folder"], "p2p-test")

    def test_multiple_contacts_require_selection(self) -> None:
        data = migrate_vault(_flat_smtp())
        upsert_contact(
            data,
            {
                "peer_email": "carol@example.com",
                "psk_b64": "x",
                "mode": "smtp",
                "folder": "INBOX",
            },
        )
        self.assertEqual(len(list_contacts(data)), 2)
        with self.assertRaises(ValueError):
            select_contact(data)
        key, contact = select_contact(data, "carol@example.com")
        self.assertEqual(key, "carol@example.com")

    def test_upsert_duplicate_needs_force(self) -> None:
        data = migrate_vault(_flat_smtp())
        with self.assertRaises(ValueError):
            upsert_contact(
                data,
                {"peer_email": "bob@example.com", "psk_b64": "new", "mode": "smtp"},
            )
        upsert_contact(
            data,
            {"peer_email": "bob@example.com", "psk_b64": "new", "mode": "smtp"},
            force=True,
        )
        self.assertEqual(data["contacts"]["bob@example.com"]["psk_b64"], "new")

    def test_create_then_join_contact(self) -> None:
        alice = {
            "version": 2,
            "identity": {"my_email": "alice@example.com", "mode": "smtp"},
            "contacts": {},
        }
        key, contact, code = create_contact(alice, "bob@example.com")
        self.assertEqual(key, "bob@example.com")
        self.assertTrue(code.startswith("p2p1:"))

        bob = {
            "version": 2,
            "identity": {"my_email": "bob@example.com", "mode": "smtp"},
            "contacts": {},
        }
        joined_key, joined = join_contact(bob, code)
        self.assertEqual(joined_key, "alice@example.com")
        self.assertEqual(joined["psk_b64"], contact["psk_b64"])
        self.assertEqual(contact["role"], "initiator")
        self.assertEqual(joined["role"], "responder")
        self.assertEqual(len(bob["contacts"]), 1)

    def test_create_responder_invite_becomes_initiator(self) -> None:
        alice = {
            "version": 2,
            "identity": {"my_email": "alice@example.com", "mode": "smtp"},
            "contacts": {},
        }
        _, contact, code = create_contact(alice, "bob@example.com", role="responder")
        self.assertEqual(contact["role"], "responder")

        bob = {
            "version": 2,
            "identity": {"my_email": "bob@example.com", "mode": "smtp"},
            "contacts": {},
        }
        _, joined = join_contact(bob, code)
        self.assertEqual(joined["role"], "initiator")

    def test_legacy_invite_without_role_defaults(self) -> None:
        code = encode_invite(
            {
                "v": 1,
                "mode": "smtp",
                "my_email": "alice@example.com",
                "peer_email": "bob@example.com",
                "psk_b64": "cHNr",
            }
        )
        bob = {
            "version": 2,
            "identity": {"my_email": "bob@example.com", "mode": "smtp"},
            "contacts": {},
        }
        _, joined = join_contact(bob, code)
        self.assertEqual(joined["role"], "responder")

    def test_migrate_v2_adds_default_role(self) -> None:
        raw = {
            "version": 2,
            "identity": {"my_email": "alice@example.com", "mode": "smtp"},
            "contacts": {
                "bob@example.com": {
                    "peer_email": "bob@example.com",
                    "psk_b64": "cHNr",
                    "mode": "smtp",
                    "folder": "INBOX",
                }
            },
        }
        data = migrate_vault(raw)
        self.assertEqual(data["contacts"]["bob@example.com"]["role"], "initiator")
        self.assertNotIn("role", raw["contacts"]["bob@example.com"])

    def test_invalid_role_rejected(self) -> None:
        alice = {
            "version": 2,
            "identity": {"my_email": "alice@example.com", "mode": "smtp"},
            "contacts": {},
        }
        with self.assertRaises(ValueError):
            create_contact(alice, "bob@example.com", role="both")

    def test_join_checks_identity_email(self) -> None:
        alice = {
            "version": 2,
            "identity": {"my_email": "alice@example.com", "mode": "smtp"},
            "contacts": {},
        }
        _, _, code = create_contact(alice, "bob@example.com")
        charlie = {
            "version": 2,
            "identity": {"my_email": "charlie@example.com", "mode": "smtp"},
            "contacts": {},
        }
        with self.assertRaises(ValueError):
            join_contact(charlie, code)


if __name__ == "__main__":
    unittest.main()
