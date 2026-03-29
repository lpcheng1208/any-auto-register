from __future__ import annotations

from typing import Any, Protocol

from curl_cffi import requests as cffi_requests

from core.base_platform import Account, RegisterConfig
from core.config_store import config_store
from core.registry import get, load_all


class SyncAccountLike(Protocol):
    platform: str
    email: str

    def get_extra(self) -> dict[str, Any]: ...

    def set_extra(self, data: dict[str, Any]) -> None: ...


def _get_extra(account: SyncAccountLike) -> dict[str, Any]:
    extra = account.get_extra()
    return extra if isinstance(extra, dict) else {}


def _build_codex_account(account: SyncAccountLike) -> Any:
    extra = _get_extra(account)

    class _Acc:
        pass

    codex_account = _Acc()
    codex_account.email = account.email
    codex_account.access_token = extra.get("access_token") or getattr(account, "token", "")
    codex_account.refresh_token = extra.get("refresh_token", "")
    codex_account.id_token = extra.get("id_token", "")
    codex_account.session_token = extra.get("session_token", "")
    codex_account.client_id = extra.get("client_id", "app_EMoamEEZ73f0CkXaXp7hrann")
    codex_account.cookies = extra.get("cookies", "")
    return codex_account


def _build_platform_account(account: SyncAccountLike) -> Account:
    extra = _get_extra(account)
    return Account(
        platform=getattr(account, "platform", ""),
        email=getattr(account, "email", ""),
        password=getattr(account, "password", ""),
        user_id=getattr(account, "user_id", ""),
        region=getattr(account, "region", ""),
        token=extra.get("access_token") or getattr(account, "token", ""),
        extra=extra,
    )


def _normalize_provider(provider: str | None, account: SyncAccountLike | None = None) -> str:
    raw = (provider or "").strip().lower()
    if not raw and account is not None:
        sync_target = _get_extra(account).get("sync_target") or {}
        if isinstance(sync_target, dict):
            raw = str(sync_target.get("provider") or "").strip().lower()
    if not raw:
        raw = str(config_store.get("chatgpt_sync_target_provider", "") or "").strip().lower()

    if raw in {"cpa", "cliproxyapi"}:
        return "cliproxyapi"
    if raw == "sub2api":
        return "sub2api"

    sub2api_url = str(config_store.get("sub2api_url", "") or "").strip()
    sub2api_admin_key = str(config_store.get("sub2api_admin_key", "") or "").strip()
    if sub2api_url and sub2api_admin_key:
        return "sub2api"

    cpa_url = str(config_store.get("cpa_api_url", "") or "").strip()
    management_key = str(config_store.get("cliproxyapi_management_key", "") or config_store.get("cpa_api_key", "")).strip()
    if cpa_url and management_key:
        return "cliproxyapi"

    return ""


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
    ]
    data = payload.get("data")
    if isinstance(data, dict):
        candidates.extend([data.get("items"), data.get("files"), data.get("accounts")])
    elif isinstance(data, list):
        candidates.append(data)

    for candidate in candidates:
        if isinstance(candidate, list):
            return [item for item in candidate if isinstance(item, dict)]
    return []


def _match_email(item: dict[str, Any], email: str) -> bool:
    item_email = str(item.get("email") or item.get("username") or item.get("name") or "").strip().lower()
    target_email = email.strip().lower()
    return bool(item_email) and item_email == target_email


def _build_cliproxy_snapshot(account: SyncAccountLike, item: dict[str, Any] | None = None, *, exists: bool) -> dict[str, Any]:
    remote_name = f"{account.email}.json"
    remote_status = "active" if exists else "deleted"
    snapshot: dict[str, Any] = {
        "provider": "cliproxyapi",
        "remote_name": remote_name,
        "remote_status": remote_status,
        "exists": exists,
    }
    if item:
        snapshot["remote_name"] = str(item.get("name") or item.get("file_name") or remote_name)
        snapshot["remote_status"] = str(item.get("status") or item.get("state") or remote_status)
        remote_id = item.get("id")
        if remote_id not in (None, ""):
            snapshot["remote_id"] = str(remote_id)
    return snapshot


