"""HQ-020 · блок 1 Pack: подписи статуса закреплены за порядком порогов methodology.yaml.
PASS — самый низкий (отказ: вне тезиса или < pass_min_substantive_claims утверждений),
NEXT — самый высокий (readiness ≥ next_min, крит. пробелов ≤ max_critical_gaps_for_next, без конфликтов)."""
import unittest

from s1_intake.config import load_methodology
from s1_intake.scoring import decide

M = load_methodology()
SC = M["scoring"]
MAP = M["working"]["pack_block1"]["map"]
RANK = {"NOT READY": 0, "ITERATE": 1, "READY FOR INVESTOR CONVERSATION": 2}


class Block1OrderTests(unittest.TestCase):
    def verdict(self, readiness, claims, gaps=()):
        return decide(readiness, True, list(gaps), [], M, claims)[0]

    def test_thresholds_order_pass_lowest_next_highest(self):
        n = SC["next_min"]
        low = self.verdict(100, SC["pass_min_substantive_claims"] - 1)          # даже при 100 — PASS
        mid = self.verdict(n - 1, SC["pass_min_substantive_claims"])
        high = self.verdict(n, SC["pass_min_substantive_claims"])
        self.assertEqual((low, mid, high), ("PASS", "HOLD", "NEXT"))
        self.assertEqual(self.verdict(n, 99, ["g"] * (SC["max_critical_gaps_for_next"] + 1)), "HOLD")
        self.assertEqual(decide(100, False, [], [], M, 99)[0], "PASS")         # вне тезиса — PASS

    def test_mapping_follows_threshold_order(self):
        order = ["PASS", "HOLD", "NEXT"]  # от низшего к высшему по порогам выше
        self.assertEqual([RANK[MAP[v]] for v in order], [0, 1, 2])
        self.assertEqual(MAP, {"NEXT": "READY FOR INVESTOR CONVERSATION", "HOLD": "ITERATE", "PASS": "NOT READY"})


if __name__ == "__main__":
    unittest.main()
