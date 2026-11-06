from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

import pandas as pd


SCRIPT = Path(__file__).with_name("analyze_ad_reports.py")
SPEC = importlib.util.spec_from_file_location("ad_analyzer", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class MetricContractTests(unittest.TestCase):
    def test_zero_denominator_is_null(self) -> None:
        value, reason = MODULE.safe_ratio(12, 0, "acos")
        self.assertIsNone(value)
        self.assertEqual(reason, "acos_denominator_zero")

    def test_wilson_interval_is_bounded(self) -> None:
        interval = MODULE.wilson_interval(2, 20, 0.95)
        self.assertIsNone(interval["reason"])
        self.assertGreaterEqual(interval["lower"], 0)
        self.assertLessEqual(interval["upper"], 1)
        self.assertLess(interval["lower"], interval["upper"])

    def test_orders_above_clicks_do_not_fake_interval(self) -> None:
        interval = MODULE.wilson_interval(3, 2, 0.95)
        self.assertEqual(interval["reason"], "orders_outside_binomial_range")
        self.assertIsNone(interval["lower"])

    def test_builder_plan_hash_contract_is_verified(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "ad_plan.json"
            plan = {
                "schema_version": "2.0",
                "plan_id": "AD-TEST",
                "plan_version": 1,
                "hash_schema": "ad-plan-c14n-v1",
                "_meta": {"generated_at": "2026-08-09T00:00:00Z"},
                "campaigns": [],
            }
            plan["plan_hash"] = MODULE.compute_ad_plan_hash(plan)
            path.write_text(json.dumps(plan), encoding="utf-8")
            self.assertTrue(MODULE.plan_reference(str(path))["plan_hash_valid"])
            plan["campaigns"].append({"campaign_name": "mutated"})
            path.write_text(json.dumps(plan), encoding="utf-8")
            self.assertFalse(MODULE.plan_reference(str(path))["plan_hash_valid"])

            plan["campaigns"] = []
            plan["hash_schema"] = "unsupported-c14n"
            plan["plan_hash"] = MODULE.compute_ad_plan_hash(plan)
            path.write_text(json.dumps(plan), encoding="utf-8")
            self.assertFalse(MODULE.plan_reference(str(path))["plan_hash_valid"])

    def test_plan_hash_normalizes_unicode_and_ignores_generation_time(self) -> None:
        left = {
            "hash_schema": "ad-plan-c14n-v1",
            "assumptions": ["cafe\u0301"],
            "_meta": {"generated_at": "2026-08-09T00:00:00Z"},
        }
        right = {
            "hash_schema": "ad-plan-c14n-v1",
            "assumptions": ["caf\u00e9"],
            "_meta": {"generated_at": "2099-01-01T00:00:00Z"},
        }
        self.assertEqual(
            MODULE.compute_ad_plan_hash(left), MODULE.compute_ad_plan_hash(right)
        )

    def test_filename_short_ad_type_code(self) -> None:
        frame = pd.DataFrame({"campaign": ["x"]})
        self.assertEqual(MODULE.infer_ad_type(frame, Path("SD_Campaign_Report.csv"), "CSV"), "SD")

    def test_evidence_builders_are_conservative(self) -> None:
        empty = {"search_term": pd.DataFrame()}
        self.assertEqual(MODULE.build_traffic_concentration(empty)["status"], "unknown")
        self.assertFalse(MODULE.build_natural_flow_impact({})["causal_claim"])
        contribution = MODULE.build_contribution_evidence({}, {"campaign": pd.DataFrame()})
        self.assertEqual(contribution["reason"], "expected_baseline_not_supplied")

    def test_negative_preview_is_non_executable(self) -> None:
        frame = pd.DataFrame([{
            "search_term": "bad term", "clicks": 20, "orders": 0, "impressions": 100,
            "spend": 5, "sales": 0, "source_unit_id": "U1", "source_id": "S1",
            "attribution_window_days": 7,
        }])
        previews = MODULE.build_negative_impact_previews({"search_term": frame}, 20)
        self.assertEqual(len(previews), 1)
        self.assertFalse(previews[0]["executable"])


class EndToEndTests(unittest.TestCase):
    def test_mixed_headers_and_attribution_windows_stay_separate(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            inputs = root / "inputs"
            outputs = root / "outputs"
            inputs.mkdir()

            sp = pd.DataFrame([
                {
                    "Campaign Name": "SP-RES",
                    "广告组名称": "RES-1",
                    "Targeting": "drawer organizer",
                    "Match Type": "BROAD",
                    "Customer Search Term": "drawer divider",
                    "Impressions": 1000,
                    "点击量": 25,
                    "Spend": 20,
                    "7 Day Total Sales": 0,
                    "7 Day Total Orders (#)": 0,
                },
                {
                    "Campaign Name": "SP-RES",
                    "广告组名称": "RES-1",
                    "Targeting": "drawer organizer",
                    "Match Type": "BROAD",
                    "Customer Search Term": "adjustable drawer divider",
                    "Impressions": 800,
                    "点击量": 20,
                    "Spend": 12,
                    "7 Day Total Sales": 60,
                    "7 Day Total Orders (#)": 2,
                },
            ])
            sp.to_csv(inputs / "SP_Search_Term_Report.csv", index=False, encoding="utf-8-sig")

            sb = pd.DataFrame([
                {
                    "广告活动名称": "SB-VIDEO",
                    "投放": "drawer dividers",
                    "匹配类型": "EXACT",
                    "展示量": 500,
                    "点击量": 10,
                    "花费": 8,
                    "14天总销售额": 40,
                    "14天总订单数(#)": 1,
                }
            ])
            sb.to_csv(inputs / "SB_品牌推广_关键词.csv", index=False, encoding="utf-8-sig")

            exit_code = MODULE.main([
                "--input", str(inputs),
                "--output", str(outputs),
                "--run-id", "RUN-TEST",
                "--cycle-id", "CYCLE-TEST",
            ])
            self.assertEqual(exit_code, 0)

            analysis = json.loads((outputs / "ad_analysis.yaml").read_text(encoding="utf-8"))
            self.assertEqual(analysis["coverage"]["accepted_source_units"], 2)
            self.assertEqual(analysis["scope"]["attribution_windows"], [7, 14])
            self.assertFalse(analysis["coverage"]["cross_attribution_window_aggregation"])
            self.assertEqual(set(analysis["coverage"]["ad_types"]), {"SP", "SB"})

            search_terms = pd.read_csv(outputs / "aggregations" / "search_term.csv")
            no_sale = search_terms.loc[search_terms["search_term"] == "drawer divider"].iloc[0]
            self.assertTrue(pd.isna(no_sale["acos"]))
            self.assertEqual(no_sale["acos_reason"], "acos_denominator_zero")

            actions = json.loads((outputs / "optimization_actions.yaml").read_text(encoding="utf-8"))
            self.assertTrue(actions["actions"])
            self.assertTrue(all(action["requires_user_confirmation"] for action in actions["actions"]))
            self.assertTrue(all(not transfer["source_negative"]["executable"] for transfer in actions["transfers"]))


if __name__ == "__main__":
    unittest.main()
