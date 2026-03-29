from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlmodel import Session, select

from core.config_store import config_store
from core.db import AccountModel, ProxyModel, TaskRun, engine
from services.task_service import (
    RETRYABLE_TASK_STATUSES,
    SCHEDULE_STATUS_CANCELLED,
    SCHEDULE_STATUS_FAILED,
    SCHEDULE_STATUS_INTERRUPTED,
    SCHEDULE_STATUS_SUCCESS,
    TASK_STATUS_CANCELLED,
    TASK_STATUS_CANCEL_REQUESTED,
    TASK_STATUS_FAILED,
    TASK_STATUS_INTERRUPTED,
    TASK_STATUS_PARTIAL_SUCCESS,
    TASK_STATUS_RUNNING,
    TASK_STATUS_SUCCESS,
    TERMINAL_STATUSES,
    add_task_cashier_url,
    append_task_event,
    append_task_item,
    build_schedule_next_run,
    build_scheduled_register_response,
    build_task_response,
    claim_schedule_dispatch,
    create_scheduled_register_task,
    create_task_run,
    create_task_thread,
    delete_scheduled_register_task,
    delete_task,
    delete_task_logs,
    ensure_runtime_task,
    generate_task_id,
    get_runtime_task,
    get_scheduled_register_task,
    get_task,
    get_task_payload,
    is_cancel_requested,
    list_due_scheduled_register_tasks,
    list_legacy_task_logs,
    list_scheduled_register_tasks,
    list_task_events,
    list_task_items,
    list_tasks as list_task_runs,
    mark_schedule_finished,
    mark_schedule_skipped,
    request_task_cancel,
    sanitize_task_payload_for_storage,
    should_dispatch_schedule,
    update_scheduled_register_task,
    update_task_status,
)

router = APIRouter(prefix="/tasks", tags=["tasks"])
logger = logging.getLogger(__name__)


class RegisterTaskRequest(BaseModel):
    platform: str
    email: Optional[str] = None
    password: Optional[str] = None
    count: int = 1
    count_mode: str = "fixed"
    concurrency: int = 1
    register_delay_seconds: float = 0
    proxy: Optional[str] = None
    executor_type: str = "protocol"
    captcha_solver: str = "yescaptcha"
    extra: dict = Field(default_factory=dict)


class AccountCheckTaskRequest(BaseModel):
    platform: Optional[str] = None
    limit: int = 50
    account_ids: list[int] = Field(default_factory=list)
    sync_with_target: bool = False
    delete_invalid: bool = False


class ProxyCheckTaskRequest(BaseModel):
    proxy_ids: list[int] = Field(default_factory=list)


class TaskLogBatchDeleteRequest(BaseModel):
    ids: list[int]


class RetryTaskRequest(BaseModel):
    inherit_payload: bool = True


class TrialExpiryTaskRequest(BaseModel):
    limit: int = 200


class ScheduledRegisterTaskPayload(BaseModel):
    name: str
    enabled: bool = True
    interval_value: int = Field(default=1, ge=1)
    interval_unit: str = Field(default="hours")
    register: RegisterTaskRequest


class ScheduledRegisterRunRequest(BaseModel):
    inherit_payload: bool = True


def _normalize_count_mode(value: str) -> str:
    normalized = (value or "fixed").strip().lower()
    if normalized not in {"fixed", "dynamic"}:
        raise HTTPException(400, "count_mode 仅支持 fixed 或 dynamic")
    return normalized


def _parse_positive_int_config(key: str) -> int | None:
    raw = str(config_store.get(key, "") or "").strip()
    if not raw:
        return None
    if not raw.isdigit() or int(raw) <= 0:
        raise HTTPException(400, f"配置项 {key} 必须是正整数")
    return int(raw)


def _resolve_target_count(req: RegisterTaskRequest) -> int:
    if req.count_mode == "dynamic":
        configured_count = _parse_positive_int_config("default_target_count")
        if configured_count is not None:
            return configured_count
        raise HTTPException(400, "动态补量模式必须先配置 default_target_count")
    if req.count > 0:
        return req.count
    configured_count = _parse_positive_int_config("default_target_count")
    if configured_count is not None:
        return configured_count
    raise HTTPException(400, "count 必须大于 0，或先配置 default_target_count")


def _normalize_terminal_status(success: int, failed: int, cancelled: bool) -> str:
    if cancelled:
        if success > 0:
            return TASK_STATUS_PARTIAL_SUCCESS
        return TASK_STATUS_CANCELLED
    if failed == 0:
        return TASK_STATUS_SUCCESS
    if success > 0:
        return TASK_STATUS_PARTIAL_SUCCESS
    return TASK_STATUS_FAILED


def _build_task_item_key(index: int, email: Optional[str] = None) -> str:
    if email:
        return email
    return f"item_{index + 1}"


def _sanitize_log_message(message: str) -> str:
    return message


