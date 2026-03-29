from __future__ import annotations

import unittest
from unittest.mock import patch

from core.base_mailbox import DriftMailMailbox, MailboxAccount, create_mailbox


class DummyResponse:
    def __init__(self, payload, status_code: int = 200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"http {self.status_code}")


class DriftMailboxTests(unittest.TestCase):
    def test_create_mailbox_returns_driftmail_instance(self):
        mailbox = create_mailbox(
            "driftmail",
            {
                "drift_mail_base_url": "https://drift-mail.example.com",
                "drift_mail_access_key": "drift-access-key",
                "drift_mail_domain": "example.com",
            },
        )

        self.assertIsInstance(mailbox, DriftMailMailbox)
        self.assertEqual(mailbox.api, "https://drift-mail.example.com")
        self.assertEqual(mailbox.access_key, "drift-access-key")
        self.assertEqual(mailbox.domain, "example.com")

    @patch("requests.patch")
    @patch("requests.post")
    def test_get_email_creates_mailbox_and_extends_expiry(self, mock_post, mock_patch):
        mock_post.return_value = DummyResponse(
            {
                "address": "hello@example.com",
                "token": "mailbox-token",
            }
        )
        mock_patch.return_value = DummyResponse({"ok": True})
        mailbox = DriftMailMailbox(
            api_url="https://drift-mail.example.com",
            access_key="drift-access-key",
            domain="example.com",
        )

        account = mailbox.get_email()

        self.assertEqual(account.email, "hello@example.com")
        self.assertEqual(account.account_id, "mailbox-token")
        self.assertEqual(account.extra, {"token": "mailbox-token"})
        mock_post.assert_called_once()
        self.assertEqual(mock_post.call_args.args[0], "https://drift-mail.example.com/api/generate")
        self.assertEqual(mock_post.call_args.kwargs["json"], {"domain": "example.com"})
        self.assertEqual(mock_post.call_args.kwargs["headers"]["x-access-key"], "drift-access-key")
        mock_patch.assert_called_once()
        self.assertEqual(mock_patch.call_args.args[0], "https://drift-mail.example.com/api/me/extend")
        self.assertEqual(mock_patch.call_args.kwargs["json"], {"minutes": 30})
        self.assertEqual(mock_patch.call_args.kwargs["headers"]["Authorization"], "Bearer mailbox-token")

    @patch("requests.get")
    def test_wait_for_code_reads_message_detail_and_extracts_otp(self, mock_get):
        mock_get.side_effect = [
            DummyResponse({"hydra:member": [{"id": "msg-1", "subject": "welcome"}], "hydra:totalItems": 1}),
            DummyResponse(
                {
                    "id": "msg-1",
                    "subject": "OpenAI verification",
                    "text": "Your verification code is 123456",
                }
            ),
        ]
        mailbox = DriftMailMailbox(
            api_url="https://drift-mail.example.com",
            access_key="drift-access-key",
        )
        account = MailboxAccount(
            email="hello@example.com",
            account_id="mailbox-token",
            extra={"token": "mailbox-token"},
        )

        code = mailbox.wait_for_code(account, timeout=3)

        self.assertEqual(code, "123456")
        self.assertEqual(mock_get.call_count, 2)
        self.assertEqual(mock_get.call_args_list[0].args[0], "https://drift-mail.example.com/api/messages")
        self.assertEqual(mock_get.call_args_list[1].args[0], "https://drift-mail.example.com/api/messages/msg-1")


if __name__ == "__main__":
    unittest.main()
