from rule_engine.geometry import foot_point, iou, point_in_polygon, region_box

SQ = [(0, 0), (10, 0), (10, 10), (0, 10)]
L = [(0, 0), (10, 0), (10, 4), (4, 4), (4, 10), (0, 10)]          # concave L shape


def test_point_in_polygon_basic_and_edges():
    assert point_in_polygon((5, 5), SQ)
    assert not point_in_polygon((11, 5), SQ)
    assert point_in_polygon((0, 5), SQ)       # on edge counts as inside
    assert point_in_polygon((10, 10), SQ)     # vertex


def test_point_in_polygon_concave():
    assert point_in_polygon((2, 8), L)
    assert point_in_polygon((8, 2), L)
    assert not point_in_polygon((8, 8), L)    # inside the notch


def test_iou_and_foot_point_and_region():
    assert iou((0, 0, 10, 10), (0, 0, 10, 10)) == 1.0
    assert iou((0, 0, 10, 10), (20, 20, 30, 30)) == 0.0
    assert abs(iou((0, 0, 10, 10), (5, 0, 15, 10)) - 1 / 3) < 1e-9
    assert foot_point((100, 50, 200, 300)) == (150, 300)
    assert region_box((100, 100, 200, 300), (0, 0, 1, 0.5)) == (100, 100, 200, 200)
