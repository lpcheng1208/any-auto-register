"""定时任务调度 - 账号有效性检测、trial 到期提醒、定时注册"""
from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta, timezone

from api.tasks import (
    AccountCheckTaskRequest,
    TrialExpiryTaskRequest,
    dispatch_due_scheduled_register_tasks,
    start_account_check_task,
    start_trial_expiry_task,
)
from services.task_service import reset_running_schedules


class Scheduler:
    def __init__(self):
        self._running = False
        self._thread: threading.Thread | None = None
        self._next_trial_check_at: datetime | None = None

    def start(self):
        if self._running:
            return
        reset_running_schedules()
        self._next_trial_check_at = datetime.now(timezone.utc)
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        print("[Scheduler] 已启动")

    def stop(self):
        self._running = False

    def _loop(self):
        while self._running:
            try:
                self.dispatch_scheduled_registers()
                self.maybe_check_trial_expiry()
            except Exception as exc:
                print(f"[Scheduler] 错误: {exc}")
            time.sleep(30)

    def dispatch_scheduled_registers(self):
        created_task_ids = dispatch_due_scheduled_register_tasks()
        if created_task_ids:
            print(f"[Scheduler] 已触发 {len(created_task_ids)} 个定时注册任务")
        return created_task_ids

    def maybe_check_trial_expiry(self):
        now = datetime.now(timezone.utc)
        if self._next_trial_check_at and now < self._next_trial_check_at:
            return None
        self._next_trial_check_at = now + timedelta(hours=1)
        return self.check_trial_expiry()

    def check_trial_expiry(self):
        """检查 trial 到期账号，统一记录为 scheduler 任务。"""
        return start_trial_expiry_task(TrialExpiryTaskRequest(limit=200))

    def check_accounts_valid(self, platform: str = None, limit: int = 50):
        """批量检测账号有效性，并记录为系统任务。"""
        return start_account_check_task(
            AccountCheckTaskRequest(platform=platform, limit=limit),
            background_tasks=None,
            trigger_source="scheduler",
        )


scheduler = Scheduler()
