"""Regress native/grid readability, real segment clearance, and fixed budgets."""
import math
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'workers'))
from circuit_path_readability import simplify_polyline, polyline_length, direction_changes, short_bend_issues


def staircase(count=24):
    points = [(0, 0)]
    for _ in range(count):
        x, y = points[-1]
        points.extend(((x+1, y), (x+4, y+3)))
    return points


def on_segment(point, a, b):
    dx, dy = b[0]-a[0], b[1]-a[1]
    return abs((point[0]-a[0])*dy-(point[1]-a[1])*dx) < 1e-8 and \
        min(a[0], b[0])-1e-8 <= point[0] <= max(a[0], b[0])+1e-8 and \
        min(a[1], b[1])-1e-8 <= point[1] <= max(a[1], b[1])+1e-8


class ReadabilityTests(unittest.TestCase):
    def test_fewer_total_bends_cannot_create_a_new_short_ABA_jog(self):
        original = [(0, 0), (2, 0), (4, 2), (4, 4), (6, 6), (6, 8.2), (8, 10.2), (10, 10.2)]
        tempting = [(0, 0), (5, 5), (5, 5.2), (10, 10.2)]
        allowed = list(zip(original, original[1:]))+list(zip(tempting, tempting[1:]))
        def legal(a, b):
            return any(on_segment(a, c, d) and on_segment(b, c, d) for c, d in allowed)
        naive = simplify_polyline(original, legal)
        self.assertTrue(short_bend_issues(naive), 'Fixture must tempt the total-bend-only objective')
        result = simplify_polyline(original, legal, short_step_length=.5)
        self.assertFalse(short_bend_issues(result))
        self.assertTrue(all(legal(a, b) for a, b in zip(result, result[1:])))

    def test_same_corner_count_can_spend_budget_to_remove_short_ABA_jog(self):
        original = [(0, 0), (4, 0), (4, .1), (10, .1)]
        def legal(a, b):
            if any(on_segment(a, c, d) and on_segment(b, c, d) for c, d in zip(original, original[1:])):
                return True
            return all(a[1]+(b[1]-a[1])*i/100 >= .6-1e-8 or
                       abs(a[0]+(b[0]-a[0])*i/100) < 1e-8 or
                       abs(a[0]+(b[0]-a[0])*i/100-10) < 1e-8 for i in range(101))
        result = simplify_polyline(original, legal, 1.2, short_step_length=.5)
        self.assertEqual(direction_changes(original), direction_changes(result))
        self.assertFalse(short_bend_issues(result))
        self.assertGreater(polyline_length(result), polyline_length(original))
        self.assertLessEqual(polyline_length(result), polyline_length(original)+1.2+1e-8)

    def test_floating_mm_collinearity_does_not_report_false_alternating_turn(self):
        self.assertFalse(short_bend_issues([(90.4, 11.7), (90.8, 12.1), (91.1, 12.4), (91.1, 12.5)]))

    def test_short_local_issue_detection_distinguishes_chamfer_and_backtracking(self):
        self.assertFalse(short_bend_issues([(0, 0), (2, 0), (2.1, .1), (2.1, 2)]))
        rows = short_bend_issues([(0, 0), (2, 0), (2.1, .1), (3, .1), (3.1, .2), (4, .2)])
        self.assertEqual(2, len([r for r in rows if r['kind'] == 'short-alternating-bends']))
        rows = short_bend_issues([(0, 0), (2, 0), (2, .2), (1, .2)])
        self.assertTrue(any(r['kind'] == 'local-backtracking' for r in rows))

    def test_detected_late_short_zigzag_gets_budget_before_obstructed_prefix(self):
        original = [(0, 0), (0, 10), (10, 10), (10, 20), (20, 20), (20, 30),
                    (30, 30), (30.1, 30.1), (30.2, 30.1), (31, 30.9)]
        calls = []
        def legal(a, b):
            calls.append((a, b))
            return min(a[0], b[0]) >= 30
        result = simplify_polyline(original, legal, short_step_length=.5, max_segment_checks=96)
        self.assertLess(direction_changes(result), direction_changes(original))
        self.assertLessEqual(len(calls), 96)
        self.assertEqual((result[0], result[-1]), (original[0], original[-1]))

    def test_compacted_contextual_join_is_screened_as_one_complete_leg(self):
        original = [(0, 0), (1, 1), (2, 1), (3, 2), (4, 2)]
        def legal(a, b):
            # Legal pieces cannot authorise a merged diagonal that crosses a
            # separate blocked region farther along its complete centreline.
            return not (a == (0, 0) and b[0] > 1 and abs(b[0]-b[1]) < 1e-8)
        result = simplify_polyline(original, legal, short_step_length=1.)
        self.assertTrue(all(legal(a, b) for a, b in zip(result, result[1:])))

    def test_staircase_with_no_direct_octilinear_end_to_end_segment(self):
        original = staircase()
        result = simplify_polyline(original, lambda a, b: True)
        self.assertEqual((result[0], result[-1]), (original[0], original[-1]))
        self.assertEqual(direction_changes(result), 1)
        self.assertLessEqual(polyline_length(result), polyline_length(original)+1e-8)
        self.assertNotEqual(abs(result[-1][0]), abs(result[-1][1]))

    def test_sliding_three_leg_45_degree_bridge_avoids_obstacle(self):
        original = [(0, 0), (2, 0), (4, 2), (8, 2), (10, 0), (12, 0)]
        def legal(a, b):
            for step in range(501):
                t = step/500
                x, y = a[0]+(b[0]-a[0])*t, a[1]+(b[1]-a[1])*t
                if 5 <= x <= 7 and -1 <= y <= 1:
                    return False
            return True
        result = simplify_polyline(original, legal)
        self.assertEqual(direction_changes(result), 2)
        self.assertLessEqual(polyline_length(result), polyline_length(original)+1e-8)
        self.assertTrue(all(legal(a, b) for a, b in zip(result, result[1:])))

    def test_extra_length_is_optional_and_bounded_by_one_original_reference(self):
        original = [(0, 0), (2, 0), (2.1, .1), (4, .1), (4.1, 0),
                    (6, 0), (6.1, -.1), (8, -.1), (8.1, 0), (12, 0)]
        def legal(a, b):
            if any(on_segment(a, c, d) and on_segment(b, c, d) for c, d in zip(original, original[1:])):
                return True
            return all((a[1]+(b[1]-a[1])*step/100 <= -.5+1e-8 or
                        abs(a[0]+(b[0]-a[0])*step/100) < 1e-8 or
                        abs(a[0]+(b[0]-a[0])*step/100-12) < 1e-8) for step in range(101))
        unchanged = simplify_polyline(original, legal, 0)
        result = simplify_polyline(original, legal, 2)
        self.assertEqual(unchanged, original)
        self.assertLess(direction_changes(result), direction_changes(original))
        self.assertGreater(polyline_length(result), polyline_length(original)+.1)
        self.assertLessEqual(polyline_length(result), polyline_length(original)+2+1e-8)
        self.assertEqual((result[0], result[-1]), (original[0], original[-1]))

    def test_native_off_grid_endpoints_are_preserved_exactly(self):
        original = [(x+.037, y+.087) for x, y in staircase(10)]
        result = simplify_polyline(original, lambda a, b: True)
        self.assertEqual(result[0], original[0]); self.assertEqual(result[-1], original[-1])
        self.assertLessEqual(direction_changes(result), 2)

    def test_callback_quota_and_repeatability(self):
        original = staircase(100)
        calls = []
        result = simplify_polyline(original, lambda a, b: calls.append((a, b)) or False, max_segment_checks=7)
        self.assertEqual(result, original)
        self.assertEqual(len(calls), 7)
        first = simplify_polyline(original, lambda a, b: True)
        self.assertEqual(first, simplify_polyline(original, lambda a, b: True))
        self.assertLess(direction_changes(first), direction_changes(original)/5)


class GridReadabilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import numpy as np
        from grid_autorouter import smooth_path
        cls.np = np
        cls.smooth = staticmethod(smooth_path)

    def model(self, *, planes=(False, False), native=None):
        np = self.np
        class Model:
            cell = .1
            policy = {}
            plane_flag = planes
            def point_of(self, row, col):
                return .037+col*self.cell, .087+row*self.cell
            def _sample(self, a, b):
                t = np.linspace(0, 1, max(2, math.ceil(math.dist(a, b)/.05)+1))
                return np.stack((np.round((a[1]+(b[1]-a[1])*t-.087)/self.cell),
                                 np.round((a[0]+(b[0]-a[0])*t-.037)/self.cell)), axis=1).astype(int)
            def native_segment_legal(self, net_index, layer, a, b):
                return native(layer, a, b) if native else True
        return Model()

    def test_layers_via_locations_and_full_dynamic_grid_expansion(self):
        first = [(0, y+10, x+10) for x, y in staircase(8)]
        start = first[-1]
        second = [(1, start[1]+y, start[2]+x) for x, y in staircase(8)]
        path = first+second
        result = self.smooth(path, self.model(), self.np.zeros((2, 100, 100), bool), 0)
        self.assertEqual((result[0], result[-1]), (path[0], path[-1]))
        vias = [(a, b) for a, b in zip(result, result[1:]) if a[0] != b[0]]
        self.assertEqual(vias, [(first[-1], second[0])])
        for a, b in zip(result, result[1:]):
            if a[0] == b[0]:
                self.assertLessEqual(max(abs(a[1]-b[1]), abs(a[2]-b[2])), 1)

    def test_blocked_mask_and_full_native_segment_screen_both_apply(self):
        path = [(0, y+10, x+10) for x, y in [(0, 0), (2, 0), (4, 2), (8, 2), (10, 0), (12, 0)]]
        native_checks = []
        def native(layer, a, b):
            native_checks.append((a, b))
            return not any(15 <= a[1]+(b[1]-a[1])*step/200 <= 17 and
                           9 <= a[0]+(b[0]-a[0])*step/200 <= 11 for step in range(201))
        blocked = self.np.zeros((2, 60, 60), bool)
        blocked[0, 13:60, 13:20] = True
        result = self.smooth(path, self.model(native=native), blocked, 0)
        self.assertTrue(native_checks)
        self.assertTrue(all(native(0, a[1:], b[1:]) for a, b in zip(result, result[1:])))
        self.assertFalse(any(blocked[node] for node in result))

    def test_overlapping_chamfers_do_not_create_non_octilinear_legs(self):
        path = [(0, 10, 10), (0, 10, 11), (0, 11, 11), (0, 11, 12)]
        allowed = {tuple(sorted((path[0][1:], path[2][1:]))),
                   tuple(sorted((path[1][1:], path[3][1:])))}
        model = self.model(native=lambda layer, a, b: max(abs(a[0]-b[0]), abs(a[1]-b[1])) <= 1 and
                           (a[0] == b[0] or a[1] == b[1] or tuple(sorted((a, b))) in allowed))
        result = self.smooth(path, model, self.np.zeros((2, 30, 30), bool), 0)
        for a, b in zip(result, result[1:]):
            dy, dx = abs(a[1]-b[1]), abs(a[2]-b[2])
            self.assertTrue(dx == 0 or dy == 0 or dx == dy)

    def test_integer_detour_uses_bounded_allowance_and_plane_run_cannot_grow(self):
        original = [(0, 0), (20, 0), (21, 1), (40, 1), (41, 0),
                    (60, 0), (61, -1), (80, -1), (81, 0), (120, 0)]
        def native(layer, a, b):
            a, b = (a[1]-20, a[0]-20), (b[1]-20, b[0]-20)
            if any(on_segment(a, c, d) and on_segment(b, c, d) for c, d in zip(original, original[1:])):
                return True
            return all((a[1]+(b[1]-a[1])*step/100 <= -5+1e-8 or
                        abs(a[0]+(b[0]-a[0])*step/100) < 1e-8 or
                        abs(a[0]+(b[0]-a[0])*step/100-120) < 1e-8) for step in range(101))
        path = [(0, y+20, x+20) for x, y in original]
        blocked = self.np.zeros((2, 80, 180), bool)
        ordinary = self.smooth(path, self.model(native=native), blocked, 0)
        plane = self.smooth(path, self.model(planes=(True, False), native=native), blocked, 0)
        old_length = polyline_length(original)
        ordinary_length = polyline_length([node[1:] for node in ordinary])
        self.assertLess(direction_changes([node[1:] for node in ordinary]), direction_changes(original))
        self.assertGreater(ordinary_length, old_length)
        self.assertLessEqual(ordinary_length-old_length, min(2/.1, old_length*.08)+1e-8)
        self.assertAlmostEqual(polyline_length([node[1:] for node in plane]), old_length)


if __name__ == '__main__':
    unittest.main()