def _append_log(task_id: str, message: str, level: str = "info") -> None:
    safe_message = _sanitize_log_message(message)
    append_task_event(task_id, safe_message, level=level)
    logger.info("[%s] %s", task_id, safe_message)


def _auto_upload_integrations(task_id: str, account) -> None:
    try:
        from services.external_sync import sync_account

        for result in sync_account(account):
            name = result.get("name", "Auto Upload")
            ok = bool(result.get("ok"))
            msg = result.get("msg", "")
            _append_log(task_id, f"  [{name}] {'✓ ' + msg if ok else '✗ ' + msg}", level="info" if ok else "warning")
    except Exception as exc:
        _append_log(task_id, f"  [Auto Upload] 自动导入异常: {exc}", level="warning")


def _build_register_payload(req: RegisterTaskRequest) -> dict:
    return sanitize_task_payload_for_storage("register_batch", req.model_dump())


def _hydrate_register_retry_payload(payload: dict) -> dict:
    hydrated = dict(payload or {})
    extra = dict(hydrated.get("extra") or {})

    for key in [
        "laoudo_auth",
        "laoudo_email",
        "laoudo_account_id",
        "yescaptcha_key",
        "drift_mail_base_url",
        "drift_mail_access_key",
        "drift_mail_domain",
        "duckmail_api_url",
        "duckmail_provider_url",
        "duckmail_bearer",
        "freemail_api_url",
        "freemail_admin_token",
        "freemail_username",
        "freemail_password",
        "moemail_api_url",
        "mail_provider",
        "mail215_api_url",
        "mail215_api_key",
        "mail215_domain",
        "mail215_address_prefix",
        "cfworker_api_url",
        "cfworker_admin_token",
        "cfworker_domain",
        "cfworker_fingerprint",
    ]:
        if not extra.get(key):
            value = config_store.get(key, "")
            if value not in (None, ""):
                extra[key] = value

    hydrated["extra"] = extra
    if not hydrated.get("captcha_solver"):
        hydrated["captcha_solver"] = str(config_store.get("default_captcha_solver", "") or "yescaptcha")
    return hydrated


def _prepare_register_request(req: RegisterTaskRequest) -> RegisterTaskRequest:
    payload = req.model_dump()
    payload["count_mode"] = _normalize_count_mode(req.count_mode)
    payload["count"] = _resolve_target_count(req)
    return RegisterTaskRequest(**payload)


def _interval_to_minutes(value: int, unit: str) -> int:
    if unit == "minutes":
        return value
    if unit == "hours":
        return value * 60
    raise HTTPException(400, "interval_unit 仅支持 minutes 或 hours")


def _map_schedule_terminal_status(task_status: str) -> str:
    if task_status == TASK_STATUS_CANCELLED:
        return SCHEDULE_STATUS_CANCELLED
    if task_status == TASK_STATUS_INTERRUPTED:
        return SCHEDULE_STATUS_INTERRUPTED
    if task_status in {TASK_STATUS_SUCCESS, TASK_STATUS_PARTIAL_SUCCESS}:
        return SCHEDULE_STATUS_SUCCESS
    return SCHEDULE_STATUS_FAILED


def _build_register_summary(
    req: RegisterTaskRequest,
    *,
    effective_count: int,
    errors: list[str] | None = None,
    cashier_urls: list[str] | None = None,
    current_valid_count: int | None = None,
) -> dict:
    summary = {
        "errors": errors or [],
        "cashier_urls": cashier_urls or [],
        "count_mode": req.count_mode,
        "target_count": req.count,
        "effective_count": effective_count,
    }
    if current_valid_count is not None:
        summary["current_valid_count"] = current_valid_count
    return summary


def _resolve_register_count(req: RegisterTaskRequest) -> tuple[int, int | None]:
    if req.count_mode != "dynamic":
        return req.count, None

    with Session(engine) as session:
        current_valid_count = len(
            session.exec(
                select(AccountModel.id)
                .where(AccountModel.platform == req.platform)
                .where(AccountModel.status.in_(["registered", "trial", "subscribed"]))
            ).all()
        )

    return max(req.count - current_valid_count, 0), current_valid_count


