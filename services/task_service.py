from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sqlmodel import Session, select

from core.db import ScheduledRegisterTask, TaskEvent, TaskLog, TaskRun, engine


TASK_STATUS_PENDING = "pending"
TASK_STATUS_RUNNING = "running"
TASK_STATUS_SUCCESS = "success"
TASK_STATUS_PARTIAL_SUCCESS = "partial_success"
TASK_STATUS_FAILED = "failed"
TASK_STATUS_CANCEL_REQUESTED = "cancel_requested"
TASK_STATUS_CANCELLED = "cancelled"
TASK_STATUS_INTERRUPTED = "interrupted"

TERMINAL_STATUSES = {
    TASK_STATUS_SUCCESS,
    TASK_STATUS_PARTIAL_SUCCESS,
    TASK_STATUS_FAILED,
    TASK_STATUS_CANCELLED,
    TASK_STATUS_INTERRUPTED,
}

SCHEDULE_STATUS_IDLE = "idle"
SCHEDULE_STATUS_RUNNING = "running"
SCHEDULE_STATUS_SUCCESS = "success"
SCHEDULE_STATUS_FAILED = "failed"
SCHEDULE_STATUS_CANCELLED = "cancelled"
SCHEDULE_STATUS_INTERRUPTED = "interrupted"

RETRYABLE_TASK_STATUSES = {
    TASK_STATUS_FAILED,
    TASK_STATUS_PARTIAL_SUCCESS,
    TASK_STATUS_CANCELLED,
    TASK_STATUS_INTERRUPTED,
}


@dataclass
class RuntimeTaskState:
    task_id: str
    cancel_requested: bool = False
    logs: list[str] = field(default_factory=list)
    cashier_urls: list[str] = field(default_factory=list)


_runtime_tasks: dict[str, RuntimeTaskState] = {}
_runtime_lock = threading.Lock()
_schedule_dispatch_lock = threading.Lock()


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def generate_task_id(prefix: str = "task") -> str:
    return f"{prefix}_{time.time_ns()}"


def ensure_runtime_task(task_id: str) -> RuntimeTaskState:
    with _runtime_lock:
        state = _runtime_tasks.get(task_id)
        if state is None:
            state = RuntimeTaskState(task_id=task_id)
            _runtime_tasks[task_id] = state
        return state


def get_runtime_task(task_id: str) -> Optional[RuntimeTaskState]:
    with _runtime_lock:
        return _runtime_tasks.get(task_id)


def remove_runtime_task(task_id: str) -> None:
    with _runtime_lock:
        _runtime_tasks.pop(task_id, None)


def create_task_run(
    *,
    task_id: str,
    task_type: str,
    trigger_source: str = "manual",
    target_platform: str | None = None,
    payload: dict[str, Any] | None = None,
    total_count: int = 0,
    parent_task_id: str | None = None,
    scheduler_key: str = "",
) -> TaskRun:
    ensure_runtime_task(task_id)
    with Session(engine) as session:
        task = TaskRun(
            id=task_id,
            task_type=task_type,
            trigger_source=trigger_source,
            status=TASK_STATUS_PENDING,
            target_platform=target_platform,
            payload_json=json.dumps(payload or {}, ensure_ascii=False),
            summary_json=json.dumps({}, ensure_ascii=False),
            total_count=total_count,
            parent_task_id=parent_task_id,
            scheduler_key=scheduler_key,
        )
        session.add(task)
        session.commit()
        session.refresh(task)
        return task


def get_task(task_id: str) -> TaskRun | None:
    with Session(engine) as session:
        return session.get(TaskRun, task_id)


def list_tasks(
    *,
    task_type: str | None = None,
    status: str | None = None,
    trigger_source: str | None = None,
    target_platform: str | None = None,
    page: int = 1,
    page_size: int = 50,
) -> tuple[int, list[TaskRun]]:
    with Session(engine) as session:
        query = select(TaskRun)
        if task_type:
            query = query.where(TaskRun.task_type == task_type)
        if status:
            query = query.where(TaskRun.status == status)
        if trigger_source:
            query = query.where(TaskRun.trigger_source == trigger_source)
        if target_platform:
            query = query.where(TaskRun.target_platform == target_platform)
        items = list(
            session.exec(
                query.order_by(TaskRun.created_at.desc())
                .offset(max(page - 1, 0) * page_size)
                .limit(page_size)
            ).all()
        )
        total = len(session.exec(query).all())
        return total, items


