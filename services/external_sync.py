"""外部系统同步（自动导入 / 回填）"""

from __future__ import annotations

from curl_cffi import requests as cffi_requests
from typing import Any
import re


def _get_extra(account) -> dict[str, Any]:
    extra = getattr(account, "extra", None)
    if isinstance(extra, dict):
        return extra

    getter = getattr(account, "get_extra", None)
    if callable(getter):
        value = getter()
        if isinstance(value, dict):
            return value
    return {}


def _normalize_chatgpt_sync_provider(raw: str | None) -> str:
    value = str(raw or "").strip().lower()
    if value in {"cpa", "cliproxyapi"}:
        return "cliproxyapi"
    if value in {"team_manager", "teammanager", "tm"}:
        return "team_manager"
    if value == "sub2api":
        return "sub2api"
    return ""


def _resolve_chatgpt_sync_provider() -> str:
    from core.config_store import config_store

    configured = _normalize_chatgpt_sync_provider(config_store.get("chatgpt_sync_target_provider", ""))
    if configured:
        return configured

    sub2api_url = str(config_store.get("sub2api_url", "") or "").strip()
    sub2api_admin_key = str(config_store.get("sub2api_admin_key", "") or "").strip()
    if sub2api_url and sub2api_admin_key:
        return "sub2api"

    team_manager_url = str(config_store.get("team_manager_url", "") or "").strip()
    team_manager_key = str(config_store.get("team_manager_key", "") or "").strip()
    if team_manager_url and team_manager_key:
        return "team_manager"

    cpa_url = str(config_store.get("cpa_api_url", "") or "").strip()
    cpa_key = str(config_store.get("cliproxyapi_management_key", "") or config_store.get("cpa_api_key", "")).strip()
    if cpa_url and cpa_key:
        return "cliproxyapi"

    return ""


def _build_chatgpt_upload_account(account):
    extra = _get_extra(account)

    class _A:
        pass

    a = _A()
    a.email = getattr(account, "email", "")
    a.access_token = extra.get("access_token") or getattr(account, "token", "")
    a.refresh_token = extra.get("refresh_token", "")
    a.id_token = extra.get("id_token", "")
    a.session_token = extra.get("session_token", "")
    a.client_id = extra.get("client_id", "app_EMoamEEZ73f0CkXaXp7hrann")
    a.cookies = extra.get("cookies", "")
    return a


def _request_json(
    method: str,
    url: str,
    *,
    headers: dict[str, str],
    params: dict[str, Any] | None = None,
    json_body: dict[str, Any] | None = None,
) -> tuple[int, Any]:
    response = cffi_requests.request(
        method=method,
        url=url,
        headers=headers,
        params=params,
        json=json_body,
        proxies=None,
        verify=False,
        timeout=30,
        impersonate="chrome110",
    )
    try:
        return response.status_code, response.json()
    except Exception:
        return response.status_code, response.text


