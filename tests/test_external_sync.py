from __future__ import annotations

import unittest
from unittest.mock import patch

from services import external_sync


class DummyAccount:
    def __init__(self, email: str = "demo@example.com"):
        self.platform = "chatgpt"
        self.email = email
        self.token = "access-token"
        self.extra = {
            "access_token": "access-token",
            "refresh_token": "refresh-token",
            "id_token": "id-token",
            "session_token": "session-token",
            "client_id": "client-id",
            "cookies": "cookie=1",
        }


class ExternalSyncTests(unittest.TestCase):
    def test_list_remote_chatgpt_accounts_detail_returns_deleted_error_account_count(self):
        def fake_request_json(method, url, *, headers, params=None, json_body=None):
            self.assertEqual(headers, {"X-API-Key": "admin-key"})
            if method == "GET" and url.endswith("/api/v1/admin/accounts"):
                return 200, {
                    "data": {
                        "items": [
                            {"id": 11, "name": "error@example.com", "status": "error"},
                            {"id": 12, "name": "healthy@example.com", "status": "active"},
                            {"id": 13, "name": "CPA", "status": "active"},
                        ]
                    }
                }
            if method == "DELETE" and url.endswith("/api/v1/admin/accounts/11"):
                return 200, {"message": "deleted"}
            raise AssertionError(f"unexpected call: {method} {url}")

        with patch.object(external_sync, "_request_json", side_effect=fake_request_json):
            with patch("core.config_store.config_store.get", side_effect=lambda key, default="": {
                "sub2api_url": "https://sub2api.example.com",
                "sub2api_admin_key": "admin-key",
            }.get(key, default)):
                detail = external_sync.list_remote_chatgpt_accounts_detail("sub2api")

        self.assertEqual(detail["provider"], "sub2api")
        self.assertEqual(detail["emails"], {"healthy@example.com"})
        self.assertEqual(detail["deleted_error_accounts"], 1)

    def test_list_remote_chatgpt_accounts_deletes_sub2api_error_accounts(self):
        calls = []

        def fake_request_json(method, url, *, headers, params=None, json_body=None):
            calls.append((method, url, params, json_body))
            self.assertEqual(headers, {"X-API-Key": "admin-key"})
            if method == "GET" and url.endswith("/api/v1/admin/accounts"):
                return 200, {
                    "data": {
                        "items": [
                            {"id": 11, "name": "error@example.com", "status": "error"},
                            {"id": 12, "name": "healthy@example.com", "status": "active"},
                            {"id": 13, "name": "Custom-hk", "status": "active"},
                        ]
                    }
                }
            if method == "DELETE" and url.endswith("/api/v1/admin/accounts/11"):
                return 200, {"message": "deleted"}
            raise AssertionError(f"unexpected call: {method} {url}")

        with patch.object(external_sync, "_request_json", side_effect=fake_request_json):
            with patch("core.config_store.config_store.get", side_effect=lambda key, default="": {
                "sub2api_url": "https://sub2api.example.com",
                "sub2api_admin_key": "admin-key",
            }.get(key, default)):
                emails = external_sync.list_remote_chatgpt_accounts("sub2api")

        self.assertEqual(emails, {"healthy@example.com"})
        delete_calls = [call for call in calls if call[0] == "DELETE"]
        self.assertEqual(len(delete_calls), 1)

    def test_list_remote_chatgpt_accounts_for_sub2api_uses_name_field_only(self):
        def fake_request_json(method, url, *, headers, params=None, json_body=None):
            self.assertEqual(headers, {"X-API-Key": "admin-key"})
            if method == "GET" and url.endswith("/api/v1/admin/accounts"):
                return 200, {
                    "data": {
                        "items": [
                            {"id": 31, "email": "ignored@example.com", "name": "real@example.com", "status": "active"},
                            {"id": 32, "name": "CPA", "status": "active"},
                        ]
                    }
                }
            raise AssertionError(f"unexpected call: {method} {url}")

        with patch.object(external_sync, "_request_json", side_effect=fake_request_json):
            with patch("core.config_store.config_store.get", side_effect=lambda key, default="": {
                "sub2api_url": "https://sub2api.example.com",
                "sub2api_admin_key": "admin-key",
            }.get(key, default)):
                emails = external_sync.list_remote_chatgpt_accounts("sub2api")

        self.assertEqual(emails, {"real@example.com"})

    def test_list_remote_chatgpt_accounts_keeps_error_email_when_sub2api_delete_fails(self):
        def fake_request_json(method, url, *, headers, params=None, json_body=None):
            self.assertEqual(headers, {"X-API-Key": "admin-key"})
            if method == "GET" and url.endswith("/api/v1/admin/accounts"):
                return 200, {"data": {"items": [{"id": 21, "name": "error@example.com", "status": "error"}]}}
            if method == "DELETE" and url.endswith("/api/v1/admin/accounts/21"):
                return 500, {"message": "boom"}
            raise AssertionError(f"unexpected call: {method} {url}")

        with patch.object(external_sync, "_request_json", side_effect=fake_request_json):
            with patch("core.config_store.config_store.get", side_effect=lambda key, default="": {
                "sub2api_url": "https://sub2api.example.com",
                "sub2api_admin_key": "admin-key",
            }.get(key, default)):
                with self.assertRaisesRegex(RuntimeError, "sub2api 删除失败: boom"):
                    external_sync.list_remote_chatgpt_accounts("sub2api")

    @patch("platforms.chatgpt.cpa_upload.upload_to_cpa", return_value=(True, "ok"))
    @patch("platforms.chatgpt.cpa_upload.generate_token_json", return_value={"email": "demo@example.com"})
    def test_sync_account_uses_selected_cpa_provider(self, _generate_token_json, mock_upload_to_cpa):
        account = DummyAccount()
        with patch.object(external_sync, "_resolve_chatgpt_sync_provider", return_value="cliproxyapi"):
            results = external_sync.sync_account(account)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["name"], "CPA")
        mock_upload_to_cpa.assert_called_once()

    @patch("platforms.chatgpt.cpa_upload.upload_to_team_manager", return_value=(True, "ok"))
    def test_sync_account_uses_selected_team_manager_provider(self, mock_upload_to_team_manager):
        account = DummyAccount()
        with patch.object(external_sync, "_resolve_chatgpt_sync_provider", return_value="team_manager"):
            results = external_sync.sync_account(account)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["name"], "Team Manager")
        mock_upload_to_team_manager.assert_called_once()

    @patch("platforms.chatgpt.cpa_upload.upload_to_cpa", return_value=(True, "ok"))
    @patch("platforms.chatgpt.cpa_upload.upload_to_sub2api", return_value=(True, "ok"))
    def test_sync_account_uses_selected_sub2api_provider_instead_of_cpa(self, mock_upload_to_sub2api, mock_upload_to_cpa):
        account = DummyAccount()
        with patch.object(external_sync, "_resolve_chatgpt_sync_provider", return_value="sub2api"):
            results = external_sync.sync_account(account)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["name"], "sub2api")
        mock_upload_to_sub2api.assert_called_once()
        mock_upload_to_cpa.assert_not_called()

    @patch.object(external_sync, "sync_account", return_value=[{"name": "sub2api", "ok": True, "msg": "ok"}])
    @patch.object(
        external_sync,
        "list_remote_chatgpt_accounts_detail",
        return_value={"provider": "sub2api", "emails": {"exists@example.com"}, "deleted_error_accounts": 2},
    )
    def test_ensure_chatgpt_accounts_synced_only_syncs_missing_local_accounts(self, _list_remote_detail, mock_sync_account):
        accounts = [
            DummyAccount("exists@example.com"),
            DummyAccount("missing@example.com"),
        ]

        summary = external_sync.ensure_chatgpt_accounts_synced(accounts, provider="sub2api")

        self.assertEqual(summary["checked"], 2)
        self.assertEqual(summary["missing"], 1)
        self.assertEqual(summary["synced"], 1)
        self.assertEqual(summary["deleted_error_accounts"], 2)
        mock_sync_account.assert_called_once()
        synced_account = mock_sync_account.call_args.kwargs.get("account") or mock_sync_account.call_args.args[0]
        self.assertEqual(synced_account.email, "missing@example.com")


if __name__ == "__main__":
    unittest.main()