def update_task_status(
    task_id: str,
    *,
    status: str,
    error: str | None = None,
    processed_count: int | None = None,
    success_count: int | None = None,
    failed_count: int | None = None,
    summary: dict[str, Any] | None = None,
) -> TaskRun:
    with Session(engine) as session:
        task = session.get(TaskRun, task_id)
        if task is None:
            raise ValueError(f"任务不存在: {task_id}")
        task.status = status
        if error is not None:
            task.error = error
        if processed_count is not None:
            task.processed_count = processed_count
        if success_count is not None:
            task.success_count = success_count
        if failed_count is not None:
            task.failed_count = failed_count
        if summary is not None:
            task.summary_json = json.dumps(summary, ensure_ascii=False)
        if status == TASK_STATUS_RUNNING and task.started_at is None:
            task.started_at = utcnow()
        if status in TERMINAL_STATUSES:
            task.finished_at = utcnow()
        task.updated_at = utcnow()
        session.add(task)
        session.commit()
        session.refresh(task)
        if status in TERMINAL_STATUSES:
            remove_runtime_task(task_id)
        return task


def append_task_event(task_id: str, message: str, level: str = "info") -> TaskEvent:
    timestamp = time.strftime("%H:%M:%S")
    line = f"[{timestamp}] {message}"
    runtime = ensure_runtime_task(task_id)
    with _runtime_lock:
        runtime.logs.append(line)
    with Session(engine) as session:
        event = TaskEvent(task_id=task_id, level=level, message=line)
        session.add(event)
        session.commit()
        session.refresh(event)
        return event


def list_task_events(task_id: str, since_id: int = 0, limit: int = 200) -> list[TaskEvent]:
    with Session(engine) as session:
        query = (
            select(TaskEvent)
            .where(TaskEvent.task_id == task_id)
            .where(TaskEvent.id > since_id)
            .order_by(TaskEvent.id.asc())
            .limit(limit)
        )
        return list(session.exec(query).all())


def append_task_item(
    *,
    task_id: str,
    item_type: str,
    item_key: str,
    platform: str,
    email: str,
    status: str,
    error: str = "",
    detail: dict[str, Any] | None = None,
) -> TaskLog:
    with Session(engine) as session:
        item = TaskLog(
            task_id=task_id,
            item_type=item_type,
            item_key=item_key,
            platform=platform,
            email=email,
            status=status,
            error=error,
            detail_json=json.dumps(detail or {}, ensure_ascii=False),
        )
        session.add(item)
        session.commit()
        session.refresh(item)
        return item


def list_task_items(task_id: str, page: int = 1, page_size: int = 100) -> tuple[int, list[TaskLog]]:
    with Session(engine) as session:
        base_query = select(TaskLog).where(TaskLog.task_id == task_id)
        items = list(
            session.exec(
                base_query.order_by(TaskLog.id.desc())
                .offset(max(page - 1, 0) * page_size)
                .limit(page_size)
            ).all()
        )
        total = len(session.exec(base_query).all())
        return total, items


def list_legacy_task_logs(platform: str | None = None, page: int = 1, page_size: int = 50) -> tuple[int, list[TaskLog]]:
    with Session(engine) as session:
        query = select(TaskLog)
        if platform:
            query = query.where(TaskLog.platform == platform)
        items = list(
            session.exec(
                query.order_by(TaskLog.id.desc())
                .offset(max(page - 1, 0) * page_size)
                .limit(page_size)
            ).all()
        )
        total = len(session.exec(query).all())
        return total, items


def delete_task_logs(ids: list[int]) -> tuple[int, list[int]]:
    unique_ids = list(dict.fromkeys(ids))
    with Session(engine) as session:
        logs = session.exec(select(TaskLog).where(TaskLog.id.in_(unique_ids))).all()
        found_ids = {log.id for log in logs if log.id is not None}
        for log in logs:
            session.delete(log)
        session.commit()
        not_found = [log_id for log_id in unique_ids if log_id not in found_ids]
        return len(found_ids), not_found


def request_task_cancel(task_id: str) -> TaskRun:
    runtime = ensure_runtime_task(task_id)
    with _runtime_lock:
        runtime.cancel_requested = True
    append_task_event(task_id, "收到取消请求", level="warning")
    return update_task_status(task_id, status=TASK_STATUS_CANCEL_REQUESTED)


def is_cancel_requested(task_id: str) -> bool:
    task = get_task(task_id)
    if task is not None and task.status == TASK_STATUS_CANCEL_REQUESTED:
        return True
    runtime = get_runtime_task(task_id)
    return bool(runtime and runtime.cancel_requested)


