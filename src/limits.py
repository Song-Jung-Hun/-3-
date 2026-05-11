"""트럭 + 도로 등급 + 적재 아이템에 대한 4 조건 검사기.

LH 매뉴얼 §2.7.1 표 기준:
  - 길이 ≤ 19m, 너비 ≤ 3.2~3.5m, 높이 ≤ 4.5m, 차량+화물 ≤ 40t
  - 모듈 단변폭 < 3.5m (플레이트 폭)
  - 차량높이(0.7~1.0m) + 모듈높이 ≤ 4.2~4.5m

광폭 거실모듈(3.6m·3.9m)은 §3.2.3에 따라 광로 검토 필요 → flag 반환.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Union

from .models import Module, Panel, Truck, RoadClass


Item = Union[Module, Panel]


@dataclass(frozen=True)
class CarryResult:
    """can_carry 결과. ok=False여도 reasons 누적, wide_check는 광폭 검토 플래그."""

    ok: bool
    reasons: tuple[str, ...] = ()
    wide_check: bool = False  # 광폭(3.4m 초과) 모듈 — 광로 검토 필요

    def __bool__(self) -> bool:
        return self.ok


def _item_dims(item: Item) -> tuple[float, float, float, float]:
    """(length, width, height, weight) 통일 추출.

    Panel은 두께를 height로 본다 (적층/평적 시 한 장 높이).
    """
    if isinstance(item, Module):
        return item.length, item.width, item.height, item.weight
    # Panel
    if item.kind == "lshape" and item.wall_height > 0:
        # L자 패널: 벽 부분이 위로 솟으므로 높이 = wall_height
        return item.length, item.width, item.wall_height, item.weight
    return item.length, item.width, item.thickness, item.weight


def can_carry(item: Item, truck: Truck, road: RoadClass) -> CarryResult:
    """단일 아이템 1개를 단일 트럭으로 단일 도로에서 운송 가능한가?

    중량은 차량 자체 무게는 별도이므로 화물 ≤ truck.max_weight 만 본다.
    높이는 차량 높이 오프셋을 더한 외측 높이가 도로 한도를 넘지 않는지 본다.
    """
    length, width, height, weight = _item_dims(item)
    reasons: list[str] = []

    # 1) 길이
    length_lim = min(truck.max_length, road.max_length)
    if length > length_lim:
        reasons.append(
            f"길이 초과: {length:.0f}mm > {length_lim:.0f}mm "
            f"(트럭 {truck.max_length:.0f} / 도로 {road.max_length:.0f})"
        )

    # 2) 폭
    width_lim = min(truck.max_width, road.max_width)
    if width > width_lim:
        reasons.append(
            f"폭 초과: {width:.0f}mm > {width_lim:.0f}mm "
            f"(트럭 {truck.max_width:.0f} / 도로 {road.max_width:.0f})"
        )

    # 3) 높이 (차량 + 화물 외측 높이)
    outer_height = height + truck.vehicle_height_offset
    height_lim = min(truck.max_height, road.max_height)
    if outer_height > height_lim:
        reasons.append(
            f"외측높이 초과: {outer_height:.0f}mm "
            f"(차량 {truck.vehicle_height_offset:.0f} + 화물 {height:.0f}) "
            f"> {height_lim:.0f}mm"
        )

    # 4) 중량 (화물 단독 — 차량 무게는 도로 한도 별도)
    weight_lim = min(truck.max_weight, road.max_weight)
    if weight > weight_lim:
        reasons.append(
            f"중량 초과: {weight:.0f}kg > {weight_lim:.0f}kg "
            f"(트럭 {truck.max_weight:.0f} / 도로 {road.max_weight:.0f})"
        )

    # 광폭 모듈 검토 플래그 (LH §3.2.3)
    wide_check = isinstance(item, Module) and item.is_wide()

    return CarryResult(ok=not reasons, reasons=tuple(reasons), wide_check=wide_check)
