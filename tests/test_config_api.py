from __future__ import annotations

import unittest

from fastapi.testclient import TestClient

import api.config as config_api
from core.config_store import config_store
from main import app


class ConfigApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app)
        self._config_keys = [
            "chatgpt_sync_target_provider",
            "sub2api_url",
            "sub2api_admin_key",
            "drift_mail_base_url",
            "drift_mail_access_key",
            "drift_mail_domain",
        ]
        self._original_config = {key: config_store.get(key, "") for key in self._config_keys}
        self.addCleanup(self._restore_config)

    def _restore_config(self) -> None:
        config_store.set_many(self._original_config)

    def test_update_config_accepts_sync_target_provider_and_sub2api_admin_key(self):
        response = self.client.put(
            "/api/config",
            json={
                "data": {
                    "chatgpt_sync_target_provider": "sub2api",
                    "sub2api_url": "http://127.0.0.1:8081",
                    "sub2api_admin_key": "admin-demo-key",
                    "drift_mail_base_url": "https://drift-mail.example.com",
                    "drift_mail_access_key": "drift-access-key",
                    "drift_mail_domain": "example.com",
                }
            },
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["ok"])
        self.assertIn("chatgpt_sync_target_provider", body["updated"])
        self.assertIn("sub2api_url", body["updated"])
        self.assertIn("sub2api_admin_key", body["updated"])
        self.assertIn("drift_mail_base_url", body["updated"])
        self.assertIn("drift_mail_access_key", body["updated"])
        self.assertIn("drift_mail_domain", body["updated"])

    def test_get_config_returns_sync_target_fields(self):
        response = self.client.get("/api/config")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertIn("chatgpt_sync_target_provider", body)
        self.assertIn("sub2api_url", body)
        self.assertIn("sub2api_admin_key", body)
        self.assertIn("drift_mail_base_url", body)
        self.assertIn("drift_mail_access_key", body)
        self.assertIn("drift_mail_domain", body)


if __name__ == "__main__":
    unittest.main()
