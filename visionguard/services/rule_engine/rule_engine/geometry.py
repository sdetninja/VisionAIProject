"""Small, dependency-free geometry helpers."""
from __future__ import annotations


def area(b) -> float:
    return max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])


def center(b):
    return ((b[0] + b[2]) / 2.0, (b[1] + b[3]) / 2.0)


def foot_point(b):
    """Bottom-centre of a person box: where they stand on the floor. Use this (not the box centre) for zones."""
    return ((b[0] + b[2]) / 2.0, b[3])


def iou(a, b) -> float:
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = area(a) + area(b) - inter
    return inter / union if union > 0 else 0.0


def point_in_box(p, b) -> bool:
    return b[0] <= p[0] <= b[2] and b[1] <= p[1] <= b[3]


def region_box(person_box, frac):
    """Sub-region of a person box given as fractions (fx1, fy1, fx2, fy2); may extend past 0..1."""
    x1, y1, x2, y2 = person_box
    w, h = x2 - x1, y2 - y1
    return (x1 + frac[0] * w, y1 + frac[1] * h, x1 + frac[2] * w, y1 + frac[3] * h)


def point_in_polygon(p, poly) -> bool:
    """Ray casting; points on an edge count as inside."""
    x, y = p
    n = len(poly)
    inside = False
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        # on-edge check
        if (min(x1, x2) - 1e-9 <= x <= max(x1, x2) + 1e-9 and min(y1, y2) - 1e-9 <= y <= max(y1, y2) + 1e-9
                and abs((x2 - x1) * (y - y1) - (y2 - y1) * (x - x1)) < 1e-9):
            return True
        if (y1 > y) != (y2 > y):
            xin = (x2 - x1) * (y - y1) / (y2 - y1) + x1
            if x < xin:
                inside = not inside
    return inside