def _run_register(
    task_id: str,
    payload: dict,
    schedule_id: int | None = None,
    effective_count_override: int | None = None,
    current_valid_count_override: int | None = None,
) -> None:
    from core.base_mailbox import create_mailbox
    from core.base_platform import RegisterConfig
    from core.db import save_account
    from core.proxy_pool import proxy_pool
    from core.registry import get

    req = _prepare_register_request(RegisterTaskRequest(**payload))
    if effective_count_override is None:
        effective_count, current_valid_count = _resolve_register_count(req)
    else:
        effective_count = effective_count_override
        current_valid_count = current_valid_count_override
    initial_summary = _build_register_summary(
        req,
        effective_count=effective_count,
        current_valid_count=current_valid_count,
    )
    ensure_runtime_task(task_id)
    update_task_status(
        task_id,
        status=TASK_STATUS_RUNNING,
        processed_count=0,
        success_count=0,
        failed_count=0,
        summary=initial_summary,
    )

    if effective_count == 0:
        _append_log(task_id, "动态补量检查完成，当前有效账号数已达到目标，无需新增")
        update_task_status(
            task_id,
            status=TASK_STATUS_SUCCESS,
            processed_count=0,
            success_count=0,
            failed_count=0,
            summary=initial_summary,
        )
        if schedule_id is not None:
            mark_schedule_finished(schedule_id, status=SCHEDULE_STATUS_SUCCESS, error="")
        return

    success = 0
    failed = 0
    processed = 0
    errors: list[str] = []
    start_gate_lock = threading.Lock()
    next_start_time = time.time()

    try:
        platform_cls = get(req.platform)
    except Exception as exc:
        _append_log(task_id, f"致命错误: {exc}", level="error")
        update_task_status(
            task_id,
            status=TASK_STATUS_FAILED,
            error="任务启动失败",
            summary=_build_register_summary(
                req,
                effective_count=effective_count,
                errors=["任务启动失败"],
                current_valid_count=current_valid_count,
            ),
        )
        if schedule_id is not None:
            mark_schedule_finished(schedule_id, status=SCHEDULE_STATUS_FAILED, error="任务启动失败")
        return

    def _build_mailbox(proxy: Optional[str]):
        return create_mailbox(
            provider=req.extra.get("mail_provider", "laoudo"),
            extra=req.extra,
            proxy=proxy,
        )

    def _do_one(index: int) -> dict:
        nonlocal next_start_time
        if is_cancel_requested(task_id):
            return {"status": "cancelled", "item_key": _build_task_item_key(index, req.email)}

        selected_proxy: Optional[str] = None
        try:
            selected_proxy = req.proxy or proxy_pool.get_next()
            if req.register_delay_seconds > 0:
                with start_gate_lock:
                    if is_cancel_requested(task_id):
                        return {"status": "cancelled", "item_key": _build_task_item_key(index, req.email)}
                    now = time.time()
                    wait_seconds = max(0.0, next_start_time - now)
                    if wait_seconds > 0:
                        _append_log(task_id, f"第 {index + 1} 个账号启动前延迟 {wait_seconds:g} 秒")
                        time.sleep(wait_seconds)
                    next_start_time = time.time() + req.register_delay_seconds

            if is_cancel_requested(task_id):
                return {"status": "cancelled", "item_key": _build_task_item_key(index, req.email)}

            config = RegisterConfig(
                executor_type=req.executor_type,
                captcha_solver=req.captcha_solver,
                proxy=selected_proxy,
                extra=req.extra,
            )
            mailbox = _build_mailbox(selected_proxy)
            platform = platform_cls(config=config, mailbox=mailbox)
            platform._log_fn = lambda message: _append_log(task_id, message)
            if getattr(platform, "mailbox", None) is not None:
                platform.mailbox._log_fn = platform._log_fn

            _append_log(task_id, f"开始注册第 {index + 1}/{effective_count} 个账号")
            if selected_proxy:
                _append_log(task_id, f"使用代理: {selected_proxy}")

            account = platform.register(email=req.email or None, password=req.password)
            save_account(account)
            if selected_proxy:
                proxy_pool.report_success(selected_proxy)

            cashier_url = (account.extra or {}).get("cashier_url", "")
            _append_log(task_id, f"✓ 注册成功: {account.email}")
            append_task_item(
                task_id=task_id,
                item_type="account",
                item_key=account.email,
                platform=req.platform,
                email=account.email,
                status="success",
                detail={"cashier_url": cashier_url},
            )
            _auto_upload_integrations(task_id, account)
            if cashier_url:
                _append_log(task_id, f"  [升级链接] {cashier_url}")
                add_task_cashier_url(task_id, cashier_url)
            return {
                "status": "success",
                "email": account.email,
                "cashier_url": cashier_url,
                "account": account,
            }
        except Exception as exc:
            if selected_proxy:
                proxy_pool.report_fail(selected_proxy)
            _append_log(task_id, f"✗ 注册失败: {exc}", level="error")
            append_task_item(
                task_id=task_id,
                item_type="account",
                item_key=_build_task_item_key(index, req.email),
                platform=req.platform,
                email=req.email or "",
                status="failed",
                error="注册失败",
                detail={},
            )
            return {"status": "failed", "error": "注册失败"}

    max_workers = min(req.concurrency, effective_count, 5)
    cancelled = False
    successful_accounts: list = []
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = [pool.submit(_do_one, index) for index in range(effective_count)]
        for future in as_completed(futures):
            try:
                result = future.result()
            except Exception:
                result = {"status": "failed", "error": "任务线程异常"}
                _append_log(task_id, "✗ 任务线程异常", level="error")
            processed += 1
            if result["status"] == "success":
                success += 1
                if result.get("account") is not None:
                    successful_accounts.append(result["account"])
            elif result["status"] == "failed":
                failed += 1
                errors.append(result["error"])
            elif result["status"] == "cancelled":
                cancelled = True
            update_task_status(
                task_id,
                status=TASK_STATUS_CANCEL_REQUESTED if is_cancel_requested(task_id) else TASK_STATUS_RUNNING,
                processed_count=processed,
                success_count=success,
                failed_count=failed,
                summary=_build_register_summary(
                    req,
                    effective_count=effective_count,
                    errors=errors,
                    cashier_urls=list(get_runtime_task(task_id).cashier_urls) if get_runtime_task(task_id) else [],
                    current_valid_count=current_valid_count,
                ),
            )

    final_cashier_urls = list(get_runtime_task(task_id).cashier_urls) if get_runtime_task(task_id) else []
    final_status = _normalize_terminal_status(success, failed, cancelled or is_cancel_requested(task_id))
    if final_status == TASK_STATUS_PARTIAL_SUCCESS and successful_accounts:
        _append_log(task_id, f"任务部分成功，补做 {len(successful_accounts)} 个成功账号的远端同步")
        for account in successful_accounts:
            _auto_upload_integrations(task_id, account)
    _append_log(task_id, f"完成: 成功 {success} 个, 失败 {failed} 个")
    update_task_status(
        task_id,
        status=final_status,
        processed_count=processed,
        success_count=success,
        failed_count=failed,
        error="; ".join(errors[:3]) if final_status == TASK_STATUS_FAILED else "",
        summary=_build_register_summary(
            req,
            effective_count=effective_count,
            errors=errors,
            cashier_urls=final_cashier_urls,
            current_valid_count=current_valid_count,
        ),
    )
    if schedule_id is not None:
        mark_schedule_finished(
            schedule_id,
            status=_map_schedule_terminal_status(final_status),
            error="; ".join(errors[:3]) if errors else "",
        )


