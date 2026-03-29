from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from services import chatgpt_target_sync as sync


class DummyAccount:
    def __init__(self, platform: str = "chatgpt", email: str = "demo@example.com"):
        self.platform = platform
        self.email = email
        self.extra_json = "{}"

    def get_extra(self) -> dict:
        return json.loads(self.extra_json or "{}")

    def set_extra(self, data: dict) -> None:
        self.extra_json = json.dumps(data, ensure_ascii=False)


class ChatGPTTargetSyncTests(unittest.TestCase):
    def test_apply_sync_target_snapshot_updates_extra_json(self):
        account = DummyAccount()

        sync.apply_sync_target_snapshot(
            account,
            {
                "provider": "cliproxyapi",
                "remote_name": "demo@example.com.json",
                "remote_status": "active",
                "exists": True,
            },
        )

        self.assertEqual(
            account.get_extra()["sync_target"],
            {
                "provider": "cliproxyapi",
                "remote_name": "demo@example.com.json",
                "remote_status": "active",
                "exists": True,
            },
        )

    @patch.object(sync, "refresh_chatgpt_target_state")
    @patch.object(sync, "check_account_validity", return_value=True)
    def test_sync_and_cleanup_refreshes_target_for_valid_chatgpt_account(self, _check_valid, mock_refresh):
        account = DummyAccount()
        mock_refresh.return_value = {
            "provider": "sub2api",
            "exists": True,
            "confirmed": True,
            "message": "账号存在于 sub2api",
            "snapshot": {
                "provider": "sub2api",
                "remote_id": "12",
                "remote_status": "active",
                "exists": True,
            },
        }

        result = sync.sync_and_cleanup_account(account, delete_invalid=True, refresh_target=True)

        self.assertTrue(result["valid"])
        self.assertFalse(result["delete_local"])
        self.assertEqual(result["target"]["provider"], "sub2api")
        self.assertEqual(account.get_extra()["sync_target"]["remote_id"], "12")

    @patch.object(sync, "remove_chatgpt_account_from_target")
    @patch.object(sync, "check_account_validity", return_value=False)
    def test_sync_and_cleanup_marks_local_delete_for_invalid_chatgpt_account(self, _check_valid, mock_remove):
        account = DummyAccount()
        mock_remove.return_value = {
            "provider": "cliproxyapi",
            "exists": False,
            "confirmed": True,
            "message": "目标侧账号已删除",
            "snapshot": {
                "provider": "cliproxyapi",
                "remote_status": "deleted",
                "exists": False,
            },
        }

        result = sync.sync_and_cleanup_account(account, delete_invalid=True, refresh_target=True)

        self.assertFalse(result["valid"])
        self.assertTrue(result["delete_local"])
        self.assertEqual(result["target"]["provider"], "cliproxyapi")
        self.assertEqual(account.get_extra()["sync_target"]["remote_status"], "deleted")

    @patch.object(sync, "refresh_chatgpt_target_state")
    @patch.object(sync, "check_account_validity", return_value=True)
    def test_sync_and_cleanup_does_not_delete_local_when_target_snapshot_is_deleted(self, _check_valid, mock_refresh):
        account = DummyAccount()
        mock_refresh.return_value = {
            "provider": "sub2api",
            "exists": False,
            "confirmed": True,
            "message": "sub2api 中未找到账号",
            "snapshot": {
                "provider": "sub2api",
                "remote_status": "deleted",
                "exists": False,
            },
        }

        result = sync.sync_and_cleanup_account(account, delete_invalid=True, refresh_target=True)

        self.assertTrue(result["valid"])
        self.assertFalse(result["delete_local"])
        self.assertFalse(result["target"]["exists"])
        self.assertEqual(account.get_extra()["sync_target"]["remote_status"], "deleted")

    @patch.object(sync, "remove_chatgpt_account_from_target")
    @patch.object(sync, "check_account_validity", return_value=False)
    def test_sync_and_cleanup_keeps_local_account_when_invalid_delete_disabled(self, _check_valid, mock_remove):
        account = DummyAccount()
        mock_remove.return_value = {
            "provider": "sub2api",
            "exists": False,
            "confirmed": True,
            "message": "目标侧账号已删除",
            "snapshot": {
                "provider": "sub2api",
                "remote_status": "deleted",
                "exists": False,
            },
        }

        result = sync.sync_and_cleanup_account(account, delete_invalid=False, refresh_target=True)

        self.assertFalse(result["valid"])
        self.assertFalse(result["delete_local"])
        self.assertEqual(result["target"]["message"], "未执行")
        mock_remove.assert_not_called()
        self.assertEqual(account.get_extra(), {})

    @patch.object(sync, "remove_chatgpt_account_from_target")
    @patch.object(sync, "check_account_validity", return_value=False)
    def test_sync_and_cleanup_does_not_delete_local_on_unconfirmed_target_result(self, _check_valid, mock_remove):
        account = DummyAccount()
        mock_remove.return_value = {
            "provider": "sub2api",
            "exists": False,
            "confirmed": False,
            "message": "查询失败",
            "snapshot": {},
        }

        result = sync.sync_and_cleanup_account(account, delete_invalid=True, refresh_target=True)

        self.assertFalse(result["valid"])
        self.assertFalse(result["delete_local"])
        self.assertEqual(account.get_extra(), {})


if __name__ == "__main__":
    unittest.main()