def _build_sub2api_snapshot(item: dict[str, Any] | None, *, exists: bool) -> dict[str, Any]:
    snapshot: dict[str, Any] = {
        "provider": "sub2api",
        "exists": exists,
        "remote_status": "active" if exists else "deleted",
    }
    if not item:
        return snapshot

    remote_id = item.get("id")
    if remote_id not in (None, ""):
        snapshot["remote_id"] = str(remote_id)

    email = item.get("email") or item.get("username")
    if email:
        snapshot["email"] = str(email)

    status = item.get("status") or item.get("state") or item.get("account_status")
    if status not in (None, ""):
        snapshot["remote_status"] = str(status)

    return snapshot


def _find_sub2api_account(base_url: str, admin_key: str, email: str) -> dict[str, Any] | None:
    headers = {"X-API-Key": admin_key}
    status_code, payload = _request_json(
        "GET",
        f"{base_url.rstrip('/')}/api/v1/admin/accounts",
        headers=headers,
        params={"search": email, "page": 1, "page_size": 100},
    )
    if status_code >= 400:
        raise RuntimeError(f"sub2api 查询失败: HTTP {status_code}")

    for item in _extract_items(payload):
        if _match_email(item, email):
            return item
    return None


def apply_sync_target_snapshot(account: SyncAccountLike, snapshot: dict[str, Any]) -> dict[str, Any]:
    extra = _get_extra(account)
    extra["sync_target"] = dict(snapshot)
    account.set_extra(extra)
    return extra["sync_target"]


def check_account_validity(account: SyncAccountLike) -> bool:
    if getattr(account, "platform", "") != "chatgpt":
        return False

    load_all()
    platform_cls = get("chatgpt")
    plugin = platform_cls(config=RegisterConfig())
    return bool(plugin.check_valid(_build_platform_account(account)))


def _build_unconfirmed_target_result(account: SyncAccountLike, provider: str | None = None, *, message: str = "未执行") -> dict[str, Any]:
    return {
        "provider": _normalize_provider(provider, account),
        "exists": None,
        "confirmed": False,
        "message": message,
        "snapshot": _get_extra(account).get("sync_target") or {},
    }


def refresh_chatgpt_target_state(account: SyncAccountLike, provider: str | None = None) -> dict[str, Any]:
    if getattr(account, "platform", "") != "chatgpt":
        snapshot = {
            "provider": _normalize_provider(provider, account),
            "exists": False,
            "remote_status": "unsupported",
        }
        return {"provider": snapshot["provider"], "exists": False, "confirmed": True, "message": "仅支持 chatgpt 账号", "snapshot": snapshot}

    resolved_provider = _normalize_provider(provider, account)
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

        for item in _extract_items(payload):
            if str(item.get("name") or "") == f"{account.email}.json":
                snapshot = _build_cliproxy_snapshot(account, item, exists=True)
                return {
                    "provider": resolved_provider,
                    "exists": True,
                    "confirmed": True,
                    "message": "账号存在于 cliproxyapi",
                    "snapshot": snapshot,
                }

        snapshot = _build_cliproxy_snapshot(account, exists=False)
        return {
            "provider": resolved_provider,
            "exists": False,
            "confirmed": True,
            "message": "cliproxyapi 中未找到账号",
            "snapshot": snapshot,
        }

    if resolved_provider == "sub2api":
        base_url = str(config_store.get("sub2api_url", "") or "").strip()
        admin_key = str(config_store.get("sub2api_admin_key", "") or "").strip()
        if not base_url:
            raise RuntimeError("sub2api URL 未配置")
        if not admin_key:
            raise RuntimeError("sub2api Admin Key 未配置")

        account_item = _find_sub2api_account(base_url, admin_key, account.email)
        if not account_item:
            snapshot = _build_sub2api_snapshot(None, exists=False)
            return {
                "provider": resolved_provider,
                "exists": False,
                "confirmed": True,
                "message": "sub2api 中未找到账号",
                "snapshot": snapshot,
            }

        remote_id = account_item.get("id")
        if remote_id not in (None, ""):
            _request_json(
                "POST",
                f"{base_url.rstrip('/')}/api/v1/admin/accounts/{remote_id}/refresh",
                headers={"X-API-Key": admin_key},
                json_body={},
            )
            refreshed_item = _find_sub2api_account(base_url, admin_key, account.email)
            if refreshed_item:
                account_item = refreshed_item

        snapshot = _build_sub2api_snapshot(account_item, exists=True)
        return {
            "provider": resolved_provider,
            "exists": True,
            "confirmed": True,
            "message": "账号存在于 sub2api",
            "snapshot": snapshot,
        }

    snapshot = {"provider": resolved_provider, "exists": False, "remote_status": "unsupported"}
    return {
        "provider": resolved_provider,
        "exists": False,
        "confirmed": False,
        "message": "未配置可用的同步目标",
        "snapshot": snapshot,
    }