def _run_account_check(task_id: str, payload: dict) -> None:
    from core.base_platform import Account, RegisterConfig
    from core.registry import get, load_all

    req = AccountCheckTaskRequest(**payload)
    load_all()

    with Session(engine) as session:
        query = select(AccountModel)
        if req.account_ids:
            query = query.where(AccountModel.id.in_(req.account_ids))
        else:
            query = query.where(AccountModel.status.in_(["registered", "trial", "subscribed"]))
            if req.platform:
                query = query.where(AccountModel.platform == req.platform)
        accounts = list(session.exec(query.limit(req.limit)).all())

    update_task_status(
        task_id,
        status=TASK_STATUS_RUNNING,
        processed_count=0,
        success_count=0,
        failed_count=0,
        summary={"valid": 0, "invalid": 0, "error": 0},
    )

    valid_count = 0
    invalid_count = 0
    error_count = 0
    deleted_count = 0
    processed = 0

    for account_row in accounts:
        if is_cancel_requested(task_id):
            break
        try:
            detail: dict[str, Any] = {}
            valid = False
            was_deleted = False
            if req.sync_with_target and account_row.platform == "chatgpt":
                from services.chatgpt_target_sync import sync_and_cleanup_account

                with Session(engine) as session:
                    db_account = session.get(AccountModel, account_row.id)
                    if db_account is None:
                        processed += 1
                        continue

                    result = sync_and_cleanup_account(
                        db_account,
                        delete_invalid=req.delete_invalid,
                        refresh_target=True,
                    )
                    detail = {
                        "valid": result["valid"],
                        "target": result.get("target") or {},
                        "delete_local": result.get("delete_local", False),
                    }
                    if result["valid"]:
                        db_account.updated_at = datetime.now(timezone.utc)
                        session.add(db_account)
                        session.commit()
                    elif result.get("delete_local"):
                        session.delete(db_account)
                        session.commit()
                        deleted_count += 1
                        was_deleted = True
                    else:
                        db_account.status = "invalid"
                        db_account.updated_at = datetime.now(timezone.utc)
                        session.add(db_account)
                        session.commit()

                valid = bool(result["valid"])
            else:
                platform_cls = get(account_row.platform)
                plugin = platform_cls(config=RegisterConfig())
                account_obj = Account(
                    platform=account_row.platform,
                    email=account_row.email,
                    password=account_row.password,
                    user_id=account_row.user_id,
                    region=account_row.region,
                    token=account_row.token,
                    extra=json.loads(account_row.extra_json or "{}"),
                )
                valid = plugin.check_valid(account_obj)
                detail = {"valid": valid}
                with Session(engine) as session:
                    db_account = session.get(AccountModel, account_row.id)
                    if db_account is not None:
                        db_account.status = db_account.status if valid else "invalid"
                        db_account.updated_at = datetime.now(timezone.utc)
                        session.add(db_account)
                        session.commit()

            if valid:
                valid_count += 1
                append_task_item(
                    task_id=task_id,
                    item_type="account",
                    item_key=account_row.email,
                    platform=account_row.platform,
                    email=account_row.email,
                    status="success",
                    detail=detail,
                )
                _append_log(task_id, f"✓ 账号有效: {account_row.email}")
            elif was_deleted:
                append_task_item(
                    task_id=task_id,
                    item_type="account",
                    item_key=account_row.email,
                    platform=account_row.platform,
                    email=account_row.email,
                    status="success",
                    detail=detail,
                )
                _append_log(task_id, f"✓ 目标端账号已失效，已删除本地账号: {account_row.email}", level="warning")
            else:
                invalid_count += 1
                append_task_item(
                    task_id=task_id,
                    item_type="account",
                    item_key=account_row.email,
                    platform=account_row.platform,
                    email=account_row.email,
                    status="failed",
                    error="invalid",
                    detail=detail,
                )
                _append_log(task_id, f"✗ 账号失效: {account_row.email}", level="warning")
        except Exception:
            error_count += 1
            append_task_item(
                task_id=task_id,
                item_type="account",
                item_key=account_row.email,
                platform=account_row.platform,
                email=account_row.email,
                status="failed",
                error="账号检测异常",
                detail={},
            )
            _append_log(task_id, f"✗ 检测账号异常: {account_row.email}", level="error")
        processed += 1
        update_task_status(
            task_id,
            status=TASK_STATUS_CANCEL_REQUESTED if is_cancel_requested(task_id) else TASK_STATUS_RUNNING,
            processed_count=processed,
            success_count=valid_count + deleted_count,
            failed_count=invalid_count + error_count,
            summary={"valid": valid_count, "invalid": invalid_count, "error": error_count, "deleted": deleted_count},
        )

    final_status = _normalize_terminal_status(valid_count + deleted_count, invalid_count + error_count, is_cancel_requested(task_id))
    update_task_status(
        task_id,
        status=final_status,
        processed_count=processed,
        success_count=valid_count + deleted_count,
        failed_count=invalid_count + error_count,
        summary={"valid": valid_count, "invalid": invalid_count, "error": error_count, "deleted": deleted_count},
    )


