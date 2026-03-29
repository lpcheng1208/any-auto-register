"""
CPA (Codex Protocol API) 上传功能
"""

import json
import base64
import logging
from typing import Any, Tuple
from datetime import datetime, timezone, timedelta

from curl_cffi import requests as cffi_requests
from curl_cffi import CurlMime

logger = logging.getLogger(__name__)

SUB2API_DEFAULT_CONCURRENCY = 2
SUB2API_DEFAULT_LOAD_FACTOR = 1
SUB2API_DEFAULT_PRIORITY = 1
SUB2API_DEFAULT_RATE_MULTIPLIER = 1.0
SUB2API_DEFAULT_MODEL_MAPPING = {
    "claude-opus-4-6": "gpt-5.4",
    "claude-opus-4-6-thinking": "gpt-5.4",
    "claude-opus-4-5": "gpt-5.4",
    "claude-opus-4-5-20251101": "gpt-5.4",
    "claude-opus-4-5-thinking": "gpt-5.4",
    "claude-sonnet-4-6": "gpt-5.4",
    "claude-sonnet-4-6-thinking": "gpt-5.4",
    "claude-sonnet-4-5": "gpt-5.4",
    "claude-sonnet-4-5-20250929": "gpt-5.4",
    "claude-sonnet-4-5-thinking": "gpt-5.4",
    "claude-haiku-4-5": "gpt-5.4",
    "claude-haiku-4-5-20251001": "gpt-5.4",
}


def _decode_jwt_payload(token: str) -> dict:
    try:
        parts = token.split(".")
        if len(parts) < 2:
            return {}
        payload = parts[1]
        padding = 4 - len(payload) % 4
        if padding != 4:
            payload += "=" * padding
        decoded = base64.urlsafe_b64decode(payload)
        return json.loads(decoded)
    except Exception:
        return {}


def _get_config_value(key: str) -> str:
    try:
        from core.config_store import config_store
        return config_store.get(key, "")
    except Exception:
        return ""


def generate_token_json(account) -> dict:
    """
    生成 CPA 格式的 Token JSON。
    接受任意 duck-typed 对象（需有 email, access_token, refresh_token 属性），
    expired / account_id 从 JWT 自动解码，与 chatgpt_register 逻辑一致。
    """
    email = getattr(account, "email", "")
    access_token = getattr(account, "access_token", "")
    refresh_token = getattr(account, "refresh_token", "")
    id_token = getattr(account, "id_token", "")

    expired_str = ""
    account_id = ""
    if access_token:
        payload = _decode_jwt_payload(access_token)
        auth_info = payload.get("https://api.openai.com/auth", {})
        account_id = auth_info.get("chatgpt_account_id", "")
        exp_timestamp = payload.get("exp")
        if isinstance(exp_timestamp, int) and exp_timestamp > 0:
            exp_dt = datetime.fromtimestamp(
                exp_timestamp, tz=timezone(timedelta(hours=8)))
            expired_str = exp_dt.strftime("%Y-%m-%dT%H:%M:%S+08:00")

    now = datetime.now(tz=timezone(timedelta(hours=8)))
    return {
        "type": "codex",
        "email": email,
        "expired": expired_str,
        "id_token": id_token,
        "account_id": account_id,
        "access_token": access_token,
        "last_refresh": now.strftime("%Y-%m-%dT%H:%M:%S+08:00"),
        "refresh_token": refresh_token,
    }


def upload_to_cpa(
    token_data: dict,
    api_url: str = None,
    api_key: str = None,
    proxy: str = None,
) -> Tuple[bool, str]:
    """上传单个账号到 CPA 管理平台（不走代理）。
    api_url / api_key 为空时自动从 ConfigStore 读取。"""
    if not api_url:
        api_url = _get_config_value("cpa_api_url")
    if not api_key:
        api_key = _get_config_value("cliproxyapi_management_key") or _get_config_value("cpa_api_key")
    if not api_url:
        return False, "CPA API URL 未配置"
    if not api_key:
        return False, "CPA 管理密钥未配置"

    upload_url = f"{api_url.rstrip('/')}/v0/management/auth-files"

    filename = f"{token_data['email']}.json"
    file_content = json.dumps(token_data, ensure_ascii=False, indent=2).encode("utf-8")

    headers = {
        "Authorization": f"Bearer {api_key}",
    }

    mime = None
    try:
        mime = CurlMime()
        mime.addpart(
            name="file",
            data=file_content,
            filename=filename,
            content_type="application/json",
        )

        response = cffi_requests.post(
            upload_url,
            multipart=mime,
            headers=headers,
            proxies=None,
            verify=False,
            timeout=30,
            impersonate="chrome110",
        )

        if response.status_code in (200, 201):
            return True, "上传成功"

        error_msg = f"上传失败: HTTP {response.status_code}"
        try:
            error_detail = response.json()
            if isinstance(error_detail, dict):
                error_msg = error_detail.get("message") or error_detail.get("error") or error_msg
        except Exception:
            error_msg = f"{error_msg} - {response.text[:200]}"
        return False, error_msg

    except Exception as e:
        logger.error(f"CPA 上传异常: {e}")
        return False, f"上传异常: {str(e)}"
    finally:
        if mime:
            mime.close()