def _should_delete_local_from_target_result(target_result: dict[str, Any]) -> bool:
    if not target_result or not target_result.get("confirmed"):
        return False
    if target_result.get("exists") is False:
        return True

    snapshot = target_result.get("snapshot")
    if not isinstance(snapshot, dict):
        return False

    remote_status = str(snapshot.get("remote_status") or "").strip().lower()
    return remote_status in {"deleted", "invalid", "inactive", "disabled", "not_found"}


def remove_chatgpt_account_from_target(account: SyncAccountLike, provider: str | None = None) -> dict[str, Any]:
    resolved_provider = _normalize_provider(provider, account)

    if resolved_provider == "cliproxyapi":
        api_url = str(config_store.get("cpa_api_url", "") or "").strip()
        api_key = str(config_store.get("cliproxyapi_management_key", "") or config_store.get("cpa_api_key", "")).strip()
        if not api_url:
            raise RuntimeError("CPA API URL 未配置")
        if not api_key:
            raise RuntimeError("CPA 管理密钥未配置")

        snapshot = _build_cliproxy_snapshot(account, exists=False)
        remote_name = str((_get_extra(account).get("sync_target") or {}).get("remote_name") or snapshot["remote_name"])
        status_code, payload = _request_json(
            "DELETE",
            f"{api_url.rstrip('/')}/v0/management/auth-files",
            headers={"Authorization": f"Bearer {api_key}"},
            params={"name": remote_name},
        )
        if status_code >= 400 and status_code != 404:
            raise RuntimeError(f"cliproxyapi 删除失败: HTTP {status_code}")

        message = "目标侧账号已删除"
        if isinstance(payload, dict):
            message = str(payload.get("message") or payload.get("status") or message)
        return {
            "provider": resolved_provider,
            "exists": False,
            "confirmed": True,
            "message": message,
            "snapshot": snapshot,
        }

    if resolved_provider == "sub2api":
        base_url = str(config_store.get("sub2api_url", "") or "").strip()
        admin_key = str(config_store.get("sub2api_admin_key", "") or "").strip()
        if not base_url:
            raise RuntimeError("sub2api URL 未配置")
        if not admin_key:
            raise RuntimeError("sub2api Admin Key 未配置")

        account_item = _find_sub2api_account(base_url, admin_key, account.email)
        snapshot = _build_sub2api_snapshot(account_item, exists=False)
        snapshot["remote_status"] = "deleted"
        if account_item is None:
            return {
                "provider": resolved_provider,
                "exists": False,
                "confirmed": True,
                "message": "sub2api 中未找到账号",
                "snapshot": snapshot,
            }

        remote_id = account_item.get("id")
        status_code, _payload = _request_json(
            "DELETE",
            f"{base_url.rstrip('/')}/api/v1/admin/accounts/{remote_id}",
            headers={"X-API-Key": admin_key},
        )
        if status_code >= 400 and status_code != 404:
            raise RuntimeError(f"sub2api 删除失败: HTTP {status_code}")
        return {
            "provider": resolved_provider,
            "exists": False,
            "confirmed": True,
            "message": "目标侧账号已删除",
            "snapshot": snapshot,
        }

    snapshot = {"provider": resolved_provider, "exists": False, "remote_status": "unsupported"}
    return {
        "provider": resolved_provider,
        "exists": False,
        "confirmed": False,
        "message": "未配置可用的同步目标",
        "snapshot": snapshot,
    }


def sync_and_cleanup_account(
    account: SyncAccountLike,
    *,
    delete_invalid: bool = True,
    refresh_target: bool = True,
    provider: str | None = None,
) -> dict[str, Any]:
    valid = check_account_validity(account)
    target_result = _build_unconfirmed_target_result(account, provider)

    if refresh_target:
        if valid:
            target_result = refresh_chatgpt_target_state(account, provider=provider)
        elif delete_invalid:
            target_result = remove_chatgpt_account_from_target(account, provider=provider)

    snapshot = target_result.get("snapshot")
    if isinstance(snapshot, dict) and snapshot:
        apply_sync_target_snapshot(account, snapshot)

    delete_local = delete_invalid and (not valid) and _should_delete_local_from_target_result(target_result)
    return {
        "valid": valid,
        "delete_local": delete_local,
        "target": target_result,
    }