def _run_proxy_check(task_id: str, payload: dict) -> None:
    import requests

    req = ProxyCheckTaskRequest(**payload)
    with Session(engine) as session:
        query = select(ProxyModel)
        if req.proxy_ids:
            query = query.where(ProxyModel.id.in_(req.proxy_ids))
        proxies = list(session.exec(query).all())

    from core.proxy_pool import proxy_pool

    ok_count = 0
    fail_count = 0
    processed = 0
    update_task_status(
        task_id,
        status=TASK_STATUS_RUNNING,
        processed_count=0,
        success_count=0,
        failed_count=0,
        summary={"ok": 0, "fail": 0},
    )

    for proxy in proxies:
        if is_cancel_requested(task_id):
            break
        try:
            response = requests.get(
                "https://httpbin.org/ip",
                proxies={"http": proxy.url, "https": proxy.url},
                timeout=8,
            )
            if response.status_code == 200:
                proxy_pool.report_success(proxy.url)
                ok_count += 1
                append_task_item(
                    task_id=task_id,
                    item_type="proxy",
                    item_key="proxy",
                    platform="",
                    email="",
                    status="success",
                    detail={"region": proxy.region},
                )
                _append_log(task_id, f"✓ 代理可用: {proxy.url}")
            else:
                proxy_pool.report_fail(proxy.url)
                fail_count += 1
                append_task_item(
                    task_id=task_id,
                    item_type="proxy",
                    item_key="proxy",
                    platform="",
                    email="",
                    status="failed",
                    error=f"http {response.status_code}",
                    detail={"region": proxy.region},
                )
                _append_log(task_id, f"✗ 代理不可用: {proxy.url}", level="warning")
        except Exception as exc:
            proxy_pool.report_fail(proxy.url)
            fail_count += 1
            append_task_item(
                task_id=task_id,
                item_type="proxy",
                item_key="proxy",
                platform="",
                email="",
                status="failed",
                error="代理检测异常",
                detail={"region": proxy.region},
            )
            _append_log(task_id, f"✗ 代理检测异常: {proxy.url} - {exc}", level="error")
        processed += 1
        update_task_status(
            task_id,
            status=TASK_STATUS_CANCEL_REQUESTED if is_cancel_requested(task_id) else TASK_STATUS_RUNNING,
            processed_count=processed,
            success_count=ok_count,
            failed_count=fail_count,
            summary={"ok": ok_count, "fail": fail_count},
        )

    final_status = _normalize_terminal_status(ok_count, fail_count, is_cancel_requested(task_id))
    update_task_status(
        task_id,
        status=final_status,
        processed_count=processed,
        success_count=ok_count,
        failed_count=fail_count,
        summary={"ok": ok_count, "fail": fail_count},
    )