def _extract_items(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []

    candidates: list[Any] = [
        payload.get("items"),
        payload.get("files"),
        payload.get("accounts"),
        payload.get("data"),
    ]
    data = payload.get("data")
    if isinstance(data, dict):
        candidates.extend([data.get("items"), data.get("files"), data.get("accounts")])

    for candidate in candidates:
        if isinstance(candidate, list):
            return [item for item in candidate if isinstance(item, dict)]
    return []


def _extract_remote_email(provider: str, item: dict[str, Any]) -> str:
    if provider == "sub2api":
        name = str(item.get("name") or "").strip().lower()
        if re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", name):
            return name
        return ""
    if provider == "cliproxyapi":
        name = str(item.get("name") or item.get("file_name") or "").strip()
        if name.endswith(".json"):
            return name[:-5].strip().lower()
        return ""
    return str(item.get("email") or item.get("username") or item.get("name") or "").strip().lower()


def _extract_remote_id(item: dict[str, Any]) -> str:
    remote_id = item.get("id")
    return str(remote_id).strip() if remote_id not in (None, "") else ""


def _is_error_status(item: dict[str, Any]) -> bool:
    status = str(item.get("status") or item.get("state") or item.get("account_status") or "").strip().lower()
    return status == "error"


def _delete_sub2api_account(base_url: str, admin_key: str, remote_id: str) -> None:
    status_code, payload = _request_json(
        "DELETE",
        f"{base_url.rstrip('/')}/api/v1/admin/accounts/{remote_id}",
        headers={"X-API-Key": admin_key},
    )
    if status_code >= 400:
        if isinstance(payload, dict):
            detail = payload.get("message") or payload.get("error")
            if detail:
                raise RuntimeError(f"sub2api 删除失败: {detail}")
        raise RuntimeError(f"sub2api 删除失败: HTTP {status_code}")


def list_remote_chatgpt_accounts_detail(provider: str | None = None) -> dict[str, Any]:
    from core.config_store import config_store

    resolved_provider = provider or _resolve_chatgpt_sync_provider()
    if not resolved_provider:
        return {"provider": "", "emails": set(), "total": 0, "deleted_error_accounts": 0}

    if resolved_provider == "cliproxyapi":
        api_url = str(config_store.get("cpa_api_url", "") or "").strip()
        api_key = str(config_store.get("cliproxyapi_management_key", "") or config_store.get("cpa_api_key", "")).strip()
        if not api_url:
            raise RuntimeError("CPA API URL 未配置")
        if not api_key:
            raise RuntimeError("CPA 管理密钥未配置")

        status_code, payload = _request_json(
            "GET",
            f"{api_url.rstrip('/')}/v0/management/auth-files",
            headers={"Authorization": f"Bearer {api_key}"},
        )
        if status_code >= 400:
            raise RuntimeError(f"cliproxyapi 查询失败: HTTP {status_code}")
        return {
            "provider": resolved_provider,
            "emails": {email for email in (_extract_remote_email(resolved_provider, item) for item in _extract_items(payload)) if email},
            "total": len(_extract_items(payload)),
            "deleted_error_accounts": 0,
        }

    if resolved_provider == "sub2api":
        base_url = str(config_store.get("sub2api_url", "") or "").strip()
        admin_key = str(config_store.get("sub2api_admin_key", "") or "").strip()
        if not base_url:
            raise RuntimeError("sub2api URL 未配置")
        if not admin_key:
            raise RuntimeError("sub2api Admin Key 未配置")

        status_code, payload = _request_json(
            "GET",
            f"{base_url.rstrip('/')}/api/v1/admin/accounts",
            headers={"X-API-Key": admin_key},
            params={"page": 1, "page_size": 1000},
        )
        if status_code >= 400:
            raise RuntimeError(f"sub2api 查询失败: HTTP {status_code}")
        remote_emails: set[str] = set()
        remote_total = 0
        deleted_error_accounts = 0
        for item in _extract_items(payload):
            email = _extract_remote_email(resolved_provider, item)
            if _is_error_status(item):
                remote_id = _extract_remote_id(item)
                if remote_id:
                    _delete_sub2api_account(base_url, admin_key, remote_id)
                    deleted_error_accounts += 1
                    continue
            if email:
                remote_emails.add(email)
            remote_total += 1
        return {
            "provider": resolved_provider,
            "emails": remote_emails,
            "total": remote_total,
            "deleted_error_accounts": deleted_error_accounts,
        }

    if resolved_provider == "team_manager":
        api_url = str(config_store.get("team_manager_url", "") or "").strip()
        api_key = str(config_store.get("team_manager_key", "") or "").strip()
        if not api_url:
            raise RuntimeError("Team Manager API URL 未配置")
        if not api_key:
            raise RuntimeError("Team Manager API Key 未配置")

        status_code, payload = _request_json(
            "GET",
            f"{api_url.rstrip('/')}/api/accounts",
            headers={"X-API-Key": api_key},
            params={"page": 1, "page_size": 1000},
        )
        if status_code >= 400:
            raise RuntimeError(f"Team Manager 查询失败: HTTP {status_code}")
        return {
            "provider": resolved_provider,
            "emails": {email for email in (_extract_remote_email(resolved_provider, item) for item in _extract_items(payload)) if email},
            "total": len(_extract_items(payload)),
            "deleted_error_accounts": 0,
        }

    return {"provider": resolved_provider, "emails": set(), "total": 0, "deleted_error_accounts": 0}


def list_remote_chatgpt_accounts(provider: str | None = None) -> set[str]:
    return set(list_remote_chatgpt_accounts_detail(provider).get("emails") or set())


def sync_account(account, provider: str | None = None) -> list[dict[str, Any]]:
    """根据平台将账号同步到外部系统。"""
    from core.config_store import config_store

    platform = getattr(account, "platform", "")
    results: list[dict[str, Any]] = []

    if platform == "chatgpt":
        from platforms.chatgpt.cpa_upload import (
            generate_token_json,
            upload_to_cpa,
            upload_to_sub2api,
            upload_to_team_manager,
        )

        provider = provider or _resolve_chatgpt_sync_provider()
        a = _build_chatgpt_upload_account(account)
        if provider == "cliproxyapi":
            ok, msg = upload_to_cpa(generate_token_json(a))
            results.append({"name": "CPA", "ok": ok, "msg": msg})
        elif provider == "team_manager":
            ok, msg = upload_to_team_manager(a)
            results.append({"name": "Team Manager", "ok": ok, "msg": msg})
        elif provider == "sub2api":
            ok, msg = upload_to_sub2api(a)
            results.append({"name": "sub2api", "ok": ok, "msg": msg})

    elif platform == "grok":
        grok2api_url = str(config_store.get("grok2api_url", "") or "").strip()
        if grok2api_url:
            from services.grok2api_runtime import ensure_grok2api_ready
            from platforms.grok.grok2api_upload import upload_to_grok2api

            ready, ready_msg = ensure_grok2api_ready()
            if not ready:
                results.append({"name": "grok2api", "ok": False, "msg": ready_msg})
                return results

            ok, msg = upload_to_grok2api(account)
            results.append({"name": "grok2api", "ok": ok, "msg": msg})

    elif platform == "kiro":
        from platforms.kiro.account_manager_upload import resolve_manager_path, upload_to_kiro_manager

        configured_path = str(config_store.get("kiro_manager_path", "") or "").strip()
        target_path = resolve_manager_path(configured_path or None)
        if configured_path or target_path.parent.exists() or target_path.exists():
            ok, msg = upload_to_kiro_manager(account, path=configured_path or None)
            results.append({"name": "Kiro Manager", "ok": ok, "msg": msg})

    return results


def ensure_chatgpt_accounts_synced(accounts: list[Any], provider: str | None = None) -> dict[str, Any]:
    resolved_provider = provider or _resolve_chatgpt_sync_provider()
    chatgpt_accounts = [account for account in accounts if getattr(account, "platform", "") == "chatgpt"]
    if not chatgpt_accounts:
        return {"provider": resolved_provider, "checked": 0, "missing": 0, "synced": 0, "failed": 0}
    if not resolved_provider:
        return {"provider": "", "checked": len(chatgpt_accounts), "missing": 0, "synced": 0, "failed": 0}

    remote_detail = list_remote_chatgpt_accounts_detail(resolved_provider)
    remote_emails = set(remote_detail.get("emails") or set())
    missing_accounts = []
    for account in chatgpt_accounts:
        email = str(getattr(account, "email", "") or "").strip().lower()
        if email and email not in remote_emails:
            missing_accounts.append(account)

    synced = 0
    failed = 0
    for account in missing_accounts:
        results = sync_account(account, provider=resolved_provider)
        ok = any(bool(result.get("ok")) for result in results)
        if ok:
            synced += 1
            remote_emails.add(str(getattr(account, "email", "") or "").strip().lower())
        else:
            failed += 1

    return {
        "provider": resolved_provider,
        "checked": len(chatgpt_accounts),
        "missing": len(missing_accounts),
        "synced": synced,
        "failed": failed,
        "deleted_error_accounts": int(remote_detail.get("deleted_error_accounts") or 0),
    }
