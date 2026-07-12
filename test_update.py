import unittest

import update


class RegulationScoreTests(unittest.TestCase):
    def test_extracts_first_two_periods_from_summary(self):
        data = {"header": {"competitions": [{"competitors": [
            {"homeAway": "home", "linescores": [
                {"displayValue": "1"}, {"displayValue": "1"},
                {"displayValue": "0"}, {"displayValue": "1"},
            ]},
            {"homeAway": "away", "linescores": [
                {"displayValue": "1"}, {"displayValue": "1"},
                {"displayValue": "0"}, {"displayValue": "0"},
            ]},
        ]}]}}
        self.assertEqual(update.regulation_score_from_summary(data), (2, 2))

    def test_rejects_incomplete_period_data(self):
        data = {"header": {"competitions": [{"competitors": [
            {"homeAway": "home", "linescores": [{"displayValue": "1"}]},
            {"homeAway": "away", "linescores": [{"displayValue": "0"}]},
        ]}]}}
        self.assertIsNone(update.regulation_score_from_summary(data))

    def test_scoring_uses_regulation_score(self):
        match = {
            "home_score": 3, "away_score": 2,
            "home_score_90": 1, "away_score_90": 1,
        }
        actual = update.regulation_score(match)
        self.assertEqual(update.match_points((1, 1), actual)[0], 2)
        self.assertEqual(update.match_points((3, 2), actual)[0], 0)

    def test_legacy_normal_time_match_falls_back_to_final_score(self):
        match = {"home_score": 2, "away_score": 0, "status_detail": "FT"}
        self.assertEqual(update.regulation_score(match), (2, 0))

    def test_missing_summary_does_not_score_extra_time_result(self):
        match = {"home_score": 3, "away_score": 2, "status_detail": "AET"}
        self.assertEqual(update.regulation_score(match), (None, None))

    def test_penalties_and_extra_time_are_detected(self):
        self.assertTrue(update.went_to_extra_time({"status_detail": "AET"}))
        self.assertTrue(update.went_to_extra_time({"status_detail": "FT-Pens"}))
        self.assertFalse(update.went_to_extra_time({"status_detail": "FT", "display_clock": "90'+5'"}))

    def test_knockout_payload_exposes_regulation_score(self):
        store = {"knockout_matches": [{
            "round": "Quarterfinals", "home": "Argentina", "away": "Switzerland",
            "home_score": 3, "away_score": 1,
            "home_score_90": 1, "away_score_90": 1,
            "played": True, "status_detail": "AET",
        }]}
        match = update.knockout_payload(store)[0]
        self.assertEqual((match["hs"], match["as"]), (3, 1))
        self.assertEqual((match["hs90"], match["as90"]), (1, 1))


if __name__ == "__main__":
    unittest.main()