def add_task_cashier_url(task_id: str, url: str) -> None:
    runtime = ensure_runtime_task(task_id)
    with _runtime_lock:
        runtime.cashier_urls.append(url)


def get_task_payload(task_id: str) -> dict[str, Any]:
    task = get_task(task_id)
    if task is None:
        raise ValueError(f"任务不存在: {task_id}")
    return task.get_payload()


def build_task_response(task: TaskRun) -> dict[str, Any]:
    runtime = get_runtime_task(task.id)
    summary = task.get_summary()
    progress = f"{task.processed_count}/{task.total_count}" if task.total_count else "0/0"
    return {
        "id": task.id,
        "task_id": task.id,
        "task_type": task.task_type,
        "trigger_source": task.trigger_source,
        "status": task.status,
        "platform": task.target_platform,
        "progress": progress,
        "processed_count": task.processed_count,
        "total_count": task.total_count,
        "success": task.success_count,
        "failed": task.failed_count,
        "errors": summary.get("errors", []),
        "summary": summary,
        "error": task.error,
        "cashier_urls": list(runtime.cashier_urls) if runtime else summary.get("cashier_urls", []),
        "created_at": task.created_at,
        "started_at": task.started_at,
        "finished_at": task.finished_at,
        "updated_at": task.updated_at,
        "parent_task_id": task.parent_task_id,
    }


def mark_unfinished_tasks_interrupted() -> int:
    with Session(engine) as session:
        tasks = session.exec(
            select(TaskRun).where(
                TaskRun.status.in_(
                    [
                        TASK_STATUS_RUNNING,
                        TASK_STATUS_CANCEL_REQUESTED,
                    ]
                )
            )
        ).all()
        if not tasks:
            return 0
        now = utcnow()
        count = 0
        for task in tasks:
            task.status = TASK_STATUS_INTERRUPTED
            task.finished_at = now
            task.updated_at = now
            session.add(task)
            session.flush()
            session.add(
                TaskEvent(
                    task_id=task.id,
                    level="warning",
                    message="[系统] 服务重启，任务已标记为 interrupted",
                )
            )
            count += 1
        session.commit()
        return count


def delete_task(task_id: str) -> bool:
    with Session(engine) as session:
        task = session.get(TaskRun, task_id)
        if task is None:
            return False
        if task.status not in TERMINAL_STATUSES:
            raise ValueError("活跃任务不能直接删除")
        items = session.exec(select(TaskLog).where(TaskLog.task_id == task_id)).all()
        events = session.exec(select(TaskEvent).where(TaskEvent.task_id == task_id)).all()
        for item in items:
            session.delete(item)
        for event in events:
            session.delete(event)
        session.delete(task)
        session.commit()
        remove_runtime_task(task_id)
        return True


def sanitize_scheduled_register_payload(payload: dict[str, Any]) -> dict[str, Any]:
    register = dict(payload)
    register["password"] = ""
    register["proxy"] = ""
    extra = dict(register.get("extra") or {})
    for key in [
        "laoudo_auth",
        "yescaptcha_key",
        "duckmail_bearer",
        "freemail_admin_token",
        "freemail_password",
        "mail215_api_key",
        "cfworker_admin_token",
        "team_manager_key",
        "cpa_api_key",
        "grok2api_app_key",
    ]:
        if key in extra:
            extra[key] = ""
    register["extra"] = extra
    return register


def build_schedule_next_run(interval_minutes: int, *, from_time: datetime | None = None) -> datetime:
    base_time = from_time or utcnow()
    return base_time + timedelta(minutes=interval_minutes)


def create_scheduled_register_task(
    *,
    name: str,
    enabled: bool,
    platform: str,
    interval_minutes: int,
    payload: dict[str, Any],
) -> ScheduledRegisterTask:
    now = utcnow()
    with Session(engine) as session:
        item = ScheduledRegisterTask(
            name=name,
            enabled=enabled,
            platform=platform,
            interval_minutes=interval_minutes,
            payload_json=json.dumps(payload, ensure_ascii=False),
            next_run_at=build_schedule_next_run(interval_minutes, from_time=now) if enabled else None,
            updated_at=now,
        )
        session.add(item)
        session.commit()
        session.refresh(item)
        return item