def upload_to_team_manager(
    account,
    api_url: str = None,
    api_key: str = None,
) -> Tuple[bool, str]:
    """上传单账号到 Team Manager（直连，不走代理）。
    api_url / api_key 为空时自动从 ConfigStore 读取。"""
    if not api_url:
        api_url = _get_config_value("team_manager_url")
    if not api_key:
        api_key = _get_config_value("team_manager_key")
    if not api_url:
        return False, "Team Manager API URL 未配置"
    if not api_key:
        return False, "Team Manager API Key 未配置"

    email = getattr(account, "email", "")
    access_token = getattr(account, "access_token", "")
    if not access_token:
        return False, "账号缺少 access_token"

    url = api_url.rstrip("/") + "/api/accounts/import"
    headers = {
        "X-API-Key": api_key,
        "Content-Type": "application/json",
    }
    payload = {
        "import_type": "single",
        "email": email,
        "access_token": access_token,
        "session_token": getattr(account, "session_token", ""),
        "refresh_token": getattr(account, "refresh_token", ""),
        "client_id": getattr(account, "client_id", ""),
    }

    try:
        resp = cffi_requests.post(
            url,
            headers=headers,
            json=payload,
            proxies=None,
            verify=False,
            timeout=30,
            impersonate="chrome110",
        )
        if resp.status_code in (200, 201):
            return True, "上传成功"
        error_msg = f"上传失败: HTTP {resp.status_code}"
        try:
            detail = resp.json()
            if isinstance(detail, dict):
                error_msg = detail.get("message", error_msg)
        except Exception:
            error_msg = f"{error_msg} - {resp.text[:200]}"
        return False, error_msg
    except Exception as e:
        logger.error(f"Team Manager 上传异常: {e}")
        return False, f"上传异常: {str(e)}"


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
        payload.get("accounts"),
    ]
    data = payload.get("data")
    if isinstance(data, dict):
        candidates.extend([data.get("items"), data.get("accounts")])
    elif isinstance(data, list):
        candidates.append(data)

    for candidate in candidates:
        if isinstance(candidate, list):
            return [item for item in candidate if isinstance(item, dict)]
    return []


def _match_account_email(item: dict[str, Any], email: str) -> bool:
    item_email = str(item.get("email") or item.get("username") or item.get("name") or "").strip().lower()
    return bool(item_email) and item_email == email.strip().lower()


def _build_sub2api_credentials(
    *,
    access_token: str,
    refresh_token: str,
    session_token: str,
    id_token: str,
    client_id: str,
    cookies: str,
) -> dict[str, Any]:
    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "session_token": session_token,
        "id_token": id_token,
        "client_id": client_id,
        "cookies": cookies,
        "model_mapping": dict(SUB2API_DEFAULT_MODEL_MAPPING),
    }


def _build_sub2api_account_payload(
    *,
    email: str,
    credentials: dict[str, Any],
    group_ids: list[int],
    include_platform: bool,
    proxy_id: int | None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "name": email,
        "type": "oauth",
        "credentials": credentials,
        "proxy_id": proxy_id,
        "concurrency": SUB2API_DEFAULT_CONCURRENCY,
        "priority": SUB2API_DEFAULT_PRIORITY,
        "rate_multiplier": SUB2API_DEFAULT_RATE_MULTIPLIER,
        "load_factor": SUB2API_DEFAULT_LOAD_FACTOR,
        "group_ids": group_ids,
    }
    if include_platform:
        payload["platform"] = "openai"
    return payload


def _list_active_sub2api_group_ids(base_url: str, admin_key: str) -> list[int]:
    status_code, payload = _request_json(
        "GET",
        f"{base_url.rstrip('/')}/api/v1/admin/groups/all",
        headers={"X-API-Key": admin_key},
        params={"platform": "openai"},
    )
    if status_code >= 400:
        raise RuntimeError(f"sub2api 分组查询失败: HTTP {status_code}")

    group_ids: list[int] = []
    for item in _extract_items(payload):
        status = str(item.get("status") or "active").strip().lower()
        if status and status != "active":
            continue
        group_id = item.get("id")
        if isinstance(group_id, int):
            group_ids.append(group_id)
        elif isinstance(group_id, str) and group_id.isdigit():
            group_ids.append(int(group_id))
    return group_ids


