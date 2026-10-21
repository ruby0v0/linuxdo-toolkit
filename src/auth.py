"""认证模块：Cookie 解析 + 登录链（Cookie → 账号密码降级）+ 登录验证（BR-1）。

登录成功判定：GET /session/current.json 返回 200 且含 current_user.id。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from loguru import logger

from src.config import Settings
from src.http_client import HttpClient
from src.retry import with_retry

BASE_URL = "https://linux.do"

# 账号密码登录成功后，LinuxDo（Discourse）返回的 Cookie 名，用于注入浏览器
_SESSION_COOKIE_KEYS = ("_t", "_forum_session")


class AuthError(Exception):
    """登录失败。"""


@dataclass(frozen=True)
class AuthResult:
    """认证结果。"""

    method: str  # "cookie" | "password"
    username: str | None
    cookies: dict[str, str]  # 已验证的会话 Cookie（用于注入浏览器）


def parse_cookies(raw: str) -> dict[str, str]:
    """解析浏览器复制的 Cookie 字符串（``k1=v1; k2=v2``）。

    容忍空段与无 ``=`` 的段（跳过并记录 warning）。
    """
    cookies: dict[str, str] = {}
    for part in raw.split(";"):
        part = part.strip()
        if not part:
            continue
        if "=" not in part:
            logger.warning("忽略无法解析的 Cookie 片段：{!r}", part[:20])
            continue
        name, _, value = part.partition("=")
        name, value = name.strip(), value.strip()
        if not name:
            logger.warning("忽略无名称的 Cookie 片段：{!r}", part[:20])
            continue
        cookies[name] = value
    return cookies


def mask_cookie(raw: str) -> str:
    """日志用脱敏：仅显示首尾片段。"""
    if len(raw) <= 12:
        return "***"
    return f"{raw[:6]}***{raw[-3:]}"


def verify_login(http: HttpClient) -> dict[str, Any]:
    """验证当前会话是否已登录，返回 current_user 信息，失败抛 AuthError。"""
    try:
        resp = http.get(f"{BASE_URL}/session/current.json")
    except Exception as exc:
        raise AuthError(f"请求 current-user 接口失败：{exc}") from exc
    if resp.status_code != 200:
        raise AuthError(f"未登录（current.json 返回 {resp.status_code}）")
    try:
        data = resp.json()
    except Exception as exc:
        raise AuthError("current.json 响应不是有效 JSON") from exc
    user = data.get("current_user")
    if not user or not user.get("id"):
        raise AuthError("current.json 中无有效 current_user")
    logger.info("登录验证通过：用户 {}（id={}）", user.get("username"), user.get("id"))
    return user


def _login_with_cookies(settings: Settings, http: HttpClient) -> AuthResult:
    """Cookie 方式：解析 → 注入客户端 → 请求 current.json 验证。"""
    raw = settings.linuxdo_cookies or ""
    logger.info("尝试 Cookie 登录（{}）", mask_cookie(raw))
    cookies = parse_cookies(raw)
    if not cookies:
        raise AuthError("Cookie 字符串解析结果为空")
    # 关键：把 Cookie 挂到客户端 cookie jar 上再验证（否则验证请求不带凭据）
    http.set_cookies(cookies)
    user = verify_login(http)
    return AuthResult(
        method="cookie", username=user.get("username"), cookies=http_cookies(http)
    )


def http_cookies(http: HttpClient) -> dict[str, str]:
    """从客户端 cookie jar 提取全部 Cookie（注入浏览器用）。

    客户端未暴露 jar（如测试桩）时返回空 dict。
    """
    jar = getattr(http, "_session", None) or getattr(http, "_client", None)
    if jar is None:
        return {}
    return {c.name: c.value for c in jar.cookies if c.value is not None}  # type: ignore[union-attr]


def _login_with_password(settings: Settings, http: HttpClient) -> AuthResult:
    """账号密码方式：GET /session/csrf → POST /session → 验证。"""
    assert settings.linuxdo_username and settings.linuxdo_password
    logger.info("尝试账号密码登录（用户：{}）", settings.linuxdo_username)

    def _get_csrf() -> str:
        resp = http.get(f"{BASE_URL}/session/csrf")
        if resp.status_code != 200:
            raise AuthError(f"获取 CSRF token 失败（{resp.status_code}）")
        token = resp.json().get("csrf")
        if not token:
            raise AuthError("CSRF 接口未返回 token")
        return token

    csrf = with_retry(_get_csrf, what="获取 CSRF token")

    payload = {
        "login": settings.linuxdo_username,
        "password": settings.linuxdo_password,
        "second_factor_method": "1",
        "timezone": "Asia/Shanghai",
    }
    headers = {
        "X-CSRF-Token": csrf,
        "X-Requested-With": "XMLHttpRequest",
        "Referer": f"{BASE_URL}/login",
    }

    def _do_login() -> dict[str, Any]:
        resp = http.post(f"{BASE_URL}/session", json=payload, headers=headers)
        data: dict[str, Any] = {}
        try:
            data = resp.json()
        except Exception:
            pass
        if resp.status_code not in (200, 201):
            error = data.get("error") or data.get("message") or resp.text[:120]
            # 登录接口失败（凭据错误/需要二次验证）不重试，直接抛
            raise AuthError(f"POST /session 失败（{resp.status_code}）：{error}")
        if not data.get("user"):
            raise AuthError(f"POST /session 响应缺少 user 字段：{str(data)[:120]}")
        return data

    data = with_retry(
        _do_login,
        what="账号密码登录",
        no_retry=(AuthError,),  # 凭据错误等永久性失败不重试
    )
    logger.info("账号密码登录成功：用户 {}", data.get("user", {}).get("username"))

    user = verify_login(http)
    return AuthResult(method="password", username=user.get("username"), cookies=http_cookies(http))


def login(settings: Settings, http: HttpClient) -> AuthResult:
    """登录主链（BR-1 优先级）：Cookie → 账号密码 → AuthError。

    Cookie 存在但验证失败时，若提供了账号密码则自动降级重试（边界条件 1）。
    """
    if settings.has_cookies:
        try:
            result = with_retry(
                lambda: _login_with_cookies(settings, http),
                what="Cookie 登录",
                exceptions=(AuthError,),
            )
            logger.info("Cookie 登录成功，跳过账号密码流程")
            return result
        except AuthError as exc:
            logger.warning("Cookie 登录失败：{}", exc)
            if not settings.has_credentials:
                raise AuthError(
                    f"Cookie 无效且未提供账号密码（LINUXDO_USERNAME/LINUXDO_PASSWORD）：{exc}"
                ) from exc
            logger.info("降级为账号密码登录")

    return _login_with_password(settings, http)
