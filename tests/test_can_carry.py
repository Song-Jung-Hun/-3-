"""src.limits.can_carry 단위 테스트.

8 케이스 — 4 조건 위반 각 1, 정상 통과 2, 광폭 검토 플래그 1, 패널 정상 1.
"""
from __future__ import annotations

import sys
from pathlib import Path

# 프로젝트 루트를 sys.path에 추가 (pytest를 루트에서 실행 가정)
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.limits import can_carry  # noqa: E402
from src.models import Module, Panel, Truck, RoadClass  # noqa: E402


# 표준 시나리오 - 25t 카고 + 광로
TRUCK_25T = Truck(
    name="25t",
    max_length=19000,
    max_width=3400,
    max_height=4500,
    max_weight=25000,
)
ROAD_BIG = RoadClass(
    name="광로",
    max_length=19000,
    max_width=3500,
    max_height=4500,
    max_weight=40000,
)
ROAD_SMALL = RoadClass(
    name="이면도로",
    max_length=12000,
    max_width=3200,
    max_height=4000,
    max_weight=15000,
)


def _mod(**kw):
    base = dict(name="m", width=3300, length=9000, height=3000, weight=8000)
    base.update(kw)
    return Module(**base)


def _panel(**kw):
    base = dict(
        name="p", kind="floor", width=3300, length=9000, thickness=200, weight=1500
    )
    base.update(kw)
    return Panel(**base)


# 1) 정상 통과 — 표준 모듈 + 25t + 광로
def test_normal_module_pass():
    r = can_carry(_mod(), TRUCK_25T, ROAD_BIG)
    assert r.ok
    assert not r.wide_check


# 2) 길이 초과
def test_length_violation():
    r = can_carry(_mod(length=20000), TRUCK_25T, ROAD_BIG)
    assert not r.ok
    assert any("길이" in s for s in r.reasons)


# 3) 폭 초과 (광폭 3.9m 모듈을 일반 25t 트럭으로)
def test_width_violation():
    r = can_carry(_mod(width=3900), TRUCK_25T, ROAD_BIG)
    assert not r.ok
    assert any("폭" in s for s in r.reasons)
    assert r.wide_check  # 광폭 검토 플래그 ON


# 4) 외측높이 초과 (차량 850 + 화물 4000 = 4850 > 4500)
def test_height_violation():
    r = can_carry(_mod(height=4000), TRUCK_25T, ROAD_BIG)
    assert not r.ok
    assert any("높이" in s for s in r.reasons)


# 5) 중량 초과 (모듈 30t > 25t)
def test_weight_violation():
    r = can_carry(_mod(weight=30000), TRUCK_25T, ROAD_BIG)
    assert not r.ok
    assert any("중량" in s for s in r.reasons)


# 6) 이면도로에서 길이 한도 - 9m 모듈은 OK, 12m 모듈은 NG
def test_road_class_length():
    ok = can_carry(_mod(length=9000), TRUCK_25T, ROAD_SMALL)
    ng = can_carry(_mod(length=12100), TRUCK_25T, ROAD_SMALL)
    assert ok.ok
    assert not ng.ok


# 7) 패널 정상 통과 (얇아서 외측높이 충분)
def test_panel_pass():
    r = can_carry(_panel(), TRUCK_25T, ROAD_BIG)
    assert r.ok


# 8) 광폭 모듈(3.5m) — 광로(3.5m) 통과하지만 wide_check=True
def test_wide_module_flag_on_big_road():
    wide = _mod(width=3500)
    r = can_carry(wide, TRUCK_25T, ROAD_BIG)
    # 25t 트럭 max_width=3400이므로 폭 초과 (트럭이 좁음) — 그래도 wide_check은 ON
    assert r.wide_check
