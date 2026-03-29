from __future__ import annotations

import unittest
from unittest.mock import patch

from fastapi import HTTPException
from sqlmodel import Session, select

from api import tasks
from api.tasks import AccountCheckTaskRequest, RegisterTaskRequest, ScheduledRegisterRunRequest
from core.base_platform import Account
from core.db import AccountModel, engine
from services import task_service


class DummySchedule:
    def __init__(self, payload: dict):
        self.id = 1
        self._payload = payload

    def get_payload(self) -> dict:
        return dict(self._payload)


class ScheduledRegisterTests(unittest.TestCase):
    def test_run_register_partial_success_retries_remote_sync_for_success_accounts(self):
        req = RegisterTaskRequest(
            platform="chatgpt",
            count=2,
            count_mode="fixed",
            concurrency=1,
            executor_type="protocol",
            captcha_solver="manual",
            extra={},
        )

        class DummyPlatform:
            register_call_count = 0

            def __init__(self, config=None, mailbox=None):
                self.config = config
                self.mailbox = mailbox
                self._log_fn = None

            def register(self, email=None, password=None):
                DummyPlatform.register_call_count += 1
                if DummyPlatform.register_call_count == 1:
                    return Account(
                        platform="chatgpt",
                        email="ok@example.com",
                        password="pw",
                        extra={},
                    )
                raise RuntimeError("boom")

        with patch.object(tasks, "_prepare_register_request", return_value=req), patch.object(
            tasks, "_resolve_register_count", return_value=(2, None)
        ), patch.object(tasks, "update_task_status"), patch.object(
            tasks, "append_task_item"
        ), patch.object(
            tasks, "_append_log"
        ), patch.object(
            tasks, "_auto_upload_integrations"
        ) as mock_sync, patch(
            "core.registry.get", return_value=DummyPlatform
        ), patch(
            "core.base_mailbox.create_mailbox", return_value=None
        ), patch(
            "core.db.save_account"
        ):
            tasks._run_register("task_test", req.model_dump())

        self.assertEqual(mock_sync.call_count, 2)

    def test_start_register_task_dynamic_zero_gap_skips(self):
        req = RegisterTaskRequest(
            platform="trae",
            count=5,
            count_mode="dynamic",
            executor_type="protocol",
            captcha_solver="manual",
            extra={},
        )

        with patch.object(tasks, "_prepare_register_request", return_value=req), patch.object(
            tasks, "_resolve_register_count", return_value=(0, 5)
        ), patch.object(tasks, "generate_task_id", return_value="task_skip"), patch.object(
            tasks, "mark_schedule_skipped", return_value=object()
        ) as mark_skip, patch.object(tasks, "create_task_run") as create_task_run:
            result = tasks.start_register_task(req, background_tasks=None, trigger_source="scheduler", schedule_id=1)

        self.assertTrue(result["skipped"])
        self.assertEqual(result["effective_count"], 0)
        self.assertEqual(result["current_valid_count"], 5)
        mark_skip.assert_called_once()
        create_task_run.assert_not_called()

    def test_dispatch_due_scheduled_register_tasks_skips_when_start_returns_no_task_id(self):
        schedule = DummySchedule(
            {
                "platform": "trae",
                "count": 5,
                "count_mode": "dynamic",
                "executor_type": "protocol",
                "captcha_solver": "manual",
                "extra": {},
            }
        )

        with patch.object(tasks, "list_due_scheduled_register_tasks", return_value=[schedule]), patch.object(
            tasks, "should_dispatch_schedule", return_value=True
        ), patch.object(
            tasks,
            "start_register_task",
            return_value={"task_id": "", "skipped": True, "reason": "target_already_satisfied"},
        ):
            result = tasks.dispatch_due_scheduled_register_tasks()

        self.assertEqual(result, [])

    def test_run_schedule_once_returns_skip_result(self):
        schedule = DummySchedule(
            {
                "platform": "trae",
                "count": 5,
                "count_mode": "dynamic",
                "executor_type": "protocol",
                "captcha_solver": "manual",
                "extra": {},
            }
        )

        with patch.object(tasks, "get_scheduled_register_task", return_value=schedule), patch.object(
            tasks, "should_dispatch_schedule", return_value=True
        ), patch.object(
            tasks,
            "start_register_task",
            return_value={"task_id": "", "skipped": True, "reason": "target_already_satisfied"},
        ):
            result = tasks.run_schedule_once(1, ScheduledRegisterRunRequest(inherit_payload=True))

        self.assertTrue(result["skipped"])
        self.assertEqual(result["reason"], "target_already_satisfied")

    def test_prepare_register_request_dynamic_uses_default_target_count(self):
        req = RegisterTaskRequest(
            platform="chatgpt",
            count=5,
            count_mode="dynamic",
            concurrency=1,
            executor_type="protocol",
            captcha_solver="manual",
            extra={},
        )

        with patch.object(tasks.config_store, "get", side_effect=lambda key, default="": "50" if key == "default_target_count" else default):
            prepared = tasks._prepare_register_request(req)

        self.assertEqual(prepared.count_mode, "dynamic")
        self.assertEqual(prepared.count, 50)

    def test_retry_register_task_reuses_payload_and_current_config(self):
        class DummyTask:
            status = tasks.TASK_STATUS_FAILED
            task_type = "register_batch"

        payload = {
            "platform": "chatgpt",
            "count": 2,
            "count_mode": "fixed",
            "concurrency": 1,
            "executor_type": "protocol",
            "captcha_solver": "",
            "proxy": "",
            "extra": {
                "mail_provider": "driftmail",
                "drift_mail_base_url": "",
                "drift_mail_access_key": "",
            },
        }

        with patch.object(tasks, "get_task", return_value=DummyTask()), patch.object(
            tasks,
            "get_task_payload",
            return_value=payload,
        ), patch.object(
            tasks,
            "start_register_task",
            return_value={"task_id": "task_retry_1"},
        ) as mock_start, patch.object(
            tasks.config_store,
            "get",
            side_effect=lambda key, default="": {
                "default_captcha_solver": "yescaptcha",
                "drift_mail_base_url": "https://drift.example.com",
                "drift_mail_access_key": "drift-secret",
            }.get(key, default),
        ):
            result = tasks.retry_task("task_1", tasks.RetryTaskRequest(inherit_payload=True), background_tasks=None)

        self.assertEqual(result["task_id"], "task_retry_1")
        retry_req = mock_start.call_args.args[0]
        self.assertEqual(retry_req.platform, "chatgpt")
        self.assertEqual(retry_req.captcha_solver, "yescaptcha")
        self.assertEqual(retry_req.extra["mail_provider"], "driftmail")
        self.assertEqual(retry_req.extra["drift_mail_base_url"], "https://drift.example.com")
        self.assertEqual(retry_req.extra["drift_mail_access_key"], "drift-secret")
        self.assertEqual(mock_start.call_args.kwargs["trigger_source"], "retry")
        self.assertEqual(mock_start.call_args.kwargs["parent_task_id"], "task_1")

    def test_retry_register_task_requires_inherit_payload(self):
        class DummyTask:
            status = tasks.TASK_STATUS_FAILED
            task_type = "register_batch"

        with patch.object(tasks, "get_task", return_value=DummyTask()):
            with self.assertRaises(HTTPException) as exc_info:
                tasks.retry_task("task_1", tasks.RetryTaskRequest(inherit_payload=False), background_tasks=None)

        self.assertEqual(exc_info.exception.status_code, 400)
        self.assertIn("必须继承原任务参数", exc_info.exception.detail)

    def test_sanitize_task_payload_for_storage_clears_sub2api_admin_key(self):
        payload = {
            "password": "secret-pass",
            "proxy": "http://proxy.local",
            "extra": {
                "sub2api_admin_key": "admin-demo-key",
                "drift_mail_access_key": "drift-access-key",
                "other": "kept",
            },
        }

        sanitized = task_service.sanitize_task_payload_for_storage("register_batch", payload)

        self.assertEqual(sanitized["password"], "")
        self.assertEqual(sanitized["proxy"], "")
        self.assertEqual(sanitized["extra"]["sub2api_admin_key"], "")
        self.assertEqual(sanitized["extra"]["drift_mail_access_key"], "")
        self.assertEqual(sanitized["extra"]["other"], "kept")

    def test_build_scheduled_register_response_masks_sub2api_admin_key(self):
        class DummyScheduledTask:
            id = 1
            name = "sync job"
            enabled = True
            platform = "chatgpt"
            interval_minutes = 60
            next_run_at = None
            last_run_at = None
            last_task_id = ""
            last_status = ""
            last_error = ""
            created_at = None
            updated_at = None

            def get_payload(self) -> dict:
                return {
                    "password": "secret-pass",
                    "proxy": "http://proxy.local",
                    "extra": {
                        "sub2api_admin_key": "admin-demo-key",
                        "drift_mail_access_key": "drift-access-key",
                        "other": "kept",
                    },
                }

        response = task_service.build_scheduled_register_response(DummyScheduledTask())

        self.assertEqual(response["register"]["password"], "")
        self.assertEqual(response["register"]["proxy"], "")
        self.assertEqual(response["register"]["extra"]["sub2api_admin_key"], "")
        self.assertEqual(response["register"]["extra"]["drift_mail_access_key"], "")
        self.assertEqual(response["register"]["extra"]["other"], "kept")

    def test_run_account_check_deletes_local_chatgpt_account_when_target_confirms_missing(self):
        with Session(engine) as session:
            account = AccountModel(
                platform="chatgpt",
                email="delete-me@example.com",
                password="pw",
                status="registered",
                extra_json="{}",
            )
            session.add(account)
            session.commit()
            session.refresh(account)
            account_id = account.id

        req = AccountCheckTaskRequest(
            account_ids=[account_id],
            sync_with_target=True,
            delete_invalid=True,
        )

        with patch.object(tasks, "update_task_status"), patch.object(tasks, "append_task_item"), patch.object(
            tasks, "_append_log"
        ), patch("services.chatgpt_target_sync.check_account_validity", return_value=False), patch(
            "services.chatgpt_target_sync.remove_chatgpt_account_from_target",
            return_value={
                "provider": "sub2api",
                "exists": False,
                "confirmed": True,
                "message": "目标侧账号已删除",
                "snapshot": {
                    "provider": "sub2api",
                    "remote_status": "deleted",
                    "exists": False,
                },
            },
        ):
            tasks._run_account_check("task_test", req.model_dump())

        with Session(engine) as session:
            deleted = session.exec(select(AccountModel).where(AccountModel.id == account_id)).first()
        self.assertIsNone(deleted)

    def test_run_account_check_keeps_local_chatgpt_account_when_target_unconfirmed(self):
        with Session(engine) as session:
            account = AccountModel(
                platform="chatgpt",
                email="keep-me@example.com",
                password="pw",
                status="registered",
                extra_json="{}",
            )
            session.add(account)
            session.commit()
            session.refresh(account)
            account_id = account.id

        req = AccountCheckTaskRequest(
            account_ids=[account_id],
            sync_with_target=True,
            delete_invalid=True,
        )

        with patch.object(tasks, "update_task_status"), patch.object(tasks, "append_task_item"), patch.object(
            tasks, "_append_log"
        ), patch("services.chatgpt_target_sync.check_account_validity", return_value=False), patch(
            "services.chatgpt_target_sync.remove_chatgpt_account_from_target",
            return_value={
                "provider": "sub2api",
                "exists": False,
                "confirmed": False,
                "message": "查询失败",
                "snapshot": {},
            },
        ):
            tasks._run_account_check("task_test", req.model_dump())

        with Session(engine) as session:
            kept = session.exec(select(AccountModel).where(AccountModel.id == account_id)).first()
            self.assertIsNotNone(kept)
            self.assertEqual(kept.status, "invalid")
            session.delete(kept)
            session.commit()


if __name__ == "__main__":
    unittest.main()
