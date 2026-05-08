"""모듈·패널 → 운송 회차 산정.

알고리즘:
  - 모듈: 부피·중량 BP. 트럭별 적재 가능 매수 산출 후 FFD.
    · lowbed: 폭 3.0m 이하 모듈만
    · extendable: 광폭 모듈(3.0m 초과)도 가능
  - 플로어 패널: 눕혀서 적층 (1열 N매 × M단)
  - 벽체 패널: A-frame 트레일러에 세워서 폭 방향 N매
  - 더니지 무게: 적층 단 사이에 추가
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Union

from .limits import can_carry
from .models import (
    DunnageSpec, Module, Panel, RoadClass, SpacingParams, Truck,
)


Item = Union[Module, Panel]


@dataclass
class Trip:
    trip_no: int
    truck: Truck
    items: list[Item] = field(default_factory=list)
    wide_check: bool = False
    blocked_reason: str | None = None
    panels_per_row: int = 1
    n_layers: int = 1
    dunnage_weight: float = 0.0     # 추가된 더니지 무게 (kg)

    @property
    def cargo_weight(self) -> float:
        """화물(아이템) 무게만."""
        return sum(getattr(i, "weight", 0) for i in self.items)

    @property
    def total_weight(self) -> float:
        """화물 + 더니지 무게."""
        return self.cargo_weight + self.dunnage_weight

    @property
    def utilization(self) -> float:
        if self.truck.max_weight <= 0:
            return 0.0
        return self.total_weight / self.truck.max_weight * 100.0

    @property
    def kind(self) -> str:
        if not self.items:
            return "empty"
        if isinstance(self.items[0], Module):
            return "module"
        return "panel"


@dataclass
class PackResult:
    trips: list[Trip]
    blocked: list[tuple[Item, str]] = field(default_factory=list)

    @property
    def total_trips(self) -> int:
        return len(self.trips)

    @property
    def module_trips(self) -> int:
        return sum(1 for t in self.trips if t.kind == "module")

    @property
    def panel_trips(self) -> int:
        return sum(1 for t in self.trips if t.kind == "panel")

    @property
    def avg_utilization(self) -> float:
        if not self.trips:
            return 0.0
        return sum(t.utilization for t in self.trips) / len(self.trips)


# ---------------------------------------------------------------------------
# 더니지 무게 계산
# ---------------------------------------------------------------------------

def _dunnage_weight_per_layer(truck_width_mm: float, ds: DunnageSpec) -> float:
    """단 사이 더니지 1층 무게 (kg).

    부피 = 100×100mm × 트럭폭 × 3개 → m³ 변환 × 밀도
    """
    volume_m3 = (
        truck_width_mm
        * ds.cross_section_mm
        * ds.cross_section_mm
        * ds.pieces_per_layer
    ) / 1e9
    return volume_m3 * ds.density_kg_per_m3


# ---------------------------------------------------------------------------
# 트럭 선택 헬퍼
# ---------------------------------------------------------------------------

def _module_compatible_trucks(trucks: list[Truck]) -> list[Truck]:
    """모듈 운송 가능 트럭 (lowbed, extendable)."""
    return [t for t in trucks if t.truck_type in ("lowbed", "extendable")]


def _floor_panel_compatible_trucks(trucks: list[Truck]) -> list[Truck]:
    """플로어 패널 운송 가능 트럭 (lowbed, extendable)."""
    return [t for t in trucks if t.truck_type in ("lowbed", "extendable")]


def _wall_panel_compatible_trucks(trucks: list[Truck]) -> list[Truck]:
    """벽체 패널 운송 가능 트럭 (aframe만)."""
    return [t for t in trucks if t.truck_type == "aframe"]


# ---------------------------------------------------------------------------
# 모듈 — 부피·중량 BP (1트럭 1개 룰 폐지)
# ---------------------------------------------------------------------------

def _max_modules_per_truck(
    module: Module, truck: Truck, spacing: SpacingParams
) -> int:
    """이 모듈을 이 트럭에 1단으로 몇 개 실을 수 있나? (적층 X)."""
    if module.width > truck.max_width:
        return 0
    if module.height + truck.vehicle_height_offset > truck.max_height:
        return 0
    usable_length = truck.max_length - 2 * spacing.truck_edge_clearance_mm
    if module.length > usable_length:
        return 0
    n_per_row = int(
        (usable_length + spacing.panel_gap_mm)
        // (module.length + spacing.panel_gap_mm)
    )
    n_per_row = max(n_per_row, 1)
    n_weight = math.floor(truck.max_weight / module.weight) if module.weight > 0 else n_per_row
    return min(n_per_row, n_weight)


def _pack_modules(
    modules: list[Module],
    trucks: list[Truck],
    road: RoadClass,
    spacing: SpacingParams,
    start_trip_no: int = 1,
) -> tuple[list[Trip], list[tuple[Module, str]]]:
    """모듈 부피·중량 BP — 같은 사양 모듈끼리 그룹핑."""
    if not modules:
        return [], []

    compat_trucks = _module_compatible_trucks(trucks)
    if not compat_trucks:
        return [], [(m, "모듈 운송 가능 트럭 없음 (lowbed/extendable 필요)") for m in modules]

    # 사양별 그룹핑
    groups: dict[tuple, list[Module]] = {}
    for m in modules:
        spec_key = (
            round(m.width, 1),
            round(m.length, 1),
            round(m.height, 1),
            round(m.weight, 1),
        )
        groups.setdefault(spec_key, []).append(m)

    blocked: list[tuple[Module, str]] = []
    trips: list[Trip] = []
    next_no = start_trip_no

    for _, group in groups.items():
        sample = group[0]

        # 적합 트럭 + 도로 한도 통과 확인
        usable: list[tuple[Truck, int]] = []
        last_reason = "운송 가능 트럭 없음"
        for tr in compat_trucks:
            r = can_carry(sample, tr, road)
            if not r.ok:
                if r.reasons:
                    last_reason = "; ".join(r.reasons)
                continue
            n_max = _max_modules_per_truck(sample, tr, spacing)
            if n_max > 0:
                usable.append((tr, n_max))

        if not usable:
            for m in group:
                blocked.append((m, last_reason))
            continue

        # 적재율 최대화: (적재율 = n_max × 단위중량 / 트럭 max_weight) 기준 정렬
        usable.sort(
            key=lambda x: (x[1] * sample.weight) / max(x[0].max_weight, 1),
            reverse=True,
        )
        chosen_truck, n_max = usable[0]

        # 매수 단위로 트럭 채우기
        idx = 0
        while idx < len(group):
            chunk = group[idx : idx + n_max]
            trips.append(
                Trip(
                    trip_no=next_no,
                    truck=chosen_truck,
                    items=list(chunk),
                    wide_check=any(m.is_wide() for m in chunk),
                )
            )
            next_no += 1
            idx += n_max

    return trips, blocked


# ---------------------------------------------------------------------------
# 플로어 패널 — 눕혀서 적층
# ---------------------------------------------------------------------------

def _max_floor_panels_per_truck(
    panel: Panel, truck: Truck, sp: SpacingParams, ds: DunnageSpec
) -> tuple[int, int, int]:
    """플로어 패널 1트럭 적재 가능 매수 (눕혀서 적층).

    Returns:
        (max_panels, panels_per_row, n_layers)
    """
    if panel.width > truck.max_width:
        return 0, 0, 0
    usable_length = truck.max_length - 2 * sp.truck_edge_clearance_mm
    if panel.length > usable_length:
        return 0, 0, 0
    panels_per_row = max(
        int((usable_length + sp.panel_gap_mm) // (panel.length + sp.panel_gap_mm)), 1
    )

    inner_height = truck.max_height - truck.vehicle_height_offset
    if panel.thickness > inner_height:
        return 0, panels_per_row, 0
    n_layers = max(
        int(
            (inner_height + sp.dunnage_thickness_mm)
            // (panel.thickness + sp.dunnage_thickness_mm)
        ),
        1,
    )
    n_volume = panels_per_row * n_layers

    # 무게 한도 (더니지 무게 차감)
    layer_dunnage = _dunnage_weight_per_layer(truck.max_width, ds)
    if panel.weight <= 0:
        return n_volume, panels_per_row, n_layers
    # n_panel × panel_weight + max(0, n_layer-1) × dunnage_weight ≤ max_weight
    # n_panel = ceil(n / per_row) layers
    # 단순화: 무게 한도 매수 = floor((max_weight - 모든 더니지) / panel.weight)
    max_dunnage = layer_dunnage * (n_layers - 1)
    weight_budget = max(truck.max_weight - max_dunnage, 0)
    n_weight = math.floor(weight_budget / panel.weight)
    return min(n_volume, n_weight), panels_per_row, n_layers


def _pack_floor_panels(
    panels: list[Panel],
    trucks: list[Truck],
    road: RoadClass,
    sp: SpacingParams,
    ds: DunnageSpec,
    start_trip_no: int,
) -> tuple[list[Trip], list[tuple[Panel, str]]]:
    if not panels:
        return [], []
    compat = _floor_panel_compatible_trucks(trucks)
    if not compat:
        return [], [(p, "플로어 패널 운송 가능 트럭 없음") for p in panels]

    groups: dict[tuple, list[Panel]] = {}
    for p in panels:
        key = (
            round(p.width, 1), round(p.length, 1),
            round(p.thickness, 1), round(p.weight, 1),
        )
        groups.setdefault(key, []).append(p)

    blocked: list[tuple[Panel, str]] = []
    trips: list[Trip] = []
    next_no = start_trip_no

    for _, group in groups.items():
        sample = group[0]
        usable: list[tuple[Truck, int, int, int]] = []
        last_reason = "운송 가능 트럭 없음"
        for tr in compat:
            r = can_carry(sample, tr, road)
            if not r.ok:
                if r.reasons:
                    last_reason = "; ".join(r.reasons)
                continue
            n_max, ppr, nl = _max_floor_panels_per_truck(sample, tr, sp, ds)
            if n_max > 0:
                usable.append((tr, n_max, ppr, nl))

        if not usable:
            for p in group:
                blocked.append((p, last_reason))
            continue

        # 가장 큰 적재량 트럭 선택
        usable.sort(key=lambda x: x[1], reverse=True)
        chosen, n_max, ppr, nl = usable[0]
        layer_dun = _dunnage_weight_per_layer(chosen.max_width, ds)

        idx = 0
        while idx < len(group):
            chunk = group[idx : idx + n_max]
            n_chunk = len(chunk)
            used_layers = math.ceil(n_chunk / ppr)
            dun_weight = max(used_layers - 1, 0) * layer_dun
            trips.append(
                Trip(
                    trip_no=next_no,
                    truck=chosen,
                    items=list(chunk),
                    panels_per_row=ppr,
                    n_layers=used_layers,
                    dunnage_weight=dun_weight,
                )
            )
            next_no += 1
            idx += n_max

    return trips, blocked


# ---------------------------------------------------------------------------
# 벽체 패널 — A-frame에 세워서 폭 방향 N매
# ---------------------------------------------------------------------------

def _max_wall_panels_per_aframe(
    panel: Panel, truck: Truck, sp: SpacingParams, ds: DunnageSpec
) -> tuple[int, int]:
    """벽체 패널을 A-frame에 세워서 적재. 두께 방향 폭에 N매.

    세움 자세:
      - 폭(width 3m) → 트럭 길이에 1매(길이>폭이라서 가운데)
      - 길이(length 9m) → 트럭 길이 방향에 위치
      - 두께(thickness 150mm) → 트럭 폭 방향에 N매 줄짓기
      - 높이 방향 = 패널 폭 = 3000mm (트럭 높이 한도 4500-700=3800mm 안)

    Returns:
        (max_panels, panels_per_truck_width)
    """
    # 패널을 세웠을 때:
    #   폭 방향: 두께(thickness) — 폭 방향에 N매
    #   길이 방향: 길이(length) — 트럭 길이 안에 들어가야 함
    #   높이 방향: 폭(width) — 트럭 높이 한도 안
    if panel.length > truck.max_length - 2 * sp.truck_edge_clearance_mm:
        return 0, 0
    if panel.width + truck.vehicle_height_offset > truck.max_height:
        return 0, 0

    usable_width = truck.max_width - 2 * sp.truck_edge_clearance_mm
    if panel.thickness > usable_width:
        return 0, 0
    n_per_width = max(
        int((usable_width + sp.panel_gap_mm) // (panel.thickness + sp.panel_gap_mm)), 1
    )

    # 무게 한도
    if panel.weight <= 0:
        return n_per_width, n_per_width
    n_weight = math.floor(truck.max_weight / panel.weight)
    return min(n_per_width, n_weight), n_per_width


def _pack_wall_panels(
    panels: list[Panel],
    trucks: list[Truck],
    road: RoadClass,
    sp: SpacingParams,
    ds: DunnageSpec,
    start_trip_no: int,
) -> tuple[list[Trip], list[tuple[Panel, str]]]:
    if not panels:
        return [], []
    compat = _wall_panel_compatible_trucks(trucks)
    if not compat:
        return [], [(p, "벽체 패널 운송 가능 A-frame 트레일러 없음") for p in panels]

    groups: dict[tuple, list[Panel]] = {}
    for p in panels:
        key = (
            round(p.width, 1), round(p.length, 1),
            round(p.thickness, 1), round(p.weight, 1),
        )
        groups.setdefault(key, []).append(p)

    blocked: list[tuple[Panel, str]] = []
    trips: list[Trip] = []
    next_no = start_trip_no

    for _, group in groups.items():
        sample = group[0]
        usable: list[tuple[Truck, int, int]] = []
        last_reason = "A-frame 적재 불가"
        for tr in compat:
            r = can_carry(sample, tr, road)
            if not r.ok:
                if r.reasons:
                    last_reason = "; ".join(r.reasons)
                continue
            n_max, n_per_width = _max_wall_panels_per_aframe(sample, tr, sp, ds)
            if n_max > 0:
                usable.append((tr, n_max, n_per_width))

        if not usable:
            for p in group:
                blocked.append((p, last_reason))
            continue

        usable.sort(key=lambda x: x[1], reverse=True)
        chosen, n_max, n_per_width = usable[0]

        # A-frame은 적층 없음 — 폭 방향 N매가 1단
        idx = 0
        while idx < len(group):
            chunk = group[idx : idx + n_max]
            trips.append(
                Trip(
                    trip_no=next_no,
                    truck=chosen,
                    items=list(chunk),
                    panels_per_row=len(chunk),
                    n_layers=1,
                    dunnage_weight=0.0,  # A-frame은 패널 사이 받침대만 있음
                )
            )
            next_no += 1
            idx += n_max

    return trips, blocked


# ---------------------------------------------------------------------------
# 트럭 교체 검사 — 사용자가 회차별로 트럭을 바꿀 때
# ---------------------------------------------------------------------------

def recheck_trip_with_truck(
    trip: Trip,
    new_truck: Truck,
    road: RoadClass,
    spacing: SpacingParams = SpacingParams(),
    dunnage: DunnageSpec = DunnageSpec(),
) -> tuple[bool, str, Trip | None]:
    """주어진 trip의 화물을 new_truck에 그대로 실을 수 있나 검사.

    가능 여부 + 사유 + (가능 시) 새 Trip 객체 반환.
    화물은 그대로 두고 트럭만 교체하는 시뮬레이션.
    """
    if not trip.items:
        return True, "(빈 회차)", trip

    sample = trip.items[0]

    # 1) 트럭 종류 호환성
    if trip.kind == "module":
        if new_truck.truck_type not in ("lowbed", "extendable"):
            return False, f"모듈은 lowbed 또는 extendable 트럭에만 적재 가능 (선택: {new_truck.truck_type})", None
    else:
        if isinstance(sample, Panel) and sample.kind == "wall":
            if new_truck.truck_type != "aframe":
                return False, f"벽체 패널은 A-frame 트럭에만 적재 가능 (선택: {new_truck.truck_type})", None
        else:
            if new_truck.truck_type not in ("lowbed", "extendable"):
                return False, f"플로어 패널은 lowbed/extendable 트럭만 적재 가능 (선택: {new_truck.truck_type})", None

    # 2) 도로/트럭 4 조건 검사
    for item in trip.items:
        r = can_carry(item, new_truck, road)
        if not r.ok:
            return False, "; ".join(r.reasons) if r.reasons else "운송 불가", None

    # 3) 부피·중량 한도 (모든 화물이 새 트럭 1대에 들어가는지)
    n = len(trip.items)
    if trip.kind == "module":
        max_n = _max_modules_per_truck(sample, new_truck, spacing)
        if n > max_n:
            return False, f"새 트럭에 {n}매 적재 불가 (최대 {max_n}매까지)", None
        new_trip = Trip(
            trip_no=trip.trip_no,
            truck=new_truck,
            items=list(trip.items),
            wide_check=any(isinstance(i, Module) and i.is_wide() for i in trip.items),
            panels_per_row=n,
            n_layers=1,
        )
        return True, "OK", new_trip

    # 패널
    if isinstance(sample, Panel) and sample.kind == "wall":
        max_n, _ = _max_wall_panels_per_aframe(sample, new_truck, spacing, dunnage)
        if n > max_n:
            return False, f"A-frame에 {n}매 적재 불가 (최대 {max_n}매)", None
        new_trip = Trip(
            trip_no=trip.trip_no,
            truck=new_truck,
            items=list(trip.items),
            panels_per_row=n,
            n_layers=1,
            dunnage_weight=0.0,
        )
        return True, "OK", new_trip

    # 플로어
    max_n, ppr, nl = _max_floor_panels_per_truck(sample, new_truck, spacing, dunnage)
    if n > max_n:
        return False, f"새 트럭에 {n}매 적재 불가 (부피·중량 최대 {max_n}매)", None
    used_layers = math.ceil(n / ppr) if ppr > 0 else 1
    layer_dun = _dunnage_weight_per_layer(new_truck.max_width, dunnage)
    new_trip = Trip(
        trip_no=trip.trip_no,
        truck=new_truck,
        items=list(trip.items),
        panels_per_row=ppr,
        n_layers=used_layers,
        dunnage_weight=max(used_layers - 1, 0) * layer_dun,
    )
    return True, "OK", new_trip


def apply_truck_overrides(
    result: PackResult,
    overrides: dict,  # {trip_no: truck_name}
    trucks: list[Truck],
    road: RoadClass,
    spacing: SpacingParams = SpacingParams(),
    dunnage: DunnageSpec = DunnageSpec(),
) -> tuple[PackResult, dict]:
    """사용자 트럭 override를 적용한 결과 반환.

    Returns:
        (new_result, override_errors)
        override_errors: {trip_no: 사유 (변경 실패 시)}
    """
    if not overrides:
        return result, {}

    truck_by_name = {t.name: t for t in trucks}
    new_trips: list[Trip] = []
    errors: dict = {}

    for trip in result.trips:
        chosen_name = overrides.get(trip.trip_no)
        if not chosen_name or chosen_name == trip.truck.name:
            new_trips.append(trip)
            continue

        new_truck = truck_by_name.get(chosen_name)
        if new_truck is None:
            new_trips.append(trip)
            continue

        ok, reason, new_trip = recheck_trip_with_truck(
            trip, new_truck, road, spacing, dunnage
        )
        if ok and new_trip is not None:
            new_trips.append(new_trip)
        else:
            errors[trip.trip_no] = reason
            new_trips.append(trip)  # 원래 트럭 유지

    return PackResult(trips=new_trips, blocked=result.blocked), errors


# ---------------------------------------------------------------------------
# 메인 진입점
# ---------------------------------------------------------------------------

def pack_items(
    modules: list[Module],
    panels: list[Panel],
    trucks: list[Truck],
    road: RoadClass,
    spacing: SpacingParams = SpacingParams(),
    dunnage: DunnageSpec = DunnageSpec(),
) -> PackResult:
    """모듈·패널 리스트 → 운송 회차 산정."""
    trips: list[Trip] = []
    blocked: list[tuple[Item, str]] = []
    next_no = 1

    # 1) 모듈 (BP)
    mod_trips, mod_blocked = _pack_modules(
        modules, trucks, road, spacing, start_trip_no=next_no
    )
    trips.extend(mod_trips)
    blocked.extend(mod_blocked)
    next_no += len(mod_trips)

    # 2) 플로어 패널 (눕혀서 적층)
    floor_panels = [p for p in panels if p.kind == "floor"]
    floor_trips, floor_blocked = _pack_floor_panels(
        floor_panels, trucks, road, spacing, dunnage, start_trip_no=next_no
    )
    trips.extend(floor_trips)
    blocked.extend(floor_blocked)
    next_no += len(floor_trips)

    # 3) 벽체 패널 (A-frame 세워서)
    wall_panels = [p for p in panels if p.kind == "wall"]
    wall_trips, wall_blocked = _pack_wall_panels(
        wall_panels, trucks, road, spacing, dunnage, start_trip_no=next_no
    )
    trips.extend(wall_trips)
    blocked.extend(wall_blocked)

    return PackResult(trips=trips, blocked=blocked)