def upload_to_sub2api(
    account,
    base_url: str = None,
    admin_key: str = None,
) -> Tuple[bool, str]:
    """上传单账号到 sub2api 管理接口。
    已存在账号时更新凭证，不存在时创建。"""
    if not base_url:
        base_url = _get_config_value("sub2api_url")
    if not admin_key:
        admin_key = _get_config_value("sub2api_admin_key")
    if not base_url:
        return False, "sub2api URL 未配置"
    if not admin_key:
        return False, "sub2api Admin Key 未配置"

    email = getattr(account, "email", "")
    access_token = getattr(account, "access_token", "")
    refresh_token = getattr(account, "refresh_token", "")
    session_token = getattr(account, "session_token", "")
    id_token = getattr(account, "id_token", "")
    client_id = getattr(account, "client_id", "app_EMoamEEZ73f0CkXaXp7hrann")
    cookies = getattr(account, "cookies", "")

    if not email:
        return False, "账号缺少 email"
    if not access_token:
        return False, "账号缺少 access_token"

    headers = {"X-API-Key": admin_key}
    group_ids = _list_active_sub2api_group_ids(base_url, admin_key)
    credentials = _build_sub2api_credentials(
        access_token=access_token,
        refresh_token=refresh_token,
        session_token=session_token,
        id_token=id_token,
        client_id=client_id,
        cookies=cookies,
    )

    try:
        status_code, payload = _request_json(
            "GET",
            f"{base_url.rstrip('/')}/api/v1/admin/accounts",
            headers=headers,
            params={"search": email, "page": 1, "page_size": 100},
        )
        if status_code >= 400:
            return False, f"sub2api 查询失败: HTTP {status_code}"

        existing = next((item for item in _extract_items(payload) if _match_account_email(item, email)), None)
        if existing:
            remote_id = existing.get("id")
            status_code, update_payload = _request_json(
                "PUT",
                f"{base_url.rstrip('/')}/api/v1/admin/accounts/{remote_id}",
                headers=headers,
                json_body=_build_sub2api_account_payload(
                    email=email,
                    credentials=credentials,
                    group_ids=group_ids,
                    include_platform=False,
                    proxy_id=0,
                ),
            )
            if status_code in (200, 201):
                return True, "同步成功（已更新到 sub2api）"
            if isinstance(update_payload, dict):
                return False, str(update_payload.get("message") or update_payload.get("error") or f"sub2api 更新失败: HTTP {status_code}")
            return False, f"sub2api 更新失败: HTTP {status_code}"

        status_code, create_payload = _request_json(
            "POST",
            f"{base_url.rstrip('/')}/api/v1/admin/accounts",
            headers=headers,
            json_body=_build_sub2api_account_payload(
                email=email,
                credentials=credentials,
                group_ids=group_ids,
                include_platform=True,
                proxy_id=None,
            ),
        )
        if status_code in (200, 201):
            return True, "同步成功（已创建到 sub2api）"
        if isinstance(create_payload, dict):
            return False, str(create_payload.get("message") or create_payload.get("error") or f"sub2api 创建失败: HTTP {status_code}")
        return False, f"sub2api 创建失败: HTTP {status_code}"
    except Exception as e:
        logger.error(f"sub2api 上传异常: {e}")
        return False, f"上传异常: {str(e)}"


def test_cpa_connection(api_url: str, api_token: str, proxy: str = None) -> Tuple[bool, str]:
    """测试 CPA 连接（不走代理）"""
    if not api_url:
        return False, "API URL 不能为空"
    if not api_token:
        return False, "API Token 不能为空"

    api_url = api_url.rstrip("/")
    test_url = f"{api_url}/v0/management/auth-files"
    headers = {"Authorization": f"Bearer {api_token}"}

    try:
        response = cffi_requests.options(
            test_url,
            headers=headers,
            proxies=None,
            verify=False,
            timeout=10,
            impersonate="chrome110",
        )

        if response.status_code in (200, 204, 401, 403, 405):
            if response.status_code == 401:
                return False, "连接成功，但 API Token 无效"
            return True, "CPA 连接测试成功"

        return False, f"服务器返回异常状态码: {response.status_code}"

    except cffi_requests.exceptions.ConnectionError as e:
        return False, f"无法连接到服务器: {str(e)}"
    except cffi_requests.exceptions.Timeout:
        return False, "连接超时，请检查网络配置"
    except Exception as e:
        return False, f"连接测试失败: {str(e)}"