def _run_trial_expiry(task_id: str, payload: dict) -> None:
    req = TrialExpiryTaskRequest(**payload)
    now = int(datetime.now(timezone.utc).timestamp())
    with Session(engine) as session:
        accounts = list(
            session.exec(
                select(AccountModel)
                .where(AccountModel.status == "trial")
                .limit(req.limit)
            ).all()
        )

    update_task_status(
        task_id,
        status=TASK_STATUS_RUNNING,
        processed_count=0,
        success_count=0,
        failed_count=0,
        summary={"expired": 0},
    )

    expired_count = 0
    processed = 0
    for account in accounts:
        if is_cancel_requested(task_id):
            break
        if account.trial_end_time and account.trial_end_time < now:
            with Session(engine) as session:
                db_account = session.get(AccountModel, account.id)
                if db_account is not None:
                    db_account.status = "expired"
                    db_account.updated_at = datetime.now(timezone.utc)
                    session.add(db_account)
                    session.commit()
            expired_count += 1
            append_task_item(
                task_id=task_id,
                item_type="scheduler",
                item_key=account.email,
                platform=account.platform,
                email=account.email,
                status="success",
                detail={"new_status": "expired"},
            )
            _append_log(task_id, f"✓ trial 到期: {account.email}")
        processed += 1
        update_task_status(
            task_id,
            status=TASK_STATUS_CANCEL_REQUESTED if is_cancel_requested(task_id) else TASK_STATUS_RUNNING,
            processed_count=processed,
            success_count=expired_count,
            failed_count=0,
            summary={"expired": expired_count},
        )

    final_status = _normalize_terminal_status(expired_count, 0, is_cancel_requested(task_id))
    update_task_status(
        task_id,
        status=final_status,
        processed_count=processed,
        success_count=expired_count,
        failed_count=0,
        summary={"expired": expired_count},
    )


def start_register_task(
    req: RegisterTaskRequest,
    *,
    background_tasks: BackgroundTasks | None,
    trigger_source: str = "manual",
    parent_task_id: str | None = None,
    schedule_id: int | None = None,
) -> dict:
    prepared_req = _prepare_register_request(req)
    effective_count, current_valid_count = _resolve_register_count(prepared_req)

    task_id = generate_task_id("task")
    if schedule_id is not None:
        if prepared_req.count_mode == "dynamic" and effective_count == 0:
            claimed_schedule = mark_schedule_skipped(schedule_id)
            if claimed_schedule is None:
                return {
                    "task_id": "",
                    "skipped": True,
                    "reason": "schedule_not_dispatchable",
                    "effective_count": 0,
                    "current_valid_count": current_valid_count,
                }
            return {
                "task_id": "",
                "skipped": True,
                "effective_count": 0,
                "current_valid_count": current_valid_count,
            }

        claimed_schedule = claim_schedule_dispatch(schedule_id, task_id=task_id)
        if claimed_schedule is None:
            return {
                "task_id": "",
                "skipped": True,
                "reason": "schedule_not_dispatchable",
                "effective_count": effective_count,
                "current_valid_count": current_valid_count,
            }

    create_task_run(
        task_id=task_id,
        task_type="register_batch",
        trigger_source=trigger_source,
        target_platform=prepared_req.platform,
        payload=_build_register_payload(prepared_req),
        total_count=effective_count,
        parent_task_id=parent_task_id,
    )
    if background_tasks is not None:
        background_tasks.add_task(
            _run_register,
            task_id,
            prepared_req.model_dump(),
            schedule_id,
            effective_count,
            current_valid_count,
        )
    else:
        create_task_thread(
            _run_register,
            task_id,
            prepared_req.model_dump(),
            schedule_id,
            effective_count,
            current_valid_count,
        )
    return {
        "task_id": task_id,
        "effective_count": effective_count,
        "current_valid_count": current_valid_count,
    }


def start_account_check_task(
    req: AccountCheckTaskRequest,
    *,
    background_tasks: BackgroundTasks | None,
    trigger_source: str = "manual",
    parent_task_id: str | None = None,
) -> dict:
    with Session(engine) as session:
        query = select(AccountModel)
        if req.account_ids:
            query = query.where(AccountModel.id.in_(req.account_ids))
        else:
            query = query.where(AccountModel.status.in_(["registered", "trial", "subscribed"]))
            if req.platform:
                query = query.where(AccountModel.platform == req.platform)
        total_count = len(session.exec(query.limit(req.limit)).all())
    task_id = generate_task_id("task")
    create_task_run(
        task_id=task_id,
        task_type="account_check_batch",
        trigger_source=trigger_source,
        target_platform=req.platform,
        payload=req.model_dump(),
        total_count=total_count,
        parent_task_id=parent_task_id,
    )
    if background_tasks is not None:
        background_tasks.add_task(_run_account_check, task_id, req.model_dump())
    else:
        create_task_thread(_run_account_check, task_id, req.model_dump())
    return {"task_id": task_id}


