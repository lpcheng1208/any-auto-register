from __future__ import annotations

import unittest
from unittest.mock import patch

from platforms.chatgpt import cpa_upload


class DummyAccount:
    email = "demo@example.com"
    access_token = "access-token"
    refresh_token = "refresh-token"
    session_token = "session-token"
    id_token = "id-token"
    client_id = "client-id"
    cookies = "cookie=1"


class ChatGPTCpaUploadTests(unittest.TestCase):
    def test_upload_to_sub2api_create_binds_all_active_groups(self):
        calls = []

        def fake_request_json(method, url, *, headers, params=None, json_body=None):
            calls.append((method, url, params, json_body))
            self.assertEqual(headers, {"X-API-Key": "admin-key"})
            if method == "GET" and url.endswith("/api/v1/admin/accounts"):
                return 200, {"items": []}
            if method == "GET" and url.endswith("/api/v1/admin/groups/all"):
                return 200, {"data": [{"id": 1, "status": "active"}, {"id": "2", "status": "active"}]}
            if method == "POST" and url.endswith("/api/v1/admin/accounts"):
                return 201, {"message": "ok"}
            raise AssertionError(f"unexpected call: {method} {url}")

        with patch.object(cpa_upload, "_request_json", side_effect=fake_request_json):
            ok, _msg = cpa_upload.upload_to_sub2api(DummyAccount(), base_url="https://sub2api.example.com", admin_key="admin-key")

        self.assertTrue(ok)
        create_call = next(call for call in calls if call[0] == "POST")
        payload = create_call[3]
        self.assertEqual(payload["group_ids"], [1, 2])
        self.assertEqual(payload["platform"], "openai")
        self.assertEqual(payload["type"], "oauth")
        self.assertEqual(payload["proxy_id"], None)
        self.assertEqual(payload["concurrency"], 2)
        self.assertEqual(payload["load_factor"], 1)
        self.assertEqual(payload["priority"], 1)
        self.assertEqual(payload["rate_multiplier"], 1.0)
        self.assertEqual(payload["credentials"]["model_mapping"], cpa_upload.SUB2API_DEFAULT_MODEL_MAPPING)

    def test_upload_to_sub2api_update_binds_all_active_groups(self):
        calls = []

        def fake_request_json(method, url, *, headers, params=None, json_body=None):
            calls.append((method, url, params, json_body))
            self.assertEqual(headers, {"X-API-Key": "admin-key"})
            if method == "GET" and url.endswith("/api/v1/admin/accounts"):
                return 200, {"items": [{"id": 7, "email": "demo@example.com"}]}
            if method == "GET" and url.endswith("/api/v1/admin/groups/all"):
                return 200, {"data": [{"id": 11, "status": "active"}]}
            if method == "PUT" and url.endswith("/api/v1/admin/accounts/7"):
                return 200, {"message": "ok"}
            raise AssertionError(f"unexpected call: {method} {url}")

        with patch.object(cpa_upload, "_request_json", side_effect=fake_request_json):
            ok, _msg = cpa_upload.upload_to_sub2api(DummyAccount(), base_url="https://sub2api.example.com", admin_key="admin-key")

        self.assertTrue(ok)
        update_call = next(call for call in calls if call[0] == "PUT")
        payload = update_call[3]
        self.assertEqual(payload["group_ids"], [11])
        self.assertEqual(payload["proxy_id"], 0)
        self.assertEqual(payload["concurrency"], 2)
        self.assertEqual(payload["load_factor"], 1)
        self.assertEqual(payload["priority"], 1)
        self.assertEqual(payload["rate_multiplier"], 1.0)
        self.assertEqual(payload["credentials"]["model_mapping"], cpa_upload.SUB2API_DEFAULT_MODEL_MAPPING)


if __name__ == "__main__":
    unittest.main()
