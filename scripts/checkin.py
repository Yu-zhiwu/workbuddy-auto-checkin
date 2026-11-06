#!/usr/bin/env python3
"""WorkBuddy 每日签到（云端）：从环境变量读登录态，调用官方接口领取积分。"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import math
import os
import sys
import time
import urllib.error
import urllib.request
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Any

ALLOWED_API_BASES = (
    "https://www.codebuddy.cn",
    "https://copilot.tencent.com",
)
DEFAULT_DOMAIN = "www.codebuddy.cn"
STATUS_PATHS = (
    "/v2/billing/meter/checkin-activity-status",
    "/v2/billing/meter/checkin-status",
)
CLAIM_PATH = "/v2/billing/meter/daily-checkin"
REFRESH_PATH = "/v2/plugin/auth/token/refresh"
EXPECTED_DAILY_CREDIT = 100
USER_RESOURCE_PATH = "/v2/billing/meter/get-user-resource"
WALLET_PAGE_SIZE = 100
WALLET_PRODUCT_CODE = "p_tcaca"
WALLET_LEDGER_KEEP = 120
BEIJING = timezone(timedelta(hours=8))
GROWTH_PREFIX = "/v2/activity/growth"
# 连登兑换档位。tier 必须是 "7d"/"14d"/"28d"，状态字段来自 /redeem/summary。
REDEEM_TIERS = (
    ("7d", "starter", "入门7天"),
    ("14d", "advanced", "进阶14天"),
    ("28d", "legendary", "巅峰28天"),
)
REDEEM_FALLBACK = {
    "7d": "+2 能量 +1 补登卡 +1 次抽奖",
    "14d": "+50 积分 +3 能量 +1 补登卡 +1 次抽奖",
    "28d": "+150 积分 +5 能量 +1 补登卡 +1 次抽奖",
}
MAKEUP_MAX_PER_RUN = 1
REPORT_PATH = "/v2/report"
CHAT_READ_LIMIT = 8192
ACTIVITY_CHAT_MODELS = ("deepseek-v4-flash", "hunyuan-chat")
# 一条没有正文的埋点会把当天记成 2 分，网页活跃地图显示「轻轻路过」。
PASSBY_SCORE = 2
ACTIVITY_PROMPT = "你好，请只用一句话回答我。"
ACTIVITY_MAX_TOKENS = 64
CHAT_COMPLETION_PATH = "/v2/chat/completions"
WEB_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
ALREADY_CODES = {10001, "10001"}
ALREADY_HINTS = ("已签到", "今天已签", "今日已签", "请明天再来")
# 与桌面端同一套客户端标识。加油站页面按这组请求写入「今日已领取」和本周打卡。
CLIENT_VERSION = "5.8.3"
USER_AGENT = f"WorkBuddy/{CLIENT_VERSION}"
TIMEOUT_SECONDS = 20

EXIT_OK = 0
EXIT_USAGE = 1
EXIT_NO_SESSION = 2
EXIT_NETWORK = 3
EXIT_BUSINESS = 4


class CheckinError(Exception):
    def __init__(
        self,
        message: str,
        exit_code: int = EXIT_BUSINESS,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.exit_code = exit_code
        self.details = details


def _env(name: str) -> str:
    return os.environ.get(name, "").strip()


def _unwrap_token(value: str) -> str:
    text = value.strip().lstrip("\ufeff").strip().strip('"').strip("'").strip()
    if text.lower().startswith("bearer "):
        text = text[7:].strip()
    return text


def _is_encrypted_token(value: Any) -> bool:
    if isinstance(value, dict):
        return "$wbEncrypted" in value or "envelope" in value
    if isinstance(value, str):
        text = value.strip()
        return text.startswith("{") and "$wbEncrypted" in text
    return False


def _string_or_blank(value: Any) -> str:
    if isinstance(value, str):
        return _unwrap_token(value)
    return ""


def interpret_login_secret(raw: str) -> dict[str, str]:
    """从 Secret 里取出 accessToken / refreshToken。支持单行令牌，也支持整份登录 JSON。"""
    text = raw.strip().lstrip("\ufeff").strip()
    if text.startswith("{"):
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, dict):
            return _from_login_json(parsed)
    if _is_encrypted_token(text):
        return {"access_token": "", "refresh_token": "", "uid": "", "domain": "", "kind": "encrypted"}
    token = _unwrap_token(text)
    return {
        "access_token": token,
        "refresh_token": token,
        "uid": "",
        "domain": "",
        "kind": "single",
    }


def _from_login_json(parsed: dict[str, Any]) -> dict[str, str]:
    auth = parsed.get("auth") if isinstance(parsed.get("auth"), dict) else {}
    account = parsed.get("account") if isinstance(parsed.get("account"), dict) else {}
    access = auth.get("accessToken", parsed.get("accessToken", parsed.get("access_token")))
    refresh = auth.get("refreshToken", parsed.get("refreshToken", parsed.get("refresh_token")))
    access_token = "" if _is_encrypted_token(access) else _string_or_blank(access)
    refresh_token = "" if _is_encrypted_token(refresh) else _string_or_blank(refresh)
    uid = _string_or_blank(account.get("uid", parsed.get("uid")))
    domain = _string_or_blank(auth.get("domain", parsed.get("domain")))
    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "uid": uid,
        "domain": domain,
        "kind": "json",
    }


def load_creds() -> dict[str, str]:
    token = _env("WORKBUDDY_ACCESS_TOKEN")
    if not token:
        raise CheckinError(
            "缺少 WORKBUDDY_ACCESS_TOKEN。请在仓库 Settings → Secrets 中配置。",
            EXIT_USAGE,
        )
    parsed = interpret_login_secret(token)
    if not parsed["access_token"] and not parsed["refresh_token"]:
        raise CheckinError(
            "WORKBUDDY_ACCESS_TOKEN 不是可用的登录态。加密信封不能直接当 token。请贴 accessToken、refreshToken，或整份未加密的登录 JSON。",
            EXIT_USAGE,
        )
    domain = _env("WORKBUDDY_DOMAIN") or parsed["domain"] or DEFAULT_DOMAIN
    refresh_token = _env("WORKBUDDY_REFRESH_TOKEN") or parsed["refresh_token"]
    return {
        "access_token": parsed["access_token"],
        "refresh_token": refresh_token,
        "uid": _env("WORKBUDDY_UID") or parsed["uid"],
        "domain": domain,
        "enterprise_id": _env("WORKBUDDY_ENTERPRISE_ID"),
    }


def _redact(text: str, creds: dict[str, str]) -> str:
    cleaned = text
    for key in ("access_token", "refresh_token", "uid", "enterprise_id"):
        secret = creds.get(key) or ""
        if len(secret) >= 8:
            cleaned = cleaned.replace(secret, "[secret]")
    return cleaned


def logged_message(body: dict[str, Any], creds: dict[str, str]) -> str:
    """写进日志前去掉 token、uid 和企业 ID。业务判断仍用原文。"""
    return _redact(message_of(body), creds)


def _safe_api_detail(status: int, body: dict[str, Any], creds: dict[str, str]) -> str:
    raw_msg = message_of(body)
    lowered = raw_msg.lower()
    if "invalid_token" in lowered or "invalid token" in lowered or "authorization required" in lowered:
        return f"HTTP {status} invalid_token"
    msg = _redact(raw_msg, creds).strip()
    code = body.get("code")
    if code not in {None, ""}:
        return f"HTTP {status} code={code} {msg[:120]}".strip()
    if msg and not msg.startswith("<") and not msg.startswith("{"):
        return f"HTTP {status} {msg[:120]}"
    return f"HTTP {status}"


def _jwt_payload(token: str) -> dict[str, Any]:
    parts = token.split(".")
    if len(parts) != 3:
        return {}
    payload = parts[1]
    pad = "=" * (-len(payload) % 4)
    try:
        data = json.loads(base64.urlsafe_b64decode(payload + pad))
    except (json.JSONDecodeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _jwt_exp(token: str) -> int | None:
    data = _jwt_payload(token)
    exp = data.get("exp")
    if isinstance(exp, bool) or not isinstance(exp, (int, float)):
        return None
    return int(exp)


def _jwt_expiry_text(token: str) -> str:
    exp = _jwt_exp(token)
    if exp is None:
        return ""
    when = datetime.fromtimestamp(exp, BEIJING)
    stamp = when.strftime("%Y-%m-%d %H:%M")
    if exp <= int(datetime.now(BEIJING).timestamp()):
        return f"这段 token 的过期时间是北京时间 {stamp}，已经过期"
    return f"这段 token 的过期时间是北京时间 {stamp}，时间上还没过期"


def api_bases() -> list[str]:
    configured = _env("WORKBUDDY_API_BASE").rstrip("/")
    if configured:
        if configured not in ALLOWED_API_BASES:
            raise CheckinError(
                "WORKBUDDY_API_BASE 不在允许名单内（仅 www.codebuddy.cn / copilot.tencent.com）。",
                EXIT_USAGE,
            )
        rest = [base for base in ALLOWED_API_BASES if base != configured]
        return [configured, *rest]
    domain = (_env("WORKBUDDY_DOMAIN") or DEFAULT_DOMAIN).lower()
    preferred = f"https://{domain}" if domain in {"www.codebuddy.cn", "copilot.tencent.com"} else ""
    if preferred in ALLOWED_API_BASES:
        rest = [base for base in ALLOWED_API_BASES if base != preferred]
        return [preferred, *rest]
    return list(ALLOWED_API_BASES)


def build_headers(creds: dict[str, str]) -> dict[str, str]:
    headers = {
        "Authorization": f"Bearer {creds['access_token']}",
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": USER_AGENT,
        "X-IDE-Type": "WorkBuddy",
        "X-IDE-Name": "WorkBuddy",
        "X-IDE-Version": CLIENT_VERSION,
        "X-Requested-With": "XMLHttpRequest",
        "X-Product": "SaaS",
    }
    if creds.get("uid"):
        headers["X-User-Id"] = creds["uid"]
    if creds.get("domain"):
        headers["X-Domain"] = creds["domain"]
    enterprise_id = creds.get("enterprise_id")
    if enterprise_id:
        headers["X-Enterprise-Id"] = enterprise_id
        headers["X-Tenant-Id"] = enterprise_id
    return headers


def parse_json_body(raw: str) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        body = json.loads(raw)
    except json.JSONDecodeError:
        return {"message": raw[:200]}
    return body if isinstance(body, dict) else {"data": body}


def unwrap_data(body: dict[str, Any]) -> dict[str, Any]:
    data = body.get("data")
    return data if isinstance(data, dict) else {}


def message_of(body: dict[str, Any]) -> str:
    data = unwrap_data(body)
    for key in ("message", "msg"):
        value = body.get(key) or data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def is_already_checked_in(body: dict[str, Any]) -> bool:
    if body.get("code") in ALREADY_CODES:
        return True
    msg = message_of(body)
    if any(hint in msg for hint in ALREADY_HINTS):
        return True
    data = unwrap_data(body)
    if data.get("today_checked_in") is True or data.get("todayCheckedIn") is True:
        return True
    return False


def is_inactive(body: dict[str, Any]) -> bool:
    data = unwrap_data(body)
    if data.get("active") is False:
        return True
    msg = message_of(body)
    return "未开启" in msg or "不在活动" in msg or "活动未" in msg


def pick_int(*values: Any) -> int | None:
    for value in values:
        if isinstance(value, bool):
            continue
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            return int(value)
        if isinstance(value, str) and value.strip().lstrip("-").isdigit():
            return int(value)
    return None


def summarize(body: dict[str, Any]) -> dict[str, Any]:
    data = unwrap_data(body)
    return {
        "credit": pick_int(
            data.get("credit"),
            data.get("today_credit"),
            data.get("daily_credit"),
            data.get("points"),
        ),
        "streak_days": pick_int(data.get("streak_days"), data.get("streakDays")),
        "total_credit": pick_int(
            data.get("total_credit"),
            data.get("total_credits"),
            data.get("accumulated_credit"),
        ),
        "active": data.get("active"),
        "today_checked_in": data.get("today_checked_in", data.get("todayCheckedIn")),
        "is_streak_day": data.get("is_streak_day"),
        "streak_bonus_credit": pick_int(data.get("streak_bonus_credit")),
    }


def client_record(body: dict[str, Any]) -> dict[str, Any]:
    """加油站页面用来画每日打卡的字段。不包含账号编号。"""
    data = unwrap_data(body)
    week = data.get("week_progress")
    if isinstance(week, list):
        week_progress = [bool(item) for item in week]
    else:
        week_progress = None
    dates_raw = data.get("checkin_dates", data.get("checkinDates"))
    dates: list[str] | None
    if isinstance(dates_raw, list):
        dates = []
        for item in dates_raw:
            if isinstance(item, str) and len(item) >= 10:
                dates.append(item[:10])
    else:
        dates = None
    checked = data.get("today_checked_in", data.get("todayCheckedIn"))
    return {
        "today_checked_in": checked if isinstance(checked, bool) else None,
        "week_progress": week_progress,
        "checkin_dates": dates,
        "week_checkin_days": pick_int(data.get("week_checkin_days"), data.get("weekCheckinDays")),
    }


def client_record_marks_today(record: dict[str, Any], today: date) -> bool | None:
    """今天有没有写进客户端会读的记录。字段都缺失时返回 None，不用猜测。"""
    if record.get("today_checked_in") is True:
        return True
    dates = record.get("checkin_dates")
    if isinstance(dates, list) and dates:
        return today.isoformat() in dates
    week = record.get("week_progress")
    if isinstance(week, list) and len(week) == 7:
        return bool(week[today.weekday()])
    if record.get("today_checked_in") is False:
        return False
    return None


def client_record_sentence(record: dict[str, Any], today: date) -> str:
    marked = client_record_marks_today(record, today)
    if marked is True:
        return "客户端加油站记录里今天已领取"
    if marked is False:
        return "客户端加油站记录里今天未领取"
    return "客户端加油站没有返回今天的领取记录"


def attach_client_record(payload: dict[str, Any], body: dict[str, Any], today: date | None = None) -> bool | None:
    day = datetime.now(BEIJING).date() if today is None else today
    record = client_record(body)
    payload["client_record"] = record
    payload["report"] = f"{payload.get('report', '')}；{client_record_sentence(record, day)}".strip("；")
    return client_record_marks_today(record, day)


def require_client_record(payload: dict[str, Any], body: dict[str, Any]) -> None:
    """领取成功时，客户端加油站要能读到今天。缺字段不猜，明确写着未领取则失败。"""
    if attach_client_record(payload, body) is False:
        raise CheckinError(
            "领取结果没有写进客户端加油站的每日记录。完全退出 WorkBuddy 后重新打开「Buddy 加油站」才能看到当天的圆点；过期扣的是账户总额，不是这条记录。",
            EXIT_BUSINESS,
        )


def format_report(result: str, summary: dict[str, Any], extra: str = "") -> str:
    parts: list[str] = []
    credit = summary.get("credit")
    streak = summary.get("streak_days")
    total = summary.get("total_credit")
    if result == "CLAIMED":
        parts.append(f"成功领取 {credit} 积分" if credit is not None else "签到成功")
    elif result == "ALREADY":
        if credit is not None:
            parts.append(f"今日已签过（今日 +{credit}")
        else:
            parts.append("今日已签过")
    elif result == "INACTIVE":
        parts.append("签到活动未开启")
    else:
        parts.append(extra or result)
    if result == "ALREADY" and credit is not None:
        if streak is not None:
            parts[-1] += f"，连续 {streak} 天"
        if total is not None:
            parts[-1] += f"，累计 {total} 积分"
        parts[-1] += "）"
    elif streak is not None and result in {"CLAIMED", "ALREADY"}:
        suffix = f"连续 {streak} 天"
        if total is not None:
            suffix += f"，累计 {total} 积分"
        parts.append(suffix)
    if extra and result in {"CLAIMED", "INACTIVE"}:
        parts.append(extra)
    return "，".join(p for p in parts if p)


def confirm_new_grant(
    before: int | None,
    after: int | None,
    granted: int | None,
    streak_after: int | None,
) -> str:
    """领取前后本期累计必须正好增加 100。读数不全也视为未确认。

    连续天数到 14 天后活动会新开一期，累计可能从上一期的 1400 回到 100。
    这种情况只要本次领取是 100、新一期累计也是 100，就不要当成没加分。
    """
    problems: list[str] = []
    if granted != EXPECTED_DAILY_CREDIT:
        problems.append(f"本次领取显示 {granted}，不是 {EXPECTED_DAILY_CREDIT}")
    period_restart = (
        streak_after == 1
        and after == EXPECTED_DAILY_CREDIT
        and before == 14 * EXPECTED_DAILY_CREDIT
    )
    if before is None and after == EXPECTED_DAILY_CREDIT and streak_after == 1:
        delta_text = f"本期累计 {after}，连续第 1 天"
    elif period_restart:
        delta_text = f"本期重新开始，累计 {after}（上一期累计 {before}）"
    elif before is None or after is None:
        problems.append(
            f"领取前累计 {before}，领取后累计 {after}，读数不完整，不能确认增加了 {EXPECTED_DAILY_CREDIT}"
        )
        delta_text = ""
    else:
        delta = after - before
        delta_text = f"本期累计 {before} → {after}，增加 {delta}"
        if delta != EXPECTED_DAILY_CREDIT:
            problems.append(f"{delta_text}，不是 {EXPECTED_DAILY_CREDIT}")
    if streak_after is not None and after is not None and after != streak_after * EXPECTED_DAILY_CREDIT:
        problems.append(
            f"本期累计 {after}，按连续 {streak_after} 天每天 {EXPECTED_DAILY_CREDIT} 计算应为 {streak_after * EXPECTED_DAILY_CREDIT}"
        )
    if problems:
        raise CheckinError("；".join(problems), EXIT_BUSINESS)
    return delta_text


def confirm_already_grant(summary: dict[str, Any]) -> str:
    """当天已经签过时，用「今日积分」和「本期累计 = 连续天数 × 100」核对。"""
    credit = summary.get("credit")
    streak = summary.get("streak_days")
    total = summary.get("total_credit")
    problems: list[str] = []
    if credit != EXPECTED_DAILY_CREDIT:
        problems.append(f"今日积分是 {credit}，不是 {EXPECTED_DAILY_CREDIT}")
    if not isinstance(streak, int) or total is None:
        problems.append(
            f"连续天数 {streak}，本期累计 {total}，读数不完整，不能确认每天增加了 {EXPECTED_DAILY_CREDIT}"
        )
    elif total != streak * EXPECTED_DAILY_CREDIT:
        problems.append(
            f"本期累计 {total}，按连续 {streak} 天每天 {EXPECTED_DAILY_CREDIT} 计算应为 {streak * EXPECTED_DAILY_CREDIT}"
        )
    if problems:
        raise CheckinError("；".join(problems), EXIT_BUSINESS)
    return f"今日 +{credit}，本期累计 {total}，等于连续 {streak} 天 × {EXPECTED_DAILY_CREDIT}"


def request_json(
    url: str,
    creds: dict[str, str],
    method: str = "POST",
    payload: dict[str, Any] | None = None,
) -> tuple[int, dict[str, Any]]:
    data = None
    if method != "GET":
        data = json.dumps({} if payload is None else payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers=build_headers(creds),
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            return resp.status, parse_json_body(raw)
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
        return exc.code, parse_json_body(raw)
    except urllib.error.URLError as exc:
        raise CheckinError(f"网络错误：{exc.reason}", EXIT_NETWORK) from exc


def post_with_fallback(
    path: str,
    creds: dict[str, str],
    extra_paths: tuple[str, ...] = (),
    payload: dict[str, Any] | None = None,
) -> tuple[str, int, dict[str, Any]]:
    last_error: CheckinError | None = None
    last_result: tuple[str, int, dict[str, Any]] | None = None
    paths = (path, *extra_paths)
    for base in api_bases():
        for current_path in paths:
            url = f"{base}{current_path}"
            status, body = request_json(url, creds, payload=payload)
            if status in {401, 403}:
                raise CheckinError(
                    f"登录态已失效（{_safe_api_detail(status, body, creds)}）。",
                    EXIT_NO_SESSION,
                )
            if status in {404, 405}:
                last_result = (base, status, body)
                continue
            if status >= 500:
                last_error = CheckinError(
                    f"接口暂时不可用 HTTP {status}（{base}{current_path}）",
                    EXIT_NETWORK,
                )
                continue
            if status >= 400:
                last_error = CheckinError(
                    f"接口错误 HTTP {status}：{logged_message(body, creds) or 'unknown'}",
                    EXIT_BUSINESS,
                )
                continue
            return base, status, body
    if last_error:
        raise last_error
    if last_result:
        base, status, body = last_result
        raise CheckinError(
            f"签到接口不存在 HTTP {status}（最后尝试 {base}）",
            EXIT_NETWORK,
        )
    raise CheckinError("所有候选域名均请求失败", EXIT_NETWORK)


def dig(obj: Any, key: str) -> Any:
    if isinstance(obj, dict):
        if key in obj and obj[key] is not None:
            return obj[key]
        for wrapper in ("data", "result", "resp", "response"):
            inner = obj.get(wrapper)
            if isinstance(inner, dict):
                found = dig(inner, key)
                if found is not None:
                    return found
    return None


def _as_int(value: Any, default: int = 0) -> int:
    picked = pick_int(value)
    return default if picked is None else picked


def _client_token() -> str:
    return f"u-{uuid.uuid4()}"


def _parse_iso_date(value: Any) -> date:
    if not isinstance(value, str) or len(value) != 10:
        raise ValueError("补登日期格式无效")
    return date.fromisoformat(value)


def makeup_candidates(streak_body: dict[str, Any], heatmap_body: dict[str, Any]) -> list[str]:
    """选出当月、今天之前、尚未补过、活跃分为 0 的日期。最近的断点排在前面。"""
    today_obj = dig(heatmap_body, "today")
    if not isinstance(today_obj, dict):
        raise ValueError("活跃日历缺少服务端日期")
    today = _parse_iso_date(today_obj.get("date"))
    launch = _parse_iso_date(dig(streak_body, "launch_date"))
    start = max(today.replace(day=1), launch, date(2026, 6, 17))
    streak = dig(streak_body, "streak")
    if not isinstance(streak, dict):
        raise ValueError("连登状态格式无效")
    made_up_raw = streak.get("makeup_dates", [])
    if not isinstance(made_up_raw, list):
        raise ValueError("已补登记录格式无效")
    made_up = {_parse_iso_date(value) for value in made_up_raw}
    cells = dig(heatmap_body, "cells")
    if not isinstance(cells, list):
        raise ValueError("活跃日历缺少日期列表")
    scores: dict[date, float] = {}
    for cell in cells:
        if not isinstance(cell, dict):
            raise ValueError("活跃日历记录格式无效")
        day = _parse_iso_date(cell.get("date"))
        score = cell.get("score")
        if isinstance(score, bool) or not isinstance(score, (int, float)) or not math.isfinite(score) or score < 0:
            raise ValueError("活跃日历分数无效")
        if day in scores and scores[day] != score:
            raise ValueError("活跃日历同一日期的记录冲突")
        scores[day] = score
    return sorted(
        (
            day.isoformat()
            for day, score in scores.items()
            if start <= day < today and score == 0 and day not in made_up
        ),
        reverse=True,
    )


def tiers_to_redeem(summary: dict[str, Any]) -> list[tuple[str, str]]:
    """summary 里非空、且不是 claimed/locked 的档位才去兑换。"""
    pending: list[tuple[str, str]] = []
    for tier, status_key, label in REDEEM_TIERS:
        status = dig(summary, f"{status_key}_status")
        if not isinstance(status, str) or not status or status in {"claimed", "locked"}:
            continue
        pending.append((tier, label))
    return pending


def reward_desc(body: dict[str, Any], tier: str) -> str:
    bits: list[str] = []
    credit = _as_int(dig(body, "credit_granted"))
    energy = _as_int(dig(body, "energy_granted"))
    cards = _as_int(dig(body, "cards_granted"))
    chances = _as_int(dig(body, "chances_granted"))
    if credit:
        bits.append(f"+{credit} 积分")
    if energy:
        bits.append(f"+{energy} 能量")
    if cards:
        bits.append(f"+{cards} 补登卡")
    if chances:
        bits.append(f"+{chances} 次抽奖")
    if bits:
        return " ".join(bits)
    return REDEEM_FALLBACK.get(tier, "奖励已到账")


def _api_ok(status: int, body: dict[str, Any]) -> bool:
    code = body.get("code")
    return 200 <= status < 300 and code in {0, 200, None}


def _is_unknown_tier(status: int, body: dict[str, Any]) -> bool:
    if status != 400:
        return False
    msg = message_of(body).lower()
    return "tier" in msg and any(hint in msg for hint in ("unknown", "unsupported", "invalid"))


def _is_tier_locked(status: int, body: dict[str, Any]) -> bool:
    if status != 403:
        return False
    msg = message_of(body)
    return "天数不足" in msg or "不足" in msg


def call_growth(
    base: str,
    path: str,
    creds: dict[str, str],
    method: str = "GET",
    payload: dict[str, Any] | None = None,
) -> tuple[int, dict[str, Any]]:
    try:
        return request_json(f"{base}{GROWTH_PREFIX}{path}", creds, method=method, payload=payload)
    except CheckinError:
        raise


def growth_base(preferred: str, creds: dict[str, str]) -> tuple[str, int, dict[str, Any]]:
    """用能打开 /streak 的域名。401 视为登录失效，404 换下一个域名。"""
    bases = [preferred, *[item for item in api_bases() if item != preferred]]
    last_status = 0
    last_body: dict[str, Any] = {}
    for base in bases:
        status, body = call_growth(base, "/streak", creds)
        if status in {401, 403} and not _is_tier_locked(status, body):
            if status == 401 or "登录" in message_of(body) or "token" in message_of(body).lower():
                raise CheckinError(
                    "登录态已失效（连登接口 HTTP 401/403）。请更新 WORKBUDDY_ACCESS_TOKEN。",
                    EXIT_NO_SESSION,
                )
        if status in {404, 405}:
            last_status, last_body = status, body
            continue
        return base, status, body
    return preferred, last_status, last_body


def claim_finished_tasks(base: str, creds: dict[str, str]) -> tuple[list[str], dict[str, Any]]:
    """只领取已经完成、尚未收下的任务奖。不主动接新任务。"""
    status, body = call_growth(base, "/tasks", creds)
    if not (200 <= status < 300):
        return [f"任务列表查询失败 HTTP {status}"], {"ok": False, "http": status}
    tasks = dig(body, "tasks") or []
    if not isinstance(tasks, list):
        return ["任务列表格式异常"], {"ok": False}
    counts: dict[str, int] = {}
    notes: list[str] = []
    claimed: list[str] = []
    for task in tasks:
        if not isinstance(task, dict):
            continue
        state = str(task.get("accept_status") or "unknown")
        counts[state] = counts.get(state, 0) + 1
        if task.get("locked") or state != "completed":
            continue
        code = task.get("task_code")
        title = task.get("title") or code
        if not isinstance(code, str) or not code:
            continue
        claim_status, claim_body = call_growth(
            base,
            f"/tasks/{code}/claim",
            creds,
            method="POST",
            payload={},
        )
        if _api_ok(claim_status, claim_body) and not dig(claim_body, "already_claimed"):
            credit = _as_int(dig(claim_body, "credit"), _as_int(task.get("reward_credit")))
            notes.append(f"领任务奖「{title}」+{credit}")
            claimed.append(code)
        elif _api_ok(claim_status, claim_body):
            notes.append(f"任务奖「{title}」已领过")
        else:
            notes.append(f"领任务奖「{title}」失败：{logged_message(claim_body, creds) or f'HTTP {claim_status}'}")
    summary = "任务：" + ("，".join(f"{key} {value}" for key, value in counts.items()) if counts else "无")
    return [summary, *notes], {"ok": True, "counts": counts, "claimed": claimed}


def today_activity_score(body: dict[str, Any]) -> int | float | None:
    today_obj = dig(body, "today")
    if not isinstance(today_obj, dict):
        return None
    return credit_number(today_obj.get("score"))


def calendar_today_score(body: dict[str, Any]) -> int | float | None:
    """活跃地图涂色看的是 cells 里当天那一格。没有格子时才退回 today.score。"""
    today_obj = dig(body, "today")
    today = ""
    if isinstance(today_obj, dict):
        raw = today_obj.get("date")
        if isinstance(raw, str):
            today = raw[:10]
    elif isinstance(today_obj, str):
        today = today_obj[:10]
    cells = dig(body, "cells")
    if isinstance(cells, list) and cells and today:
        for cell in cells:
            if not isinstance(cell, dict):
                continue
            raw = cell.get("date")
            if not isinstance(raw, str) or raw[:10] != today:
                continue
            number = credit_number(cell.get("score"))
            if number is None:
                number = credit_number(cell.get("count"))
            return 0 if number is None else number
        return 0
    return today_activity_score(body)


def calendar_is_active(score: int | float | None) -> bool:
    """2 分及以下在网页上是「轻轻路过」，还不算这天已经活跃。"""
    return score is not None and score > PASSBY_SCORE


def activity_user_id(creds: dict[str, str]) -> str:
    """活跃事件必须带用户编号，否则服务端会返回成功但丢掉。不把编号写进日志。"""
    uid = (creds.get("uid") or "").strip()
    if uid:
        return uid
    subject = _jwt_payload(creds.get("access_token") or "").get("sub")
    if isinstance(subject, str):
        return subject.strip()
    return ""


def activity_conversation_events(uid: str, now_ms: int, conversation_id: str, request_id: str) -> list[dict[str, Any]]:
    """一次完成的桌面对话：发送加上成功回执。mode 用 craft，LOCAL 只会被记成环境而不是对话。"""
    shared = {
        "timestamp": now_ms,
        "reportDelay": 0,
        "userId": uid,
        "conversationId": conversation_id,
        "requestId": request_id,
        "rootRequestId": request_id,
        "parentConversationId": conversation_id,
        "ideName": "WorkBuddy",
        "ideType": "WorkBuddy",
        "ideVersion": CLIENT_VERSION,
        "extName": "workbuddy-desktop",
        "extVersion": CLIENT_VERSION,
        "product": "SaaS",
        "timezone": "Asia/Shanghai",
        "presentAt": now_ms,
        "agentName": "default",
        "agentType": "conversation",
        "mode": "craft",
    }
    send = {
        **shared,
        "eventCode": "chat_request_send",
        "inputLength": len(ACTIVITY_PROMPT),
        "requestModelId": ACTIVITY_CHAT_MODELS[0],
        "requestModelName": "DeepSeek V4 Flash",
        "isPlan": False,
        "isAutoExecuteTerminal": False,
        "isAutoModify": False,
        "codebaseEnable": False,
    }
    response = {
        **shared,
        "eventCode": "chat_request_response",
        "isSuccessful": True,
        "finishReason": "stop",
        "toolCallCount": 0,
    }
    return [send, response]


def activity_report_bodies(uid: str, now_ms: int, conversation_id: str, request_id: str) -> list[Any]:
    """成长中心热力图认的是桌面对话事件，不是模型补全。数组被拒绝时才试带 common 的信封。"""
    events = activity_conversation_events(uid, now_ms, conversation_id, request_id)
    wrapped = {
        "common": {
            "userId": uid,
            "ideName": "WorkBuddy",
            "ideType": "WorkBuddy",
            "ideVersion": CLIENT_VERSION,
            "product": "SaaS",
            "timezone": "Asia/Shanghai",
            "mode": "craft",
            "extName": "workbuddy-desktop",
            "extVersion": CLIENT_VERSION,
        },
        "events": [dict(event) for event in events],
    }
    return [events, wrapped]


def request_text(
    url: str,
    creds: dict[str, str],
    payload: Any,
    headers: dict[str, str] | None = None,
    timeout: float | None = None,
    method: str = "POST",
) -> tuple[int, str]:
    data = None if method == "GET" else json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers=build_headers(creds) if headers is None else headers,
        method=method,
    )
    wait = TIMEOUT_SECONDS if timeout is None else timeout
    try:
        with urllib.request.urlopen(req, timeout=wait) as resp:
            raw = resp.read(CHAT_READ_LIMIT).decode("utf-8", errors="replace")
            return resp.status, raw
    except urllib.error.HTTPError as exc:
        raw = exc.read(CHAT_READ_LIMIT).decode("utf-8", errors="replace") if exc.fp else ""
        return exc.code, raw
    except urllib.error.URLError as exc:
        raise CheckinError(f"网络错误：{exc.reason}", EXIT_NETWORK) from exc


def report_accepted(status: int, body: dict[str, Any]) -> tuple[bool, str]:
    code = body.get("code")
    if status in {401, 403}:
        return False, f"HTTP {status}"
    if code not in {None, "", 0, 200}:
        return False, f"code={code}"
    if 200 <= status < 300:
        return True, ""
    return False, f"HTTP {status}"


def send_activity_report(base: str, creds: dict[str, str]) -> tuple[bool, str]:
    """一天一条完成的桌面对话事件。不调用模型，也不把用户编号写进返回说明。"""
    uid = activity_user_id(creds)
    if not uid:
        return False, "缺少用户编号，活跃事件会被丢掉"
    now_ms = int(time.time() * 1000)
    conversation_id = str(uuid.uuid4())
    request_id = "cmb-" + uuid.uuid4().hex
    last = "活跃事件没有发出"
    for payload in activity_report_bodies(uid, now_ms, conversation_id, request_id):
        try:
            status, raw = request_text(f"{base}{REPORT_PATH}", creds, payload)
        except CheckinError as exc:
            return False, str(exc)
        ok, detail = report_accepted(status, parse_json_body(raw.strip()))
        if ok:
            return True, ""
        last = detail or f"HTTP {status}"
        if status in {401, 403}:
            return False, last
    return False, last


def report_bases(preferred: str) -> list[str]:
    """桌面事件优先打到 copilot.tencent.com，成长中心接口仍用原来的域名回读。"""
    ordered: list[str] = []
    for base in ("https://copilot.tencent.com", preferred, *api_bases()):
        if base in ALLOWED_API_BASES and base not in ordered:
            ordered.append(base)
    return ordered


def reread_calendar(base: str, creds: dict[str, str]) -> tuple[int | float | None, dict[str, Any] | None]:
    status, body = call_growth(base, "/heatmap", creds)
    if not (200 <= status < 300):
        return None, None
    return calendar_today_score(body), body


def activity_models() -> list[str]:
    chosen = _env("WORKBUDDY_CHAT_MODEL")
    if chosen:
        return [chosen]
    return list(ACTIVITY_CHAT_MODELS)


def web_conversation_headers(creds: dict[str, str], origin: str) -> dict[str, str]:
    """网页活跃计数不认桌面端的 X-IDE 头，只认浏览器身份加用户编号。"""
    headers = {
        "Authorization": f"Bearer {creds['access_token']}",
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/json",
        "User-Agent": WEB_USER_AGENT,
        "Origin": origin,
        "Referer": origin + "/",
    }
    uid = activity_user_id(creds)
    if uid:
        headers["X-User-Id"] = uid
    return headers


def _append_reply_text(body: dict[str, Any], parts: list[str]) -> None:
    choices = body.get("choices")
    if isinstance(choices, list):
        for choice in choices:
            if not isinstance(choice, dict):
                continue
            delta = choice.get("delta")
            if isinstance(delta, dict) and isinstance(delta.get("content"), str):
                parts.append(delta["content"])
            message = choice.get("message")
            if isinstance(message, dict) and isinstance(message.get("content"), str):
                parts.append(message["content"])
    data = unwrap_data(body)
    for key in ("reply", "content", "text"):
        value = data.get(key)
        if isinstance(value, str):
            parts.append(value)


def chat_reply_text(raw: str) -> str:
    """只判断有没有回复。回复正文不进入日志。"""
    parts: list[str] = []
    stripped = raw.strip()
    if stripped.startswith("{"):
        _append_reply_text(parse_json_body(stripped), parts)
    for line in raw.splitlines():
        line = line.strip()
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if not data or data == "[DONE]":
            continue
        _append_reply_text(parse_json_body(data), parts)
    return "".join(parts).strip()


def completion_targets() -> list[tuple[str, bool]]:
    return [
        (f"https://www.codebuddy.cn{CHAT_COMPLETION_PATH}", False),
        (f"https://copilot.tencent.com{CHAT_COMPLETION_PATH}", False),
        ("https://www.workbuddy.cn/console/chat/completions", True),
    ]


def send_web_conversation(creds: dict[str, str]) -> tuple[bool, str]:
    """发一句完整的话，并等到模型真的回复。一个字的提示在地图上只会显示「轻轻路过」。"""
    if not activity_user_id(creds):
        return False, "缺少用户编号，网页对话不会被计入"
    last = "对话没有回复"
    for model in activity_models():
        payload = {
            "model": model,
            "stream": True,
            "max_tokens": ACTIVITY_MAX_TOKENS,
            "messages": [{"role": "user", "content": ACTIVITY_PROMPT}],
        }
        model_missing = False
        for url, use_web_headers in completion_targets():
            origin = url.split("/console/")[0] if "/console/" in url else url.split("/v2/")[0]
            headers = web_conversation_headers(creds, origin) if use_web_headers else None
            try:
                status, raw = request_text(url, creds, payload, headers=headers, timeout=45)
            except CheckinError as exc:
                last = str(exc)
                continue
            parsed = parse_json_body(raw.strip()) if raw.strip().startswith("{") else {}
            code = parsed.get("code") if parsed else None
            if code in {11102, "11102"}:
                last = "模型不可用"
                model_missing = True
                break
            if status in {401, 403}:
                last = f"HTTP {status}"
                continue
            if 200 <= status < 300 and chat_reply_text(raw) and code in {None, "", 0, 200}:
                return True, ""
            last = f"HTTP {status}" if code in {None, ""} else f"HTTP {status} code={code}"
        if model_missing:
            continue
    return False, last


def maintain_login_streak(creds: dict[str, str], preferred_base: str) -> tuple[str, dict[str, Any]]:
    """补当月断登（最多 1 天），并兑换已解锁的 7/14/28 天连登奖励。活跃地图没点亮时只写入汇报，由外层把任务标成失败。"""
    notes: list[str] = []
    detail: dict[str, Any] = {}
    try:
        base, status, streak_body = growth_base(preferred_base, creds)
    except CheckinError as exc:
        if exc.exit_code == EXIT_NO_SESSION:
            raise
        return str(exc), {"ok": False}

    if not (200 <= status < 300):
        return f"连登状态查询失败 HTTP {status}", {"ok": False, "http": status}

    streak_obj = dig(streak_body, "streak")
    streak_days = streak_obj.get("days") if isinstance(streak_obj, dict) else None
    detail["streak_days"] = streak_days
    if streak_days is not None:
        notes.append(f"连登 {streak_days} 天")

    cards_obj = dig(streak_body, "makeup_cards")
    cards = _as_int(cards_obj.get("balance") if isinstance(cards_obj, dict) else cards_obj)
    detail["makeup_cards"] = cards
    heat_status, heat_body = call_growth(base, "/heatmap", creds)
    if 200 <= heat_status < 300:
        score = calendar_today_score(heat_body)
        if score is not None:
            detail["today_score"] = score
            notes.append(f"今日活跃分 {score}")
            if 0 < score <= PASSBY_SCORE:
                notes.append("今日是轻轻路过")
        if score is not None and not calendar_is_active(score):
            new_score = score
            if score == 0:
                sent, info = send_activity_report(base, creds)
                detail["activity_report"] = {"sent": sent}
                if sent:
                    notes.append("已上报 1 条桌面对话事件")
                else:
                    notes.append(f"活跃事件失败：{info}")
                new_score, again_body = reread_calendar(base, creds)
                if again_body is not None:
                    heat_body = again_body
                if new_score is None:
                    new_score = score
                if not calendar_is_active(new_score):
                    for extra in report_bases(base):
                        if extra == base:
                            continue
                        sent_more, info_more = send_activity_report(extra, creds)
                        if sent_more:
                            sent = True
                            detail["activity_report"] = {"sent": True}
                            if "已上报 1 条桌面对话事件" not in notes:
                                notes.append("已上报 1 条桌面对话事件")
                        elif info_more and info_more not in info:
                            info = info_more
                        new_score, again_body = reread_calendar(base, creds)
                        if again_body is not None:
                            heat_body = again_body
                        if new_score is None:
                            new_score = score
                        if calendar_is_active(new_score):
                            break
            if not calendar_is_active(new_score):
                talked, talk_info = send_web_conversation(creds)
                detail["web_conversation"] = {"sent": talked}
                if talked:
                    notes.append("已完成 1 次有回复的对话")
                else:
                    notes.append(f"对话没有回复：{talk_info}")
                read_score, again_body = reread_calendar(base, creds)
                if again_body is not None:
                    heat_body = again_body
                if read_score is not None:
                    new_score = read_score
            if new_score is not None:
                detail["today_score"] = new_score
                notes.append(f"上报后活跃分 {new_score}")
            if calendar_is_active(new_score):
                detail["calendar_lit"] = True
                notes.append("活跃地图今天已点亮")
            elif new_score is not None:
                detail["calendar_lit"] = False
                notes.append("活跃地图仍是轻轻路过")
        elif calendar_is_active(score):
            detail["calendar_lit"] = True
        if cards > 0:
            try:
                dates = makeup_candidates(streak_body, heat_body)
            except ValueError as exc:
                notes.append(f"补登跳过（{exc}）")
                dates = []
            for target in dates[: min(cards, MAKEUP_MAX_PER_RUN)]:
                use_status, use_body = call_growth(
                    base,
                    "/makeup-cards/use",
                    creds,
                    method="POST",
                    payload={"target_date": target},
                )
                if _api_ok(use_status, use_body):
                    notes.append(f"补登 {target}")
                    detail["made_up"] = target
                    streak_status, refreshed = call_growth(base, "/streak", creds)
                    if 200 <= streak_status < 300:
                        streak_body = refreshed
                        streak_obj = dig(streak_body, "streak")
                        if isinstance(streak_obj, dict) and streak_obj.get("days") is not None:
                            detail["streak_days"] = streak_obj.get("days")
                            notes.append(f"补登后连登 {streak_obj.get('days')} 天")
                else:
                    notes.append(f"补登 {target} 失败：{logged_message(use_body, creds) or f'HTTP {use_status}'}")
                    break
    else:
        notes.append(f"活跃日历查询失败 HTTP {heat_status}")

    task_notes, task_detail = claim_finished_tasks(base, creds)
    notes.extend(task_notes)
    detail["tasks"] = task_detail

    summary_status, summary = call_growth(base, "/redeem/summary", creds)
    detail["redeem"] = []
    if not (200 <= summary_status < 300):
        notes.append(f"连登兑换查询失败 HTTP {summary_status}")
    else:
        detail["tier_status"] = {
            status_key: dig(summary, f"{status_key}_status")
            for _, status_key, _ in REDEEM_TIERS
        }
        pending = tiers_to_redeem(summary)
        if not pending:
            shown = [
                f"{label}={detail['tier_status'][status_key]}"
                for _, status_key, label in REDEEM_TIERS
                if detail["tier_status"][status_key]
            ]
            notes.append("连登档位：" + ("，".join(shown) if shown else "无档位状态"))
        for tier, label in pending:
            redeem_status, redeem_body = call_growth(
                base,
                "/redeem",
                creds,
                method="POST",
                payload={"tier": tier, "client_token": _client_token()},
            )
            if _is_unknown_tier(redeem_status, redeem_body):
                day_count = {"7d": 7, "14d": 14, "28d": 28}[tier]
                redeem_status, redeem_body = call_growth(
                    base,
                    "/redeem",
                    creds,
                    method="POST",
                    payload={"tier": day_count, "client_token": _client_token()},
                )
            if _is_tier_locked(redeem_status, redeem_body):
                notes.append(f"{label}未解锁")
                detail["redeem"].append({"tier": tier, "result": "locked"})
                continue
            if _api_ok(redeem_status, redeem_body):
                gained = reward_desc(redeem_body, tier)
                notes.append(f"连登兑换{label}（{gained}）")
                detail["redeem"].append({"tier": tier, "result": "claimed", "reward": gained})
                continue
            notes.append(f"连登兑换{label}失败：{logged_message(redeem_body, creds) or f'HTTP {redeem_status}'}")
            detail["redeem"].append({"tier": tier, "result": "error", "http": redeem_status})

    detail["ok"] = True
    return "；".join(notes), detail


def finish_checkin(
    payload: dict[str, Any],
    creds: dict[str, str],
    base: str,
    before: dict[str, Any] | None,
    before_error: CheckinError | None,
    claimed: bool,
) -> int:
    """先记余额。活跃地图确认今天仍是空格时，签到本身成功，任务仍失败以便收到邮件。"""
    attach_growth(payload, creds, base)
    code = conclude(payload, creds, before, before_error, claimed)
    growth = payload.get("growth")
    if isinstance(growth, dict) and growth.get("calendar_lit") is False:
        return EXIT_BUSINESS
    return code


def attach_growth(payload: dict[str, Any], creds: dict[str, str], base: str) -> None:
    try:
        note, detail = maintain_login_streak(creds, base)
    except CheckinError as exc:
        if exc.exit_code == EXIT_NO_SESSION:
            raise
        note, detail = str(exc), {"ok": False}
    if note:
        payload["report"] = f"{payload.get('report', '')}；{note}".strip("；")
    payload["growth"] = detail


def show_account_balance() -> bool:
    """Actions 日志默认不写账户总额。WORKBUDDY_SHOW_BALANCE=1 时恢复。"""
    flag = os.environ.get("WORKBUDDY_SHOW_BALANCE", "").strip().lower()
    if flag in {"1", "true", "yes", "on"}:
        return True
    if flag in {"0", "false", "no", "off"}:
        return False
    return os.environ.get("GITHUB_ACTIONS", "").strip().lower() != "true"


def _hide_balance_phrase(text: str) -> str:
    marker = "当前总额 "
    start = 0
    parts: list[str] = []
    while True:
        index = text.find(marker, start)
        if index < 0:
            parts.append(text[start:])
            break
        cursor = index + len(marker)
        end = cursor
        dot = False
        while end < len(text) and (text[end].isdigit() or (text[end] == "." and not dot)):
            if text[end] == ".":
                dot = True
            end += 1
        if end == cursor:
            parts.append(text[start:end])
            start = end
            continue
        parts.append(text[start:index])
        parts.append("当前总额已省略")
        start = end
    return "".join(parts)


def _strip_public_numbers(node: Any) -> None:
    if isinstance(node, dict):
        if "growth" in node or node.get("source") == "get-user-resource" or node.get("measured_by"):
            node.pop("total", None)
            node.pop("baseline", None)
        record = node.get("client_record")
        if isinstance(record, dict):
            record.pop("checkin_dates", None)
        report = node.get("report")
        if isinstance(report, str):
            node["report"] = _hide_balance_phrase(report)
        for value in node.values():
            _strip_public_numbers(value)
    elif isinstance(node, list):
        for item in node:
            _strip_public_numbers(item)


def public_log(payload: dict[str, Any]) -> dict[str, Any]:
    """打印用的副本。领取判断和本地账本仍保留完整数字。"""
    if show_account_balance():
        return payload
    shown = json.loads(json.dumps(payload, ensure_ascii=False))
    _strip_public_numbers(shown)
    return shown


def emit(payload: dict[str, Any]) -> None:
    print(json.dumps(public_log(payload), ensure_ascii=False, separators=(",", ":")))


def beijing_today() -> str:
    return datetime.now(BEIJING).date().isoformat()


def wallet_ledger_path() -> str:
    configured = _env("WORKBUDDY_WALLET_LEDGER")
    return configured or os.path.join("docs", "wallet-daily.json")


def wallet_retry_seconds() -> float:
    raw = os.environ.get("WORKBUDDY_WALLET_RETRY_SECONDS", "2").strip()
    try:
        return max(0.0, float(raw))
    except ValueError:
        return 2.0


def credit_number(value: Any) -> int | float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            return None
        return int(value) if value.is_integer() else value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            number = float(text)
        except ValueError:
            return None
        if not math.isfinite(number):
            return None
        return int(number) if number.is_integer() else number
    return None


def fmt_credit(value: int | float | None) -> str:
    if value is None:
        return "缺失"
    if isinstance(value, float):
        if value.is_integer():
            return str(int(value))
        return f"{value:.4f}".rstrip("0").rstrip(".")
    return str(value)


def add_credit(left: int | float, right: int | float) -> int | float:
    total = left + right
    if isinstance(total, float) and total.is_integer():
        return int(total)
    return total


def sub_credit(after: int | float, before: int | float) -> int | float:
    return add_credit(after, -before)


def wallet_query_payload() -> dict[str, Any]:
    """与桌面端查询资源包的请求一致。只读，不会领取。"""
    return {
        "PageNumber": 1,
        "PageSize": WALLET_PAGE_SIZE,
        "ProductCode": WALLET_PRODUCT_CODE,
        "Status": [0, 3],
        "OnlyValidPeriod": True,
    }


def _walk_path(body: dict[str, Any], path: tuple[str, ...]) -> tuple[Any, dict[str, Any] | None]:
    node: Any = body
    parent: dict[str, Any] | None = None
    for key in path:
        if not isinstance(node, dict) or key not in node:
            return None, None
        parent = node
        node = node[key]
    return node, parent


def extract_wallet_accounts(body: dict[str, Any]) -> tuple[list[Any] | None, int | float | None]:
    paths = (
        ("data", "Response", "Data", "Accounts"),
        ("data", "data", "Response", "Data", "Accounts"),
        ("Response", "Data", "Accounts"),
        ("data", "resources"),
        ("data", "data", "resources"),
        ("resources",),
    )
    for path in paths:
        node, parent = _walk_path(body, path)
        if not isinstance(node, list):
            continue
        total_count = None
        if isinstance(parent, dict):
            total_count = credit_number(
                parent.get("TotalCount") if parent.get("TotalCount") is not None else parent.get("totalCount")
            )
        return node, total_count
    return None, None


def parse_wallet_body(body: dict[str, Any]) -> dict[str, Any]:
    """把 get-user-resource 的有效资源包剩余额度加总。用过的包（Status=3）不算进当前总额。"""
    accounts, total_count = extract_wallet_accounts(body)
    if accounts is None:
        raise CheckinError(
            "get-user-resource 响应里没有资源包列表，不能把签到接口的本期累计当成账户总额",
            EXIT_BUSINESS,
        )
    if len(accounts) >= WALLET_PAGE_SIZE and (total_count is None or total_count > len(accounts)):
        raise CheckinError(
            "真实余额资源包超过一页，没有把残缺合计当成当前总额",
            EXIT_BUSINESS,
        )
    total: int | float = 0
    packages = 0
    items: list[dict[str, Any]] = []
    for item in accounts:
        if not isinstance(item, dict):
            raise CheckinError("真实余额的资源包格式无效", EXIT_BUSINESS)
        status = credit_number(item.get("Status") if item.get("Status") is not None else item.get("status"))
        if status not in (None, 0):
            continue
        size = credit_number(
            item.get("CycleCapacitySizePrecise")
            if item.get("CycleCapacitySizePrecise") is not None
            else item.get("CycleCapacitySize")
            if item.get("CycleCapacitySize") is not None
            else item.get("CapacitySizePrecise")
            if item.get("CapacitySizePrecise") is not None
            else item.get("CapacitySize")
            if item.get("CapacitySize") is not None
            else item.get("total")
            if item.get("total") is not None
            else item.get("limitNum")
            if item.get("limitNum") is not None
            else item.get("limit_num")
        )
        remain = credit_number(
            item.get("CycleCapacityRemainPrecise")
            if item.get("CycleCapacityRemainPrecise") is not None
            else item.get("CycleCapacityRemain")
            if item.get("CycleCapacityRemain") is not None
            else item.get("CapacityRemainPrecise")
            if item.get("CapacityRemainPrecise") is not None
            else item.get("CapacityRemain")
            if item.get("CapacityRemain") is not None
            else item.get("remaining")
            if item.get("remaining") is not None
            else item.get("remain")
        )
        if size is None or remain is None or size < 0 or remain < 0:
            raise CheckinError(
                "真实余额里有无法使用的资源包读数，没有用残缺合计当成当前总额",
                EXIT_BUSINESS,
            )
        total = add_credit(total, remain)
        packages += 1
        items.append(_package_view(item, size, remain))
    return {"total": total, "packages": packages, "items": items}


def _short_hash(*parts: str) -> str:
    digest = hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()
    return digest[:16]


def _first_text(item: dict[str, Any], *fields: str) -> str:
    for field in fields:
        value = item.get(field)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return str(value)
    return ""


def _norm_time(value: str) -> str:
    text = value.strip().replace("T", " ")
    if "." in text:
        text = text.split(".", 1)[0]
    if text.endswith("Z"):
        text = text[:-1]
    return text


def _package_view(item: dict[str, Any], size: int | float, remain: int | float) -> dict[str, Any]:
    """资源包只留比对用的键和额度。账号类原始编号只做哈希，不进账本。"""
    identity = ""
    for field in ("ResourceId", "resourceId", "PkgId", "PackageId", "packageId", "DealName", "dealName"):
        identity = _first_text(item, field)
        if identity:
            key = "id:" + _short_hash(field, identity)
            break
    else:
        for field in ("AccountId", "accountId"):
            identity = _first_text(item, field)
            if identity:
                key = "acct:" + _short_hash(identity)
                break
        else:
            code = _first_text(item, "PackageCode", "packageCode", "package_code")
            start = _norm_time(
                _first_text(item, "DeductionStartTime", "PackageStartTime", "CycleStartTime", "BeginTime")
            )
            end = _norm_time(
                _first_text(
                    item,
                    "DeductionEndTime",
                    "PackageEndTime",
                    "ExpiredTime",
                    "CycleEndTime",
                )
            )
            key = "pkg:" + _short_hash(code, start, end) if code or start or end else "anon"
    end = _norm_time(
        _first_text(
            item,
            "DeductionEndTime",
            "PackageEndTime",
            "ExpiredTime",
            "CycleEndTime",
            "end",
        )
    )
    return {"key": key, "size": size, "remain": remain, "end": end}


def _package_groups(items: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        if not isinstance(item, dict) or not item.get("key"):
            continue
        grouped.setdefault(str(item["key"]), []).append(item)
    for group in grouped.values():
        group.sort(key=lambda row: (credit_number(row.get("size")) or 0, credit_number(row.get("remain")) or 0, str(row.get("end") or "")))
    return grouped


def _pair_package_delta(before: dict[str, Any], after: dict[str, Any]) -> tuple[int | float, int | float]:
    """同一资源包上，新增额度算增长，缩掉或换期留下的剩余算失效，花掉的不算。"""
    before_size = credit_number(before.get("size")) or 0
    after_size = credit_number(after.get("size")) or 0
    before_remain = credit_number(before.get("remain")) or 0
    after_remain = credit_number(after.get("remain")) or 0
    before_end = str(before.get("end") or "")
    after_end = str(after.get("end") or "")
    if before_end and after_end and before_end != after_end:
        return after_size, before_remain
    growth: int | float = 0
    expired: int | float = 0
    if after_size > before_size:
        growth = sub_credit(after_size, before_size)
    elif after_size < before_size and before_remain > after_remain:
        shrink = sub_credit(before_size, after_size)
        remain_drop = sub_credit(before_remain, after_remain)
        expired = shrink if shrink < remain_drop else remain_drop
    return growth, expired


def diff_packages(
    before_items: list[dict[str, Any]],
    after_items: list[dict[str, Any]],
) -> tuple[int | float, int | float]:
    """增长额是新出现或变大的额度。消失的剩余额度记为失效，不从增长额里扣掉。"""
    before_groups = _package_groups(before_items)
    after_groups = _package_groups(after_items)
    growth: int | float = 0
    expired: int | float = 0
    for key in set(before_groups) | set(after_groups):
        previous = before_groups.get(key, [])
        current = after_groups.get(key, [])
        paired = min(len(previous), len(current))
        for index in range(paired):
            gained, lapsed = _pair_package_delta(previous[index], current[index])
            growth = add_credit(growth, gained)
            expired = add_credit(expired, lapsed)
        for extra in current[paired:]:
            growth = add_credit(growth, credit_number(extra.get("size")) or 0)
        for missing in previous[paired:]:
            expired = add_credit(expired, credit_number(missing.get("remain")) or 0)
    return growth, expired


def _snapshot_items(snap: dict[str, Any] | None) -> list[dict[str, Any]] | None:
    if not isinstance(snap, dict) or "items" not in snap:
        return None
    items = snap.get("items")
    if not isinstance(items, list):
        return None
    return [item for item in items if isinstance(item, dict)]


def measure_wallet_change(
    before: dict[str, Any],
    after: dict[str, Any],
) -> tuple[int | float | None, int | float | None, int | float | None]:
    """返回增长额、失效额、对比基准。有资源包清单时，失效不计入负增长。"""
    before_items = _snapshot_items(before)
    after_items = _snapshot_items(after)
    baseline = credit_number(before.get("total"))
    if before_items is not None and after_items is not None:
        growth, expired = diff_packages(before_items, after_items)
        return growth, expired, baseline
    after_total = credit_number(after.get("total"))
    if baseline is None or after_total is None:
        return None, None, baseline
    return sub_credit(after_total, baseline), None, baseline


def read_wallet(creds: dict[str, str]) -> dict[str, Any]:
    """读取真实余额。401 才视为登录失效；某个域名 403/404 时继续试下一个，避免挡住签到。"""
    payload = wallet_query_payload()
    last_error: CheckinError | None = None
    saw_unauthorized = False
    for base in api_bases():
        status, body = request_json(f"{base}{USER_RESOURCE_PATH}", creds, payload=payload)
        if status == 401 or body.get("code") in {401, "401"}:
            saw_unauthorized = True
            last_error = CheckinError(
                "登录态已失效（余额接口 HTTP 401）。请更新 WORKBUDDY_ACCESS_TOKEN。",
                EXIT_NO_SESSION,
            )
            continue
        if status in {403, 404, 405} or status >= 500:
            last_error = CheckinError(
                f"余额接口不可用 HTTP {status}（{base}）",
                EXIT_NETWORK if status >= 500 else EXIT_BUSINESS,
            )
            continue
        if status >= 400:
            last_error = CheckinError(
                f"余额接口错误 HTTP {status}：{logged_message(body, creds) or 'unknown'}",
                EXIT_BUSINESS,
            )
            continue
        code = body.get("code")
        if code not in {0, 200, None}:
            last_error = CheckinError(
                f"余额接口返回 code={code}：{logged_message(body, creds) or 'unknown'}",
                EXIT_BUSINESS,
            )
            continue
        return parse_wallet_body(body)
    if saw_unauthorized and last_error is not None and last_error.exit_code == EXIT_NO_SESSION:
        raise last_error
    if last_error is not None:
        raise last_error
    raise CheckinError("余额接口没有响应", EXIT_NETWORK)


def capture_wallet(creds: dict[str, str]) -> tuple[dict[str, Any] | None, CheckinError | None]:
    try:
        return read_wallet(creds), None
    except CheckinError as exc:
        return None, exc


def load_wallet_ledger(path: str) -> dict[str, Any]:
    if not os.path.exists(path):
        return {"version": 1, "timezone": "Asia/Shanghai", "days": []}
    try:
        with open(path, encoding="utf-8") as handle:
            raw = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise CheckinError(f"读不了积分账本：{exc}", EXIT_BUSINESS) from exc
    if not isinstance(raw, dict) or not isinstance(raw.get("days"), list):
        raise CheckinError("积分账本格式无效，没有写入新的余额", EXIT_BUSINESS)
    return raw


def save_wallet_ledger(path: str, ledger: dict[str, Any]) -> None:
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    temporary = f"{path}.tmp"
    try:
        with open(temporary, "w", encoding="utf-8") as handle:
            json.dump(ledger, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(temporary, path)
    except OSError as exc:
        raise CheckinError(f"写不了积分账本：{exc}", EXIT_BUSINESS) from exc
    finally:
        if os.path.exists(temporary):
            try:
                os.remove(temporary)
            except OSError:
                pass


def _ledger_days(ledger: dict[str, Any]) -> list[dict[str, Any]]:
    days: list[dict[str, Any]] = []
    for item in ledger.get("days", []):
        if isinstance(item, dict) and isinstance(item.get("date"), str):
            days.append(item)
    return days


def upsert_ledger_day(ledger: dict[str, Any], entry: dict[str, Any]) -> dict[str, Any]:
    days = [item for item in _ledger_days(ledger) if item.get("date") != entry["date"]]
    days.append(entry)
    days.sort(key=lambda item: str(item.get("date")))
    return {
        "version": 1,
        "timezone": "Asia/Shanghai",
        "days": days[-WALLET_LEDGER_KEEP:],
    }


def decide_daily_wallet(
    *,
    today: str,
    claimed: bool,
    before: dict[str, Any] | None,
    before_error: CheckinError | None,
    after: dict[str, Any] | None,
    after_error: CheckinError | None,
    ledger: dict[str, Any],
) -> dict[str, Any]:
    """增长额是新到账的真实额度。过期或失效的剩余另记为失效，不从增长额里扣。

    不使用签到接口返回的 credit，也不把 EXPECTED_DAILY_CREDIT 加到余额上。
    """
    days = _ledger_days(ledger)
    today_entry = next((item for item in days if item.get("date") == today), None)
    previous = None
    for item in days:
        if item.get("date", "") < today and credit_number(item.get("total")) is not None:
            previous = item

    def failure(message: str, exit_code: int, *, no_growth: bool = False, snap: dict[str, Any] | None = None) -> dict[str, Any]:
        return {
            "total": None if snap is None else snap.get("total"),
            "growth": None,
            "expired": None,
            "baseline": None if snap is None else snap.get("baseline"),
            "measured_by": "unavailable",
            "packages": None if snap is None else snap.get("packages"),
            "error": message,
            "exit_code": exit_code,
            "no_growth": no_growth,
            "ledger": None,
        }

    def ledger_items(snap: dict[str, Any]) -> list[dict[str, Any]] | None:
        rows = _snapshot_items(snap)
        if rows is None:
            return None
        stored: list[dict[str, Any]] = []
        for row in rows:
            size = credit_number(row.get("size"))
            remain = credit_number(row.get("remain"))
            if size is None or remain is None or not row.get("key"):
                continue
            stored.append(
                {
                    "key": str(row["key"]),
                    "size": size,
                    "remain": remain,
                    "end": str(row.get("end") or ""),
                }
            )
        return stored

    if after is None:
        cause = after_error or before_error
        exit_code = cause.exit_code if cause is not None else EXIT_BUSINESS
        detail = str(cause) if cause is not None else "余额接口没有返回"
        return failure(
            f"读不到 WorkBuddy 真实账户余额（get-user-resource），不能把本次领取记成增长。{detail}",
            exit_code,
        )

    previous_items = _snapshot_items(previous)
    after_items = _snapshot_items(after)
    can_match_packages = previous_items is not None and after_items is not None
    stored_growth = credit_number(today_entry.get("growth")) if today_entry else None
    stored_by = today_entry.get("measured_by") if today_entry else None
    expired: int | float | None
    if can_match_packages and previous is not None:
        growth, expired, baseline = measure_wallet_change(previous, after)
        measured_by = "previous_day"
        no_growth = growth is not None and growth <= 0
        error = _no_growth_message(after["total"], growth, expired) if no_growth else None
        exit_code = EXIT_BUSINESS if no_growth else EXIT_OK
    elif claimed:
        if before is None or (
            credit_number(before.get("total")) is None and _snapshot_items(before) is None
        ):
            cause = before_error
            exit_code = cause.exit_code if cause is not None else EXIT_BUSINESS
            detail = str(cause) if cause is not None else "领取前没有余额读数"
            entry = {
                "date": today,
                "total": after["total"],
                "growth": None,
                "expired": None,
                "baseline": None,
                "measured_by": "unknown",
                "source": "get-user-resource",
            }
            remembered = ledger_items(after)
            if remembered is not None:
                entry["items"] = remembered
            return {
                "total": after["total"],
                "growth": None,
                "expired": None,
                "baseline": None,
                "measured_by": "unknown",
                "packages": after.get("packages"),
                "error": (
                    f"领取前后没有完整的真实余额，不能把签到接口显示的积分当成增长额。"
                    f"当前总额 {fmt_credit(after['total'])}，增长额未知。{detail}"
                ),
                "exit_code": exit_code,
                "no_growth": False,
                "ledger": upsert_ledger_day(ledger, entry),
            }
        growth, expired, baseline = measure_wallet_change(before, after)
        measured_by = "same_run"
        no_growth = growth is None or growth <= 0
        if growth is None:
            error = (
                f"领取前后没有完整的真实余额，不能把签到接口显示的积分当成增长额。"
                f"当前总额 {fmt_credit(after['total'])}，增长额未知。"
            )
            exit_code = EXIT_BUSINESS
        else:
            error = _no_growth_message(after["total"], growth, expired) if no_growth else None
            exit_code = EXIT_BUSINESS if no_growth else EXIT_OK
    elif (
        today_entry is not None
        and stored_by in {"same_run", "previous_day", "earlier_today"}
        and stored_growth is not None
        and stored_growth > 0
    ):
        growth = stored_growth
        expired = credit_number(today_entry.get("expired"))
        baseline = credit_number(today_entry.get("baseline"))
        measured_by = str(stored_by)
        error = None
        exit_code = EXIT_OK
        no_growth = False
    elif previous is not None and credit_number(previous.get("total")) is not None:
        baseline = previous["total"]
        growth = sub_credit(after["total"], baseline)
        expired = None
        measured_by = "previous_day"
        no_growth = growth <= 0
        error = _no_growth_message(after["total"], growth, expired) if no_growth else None
        exit_code = EXIT_BUSINESS if no_growth else EXIT_OK
    else:
        baseline = None
        growth = None
        expired = None
        measured_by = "unknown"
        error = None
        exit_code = EXIT_OK
        no_growth = False

    entry = {
        "date": today,
        "total": after["total"],
        "growth": growth,
        "expired": expired,
        "baseline": baseline,
        "measured_by": measured_by,
        "source": "get-user-resource",
    }
    remembered = ledger_items(after)
    if remembered is not None:
        entry["items"] = remembered
    return {
        "total": after["total"],
        "growth": growth,
        "expired": expired,
        "baseline": baseline,
        "measured_by": measured_by,
        "packages": after.get("packages"),
        "error": error,
        "exit_code": exit_code,
        "no_growth": no_growth,
        "ledger": upsert_ledger_day(ledger, entry),
    }


def _no_growth_message(total: int | float, growth: int | float | None, expired: int | float | None) -> str:
    text = f"真实账户积分未增长：当前总额 {fmt_credit(total)}，增长额 {fmt_credit(growth)}"
    if expired is not None:
        text += f"，失效 {fmt_credit(expired)}"
    return text


def wallet_public(decision: dict[str, Any]) -> dict[str, Any]:
    return {
        "source": "get-user-resource",
        "total": decision.get("total"),
        "growth": decision.get("growth"),
        "expired": decision.get("expired"),
        "baseline": decision.get("baseline"),
        "measured_by": decision.get("measured_by"),
        "packages": decision.get("packages"),
    }


def wallet_sentence(decision: dict[str, Any]) -> str:
    total = fmt_credit(decision.get("total"))
    growth = decision.get("growth")
    expired = decision.get("expired")
    if growth is None:
        return f"当前总额 {total}，增长额未知（还没有更早的真实余额，get-user-resource）"
    text = f"当前总额 {total}，增长额 {fmt_credit(growth)}"
    if expired is not None:
        text += f"，失效 {fmt_credit(expired)}"
    return text + "（get-user-resource）"


def write_wallet_summary(decision: dict[str, Any]) -> None:
    path = os.environ.get("GITHUB_STEP_SUMMARY", "").strip()
    if not path:
        return
    growth = decision.get("growth")
    growth_text = "未知" if growth is None else fmt_credit(growth)
    expired = decision.get("expired")
    lines = ["### 真实积分"]
    if show_account_balance():
        lines.append(f"- 当前总额：{fmt_credit(decision.get('total'))}")
    lines.append(f"- 增长额：{growth_text}")
    if expired is not None:
        lines.append(f"- 失效：{fmt_credit(expired)}")
    lines.extend(["- 来源：get-user-resource", ""])
    with open(path, "a", encoding="utf-8") as handle:
        handle.write("\n".join(lines))


def apply_daily_wallet(
    payload: dict[str, Any],
    creds: dict[str, str],
    before: dict[str, Any] | None,
    before_error: CheckinError | None,
    claimed: bool,
) -> None:
    """记录真实余额。未增长或读不到余额时抛出 CheckinError，让 Actions 失败并发邮件。"""
    after, after_error = capture_wallet(creds)
    ledger = load_wallet_ledger(wallet_ledger_path())
    decision = decide_daily_wallet(
        today=beijing_today(),
        claimed=claimed,
        before=before,
        before_error=before_error,
        after=after,
        after_error=after_error,
        ledger=ledger,
    )
    if claimed and decision.get("no_growth"):
        time.sleep(wallet_retry_seconds())
        retried, retried_error = capture_wallet(creds)
        if retried is not None:
            after, after_error = retried, None
            decision = decide_daily_wallet(
                today=beijing_today(),
                claimed=True,
                before=before,
                before_error=before_error,
                after=after,
                after_error=after_error,
                ledger=ledger,
            )
    updated = decision.get("ledger")
    if isinstance(updated, dict):
        save_wallet_ledger(wallet_ledger_path(), updated)
    write_wallet_summary(decision)
    if decision.get("error"):
        checkin_report = payload.get("report")
        message = str(decision["error"])
        if checkin_report:
            message = f"{message}。本次签到：{checkin_report}"
        raise CheckinError(
            message,
            int(decision.get("exit_code") or EXIT_BUSINESS),
            details={
                "wallet": wallet_public(decision),
                "checkin_result": payload.get("result"),
            },
        )
    payload["wallet"] = wallet_public(decision)
    payload["report"] = f"{payload.get('report', '')}；{wallet_sentence(decision)}".strip("；")


def attach_status_wallet(payload: dict[str, Any], creds: dict[str, str]) -> None:
    """只查询。失败不改变 --status 的退出码，也不写账本。"""
    snap, err = capture_wallet(creds)
    if snap is not None:
        payload["wallet"] = {
            "source": "get-user-resource",
            "total": snap["total"],
            "growth": None,
            "measured_by": "status",
            "packages": snap["packages"],
        }
        payload["report"] = f"{payload.get('report', '')}；当前总额 {fmt_credit(snap['total'])}".strip("；")
    elif err is not None:
        payload["wallet_error"] = str(err)


def conclude(
    payload: dict[str, Any],
    creds: dict[str, str],
    before: dict[str, Any] | None,
    before_error: CheckinError | None,
    claimed: bool,
) -> int:
    apply_daily_wallet(payload, creds, before, before_error, claimed)
    emit(payload)
    return EXIT_OK


def request_refresh(url: str, creds: dict[str, str]) -> tuple[int, dict[str, Any]]:
    """只用 X-Refresh-Token 换 accessToken。不附带已经失效的 Bearer，避免网关直接 401。"""
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": USER_AGENT,
        "X-Refresh-Token": creds["refresh_token"],
        "X-Auth-Refresh-Source": "plugin",
    }
    if creds.get("domain"):
        headers["X-Domain"] = creds["domain"]
    if creds.get("uid"):
        headers["X-User-Id"] = creds["uid"]
    enterprise_id = creds.get("enterprise_id")
    if enterprise_id:
        headers["X-Enterprise-Id"] = enterprise_id
        headers["X-Tenant-Id"] = enterprise_id
    req = urllib.request.Request(url, data=b"{}", headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            return resp.status, parse_json_body(raw)
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
        return exc.code, parse_json_body(raw)
    except urllib.error.URLError as exc:
        raise CheckinError(f"网络错误：{exc.reason}", EXIT_NETWORK) from exc


def refresh_access_token(creds: dict[str, str]) -> str:
    refresh = creds.get("refresh_token") or ""
    if not refresh:
        raise CheckinError("没有可用于刷新的 refreshToken。", EXIT_NO_SESSION)
    last = "刷新接口没有响应"
    saw_auth_failure = False
    for base in api_bases():
        status, body = request_refresh(f"{base}{REFRESH_PATH}", creds)
        token = _string_field(body, "accessToken") or _string_field(body, "access_token")
        code = body.get("code")
        if 200 <= status < 300 and code in {0, 200, None} and token and not _is_encrypted_token(token):
            return token
        detail = _safe_api_detail(status, body, creds)
        last = f"刷新登录态失败（{detail}）"
        if status in {401, 403} or code in {12153, "12153"}:
            saw_auth_failure = True
            continue
        if status >= 500:
            continue
        saw_auth_failure = saw_auth_failure or status in {401, 403}
    expiry = _jwt_expiry_text(creds.get("access_token") or refresh)
    extra = f"。{expiry}" if expiry else ""
    if saw_auth_failure:
        extra += "。请把桌面端登录文件里的 refreshToken，或整份未加密 JSON，更新到 WORKBUDDY_ACCESS_TOKEN"
    raise CheckinError(last + extra, EXIT_NO_SESSION)


def _string_field(body: dict[str, Any], key: str) -> str:
    found = dig(body, key)
    if isinstance(found, str):
        return _unwrap_token(found)
    return ""


def perform(status_only: bool, creds: dict[str, str]) -> int:
    before: dict[str, Any] | None = None
    before_error: CheckinError | None = None
    if not status_only:
        before, before_error = capture_wallet(creds)

    status_base, _, status_body = post_with_fallback(
        STATUS_PATHS[0],
        creds,
        extra_paths=STATUS_PATHS[1:],
    )
    status_summary = summarize(status_body)

    if is_inactive(status_body):
        if not status_only:
            raise CheckinError("签到活动未开启，今天没有增加 100 积分", EXIT_BUSINESS)
        payload = {
            "result": "INACTIVE",
            "report": format_report("INACTIVE", status_summary),
            "api_base": status_base,
            "summary": status_summary,
        }
        attach_status_wallet(payload, creds)
        emit(payload)
        return EXIT_OK

    if is_already_checked_in(status_body):
        note = "" if status_only else confirm_already_grant(status_summary)
        report = format_report("ALREADY", status_summary)
        if note:
            report = f"{report}；{note}"
        payload = {
            "result": "ALREADY",
            "report": report,
            "api_base": status_base,
            "summary": status_summary,
        }
        if status_only:
            attach_client_record(payload, status_body)
            attach_status_wallet(payload, creds)
            emit(payload)
            return EXIT_OK
        require_client_record(payload, status_body)
        return finish_checkin(payload, creds, status_base, before, before_error, claimed=False)

    if status_only:
        payload = {
            "result": "PENDING",
            "report": "今日尚未签到",
            "api_base": status_base,
            "summary": status_summary,
        }
        attach_status_wallet(payload, creds)
        emit(payload)
        return EXIT_OK

    claim_base, _, claim_body = post_with_fallback(CLAIM_PATH, creds)
    claim_summary = {**status_summary, **{k: v for k, v in summarize(claim_body).items() if v is not None}}
    code = claim_body.get("code")

    if is_already_checked_in(claim_body):
        _, _, refreshed_body = post_with_fallback(
            STATUS_PATHS[0],
            creds,
            extra_paths=STATUS_PATHS[1:],
        )
        refreshed = summarize(refreshed_body)
        note = confirm_already_grant(refreshed)
        report = format_report("ALREADY", refreshed)
        payload = {
            "result": "ALREADY",
            "report": f"{report}；{note}",
            "api_base": claim_base,
            "summary": refreshed,
        }
        require_client_record(payload, refreshed_body)
        return finish_checkin(payload, creds, claim_base, before, before_error, claimed=False)

    if code not in {0, 200, None}:
        raise CheckinError(
            f"领取失败 code={code}：{logged_message(claim_body, creds) or 'unknown'}",
            EXIT_BUSINESS,
        )

    data = unwrap_data(claim_body)
    if data.get("success") is False:
        raise CheckinError(
            f"领取未成功：{logged_message(claim_body, creds) or 'success=false'}",
            EXIT_BUSINESS,
        )

    _, _, after_body = post_with_fallback(
        STATUS_PATHS[0],
        creds,
        extra_paths=STATUS_PATHS[1:],
    )
    after_summary = summarize(after_body)
    granted = claim_summary.get("credit")
    if granted is None:
        granted = after_summary.get("credit")
    note = confirm_new_grant(
        status_summary.get("total_credit"),
        after_summary.get("total_credit"),
        granted,
        after_summary.get("streak_days"),
    )
    merged = {**claim_summary, **{k: v for k, v in after_summary.items() if v is not None}}
    report = format_report("CLAIMED", merged, logged_message(claim_body, creds))
    payload = {
        "result": "CLAIMED",
        "report": f"{report}；{note}",
        "api_base": claim_base,
        "summary": merged,
        "credit_delta": {
            "before": status_summary.get("total_credit"),
            "after": after_summary.get("total_credit"),
            "granted": granted,
        },
    }
    require_client_record(payload, after_body)
    return finish_checkin(payload, creds, claim_base, before, before_error, claimed=True)


def run(status_only: bool) -> int:
    creds = load_creds()
    if not creds["access_token"]:
        creds["access_token"] = refresh_access_token(creds)
        creds["_did_refresh"] = "1"
    try:
        return perform(status_only, creds)
    except CheckinError as exc:
        if exc.exit_code != EXIT_NO_SESSION or creds.get("_did_refresh") or not creds.get("refresh_token"):
            raise
        try:
            creds["access_token"] = refresh_access_token(creds)
        except CheckinError as refresh_error:
            raise CheckinError(f"{exc}{refresh_error}", EXIT_NO_SESSION) from refresh_error
        creds["_did_refresh"] = "1"
        return perform(status_only, creds)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="WorkBuddy 云端每日签到")
    parser.add_argument(
        "--status",
        action="store_true",
        help="只查询今日签到状态，不领取",
    )
    args = parser.parse_args(argv)
    try:
        return run(status_only=args.status)
    except CheckinError as exc:
        payload: dict[str, Any] = {"result": "ERROR", "report": str(exc)}
        if exc.details:
            payload.update(exc.details)
        emit(payload)
        return exc.exit_code


if __name__ == "__main__":
    sys.exit(main())