def _merge_schedule_payload(existing_payload: dict[str, Any], new_payload: dict[str, Any]) -> dict[str, Any]:
    merged = dict(existing_payload)
    merged.update(new_payload)

    secret_keys = {
        "laoudo_auth",
        "yescaptcha_key",
        "duckmail_bearer",
        "freemail_admin_token",
        "freemail_password",
        "mail215_api_key",
        "cfworker_admin_token",
        "team_manager_key",
        "cpa_api_key",
        "grok2api_app_key",
    }

    if not new_payload.get("password") and existing_payload.get("password"):
        merged["password"] = existing_payload["password"]
    if not new_payload.get("proxy") and existing_payload.get("proxy"):
        merged["proxy"] = existing_payload["proxy"]

    existing_extra = dict(existing_payload.get("extra") or {})
    new_extra = dict(new_payload.get("extra") or {})
    merged_extra = dict(existing_extra)
    merged_extra.update(new_extra)
    for key in secret_keys:
        if not new_extra.get(key) and existing_extra.get(key):
            merged_extra[key] = existing_extra[key]
    merged["extra"] = merged_extra
    return merged


def update_scheduled_register_task(
    schedule_id: int,
    *,
    name: str,
    enabled: bool,
    platform: str,
    interval_minutes: int,
    payload: dict[str, Any],
) -> ScheduledRegisterTask | None:
    now = utcnow()
    with Session(engine) as session:
        item = session.get(ScheduledRegisterTask, schedule_id)
        if item is None:
            return None
        item.name = name
        item.enabled = enabled
        item.platform = platform
        item.interval_minutes = interval_minutes
        item.payload_json = json.dumps(_merge_schedule_payload(item.get_payload(), payload), ensure_ascii=False)
        item.updated_at = now
        if enabled:
            item.next_run_at = build_schedule_next_run(interval_minutes, from_time=now)
        else:
            item.next_run_at = None
        session.add(item)
        session.commit()
        session.refresh(item)
        return item


def get_scheduled_register_task(schedule_id: int) -> ScheduledRegisterTask | None:
    with Session(engine) as session:
        return session.get(ScheduledRegisterTask, schedule_id)


def list_scheduled_register_tasks() -> list[ScheduledRegisterTask]:
    with Session(engine) as session:
        return list(session.exec(select(ScheduledRegisterTask).order_by(ScheduledRegisterTask.created_at.desc())).all())


def delete_scheduled_register_task(schedule_id: int) -> bool:
    with Session(engine) as session:
        item = session.get(ScheduledRegisterTask, schedule_id)
        if item is None:
            return False
        if item.last_task_id:
            task = session.get(TaskRun, item.last_task_id)
            if task is not None and task.status not in TERMINAL_STATUSES:
                raise ValueError("存在运行中的关联任务，不能删除定时任务")
        session.delete(item)
        session.commit()
        return True


def build_scheduled_register_response(item: ScheduledRegisterTask) -> dict[str, Any]:
    payload = item.get_payload()
    return {
        "id": item.id,
        "name": item.name,
        "enabled": item.enabled,
        "platform": item.platform,
        "interval_minutes": item.interval_minutes,
        "register": sanitize_scheduled_register_payload(payload),
        "next_run_at": item.next_run_at,
        "last_run_at": item.last_run_at,
        "last_task_id": item.last_task_id,
        "last_status": item.last_status,
        "last_error": item.last_error,
        "created_at": item.created_at,
        "updated_at": item.updated_at,
    }


def list_due_scheduled_register_tasks(now: datetime | None = None) -> list[ScheduledRegisterTask]:
    current = now or utcnow()
    with Session(engine) as session:
        query = (
            select(ScheduledRegisterTask)
            .where(ScheduledRegisterTask.enabled == True)
            .where(ScheduledRegisterTask.next_run_at.is_not(None))
            .where(ScheduledRegisterTask.next_run_at <= current)
            .order_by(ScheduledRegisterTask.next_run_at.asc())
        )
        return list(session.exec(query).all())


def _is_task_active_locked(session: Session, task_id: str) -> bool:
    if not task_id:
        return False
    task = session.get(TaskRun, task_id)
    return bool(task and task.status not in TERMINAL_STATUSES)


def _can_dispatch_schedule_locked(
    session: Session,
    item: ScheduledRegisterTask | None,
    *,
    current: datetime,
    require_due: bool,
) -> bool:
    if item is None:
        return False
    if require_due:
        if not item.enabled or item.next_run_at is None or item.next_run_at > current:
            return False
    return not _is_task_active_locked(session, item.last_task_id)