def start_proxy_check_task(
    req: ProxyCheckTaskRequest,
    *,
    background_tasks: BackgroundTasks | None,
    trigger_source: str = "manual",
    parent_task_id: str | None = None,
) -> dict:
    task_id = generate_task_id("task")
    if req.proxy_ids:
        total_count = len(req.proxy_ids)
    else:
        with Session(engine) as session:
            total_count = len(session.exec(select(ProxyModel.id)).all())
    create_task_run(
        task_id=task_id,
        task_type="proxy_check_batch",
        trigger_source=trigger_source,
        target_platform=None,
        payload=req.model_dump(),
        total_count=total_count,
        parent_task_id=parent_task_id,
    )
    if background_tasks is not None:
        background_tasks.add_task(_run_proxy_check, task_id, req.model_dump())
    else:
        create_task_thread(_run_proxy_check, task_id, req.model_dump())
    return {"task_id": task_id}


def start_trial_expiry_task(
    req: TrialExpiryTaskRequest,
    *,
    trigger_source: str = "scheduler",
    scheduler_key: str = "trial_expiry",
) -> dict:
    existing_total, existing_items = list_task_runs(
        task_type="scheduler_trial_expiry",
        status=TASK_STATUS_RUNNING,
        trigger_source=trigger_source,
        page=1,
        page_size=10,
    )
    if existing_total and any(item.scheduler_key == scheduler_key for item in existing_items):
        return {"task_id": existing_items[0].id, "skipped": True}

    with Session(engine) as session:
        total_count = len(
            session.exec(
                select(AccountModel.id)
                .where(AccountModel.status == "trial")
                .limit(req.limit)
            ).all()
        )
    task_id = generate_task_id("task")
    create_task_run(
        task_id=task_id,
        task_type="scheduler_trial_expiry",
        trigger_source=trigger_source,
        target_platform=None,
        payload=req.model_dump(),
        total_count=total_count,
        scheduler_key=scheduler_key,
    )
    _run_trial_expiry(task_id, req.model_dump())
    return {"task_id": task_id}


def dispatch_due_scheduled_register_tasks() -> list[str]:
    created_task_ids: list[str] = []
    for schedule in list_due_scheduled_register_tasks():
        if not should_dispatch_schedule(schedule.id or 0):
            continue
        payload = schedule.get_payload()
        result = start_register_task(
            RegisterTaskRequest(**payload),
            background_tasks=None,
            trigger_source="scheduler",
            schedule_id=schedule.id,
        )
        if result.get("task_id"):
            created_task_ids.append(result["task_id"])
    return created_task_ids


@router.post("/register")
def create_register_task(req: RegisterTaskRequest, background_tasks: BackgroundTasks):
    return start_register_task(req, background_tasks=background_tasks)


@router.post("/account-check")
def create_account_check_task(req: AccountCheckTaskRequest, background_tasks: BackgroundTasks):
    return start_account_check_task(req, background_tasks=background_tasks)


@router.post("/proxy-check")
def create_proxy_check_task(req: ProxyCheckTaskRequest, background_tasks: BackgroundTasks):
    return start_proxy_check_task(req, background_tasks=background_tasks)


@router.get("/schedules")
def get_schedules():
    return {"items": [build_scheduled_register_response(item) for item in list_scheduled_register_tasks()]}


@router.post("/schedules")
def create_schedule(body: ScheduledRegisterTaskPayload):
    interval_minutes = _interval_to_minutes(body.interval_value, body.interval_unit)
    register_req = _prepare_register_request(body.register)
    item = create_scheduled_register_task(
        name=body.name,
        enabled=body.enabled,
        platform=register_req.platform,
        interval_minutes=interval_minutes,
        payload=register_req.model_dump(),
    )
    return build_scheduled_register_response(item)


@router.put("/schedules/{schedule_id}")
def update_schedule(schedule_id: int, body: ScheduledRegisterTaskPayload):
    interval_minutes = _interval_to_minutes(body.interval_value, body.interval_unit)
    register_req = _prepare_register_request(body.register)
    item = update_scheduled_register_task(
        schedule_id,
        name=body.name,
        enabled=body.enabled,
        platform=register_req.platform,
        interval_minutes=interval_minutes,
        payload=register_req.model_dump(),
    )
    if item is None:
        raise HTTPException(404, "定时任务不存在")
    return build_scheduled_register_response(item)


