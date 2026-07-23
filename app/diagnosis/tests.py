from django.test import SimpleTestCase

from .views import _get_expected_grade


class ExpectedGradeTest(SimpleTestCase):
    def test_total_score_thresholds(self):
        self.assertEqual(_get_expected_grade(80), "1급")
        self.assertEqual(_get_expected_grade(70), "2급")
        self.assertEqual(_get_expected_grade(60), "3급")
        self.assertEqual(_get_expected_grade(59), "탈락")
