#!/usr/bin/env python3
"""签到与连登兑换的纯逻辑测试。不访问网络，也不读取真实 token。"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
import urllib.request
from datetime import date
from unittest import mock

import checkin


class MakeupTests(unittest.TestCase):
    def test_picks_latest_broken_day_in_current_month(self) -> None:
        streak = {
            "launch_date": "2026-06-17",
            "streak": {"days": 3, "makeup_dates": ["2026-10-01"]},
        }
        heatmap = {
            "today": {"date": "2026-10-03"},
            "cells": [
                {"date": "2026-09-30", "score": 0},
                {"date": "2026-10-01", "score": 0},
                {"date": "2026-10-02", "score": 0},
            ],
        }
        self.assertEqual(checkin.makeup_candidates(streak, heatmap), ["2026-10-02"])

    def test_rejects_malformed_calendar(self) -> None:
        with self.assertRaises(ValueError):
            checkin.makeup_candidates({"launch_date": "2026-06-17", "streak": {}}, {"cells": []})


class RedeemTests(unittest.TestCase):
    def test_skips_claimed_locked_and_missing(self) -> None:
        summary = {
            "data": {
                "starter_status": "claimed",
                "advanced_status": "locked",
                "legendary_status": None,
            }
        }
        self.assertEqual(checkin.tiers_to_redeem(summary), [])

    def test_claims_only_unlocked_tier(self) -> None:
        summary = {
            "starter_status": "available",
            "advanced_status": "locked",
            "legendary_status": "claimed",
        }
        self.assertEqual(checkin.tiers_to_redeem(summary), [("7d", "入门7天")])

    def test_reward_desc_uses_granted_fields(self) -> None:
        text = checkin.reward_desc(
            {"data": {"credit_granted": 50, "energy_granted": 3, "cards_granted": 0, "chances_granted": 1}},
            "14d",
        )
        self.assertIn("+50 积分", text)
        self.assertIn("+3 能量", text)
        self.assertIn("+1 次抽奖", text)
        self.assertNotIn("补登卡", text)


class CreditCheckTests(unittest.TestCase):
    def test_new_grant_accepts_exact_plus_100(self) -> None:
        text = checkin.confirm_new_grant(200, 300, 100, 3)
        self.assertIn("200 → 300", text)
        self.assertIn("增加 100", text)

    def test_new_grant_rejects_flat_total(self) -> None:
        with self.assertRaises(checkin.CheckinError) as caught:
            checkin.confirm_new_grant(300, 300, 100, 3)
        self.assertIn("增加 0", str(caught.exception))
        self.assertEqual(caught.exception.exit_code, checkin.EXIT_BUSINESS)

    def test_new_grant_rejects_missing_reading(self) -> None:
        with self.assertRaises(checkin.CheckinError):
            checkin.confirm_new_grant(None, 400, 100, 4)

    def test_new_period_restart_is_not_a_missed_increase(self) -> None:
        text = checkin.confirm_new_grant(1400, 100, 100, 1)
        self.assertIn("重新开始", text)

    def test_unexplained_drop_still_fails(self) -> None:
        with self.assertRaises(checkin.CheckinError) as caught:
            checkin.confirm_new_grant(500, 100, 100, 1)
        self.assertIn("不是 100", str(caught.exception))

    def test_new_grant_accepts_first_day_without_before(self) -> None:
        text = checkin.confirm_new_grant(None, 100, 100, 1)
        self.assertIn("连续第 1 天", text)

    def test_already_requires_period_total(self) -> None:
        text = checkin.confirm_already_grant(
            {"credit": 100, "streak_days": 3, "total_credit": 300}
        )
        self.assertIn("300", text)

    def test_already_rejects_wrong_today_credit(self) -> None:
        with self.assertRaises(checkin.CheckinError) as caught:
            checkin.confirm_already_grant(
                {"credit": 50, "streak_days": 3, "total_credit": 300}
            )
        self.assertIn("不是 100", str(caught.exception))


class RunFlowTests(unittest.TestCase):
    def test_already_checked_in_still_redeems(self) -> None:
        seen: dict[str, str] = {}

        def fake_post(path: str, creds: dict, extra_paths: tuple = ()) -> tuple:
            body = {
                "code": 0,
                "data": {
                    "active": True,
                    "today_checked_in": True,
                    "credit": 100,
                    "streak_days": 3,
                    "total_credits": 300,
                },
            }
            return "https://www.codebuddy.cn", 200, body

        def fake_attach(payload: dict, creds: dict, base: str) -> None:
            seen["base"] = base
            payload["report"] += "；连登 3 天"
            payload["growth"] = {"ok": True, "streak_days": 3}

        with tempfile.TemporaryDirectory() as tmp:
            ledger = os.path.join(tmp, "wallet-daily.json")
            env = {
                "WORKBUDDY_ACCESS_TOKEN": "test-token",
                "WORKBUDDY_WALLET_LEDGER": ledger,
                "WORKBUDDY_WALLET_RETRY_SECONDS": "0",
            }
            with mock.patch.dict(os.environ, env, clear=False):
                with mock.patch.object(checkin, "post_with_fallback", fake_post):
                    with mock.patch.object(checkin, "attach_growth", fake_attach):
                        with mock.patch.object(
                            checkin,
                            "capture_wallet",
                            return_value=({"total": 1000, "packages": 1}, None),
                        ):
                            with mock.patch.object(checkin, "emit") as emit:
                                code = checkin.run(status_only=False)
        self.assertEqual(code, 0)
        self.assertEqual(seen["base"], "https://www.codebuddy.cn")
        payload = emit.call_args.args[0]
        self.assertEqual(payload["result"], "ALREADY")
        self.assertIn("连登 3 天", payload["report"])
        self.assertIn("× 100", payload["report"])

    def test_dark_calendar_fails_after_checkin_is_recorded(self) -> None:
        def fake_post(path: str, creds: dict, extra_paths: tuple = ()) -> tuple:
            body = {
                "code": 0,
                "data": {
                    "active": True,
                    "today_checked_in": True,
                    "credit": 100,
                    "streak_days": 3,
                    "total_credits": 300,
                },
            }
            return "https://www.codebuddy.cn", 200, body

        def fake_attach(payload: dict, creds: dict, base: str) -> None:
            payload["report"] += "；活跃地图今天仍未点亮"
            payload["growth"] = {"ok": True, "calendar_lit": False}

        with tempfile.TemporaryDirectory() as tmp:
            env = {
                "WORKBUDDY_ACCESS_TOKEN": "test-token",
                "WORKBUDDY_WALLET_LEDGER": os.path.join(tmp, "wallet-daily.json"),
                "WORKBUDDY_WALLET_RETRY_SECONDS": "0",
            }
            with mock.patch.dict(os.environ, env, clear=False):
                with mock.patch.object(checkin, "post_with_fallback", fake_post):
                    with mock.patch.object(checkin, "attach_growth", fake_attach):
                        with mock.patch.object(
                            checkin,
                            "capture_wallet",
                            return_value=({"total": 1000, "packages": 1}, None),
                        ):
                            with mock.patch.object(checkin, "emit") as emit:
                                code = checkin.run(status_only=False)
        self.assertEqual(code, checkin.EXIT_BUSINESS)
        payload = emit.call_args.args[0]
        self.assertEqual(payload["result"], "ALREADY")
        self.assertIn("活跃地图今天仍未点亮", payload["report"])
        self.assertIn("× 100", payload["report"])

    def test_status_only_does_not_redeem(self) -> None:
        def fake_post(path: str, creds: dict, extra_paths: tuple = ()) -> tuple:
            body = {"code": 0, "data": {"active": True, "today_checked_in": False}}
            return "https://www.codebuddy.cn", 200, body

        with tempfile.TemporaryDirectory() as tmp:
            ledger = os.path.join(tmp, "wallet-daily.json")
            env = {
                "WORKBUDDY_ACCESS_TOKEN": "test-token",
                "WORKBUDDY_WALLET_LEDGER": ledger,
                "WORKBUDDY_WALLET_RETRY_SECONDS": "0",
            }
            with mock.patch.dict(os.environ, env, clear=False):
                with mock.patch.object(checkin, "post_with_fallback", fake_post):
                    with mock.patch.object(checkin, "attach_growth") as attach:
                        with mock.patch.object(
                            checkin,
                            "capture_wallet",
                            return_value=({"total": 80, "packages": 1}, None),
                        ):
                            with mock.patch.object(checkin, "emit"):
                                code = checkin.run(status_only=True)
            self.assertFalse(os.path.exists(ledger))
        self.assertEqual(code, 0)
        attach.assert_not_called()

    def test_claim_fails_when_period_total_does_not_rise(self) -> None:
        calls = {"n": 0}

        def fake_post(path: str, creds: dict, extra_paths: tuple = ()) -> tuple:
            calls["n"] += 1
            if calls["n"] == 1:
                data = {
                    "active": True,
                    "today_checked_in": False,
                    "total_credits": 200,
                    "streak_days": 2,
                }
            elif path == checkin.CLAIM_PATH:
                data = {"credit": 100, "success": True, "streak_days": 3}
            else:
                data = {
                    "active": True,
                    "today_checked_in": True,
                    "credit": 100,
                    "total_credits": 200,
                    "streak_days": 3,
                }
            return "https://www.codebuddy.cn", 200, {"code": 0, "data": data}

        with mock.patch.dict(
            os.environ,
            {"WORKBUDDY_ACCESS_TOKEN": "test-token", "WORKBUDDY_WALLET_RETRY_SECONDS": "0"},
            clear=False,
        ):
            with mock.patch.object(checkin, "post_with_fallback", fake_post):
                with mock.patch.object(
                    checkin,
                    "capture_wallet",
                    return_value=({"total": 1000, "packages": 1}, None),
                ):
                    with self.assertRaises(checkin.CheckinError) as caught:
                        checkin.run(status_only=False)
        self.assertIn("增加 0", str(caught.exception))
        self.assertEqual(caught.exception.exit_code, checkin.EXIT_BUSINESS)


def _checked_in_body() -> dict:
    return {
        "code": 0,
        "data": {
            "active": True,
            "today_checked_in": True,
            "credit": 100,
            "streak_days": 7,
            "total_credits": 700,
        },
    }


def _claim_posts(period_before: int, period_after: int):
    calls = {"n": 0}

    def fake_post(path: str, creds: dict, extra_paths: tuple = ()) -> tuple:
        calls["n"] += 1
        if calls["n"] == 1:
            data = {
                "active": True,
                "today_checked_in": False,
                "total_credits": period_before,
                "streak_days": period_before // 100,
            }
        elif path == checkin.CLAIM_PATH:
            data = {
                "credit": 100,
                "success": True,
                "streak_days": period_after // 100,
                "total_credits": period_after,
            }
        else:
            data = {
                "active": True,
                "today_checked_in": True,
                "credit": 100,
                "total_credits": period_after,
                "streak_days": period_after // 100,
            }
        return "https://www.codebuddy.cn", 200, {"code": 0, "data": data}

    return fake_post


class WalletParseTests(unittest.TestCase):
    def test_sums_active_remain_and_skips_used_up_packages(self) -> None:
        snap = checkin.parse_wallet_body(
            {
                "code": 0,
                "data": {
                    "Response": {
                        "Data": {
                            "TotalCount": 3,
                            "Accounts": [
                                {
                                    "Status": 0,
                                    "CycleCapacitySizePrecise": "100",
                                    "CycleCapacityRemainPrecise": "70",
                                    "AccountId": "should-not-leak",
                                },
                                {
                                    "Status": 0,
                                    "CycleCapacitySizePrecise": 50,
                                    "CycleCapacityRemainPrecise": 20.5,
                                },
                                {
                                    "Status": 3,
                                    "CycleCapacitySizePrecise": 900,
                                    "CycleCapacityRemainPrecise": 0,
                                },
                            ],
                        }
                    }
                },
            }
        )
        self.assertEqual(snap["total"], 90.5)
        self.assertEqual(snap["packages"], 2)
        self.assertEqual(len(snap["items"]), 2)
        self.assertNotIn("should-not-leak", json.dumps(snap))
        self.assertTrue(snap["items"][0]["key"].startswith("acct:"))

    def test_accepts_resources_wrapper(self) -> None:
        snap = checkin.parse_wallet_body(
            {"data": {"resources": [{"status": 0, "total": 50, "remaining": 20}, {"total": 10, "remain": 10}]}}
        )
        self.assertEqual(snap["total"], 30)
        self.assertEqual(snap["packages"], 2)
        self.assertEqual(len(snap["items"]), 2)

    def test_rejects_unusable_active_package_and_truncated_page(self) -> None:
        with self.assertRaises(checkin.CheckinError):
            checkin.parse_wallet_body(
                {
                    "data": {
                        "Response": {
                            "Data": {
                                "Accounts": [{"Status": 0, "RenamedRemain": 1}],
                            }
                        }
                    }
                }
            )
        page = [
            {"Status": 0, "CycleCapacitySizePrecise": 1, "CycleCapacityRemainPrecise": 1}
            for _ in range(checkin.WALLET_PAGE_SIZE)
        ]
        with self.assertRaises(checkin.CheckinError) as caught:
            checkin.parse_wallet_body({"data": {"Response": {"Data": {"Accounts": page}}}})
        self.assertIn("超过一页", str(caught.exception))

    def test_read_wallet_queries_real_resource_endpoint(self) -> None:
        seen: dict = {}

        def fake_request(url: str, creds: dict, method: str = "POST", payload: dict | None = None):
            seen["url"] = url
            seen["payload"] = payload
            return 200, {
                "code": 0,
                "data": {
                    "Response": {
                        "Data": {
                            "Accounts": [
                                {
                                    "Status": 0,
                                    "CycleCapacitySizePrecise": "80",
                                    "CycleCapacityRemainPrecise": "80",
                                }
                            ],
                            "TotalCount": 1,
                        }
                    }
                },
            }

        with mock.patch.dict(os.environ, {"WORKBUDDY_ACCESS_TOKEN": "secret-token"}, clear=False):
            with mock.patch.object(checkin, "request_json", fake_request):
                snap = checkin.read_wallet(checkin.load_creds())
        self.assertEqual(snap["total"], 80)
        self.assertIn(checkin.USER_RESOURCE_PATH, seen["url"])
        self.assertEqual(seen["payload"]["ProductCode"], checkin.WALLET_PRODUCT_CODE)
        self.assertIs(seen["payload"]["OnlyValidPeriod"], True)
        self.assertNotIn("secret-token", str(snap))


class WalletGrowthTests(unittest.TestCase):
    def test_same_run_growth_is_the_real_delta_not_plus_100(self) -> None:
        decision = checkin.decide_daily_wallet(
            today="2026-10-06",
            claimed=True,
            before={"total": 1000, "packages": 1},
            before_error=None,
            after={"total": 1040, "packages": 2},
            after_error=None,
            ledger={"version": 1, "days": []},
        )
        self.assertIsNone(decision["error"])
        self.assertEqual(decision["growth"], 40)
        self.assertEqual(decision["total"], 1040)
        self.assertNotEqual(decision["growth"], checkin.EXPECTED_DAILY_CREDIT)

    def test_flat_same_run_balance_is_no_growth(self) -> None:
        decision = checkin.decide_daily_wallet(
            today="2026-10-06",
            claimed=True,
            before={"total": 1000, "packages": 1},
            before_error=None,
            after={"total": 1000, "packages": 1},
            after_error=None,
            ledger={"version": 1, "days": []},
        )
        self.assertTrue(decision["no_growth"])
        self.assertEqual(decision["growth"], 0)
        self.assertIn("当前总额 1000", decision["error"])
        self.assertIn("增长额 0", decision["error"])

    def test_already_uses_previous_day_balance(self) -> None:
        ledger = {
            "version": 1,
            "days": [{"date": "2026-10-05", "total": 2000, "growth": 100, "measured_by": "same_run"}],
        }
        grew = checkin.decide_daily_wallet(
            today="2026-10-06",
            claimed=False,
            before={"total": 2100, "packages": 1},
            before_error=None,
            after={"total": 2100, "packages": 1},
            after_error=None,
            ledger=ledger,
        )
        self.assertEqual(grew["growth"], 100)
        self.assertEqual(grew["measured_by"], "previous_day")
        flat = checkin.decide_daily_wallet(
            today="2026-10-06",
            claimed=False,
            before={"total": 2000, "packages": 1},
            before_error=None,
            after={"total": 2000, "packages": 1},
            after_error=None,
            ledger=ledger,
        )
        self.assertTrue(flat["no_growth"])
        self.assertEqual(flat["growth"], 0)

    def test_positive_growth_recorded_earlier_today_is_kept(self) -> None:
        ledger = {
            "version": 1,
            "days": [
                {
                    "date": "2026-10-06",
                    "total": 2100,
                    "growth": 100,
                    "baseline": 2000,
                    "measured_by": "same_run",
                    "source": "get-user-resource",
                }
            ],
        }
        decision = checkin.decide_daily_wallet(
            today="2026-10-06",
            claimed=False,
            before={"total": 1800, "packages": 1},
            before_error=None,
            after={"total": 1800, "packages": 1},
            after_error=None,
            ledger=ledger,
        )
        self.assertIsNone(decision["error"])
        self.assertEqual(decision["total"], 1800)
        self.assertEqual(decision["growth"], 100)

    def test_expired_quota_does_not_cancel_a_real_grant(self) -> None:
        before = {
            "total": 1000,
            "items": [
                {"key": "gift", "size": 180, "remain": 180, "end": "2026-10-06"},
                {"key": "base", "size": 820, "remain": 820, "end": "2026-12-01"},
            ],
        }
        after = {
            "total": 920,
            "items": [
                {"key": "base", "size": 820, "remain": 820, "end": "2026-12-01"},
                {"key": "checkin", "size": 100, "remain": 100, "end": "2026-10-07"},
            ],
        }
        decision = checkin.decide_daily_wallet(
            today="2026-10-06",
            claimed=True,
            before=before,
            before_error=None,
            after=after,
            after_error=None,
            ledger={"version": 1, "days": []},
        )
        self.assertIsNone(decision["error"])
        self.assertEqual(decision["total"], 920)
        self.assertEqual(decision["growth"], 100)
        self.assertEqual(decision["expired"], 180)
        self.assertNotEqual(decision["growth"], after["total"] - before["total"])

    def test_expiry_without_a_new_grant_still_fails(self) -> None:
        decision = checkin.decide_daily_wallet(
            today="2026-10-06",
            claimed=True,
            before={"total": 180, "items": [{"key": "gift", "size": 180, "remain": 180, "end": "2026-10-06"}]},
            before_error=None,
            after={"total": 0, "items": []},
            after_error=None,
            ledger={"version": 1, "days": []},
        )
        self.assertTrue(decision["no_growth"])
        self.assertEqual(decision["growth"], 0)
        self.assertEqual(decision["expired"], 180)
        self.assertIn("当前总额 0", decision["error"])
        self.assertIn("增长额 0", decision["error"])
        self.assertIn("失效 180", decision["error"])

    def test_previous_day_package_diff_keeps_grant_after_expiry(self) -> None:
        ledger = {
            "version": 1,
            "days": [
                {
                    "date": "2026-10-05",
                    "total": 1000,
                    "growth": 100,
                    "measured_by": "same_run",
                    "items": [
                        {"key": "gift", "size": 180, "remain": 180, "end": "2026-10-06"},
                        {"key": "base", "size": 820, "remain": 820, "end": "2026-12-01"},
                    ],
                }
            ],
        }
        after = {
            "total": 920,
            "items": [
                {"key": "base", "size": 820, "remain": 820, "end": "2026-12-01"},
                {"key": "checkin", "size": 100, "remain": 100, "end": "2026-10-07"},
            ],
        }
        decision = checkin.decide_daily_wallet(
            today="2026-10-06",
            claimed=False,
            before=after,
            before_error=None,
            after=after,
            after_error=None,
            ledger=ledger,
        )
        self.assertIsNone(decision["error"])
        self.assertEqual(decision["growth"], 100)
        self.assertEqual(decision["expired"], 180)
        self.assertEqual(decision["measured_by"], "previous_day")

    def test_missing_baseline_records_total_without_inventing_growth(self) -> None:
        decision = checkin.decide_daily_wallet(
            today="2026-10-06",
            claimed=False,
            before={"total": 1500, "packages": 1},
            before_error=None,
            after={"total": 1500, "packages": 1},
            after_error=None,
            ledger={"version": 1, "days": []},
        )
        self.assertIsNone(decision["error"])
        self.assertIsNone(decision["growth"])
        self.assertEqual(decision["ledger"]["days"][0]["total"], 1500)
        self.assertIsNone(decision["ledger"]["days"][0]["growth"])


class WalletRunTests(unittest.TestCase):
    def _env(self, ledger: str) -> dict[str, str]:
        return {
            "WORKBUDDY_ACCESS_TOKEN": "test-token",
            "WORKBUDDY_WALLET_LEDGER": ledger,
            "WORKBUDDY_WALLET_RETRY_SECONDS": "0",
        }

    def test_claim_records_real_wallet_delta(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ledger = os.path.join(tmp, "wallet-daily.json")
            readings = iter(
                (
                    ({"total": 5000, "packages": 2}, None),
                    ({"total": 5040, "packages": 3}, None),
                )
            )
            with mock.patch.dict(os.environ, self._env(ledger), clear=False):
                with mock.patch.object(checkin, "post_with_fallback", _claim_posts(600, 700)):
                    with mock.patch.object(checkin, "attach_growth"):
                        with mock.patch.object(checkin, "capture_wallet", side_effect=lambda creds: next(readings)):
                            with mock.patch.object(checkin, "emit") as emit:
                                code = checkin.run(status_only=False)
            payload = emit.call_args.args[0]
            with open(ledger, encoding="utf-8") as handle:
                ledger_text = handle.read()
        self.assertEqual(code, 0)
        self.assertEqual(payload["result"], "CLAIMED")
        self.assertEqual(payload["wallet"]["total"], 5040)
        self.assertEqual(payload["wallet"]["growth"], 40)
        self.assertEqual(payload["wallet"]["source"], "get-user-resource")
        self.assertIn("当前总额 5040", payload["report"])
        self.assertIn("增长额 40", payload["report"])
        self.assertIn("600 → 700", payload["report"])
        saved = json.loads(ledger_text)
        self.assertEqual(saved["days"][-1]["growth"], 40)
        self.assertNotIn("test-token", ledger_text)

    def test_claim_with_flat_wallet_fails_for_email(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ledger = os.path.join(tmp, "wallet-daily.json")
            flat = ({"total": 5000, "packages": 2}, None)
            with mock.patch.dict(os.environ, self._env(ledger), clear=False):
                with mock.patch.object(checkin, "post_with_fallback", _claim_posts(600, 700)):
                    with mock.patch.object(checkin, "attach_growth"):
                        with mock.patch.object(checkin, "capture_wallet", return_value=flat):
                            with self.assertRaises(checkin.CheckinError) as caught:
                                checkin.run(status_only=False)
            with open(ledger, encoding="utf-8") as handle:
                saved = json.loads(handle.read())
        self.assertEqual(caught.exception.exit_code, checkin.EXIT_BUSINESS)
        self.assertIn("当前总额 5000", str(caught.exception))
        self.assertIn("增长额 0", str(caught.exception))
        self.assertEqual(caught.exception.details["wallet"]["growth"], 0)
        self.assertEqual(caught.exception.details["wallet"]["total"], 5000)
        self.assertNotEqual(caught.exception.details["wallet"]["total"], 700)
        self.assertEqual(saved["days"][-1]["growth"], 0)

    def test_retry_accepts_balance_that_arrives_late(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ledger = os.path.join(tmp, "wallet-daily.json")
            readings = iter(
                (
                    ({"total": 5000, "packages": 1}, None),
                    ({"total": 5000, "packages": 1}, None),
                    ({"total": 5100, "packages": 2}, None),
                )
            )
            with mock.patch.dict(os.environ, self._env(ledger), clear=False):
                with mock.patch.object(checkin, "post_with_fallback", _claim_posts(600, 700)):
                    with mock.patch.object(checkin, "attach_growth"):
                        with mock.patch.object(checkin, "capture_wallet", side_effect=lambda creds: next(readings)):
                            with mock.patch.object(checkin, "emit") as emit:
                                code = checkin.run(status_only=False)
            payload = emit.call_args.args[0]
        self.assertEqual(code, 0)
        self.assertEqual(payload["wallet"]["growth"], 100)
        self.assertEqual(payload["wallet"]["total"], 5100)

    def test_already_without_yesterday_does_not_email(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ledger = os.path.join(tmp, "wallet-daily.json")
            with mock.patch.dict(os.environ, self._env(ledger), clear=False):
                with mock.patch.object(checkin, "post_with_fallback", lambda path, creds, extra_paths=(): (
                    "https://www.codebuddy.cn",
                    200,
                    _checked_in_body(),
                )):
                    with mock.patch.object(checkin, "attach_growth"):
                        with mock.patch.object(
                            checkin,
                            "capture_wallet",
                            return_value=({"total": 1500, "packages": 1}, None),
                        ):
                            with mock.patch.object(checkin, "emit") as emit:
                                code = checkin.run(status_only=False)
            payload = emit.call_args.args[0]
            with open(ledger, encoding="utf-8") as handle:
                saved = json.loads(handle.read())
        self.assertEqual(code, 0)
        self.assertEqual(payload["result"], "ALREADY")
        self.assertIsNone(payload["wallet"]["growth"])
        self.assertEqual(payload["wallet"]["total"], 1500)
        self.assertIn("增长额未知", payload["report"])
        self.assertEqual(saved["days"][-1]["total"], 1500)

    def test_main_emits_wallet_when_balance_does_not_grow(self) -> None:
        error = checkin.CheckinError(
            "真实账户积分未增长：当前总额 1，增长额 0",
            checkin.EXIT_BUSINESS,
            details={"wallet": {"total": 1, "growth": 0, "source": "get-user-resource"}},
        )
        with mock.patch.object(checkin, "run", side_effect=error):
            with mock.patch.object(checkin, "emit") as emit:
                code = checkin.main([])
        self.assertEqual(code, 4)
        body = emit.call_args.args[0]
        self.assertEqual(body["result"], "ERROR")
        self.assertEqual(body["wallet"]["growth"], 0)
        self.assertIn("当前总额 1", body["report"])
        self.assertIn("增长额 0", body["report"])


class LoginSecretTests(unittest.TestCase):
    def test_strips_quotes_and_bearer_prefix(self) -> None:
        parsed = checkin.interpret_login_secret('  "Bearer abc.def.ghi"  ')
        self.assertEqual(parsed["access_token"], "abc.def.ghi")
        self.assertEqual(parsed["refresh_token"], "abc.def.ghi")

    def test_json_login_file_keeps_refresh_separate(self) -> None:
        raw = json.dumps(
            {
                "account": {"uid": "u-1"},
                "auth": {
                    "accessToken": "access-1",
                    "refreshToken": "refresh-1",
                    "domain": "www.codebuddy.cn",
                },
            }
        )
        parsed = checkin.interpret_login_secret(raw)
        self.assertEqual(parsed["access_token"], "access-1")
        self.assertEqual(parsed["refresh_token"], "refresh-1")
        self.assertEqual(parsed["uid"], "u-1")
        self.assertEqual(parsed["domain"], "www.codebuddy.cn")

    def test_encrypted_access_token_is_not_used_as_bearer(self) -> None:
        raw = json.dumps(
            {
                "auth": {
                    "accessToken": {"$wbEncrypted": 1, "envelope": "cipher"},
                    "refreshToken": "refresh-1",
                }
            }
        )
        parsed = checkin.interpret_login_secret(raw)
        self.assertEqual(parsed["access_token"], "")
        self.assertEqual(parsed["refresh_token"], "refresh-1")

    def test_encrypted_blob_alone_is_rejected(self) -> None:
        raw = json.dumps({"$wbEncrypted": 1, "envelope": "cipher"})
        with mock.patch.dict(os.environ, {"WORKBUDDY_ACCESS_TOKEN": raw}, clear=False):
            with self.assertRaises(checkin.CheckinError) as caught:
                checkin.load_creds()
        self.assertEqual(caught.exception.exit_code, checkin.EXIT_USAGE)
        self.assertNotIn("cipher", str(caught.exception))

    def test_refresh_request_does_not_send_bearer(self) -> None:
        captured: dict[str, str] = {}

        class Response:
            status = 200

            def read(self) -> bytes:
                return b'{"code":0,"data":{"accessToken":"new-access"}}'

            def __enter__(self) -> "Response":
                return self

            def __exit__(self, *args: object) -> bool:
                return False

        def fake_urlopen(req: urllib.request.Request, timeout: int = 0) -> Response:
            captured.update({key.lower(): value for key, value in req.header_items()})
            captured["url"] = req.full_url
            return Response()

        creds = {
            "access_token": "expired-access",
            "refresh_token": "refresh-value",
            "domain": "www.codebuddy.cn",
            "uid": "",
            "enterprise_id": "",
        }
        with mock.patch.object(checkin.urllib.request, "urlopen", fake_urlopen):
            status, body = checkin.request_refresh("https://www.codebuddy.cn" + checkin.REFRESH_PATH, creds)
        self.assertEqual(status, 200)
        self.assertEqual(body["data"]["accessToken"], "new-access")
        self.assertEqual(captured["url"], "https://www.codebuddy.cn" + checkin.REFRESH_PATH)
        self.assertNotIn("authorization", captured)
        self.assertEqual(captured["x-refresh-token"], "refresh-value")

    def test_run_uses_refreshed_access_token_after_401(self) -> None:
        seen: list[str] = []

        def fake_post(path: str, creds: dict, extra_paths: tuple = ()) -> tuple:
            seen.append(creds["access_token"])
            if creds["access_token"] == "old-access":
                raise checkin.CheckinError("登录态已失效（HTTP 401 invalid_token）。", checkin.EXIT_NO_SESSION)
            body = {
                "code": 0,
                "data": {
                    "active": True,
                    "today_checked_in": True,
                    "credit": 100,
                    "streak_days": 3,
                    "total_credits": 300,
                },
            }
            return "https://www.codebuddy.cn", 200, body

        def fake_refresh(creds: dict) -> str:
            self.assertEqual(creds["refresh_token"], "refresh-1")
            self.assertNotEqual(creds["access_token"], "refresh-1")
            return "new-access"

        raw = json.dumps({"auth": {"accessToken": "old-access", "refreshToken": "refresh-1"}})
        with tempfile.TemporaryDirectory() as tmp:
            env = {
                "WORKBUDDY_ACCESS_TOKEN": raw,
                "WORKBUDDY_REFRESH_TOKEN": "",
                "WORKBUDDY_WALLET_LEDGER": os.path.join(tmp, "wallet-daily.json"),
                "WORKBUDDY_WALLET_RETRY_SECONDS": "0",
            }
            with mock.patch.dict(os.environ, env, clear=False):
                with mock.patch.object(checkin, "post_with_fallback", fake_post):
                    with mock.patch.object(checkin, "refresh_access_token", fake_refresh):
                        with mock.patch.object(checkin, "attach_growth"):
                            with mock.patch.object(
                                checkin,
                                "capture_wallet",
                                return_value=({"total": 1000, "packages": 1}, None),
                            ):
                                with mock.patch.object(checkin, "emit"):
                                    code = checkin.run(status_only=False)
        self.assertEqual(code, 0)
        self.assertEqual(seen, ["old-access", "new-access"])

    def test_refresh_failure_does_not_echo_secret(self) -> None:
        secret = "refresh-secret-value"
        payload = {"exp": 1700000000}
        import base64

        def pack(data: dict) -> str:
            raw = json.dumps(data, separators=(",", ":")).encode()
            return base64.urlsafe_b64encode(raw).decode().rstrip("=")

        token = f"{pack({'alg': 'none'})}.{pack(payload)}.sig"

        def fake_refresh(url: str, creds: dict) -> tuple:
            return 401, {"code": 12153, "msg": f"bad {creds['refresh_token']}"}

        creds = {
            "access_token": token,
            "refresh_token": secret,
            "domain": "www.codebuddy.cn",
            "uid": "",
            "enterprise_id": "",
        }
        with mock.patch.object(checkin, "request_refresh", fake_refresh):
            with mock.patch.object(checkin, "api_bases", return_value=["https://www.codebuddy.cn"]):
                with self.assertRaises(checkin.CheckinError) as caught:
                    checkin.refresh_access_token(creds)
        text = str(caught.exception)
        self.assertEqual(caught.exception.exit_code, checkin.EXIT_NO_SESSION)
        self.assertNotIn(secret, text)
        self.assertNotIn(token, text)
        self.assertIn("12153", text)
        self.assertIn("2023-11-15", text)


class ActivityReportTests(unittest.TestCase):
    def test_event_is_one_desktop_chat_and_carries_the_user_id(self) -> None:
        bodies = checkin.activity_report_bodies("user-1", 1000, "wb-conv", "cmb-req")
        self.assertEqual(len(bodies), 2)
        event = bodies[0][0]
        self.assertEqual(event["eventCode"], "chat_request_send")
        self.assertEqual(event["mode"], "craft")
        self.assertEqual(event["userId"], "user-1")
        self.assertEqual(event["inputLength"], len(checkin.ACTIVITY_PROMPT))
        self.assertGreater(len(checkin.ACTIVITY_PROMPT), 1)
        self.assertNotIn("messages", event)
        self.assertEqual(bodies[0][1]["eventCode"], "chat_request_response")
        self.assertIs(bodies[0][1]["isSuccessful"], True)
        self.assertEqual(bodies[1]["events"][0]["eventCode"], "chat_request_send")
        self.assertEqual(bodies[1]["events"][0]["mode"], "craft")
        self.assertEqual(bodies[1]["common"]["userId"], "user-1")

    def test_missing_user_id_does_not_call_the_network(self) -> None:
        with mock.patch.object(checkin, "request_text") as request:
            ok, detail = checkin.send_activity_report("https://www.codebuddy.cn", {"access_token": "not-a-jwt"})
        request.assert_not_called()
        self.assertFalse(ok)
        self.assertIn("用户编号", detail)
        self.assertNotIn("not-a-jwt", detail)

    def test_array_rejection_tries_the_wrapped_body_once(self) -> None:
        seen: list = []

        def fake_request(url: str, creds: dict, payload) -> tuple[int, str]:
            seen.append(payload)
            if isinstance(payload, list):
                return 400, '{"code":10001,"msg":"bad shape"}'
            return 200, '{"code":0}'

        with mock.patch.object(checkin, "request_text", fake_request):
            ok, detail = checkin.send_activity_report(
                "https://www.codebuddy.cn",
                {"uid": "user-1", "access_token": ""},
            )
        self.assertTrue(ok)
        self.assertEqual(detail, "")
        self.assertEqual(len(seen), 2)
        self.assertIsInstance(seen[0], list)
        self.assertIsInstance(seen[1], dict)

    def test_zero_score_reports_once_and_positive_score_does_not(self) -> None:
        calls = {"report": 0, "heat": 0}

        def fake_growth(base, path, creds, method="GET", payload=None):
            if path == "/streak":
                return 200, {
                    "code": 0,
                    "data": {
                        "streak": {"days": 1, "makeup_dates": []},
                        "makeup_cards": {"balance": 0},
                        "launch_date": "2026-06-17",
                    },
                }
            if path == "/heatmap":
                calls["heat"] += 1
                score = 0 if calls["heat"] == 1 else 3
                return 200, {"code": 0, "data": {"today": {"date": "2026-10-26", "score": score}, "cells": []}}
            if path == "/tasks":
                return 200, {"code": 0, "data": {"tasks": []}}
            if path == "/redeem/summary":
                return 200, {"code": 0, "data": {}}
            return 404, {}

        def fake_report(base, creds):
            calls["report"] += 1
            return True, ""

        with mock.patch.object(checkin, "call_growth", fake_growth):
            with mock.patch.object(checkin, "send_activity_report", fake_report):
                with mock.patch.object(checkin, "send_web_conversation") as web:
                    note, detail = checkin.maintain_login_streak(
                        {"uid": "user-1"},
                        "https://www.codebuddy.cn",
                    )
        web.assert_not_called()
        self.assertEqual(calls["report"], 1)
        self.assertIn("已上报 1 条桌面对话事件", note)
        self.assertIn("活跃地图今天已点亮", note)
        self.assertEqual(detail["today_score"], 3)
        self.assertIs(detail["calendar_lit"], True)
        self.assertNotIn("user-1", note)

        calls["report"] = 0
        calls["heat"] = 0

        def already_active(base, path, creds, method="GET", payload=None):
            if path == "/heatmap":
                return 200, {"code": 0, "data": {"today": {"date": "2026-10-26", "score": 7}, "cells": []}}
            return fake_growth(base, path, creds, method, payload)

        with mock.patch.object(checkin, "call_growth", already_active):
            with mock.patch.object(checkin, "send_activity_report", fake_report):
                note, _detail = checkin.maintain_login_streak({"uid": "user-1"}, "https://www.codebuddy.cn")
        self.assertEqual(calls["report"], 0)
        self.assertNotIn("桌面对话事件", note)

        calls["report"] = 0
        calls["heat"] = 0
        with mock.patch.dict(os.environ, {"WORKBUDDY_SCHEDULE": "36 16 * * *"}, clear=False):
            with mock.patch.object(checkin, "call_growth", fake_growth):
                with mock.patch.object(checkin, "send_activity_report", fake_report):
                    note, _detail = checkin.maintain_login_streak(
                        {"uid": "user-1"},
                        "https://www.codebuddy.cn",
                    )
        self.assertEqual(calls["report"], 1)
        self.assertIn("已上报 1 条桌面对话事件", note)
        self.assertNotIn("先不补连登", note)

    def test_interface_score_does_not_hide_an_empty_calendar_cell(self) -> None:
        body = {
            "data": {
                "today": {"date": "2026-11-01", "score": 2},
                "cells": [
                    {"date": "2026-10-31", "score": 2},
                    {"date": "2026-11-01", "score": 0},
                ],
            }
        }
        self.assertEqual(checkin.calendar_today_score(body), 0)
        self.assertEqual(checkin.today_activity_score(body), 2)

    def test_empty_cell_sends_one_web_conversation(self) -> None:
        calls = {"report": 0, "web": 0}

        def fake_growth(base, path, creds, method="GET", payload=None):
            if path == "/streak":
                return 200, {
                    "code": 0,
                    "data": {
                        "streak": {"days": 0, "makeup_dates": []},
                        "makeup_cards": {"balance": 0},
                        "launch_date": "2026-06-17",
                    },
                }
            if path == "/heatmap":
                return 200, {
                    "code": 0,
                    "data": {
                        "today": {"date": "2026-11-01", "score": 2},
                        "cells": [{"date": "2026-11-01", "score": 0}],
                    },
                }
            if path == "/tasks":
                return 200, {"code": 0, "data": {"tasks": []}}
            if path == "/redeem/summary":
                return 200, {"code": 0, "data": {}}
            return 404, {}

        def fake_report(base, creds):
            calls["report"] += 1
            return True, ""

        def fake_web(creds):
            calls["web"] += 1
            self.assertEqual(creds["uid"], "user-1")
            return False, "HTTP 404"

        with mock.patch.object(checkin, "call_growth", fake_growth):
            with mock.patch.object(checkin, "send_activity_report", fake_report):
                with mock.patch.object(checkin, "send_web_conversation", fake_web):
                    note, detail = checkin.maintain_login_streak(
                        {"uid": "user-1"},
                        "https://www.codebuddy.cn",
                    )
        self.assertGreaterEqual(calls["report"], 1)
        self.assertEqual(calls["web"], 1)
        self.assertIs(detail["calendar_lit"], False)
        self.assertIn("活跃地图仍是轻轻路过", note)
        self.assertIn("对话没有回复", note)
        self.assertNotIn("user-1", note)

    def test_web_conversation_uses_browser_headers_and_hides_the_token(self) -> None:
        seen: list[tuple[str, dict, dict]] = []

        def fake_request(url, creds, payload, headers=None, timeout=None, method="POST"):
            seen.append((url, headers or {}, payload if isinstance(payload, dict) else {}))
            return 200, 'data: {"choices":[{"delta":{"content":"好的，已收到。"}}]}\n\ndata: [DONE]\n'

        env = {"WORKBUDDY_CHAT_MODEL": "deepseek-v4-flash"}
        with mock.patch.dict(os.environ, env, clear=False):
            with mock.patch.object(checkin, "request_text", fake_request):
                ok, detail = checkin.send_web_conversation(
                    {"uid": "user-1", "access_token": "secret-token"}
                )
        self.assertTrue(ok)
        self.assertEqual(detail, "")
        self.assertTrue(seen)
        url, _headers, payload = seen[0]
        self.assertIn("https://www.codebuddy.cn/v2/chat/completions", url)
        self.assertEqual(payload["messages"][0]["content"], checkin.ACTIVITY_PROMPT)
        self.assertNotEqual(payload["messages"][0]["content"], "1")
        self.assertEqual(payload["max_tokens"], checkin.ACTIVITY_MAX_TOKENS)
        self.assertEqual(payload["model"], "deepseek-v4-flash")
        self.assertNotIn("secret-token", detail)
        self.assertEqual(len(seen), 1)

    def test_passby_score_still_asks_for_a_real_reply(self) -> None:
        calls = {"web": 0}

        def fake_growth(base, path, creds, method="GET", payload=None):
            if path == "/streak":
                return 200, {
                    "code": 0,
                    "data": {
                        "streak": {"days": 1, "makeup_dates": []},
                        "makeup_cards": {"balance": 0},
                        "launch_date": "2026-06-17",
                    },
                }
            if path == "/heatmap":
                return 200, {
                    "code": 0,
                    "data": {
                        "today": {"date": "2026-11-01", "score": 2},
                        "cells": [{"date": "2026-11-01", "score": 2}],
                    },
                }
            if path == "/tasks":
                return 200, {"code": 0, "data": {"tasks": []}}
            if path == "/redeem/summary":
                return 200, {"code": 0, "data": {}}
            return 404, {}

        def fake_web(creds):
            calls["web"] += 1
            return True, ""

        with mock.patch.object(checkin, "call_growth", fake_growth):
            with mock.patch.object(checkin, "send_activity_report") as report:
                with mock.patch.object(checkin, "send_web_conversation", fake_web):
                    note, detail = checkin.maintain_login_streak(
                        {"uid": "user-1"},
                        "https://www.codebuddy.cn",
                    )
        report.assert_not_called()
        self.assertEqual(calls["web"], 1)
        self.assertIn("今日是轻轻路过", note)
        self.assertIn("已完成 1 次有回复的对话", note)
        self.assertIs(detail["calendar_lit"], False)
        self.assertIn("活跃地图仍是轻轻路过", note)


class ClientRecordTests(unittest.TestCase):
    def test_headers_match_desktop_client(self) -> None:
        headers = checkin.build_headers(
            {
                "access_token": "t",
                "domain": "www.codebuddy.cn",
                "uid": "u",
                "enterprise_id": "",
            }
        )
        self.assertEqual(headers["User-Agent"], "WorkBuddy/5.8.3")
        self.assertEqual(headers["X-IDE-Type"], "WorkBuddy")
        self.assertEqual(headers["X-IDE-Name"], "WorkBuddy")
        self.assertEqual(headers["X-IDE-Version"], "5.8.3")
        self.assertEqual(headers["X-Product"], "SaaS")
        self.assertNotIn("Cloud-Checkin", headers["User-Agent"])

    def test_today_flag_marks_the_client_record(self) -> None:
        record = checkin.client_record({"data": {"today_checked_in": True, "week_checkin_days": 3}})
        self.assertTrue(checkin.client_record_marks_today(record, date(2026, 10, 26)))
        self.assertEqual(record["week_checkin_days"], 3)

    def test_dates_without_today_are_unmarked(self) -> None:
        record = checkin.client_record({"data": {"checkin_dates": ["2026-10-25"]}})
        self.assertFalse(checkin.client_record_marks_today(record, date(2026, 10, 26)))

    def test_week_progress_uses_monday_as_the_first_day(self) -> None:
        record = checkin.client_record(
            {"data": {"week_progress": [True, False, False, False, False, False, False]}}
        )
        self.assertTrue(checkin.client_record_marks_today(record, date(2026, 10, 26)))
        self.assertFalse(checkin.client_record_marks_today(record, date(2026, 10, 27)))

    def test_missing_calendar_is_unknown(self) -> None:
        record = checkin.client_record({"data": {"streak_days": 1}})
        self.assertIsNone(checkin.client_record_marks_today(record, date(2026, 10, 26)))

    def test_claim_fails_when_client_record_omits_today(self) -> None:
        def fake_post(path: str, creds: dict, extra_paths: tuple = ()) -> tuple:
            if path == checkin.CLAIM_PATH:
                data = {"credit": 100, "success": True, "streak_days": 2, "total_credits": 200}
            elif path == checkin.STATUS_PATHS[0]:
                if not hasattr(fake_post, "seen"):
                    fake_post.seen = True
                    data = {
                        "active": True,
                        "today_checked_in": False,
                        "total_credits": 100,
                        "streak_days": 1,
                    }
                else:
                    data = {
                        "active": True,
                        "today_checked_in": False,
                        "checkin_dates": ["2026-10-25"],
                        "credit": 100,
                        "total_credits": 200,
                        "streak_days": 2,
                    }
            else:
                data = {}
            return "https://www.codebuddy.cn", 200, {"code": 0, "data": data}

        with mock.patch.dict(
            os.environ,
            {"WORKBUDDY_ACCESS_TOKEN": "test-token", "WORKBUDDY_WALLET_RETRY_SECONDS": "0"},
            clear=False,
        ):
            with mock.patch.object(checkin, "post_with_fallback", fake_post):
                with mock.patch.object(checkin, "attach_growth"):
                    with mock.patch.object(
                        checkin,
                        "capture_wallet",
                        return_value=({"total": 1000, "packages": 1}, None),
                    ):
                        with self.assertRaises(checkin.CheckinError) as caught:
                            checkin.run(status_only=False)
        self.assertEqual(caught.exception.exit_code, checkin.EXIT_BUSINESS)
        self.assertIn("客户端加油站", str(caught.exception))


class PublicLogTests(unittest.TestCase):
    def sample(self) -> dict:
        return {
            "result": "ALREADY",
            "report": "今日已签过（今日 +100，连续 3 天，累计 300 积分）；当前总额 5200，增长额 100",
            "client_record": {
                "today_checked_in": True,
                "checkin_dates": ["2026-11-01", "2026-11-02"],
                "week_progress": [True, False, False, False, False, False, False],
            },
            "wallet": {
                "source": "get-user-resource",
                "total": 5200,
                "growth": 100,
                "baseline": 5100,
                "expired": None,
                "measured_by": "same_run",
                "packages": 2,
            },
        }

    def test_actions_log_hides_balance_and_checkin_dates(self) -> None:
        payload = self.sample()
        with mock.patch.dict(
            os.environ,
            {"GITHUB_ACTIONS": "true", "WORKBUDDY_SHOW_BALANCE": ""},
            clear=False,
        ):
            shown = checkin.public_log(payload)
        self.assertNotIn("total", shown["wallet"])
        self.assertNotIn("baseline", shown["wallet"])
        self.assertEqual(shown["wallet"]["growth"], 100)
        self.assertEqual(shown["wallet"]["packages"], 2)
        self.assertNotIn("checkin_dates", shown["client_record"])
        self.assertTrue(shown["client_record"]["today_checked_in"])
        self.assertNotIn("5200", shown["report"])
        self.assertIn("当前总额已省略", shown["report"])
        self.assertIn("增长额 100", shown["report"])
        self.assertIn("累计 300", shown["report"])
        self.assertEqual(payload["wallet"]["total"], 5200)
        self.assertEqual(payload["client_record"]["checkin_dates"], ["2026-11-01", "2026-11-02"])

    def test_show_balance_flag_restores_numbers_on_actions(self) -> None:
        with mock.patch.dict(
            os.environ,
            {"GITHUB_ACTIONS": "true", "WORKBUDDY_SHOW_BALANCE": "1"},
            clear=False,
        ):
            shown = checkin.public_log(self.sample())
        self.assertEqual(shown["wallet"]["total"], 5200)
        self.assertIn("5200", shown["report"])

    def test_local_run_keeps_balance(self) -> None:
        with mock.patch.dict(os.environ, {"GITHUB_ACTIONS": "", "WORKBUDDY_SHOW_BALANCE": ""}, clear=False):
            shown = checkin.public_log(self.sample())
        self.assertEqual(shown["wallet"]["total"], 5200)

    def test_missing_balance_phrase_stays(self) -> None:
        self.assertEqual(checkin._hide_balance_phrase("当前总额 缺失，增长额未知"), "当前总额 缺失，增长额未知")

    def test_step_summary_omits_total_on_actions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            summary = os.path.join(tmp, "summary.md")
            decision = {"total": 5200, "growth": 100, "expired": 0}
            with mock.patch.dict(
                os.environ,
                {
                    "GITHUB_ACTIONS": "true",
                    "WORKBUDDY_SHOW_BALANCE": "",
                    "GITHUB_STEP_SUMMARY": summary,
                },
                clear=False,
            ):
                checkin.write_wallet_summary(decision)
            with open(summary, encoding="utf-8") as handle:
                text = handle.read()
        self.assertNotIn("5200", text)
        self.assertIn("增长额：100", text)

    def test_logged_message_hides_uid_token_and_enterprise_id(self) -> None:
        body = {"msg": "user abcdefgh token secret-token-value enterprise ent-12345678"}
        creds = {
            "access_token": "secret-token-value",
            "refresh_token": "",
            "uid": "abcdefgh",
            "enterprise_id": "ent-12345678",
        }
        text = checkin.logged_message(body, creds)
        self.assertNotIn("abcdefgh", text)
        self.assertNotIn("secret-token-value", text)
        self.assertNotIn("ent-12345678", text)
        self.assertIn("[secret]", text)


if __name__ == "__main__":
    unittest.main()