def claim_schedule_dispatch(
    schedule_id: int,
    *,
    task_id: str,
    require_due: bool = False,
    now: datetime | None = None,
) -> ScheduledRegisterTask | None:
    current = now or utcnow()
    with _schedule_dispatch_lock:
        with Session(engine) as session:
            item = session.get(ScheduledRegisterTask, schedule_id)
            if not _can_dispatch_schedule_locked(session, item, current=current, require_due=require_due):
                return None
            item.last_task_id = task_id
            item.last_run_at = current
            item.last_status = SCHEDULE_STATUS_RUNNING
            item.last_error = ""
            item.next_run_at = build_schedule_next_run(item.interval_minutes, from_time=current)
            item.updated_at = current
            session.add(item)
            session.commit()
            session.refresh(item)
            return item


def skip_schedule_dispatch(
    schedule_id: int,
    *,
    status: str = SCHEDULE_STATUS_SUCCESS,
    error: str = "",
    require_due: bool = False,
    now: datetime | None = None,
) -> ScheduledRegisterTask | None:
    current = now or utcnow()
    with _schedule_dispatch_lock:
        with Session(engine) as session:
            item = session.get(ScheduledRegisterTask, schedule_id)
            if not _can_dispatch_schedule_locked(session, item, current=current, require_due=require_due):
                return None
            item.last_task_id = ""
            item.last_run_at = current
            item.last_status = status
            item.last_error = error
            item.next_run_at = build_schedule_next_run(item.interval_minutes, from_time=current)
            item.updated_at = current
            session.add(item)
            session.commit()
            session.refresh(item)
            return item


def mark_schedule_running(schedule_id: int, *, task_id: str, now: datetime | None = None) -> ScheduledRegisterTask | None:
    return claim_schedule_dispatch(schedule_id, task_id=task_id, now=now)


def mark_schedule_skipped(schedule_id: int, *, status: str = SCHEDULE_STATUS_SUCCESS, error: str = "", now: datetime | None = None) -> ScheduledRegisterTask | None:
    return skip_schedule_dispatch(schedule_id, status=status, error=error, now=now)


def mark_schedule_finished(schedule_id: int, *, status: str, error: str = "") -> ScheduledRegisterTask | None:
    with Session(engine) as session:
        item = session.get(ScheduledRegisterTask, schedule_id)
        if item is None:
            return None
        item.last_status = status
        item.last_error = error
        item.updated_at = utcnow()
        session.add(item)
        session.commit()
        session.refresh(item)
        return item


def reset_running_schedules() -> int:
    with Session(engine) as session:
        items = session.exec(
            select(ScheduledRegisterTask).where(ScheduledRegisterTask.last_status == SCHEDULE_STATUS_RUNNING)
        ).all()
        if not items:
            return 0
        count = 0
        current = utcnow()
        for item in items:
            item.last_status = SCHEDULE_STATUS_INTERRUPTED
            item.last_error = "服务重启，上次调度已中断"
            item.updated_at = current
            session.add(item)
            count += 1
        session.commit()
        return count


def is_schedule_task_active(schedule_id: int) -> bool:
    with Session(engine) as session:
        item = session.get(ScheduledRegisterTask, schedule_id)
        if item is None:
            return False
        return _is_task_active_locked(session, item.last_task_id)


def should_dispatch_schedule(schedule_id: int) -> bool:
    current = utcnow()
    with Session(engine) as session:
        item = session.get(ScheduledRegisterTask, schedule_id)
        return _can_dispatch_schedule_locked(session, item, current=current, require_due=False)


def redact_sensitive_text(text: str) -> str:
    redacted = text
    for marker in ["@", "Bearer ", "http://", "https://", "token", "key", "password"]:
        if marker in redacted and len(redacted) > 8:
            return "[已脱敏]"
    return redacted


def sanitize_task_payload_for_storage(task_type: str, payload: dict[str, Any] | None) -> dict[str, Any]:
    data = dict(payload or {})
    if task_type != "register_batch":
        return data
    data["password"] = ""
    data["proxy"] = ""
    extra = dict(data.get("extra") or {})
    for key in [
        "laoudo_auth",
        "yescaptcha_key",
        "duckmail_bearer",
        "freemail_admin_token",
        "freemail_password",
        "mail215_api_key",
        "cfworker_admin_token",
        "team_manager_key",
        "cpa_api_key",
        "grok2api_app_key",
    ]:
        if key in extra:
            extra[key] = ""
    data["extra"] = extra
    return data


def create_task_thread(target, *args) -> threading.Thread:
    thread = threading.Thread(target=target, args=args, daemon=True)
    thread.start()
    return thread