@router.delete("/schedules/{schedule_id}")
def delete_schedule(schedule_id: int):
    try:
        deleted = delete_scheduled_register_task(schedule_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    if not deleted:
        raise HTTPException(404, "定时任务不存在")
    return {"ok": True}


@router.post("/schedules/{schedule_id}/run")
def run_schedule_once(schedule_id: int, body: ScheduledRegisterRunRequest):
    item = get_scheduled_register_task(schedule_id)
    if item is None:
        raise HTTPException(404, "定时任务不存在")
    if not should_dispatch_schedule(schedule_id):
        raise HTTPException(400, "该定时任务上一次运行仍未结束")
    payload = item.get_payload() if body.inherit_payload else {**item.get_payload(), "count": 1}
    result = start_register_task(
        RegisterTaskRequest(**payload),
        background_tasks=None,
        trigger_source="scheduler",
        schedule_id=schedule_id,
    )
    return result


@router.get("/logs")
def get_logs(platform: str = "", page: int = 1, page_size: int = 50):
    total, items = list_legacy_task_logs(platform=platform or None, page=page, page_size=page_size)
    return {"total": total, "items": items}


@router.post("/logs/batch-delete")
def batch_delete_logs(body: TaskLogBatchDeleteRequest):
    if not body.ids:
        raise HTTPException(400, "任务历史 ID 列表不能为空")
    unique_ids = list(dict.fromkeys(body.ids))
    if len(unique_ids) > 1000:
        raise HTTPException(400, "单次最多删除 1000 条任务历史")
    try:
        deleted_count, not_found_ids = delete_task_logs(unique_ids)
        logger.info("批量删除任务历史成功: %s 条", deleted_count)
        return {
            "deleted": deleted_count,
            "not_found": not_found_ids,
            "total_requested": len(unique_ids),
        }
    except Exception as exc:
        logger.exception("批量删除任务历史失败")
        raise HTTPException(500, f"批量删除任务历史失败: {exc}")


@router.get("/{task_id}/items")
def get_task_items(task_id: str, page: int = 1, page_size: int = 100):
    task = get_task(task_id)
    if task is None:
        raise HTTPException(404, "任务不存在")
    total, items = list_task_items(task_id, page=page, page_size=page_size)
    return {"total": total, "items": items}


@router.get("/{task_id}/events")
def get_task_events(task_id: str, since_id: int = 0, limit: int = 200):
    task = get_task(task_id)
    if task is None:
        raise HTTPException(404, "任务不存在")
    items = list_task_events(task_id, since_id=since_id, limit=limit)
    return {"items": items}


@router.post("/{task_id}/cancel")
def cancel_task(task_id: str):
    task = get_task(task_id)
    if task is None:
        raise HTTPException(404, "任务不存在")
    if task.status in TERMINAL_STATUSES:
        return {"task_id": task_id, "status": task.status, "message": "任务已结束"}
    task = request_task_cancel(task_id)
    return {"task_id": task.id, "status": task.status}


@router.post("/{task_id}/retry")
def retry_task(task_id: str, body: RetryTaskRequest, background_tasks: BackgroundTasks):
    task = get_task(task_id)
    if task is None:
        raise HTTPException(404, "任务不存在")
    if task.status not in TERMINAL_STATUSES:
        raise HTTPException(400, "仅已结束任务允许重试")
    if task.status not in RETRYABLE_TASK_STATUSES:
        raise HTTPException(400, "当前任务状态不允许重试")

    payload = get_task_payload(task_id) if body.inherit_payload else {}
    if task.task_type == "register_batch":
        if not payload:
            raise HTTPException(400, "注册任务重试必须继承原任务参数")
        hydrated_payload = _hydrate_register_retry_payload(payload)
        return start_register_task(
            RegisterTaskRequest(**hydrated_payload),
            background_tasks=background_tasks,
            trigger_source="retry",
            parent_task_id=task_id,
        )
    if task.task_type == "account_check_batch":
        return start_account_check_task(
            AccountCheckTaskRequest(**payload),
            background_tasks=background_tasks,
            trigger_source="retry",
            parent_task_id=task_id,
        )
    if task.task_type == "proxy_check_batch":
        return start_proxy_check_task(
            ProxyCheckTaskRequest(**payload),
            background_tasks=background_tasks,
            trigger_source="retry",
            parent_task_id=task_id,
        )
    raise HTTPException(400, "当前任务类型暂不支持重试")


@router.delete("/{task_id}")
def remove_task(task_id: str):
    try:
        deleted = delete_task(task_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    if not deleted:
        raise HTTPException(404, "任务不存在")
    return {"ok": True}


@router.get("/{task_id}/logs/stream")
async def stream_logs(task_id: str, since: int = 0):
    task = get_task(task_id)
    if task is None:
        raise HTTPException(404, "任务不存在")

    async def event_generator():
        sent_id = since
        while True:
            events = list_task_events(task_id, since_id=sent_id, limit=200)
            for event in events:
                sent_id = max(sent_id, event.id or 0)
                yield f"data: {json.dumps({'line': event.message, 'id': event.id}, ensure_ascii=False)}\n\n"
            current_task = get_task(task_id)
            if current_task and current_task.status in TERMINAL_STATUSES:
                yield f"data: {json.dumps({'done': True, 'status': current_task.status}, ensure_ascii=False)}\n\n"
                break
            await asyncio.sleep(0.5)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/{task_id}")
def get_task_detail(task_id: str):
    task = get_task(task_id)
    if task is None:
        raise HTTPException(404, "任务不存在")
    return build_task_response(task)


@router.get("")
def list_tasks(
    task_type: str = "",
    status: str = "",
    trigger_source: str = "",
    platform: str = "",
    page: int = 1,
    page_size: int = 50,
):
    total, items = list_task_runs(
        task_type=task_type or None,
        status=status or None,
        trigger_source=trigger_source or None,
        target_platform=platform or None,
        page=page,
        page_size=page_size,
    )
    return {
        "total": total,
        "items": [build_task_response(item) for item in items],
    }
