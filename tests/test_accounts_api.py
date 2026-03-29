from __future__ import annotations

import os
import tempfile
import time
import unittest
from unittest.mock import patch

from sqlmodel import SQLModel, Session, create_engine

from api import accounts as accounts_api
from core.db import AccountModel


class AccountsApiTests(unittest.TestCase):
    def setUp(self):
        fd, self.db_path = tempfile.mkstemp(prefix="accounts_api_test_", suffix=".db")
        os.close(fd)
        self.engine = create_engine(f"sqlite:///{self.db_path}")
        SQLModel.metadata.create_all(self.engine)

    def tearDown(self):
        try:
            os.remove(self.db_path)
        except FileNotFoundError:
            pass

    def test_get_stats_returns_remote_summary(self):
        with Session(self.engine) as session:
            with patch(
                "api.accounts._reconcile_chatgpt_local_with_remote",
                return_value={"provider": "sub2api", "remote_total": 12, "deleted_error_accounts": 3, "deleted_local_accounts": 2},
            ):
                result = accounts_api.get_stats(session=session)

        self.assertIn("remote_summary", result)
        self.assertEqual(result["remote_summary"]["provider"], "sub2api")
        self.assertEqual(result["remote_summary"]["total"], 12)
        self.assertEqual(result["remote_summary"]["deleted_error_accounts"], 3)
        self.assertEqual(result["remote_summary"]["deleted_local_accounts"], 2)
        self.assertEqual(result["remote_summary"]["error"], "")

    def test_list_accounts_deletes_local_account_missing_from_remote(self):
        email = f"missing-{time.time_ns()}@example.com"
        with Session(self.engine) as session:
            account = AccountModel(
                platform="chatgpt",
                email=email,
                password="pw",
                status="registered",
                extra_json="{}",
            )
            session.add(account)
            session.commit()
            session.refresh(account)
            account_id = account.id

            with patch(
                "services.external_sync.list_remote_chatgpt_accounts_detail",
                return_value={"provider": "sub2api", "emails": set(), "total": 0, "deleted_error_accounts": 0},
            ):
                result = accounts_api.list_accounts(platform="chatgpt", email=email, session=session)

            self.assertEqual(result["total"], 0)
            self.assertEqual(len(result["items"]), 0)
            self.assertIsNone(session.get(AccountModel, account_id))

    def test_list_accounts_keeps_local_account_when_remote_exists(self):
        email = f"exists-{time.time_ns()}@example.com"
        with Session(self.engine) as session:
            account = AccountModel(
                platform="chatgpt",
                email=email,
                password="pw",
                status="registered",
                extra_json="{}",
            )
            session.add(account)
            session.commit()
            session.refresh(account)
            account_id = account.id

            with patch(
                "services.external_sync.list_remote_chatgpt_accounts_detail",
                return_value={"provider": "sub2api", "emails": {email}, "total": 1, "deleted_error_accounts": 0},
            ):
                result = accounts_api.list_accounts(platform="chatgpt", email=email, session=session)

            self.assertEqual(result["total"], 1)
            self.assertEqual(len(result["items"]), 1)
            self.assertEqual(result["items"][0].email, email)

            created = session.get(AccountModel, account_id)
            session.delete(created)
            session.commit()


if __name__ == "__main__":
    unittest.main()
