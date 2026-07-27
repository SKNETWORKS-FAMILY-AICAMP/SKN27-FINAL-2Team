"""추세 판정 회귀 테스트.

determine_trend 는 두 구간의 윌슨 하한을 비교한다. 하한은 표본 수가
늘어나는 것만으로도 올라가므로, 표본 크기가 크게 다른 두 구간을 비교하면
실력 변화가 아니라 표본 변화를 재게 되어 판정이 실제 성적과 반대로 나온다.
이 파일은 그 뒤집힘이 다시 생기지 않는지 고정한다.
"""

from __future__ import annotations

from unittest import TestCase

from analytics.service.weakness import determine_trend, get_weakness_config


CONFIG = get_weakness_config()


def trend_of(previous: tuple[int, int], recent: tuple[int, int]) -> str:
    previous_wrong, previous_total = previous
    recent_wrong, recent_total = recent
    return str(
        determine_trend(recent_wrong, recent_total, previous_wrong, previous_total, CONFIG)["trend"]
    )


class TrendInversionTests(TestCase):
    def test_shrinking_sample_no_longer_reads_as_improvement(self) -> None:
        """92% 에서 100% 로 나빠졌는데 좋아졌다고 판정하던 사례."""
        self.assertEqual(trend_of((11, 12), (3, 3)), CONFIG.trend_unknown)

    def test_growing_sample_no_longer_reads_as_decline(self) -> None:
        """100% 에서 84% 로 좋아졌는데 나빠졌다고 판정하던 사례."""
        self.assertEqual(trend_of((3, 3), (32, 38)), CONFIG.trend_unknown)

    def test_identical_rate_with_different_samples_is_not_a_change(self) -> None:
        self.assertEqual(trend_of((2, 4), (10, 20)), CONFIG.trend_unknown)

    def test_no_inversion_across_the_whole_sample_space(self) -> None:
        """표본 1~24 전 조합에서 판정이 실제 변화와 어긋나지 않아야 한다."""
        inversions = []
        for previous_total in range(1, 25):
            for recent_total in range(1, 25):
                for previous_wrong in range(previous_total + 1):
                    for recent_wrong in range(recent_total + 1):
                        trend = trend_of(
                            (previous_wrong, previous_total),
                            (recent_wrong, recent_total),
                        )
                        if trend == CONFIG.trend_unknown:
                            continue
                        change = recent_wrong / recent_total - previous_wrong / previous_total
                        if change > 0.02 and trend == CONFIG.trend_improving:
                            inversions.append((previous_wrong, previous_total, recent_wrong, recent_total))
                        elif change < -0.02 and trend == CONFIG.trend_worsening:
                            inversions.append((previous_wrong, previous_total, recent_wrong, recent_total))
                        elif abs(change) < 0.005 and trend != CONFIG.trend_flat:
                            inversions.append((previous_wrong, previous_total, recent_wrong, recent_total))
        self.assertEqual(inversions, [])


class TrendStillJudgesNormalCasesTests(TestCase):
    """보류가 늘었다고 정상 비교까지 막으면 안 된다."""

    def test_clear_improvement(self) -> None:
        self.assertEqual(trend_of((9, 12), (3, 12)), CONFIG.trend_improving)

    def test_clear_decline(self) -> None:
        self.assertEqual(trend_of((3, 12), (9, 12)), CONFIG.trend_worsening)

    def test_no_change_is_flat(self) -> None:
        self.assertEqual(trend_of((6, 12), (6, 12)), CONFIG.trend_flat)

    def test_moderately_different_samples_are_still_compared(self) -> None:
        self.assertEqual(trend_of((14, 20), (5, 16)), CONFIG.trend_improving)


class TrendGuardBoundaryTests(TestCase):
    def test_sample_below_the_minimum_is_withheld(self) -> None:
        minimum = CONFIG.trend_minimum_sample
        self.assertEqual(trend_of((minimum - 1, minimum - 1), (minimum, minimum)), CONFIG.trend_unknown)

    def test_unbalanced_samples_are_withheld(self) -> None:
        """한쪽이 절반 미만이면 비교하지 않는다."""
        self.assertEqual(trend_of((10, 20), (5, 9)), CONFIG.trend_unknown)

    def test_balanced_samples_at_the_boundary_are_compared(self) -> None:
        self.assertNotEqual(trend_of((15, 20), (3, 10)), CONFIG.trend_unknown)
