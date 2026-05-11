"""모듈·패널 → 운송 회차 산정.

모듈 적재 규칙:
  - 1 트럭 = 1 모듈 (LH §2.7.1 표준 — 모듈은 트럭 한 대에 한 개씩)
  - 해당 모듈을 실을 수 있는 트럭 중 용량이 가장 큰 트럭을 자동 배정

패널 적재 알고리즘: FFD (First Fit Decreasing) 빈 패킹
  - 모든 패널을 크기(무게·두께·길이) 내림차순 정렬
  - 기존 트럭 빈 공간에 먼저 채우고, 안 되면 새 트럭 오픈
  - 다른 사양 패널도 같은 트럭에 혼적 가능 → 트럭 대수 최소화

적재 방향:
  - 모듈: 1 트럭 = 1 모듈 (혼적 없음)
  - 플로어 패널: 눕혀서 적층
  - 벽체 패널: A-frame에 두께 방향으로 세워서
  - L자 패널: 눕혀서 나란히 (적층 X, 벽 부분이 위로 솟음)
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
    dunnage_weight: float = 0.0

    @property
    def cargo_weight(self) -> float:
        return sum(getattr(i, "weight", 0) for i in self.items)

    @property
    def total_weight(self) -> float:
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
    """단 사이 더니지 1층 무게 (kg)."""
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
    return [t for t in trucks if t.truck_type in ("lowbed", "extendable")]


def _floor_panel_compatible_trucks(trucks: list[Truck]) -> list[Truck]:
    return [t for t in trucks if t.truck_type in ("lowbed", "extendable")]


def _wall_panel_compatible_trucks(trucks: list[Truck]) -> list[Truck]:
    # 벽체 패널 눕혀서 적층 운송 → 플로어 패널과 동일 차종
    return [t for t in trucks if t.truck_type in ("lowbed", "extendable")]


def _lshape_compatible_trucks(trucks: list[Truck]) -> list[Truck]:
    return [t for t in trucks if t.truck_type in ("lowbed", "extendable")]


# ---------------------------------------------------------------------------
# recheck용 단일 사양 계산 헬퍼 (트럭 교체 검증에 사용)
# ---------------------------------------------------------------------------

def _max_modules_per_truck(module: Module, truck: Truck, spacing: SpacingParams) -> int:
    """단일 사양 모듈 1트럭 최대 매수."""
    if module.width > truck.max_width:
        return 0
    if module.height + truck.vehicle_height_offset > truck.max_height:
        return 0
    usable = truck.max_length - 2 * spacing.truck_edge_clearance_mm
    if module.length > usable:
        return 0
    n_len = max(int((usable + spacing.panel_gap_mm) // (module.length + spacing.panel_gap_mm)), 1)
    n_wt = math.floor(truck.max_weight / module.weight) if module.weight > 0 else n_len
    return min(n_len, n_wt)


def _max_floor_panels_per_truck(
    panel: Panel, truck: Truck, sp: SpacingParams, ds: DunnageSpec
) -> tuple[int, int, int]:
    """단일 사양 플로어 패널 1트럭 최대 (max_panels, per_row, n_layers)."""
    if panel.width > truck.max_width:
        return 0, 0, 0
    usable_len = truck.max_length - 2 * sp.truck_edge_clearance_mm
    if panel.length > usable_len:
        return 0, 0, 0
    ppr = max(int((usable_len + sp.panel_gap_mm) // (panel.length + sp.panel_gap_mm)), 1)
    inner_h = truck.max_height - truck.vehicle_height_offset
    if panel.thickness > inner_h:
        return 0, ppr, 0
    nl = max(int((inner_h + sp.dunnage_thickness_mm) // (panel.thickness + sp.dunnage_thickness_mm)), 1)
    n_vol = ppr * nl
    layer_dun = _dunnage_weight_per_layer(truck.max_width, ds)
    if panel.weight <= 0:
        return n_vol, ppr, nl
    max_dun = layer_dun * (nl - 1)
    budget = max(truck.max_weight - max_dun, 0)
    n_wt = math.floor(budget / panel.weight)
    return min(n_vol, n_wt), ppr, nl


def _max_wall_panels_per_aframe(
    panel: Panel, truck: Truck, sp: SpacingParams, ds: DunnageSpec
) -> tuple[int, int]:
    """단일 사양 벽체 패널 1 A-frame 최대 (max, per_width)."""
    if panel.length > truck.max_length - 2 * sp.truck_edge_clearance_mm:
        return 0, 0
    if panel.width + truck.vehicle_height_offset > truck.max_height:
        return 0, 0
    usable_w = truck.max_width - 2 * sp.truck_edge_clearance_mm
    if panel.thickness > usable_w:
        return 0, 0
    n_w = max(int((usable_w + sp.panel_gap_mm) // (panel.thickness + sp.panel_gap_mm)), 1)
    if panel.weight <= 0:
        return n_w, n_w
    n_wt = math.floor(truck.max_weight / panel.weight)
    return min(n_w, n_wt), n_w


def _max_lshape_panels_per_truck(panel: Panel, truck: Truck, sp: SpacingParams) -> int:
    """단일 사양 L자 패널 1트럭 최대 매수."""
    if panel.width > truck.max_width:
        return 0
    if panel.wall_height + truck.vehicle_height_offset > truck.max_height:
        return 0
    usable = truck.max_length - 2 * sp.truck_edge_clearance_mm
    if panel.length > usable:
        return 0
    n_len = max(int((usable + sp.panel_gap_mm) // (panel.length + sp.panel_gap_mm)), 1)
    if panel.weight <= 0:
        return n_len
    n_wt = math.floor(truck.max_weight / panel.weight)
    return min(n_len, n_wt)


# ---------------------------------------------------------------------------
# 공통: 아이템 사이즈에 가장 가까운 트럭 선택
# ---------------------------------------------------------------------------

def _closest_fit_truck(ok_trucks: list[Truck], ref_length: float, ref_weight: float) -> Truck:
    """아이템 사이즈에 가장 가까운 트럭 선택.

    각 트럭의 '정규화된 여유' = (truck_dim - item_dim) / item_dim 합을 최소화.
    → 딱 맞는 트럭을 고르므로 큰 트럭을 작은 화물에 낭비하지 않는다.

    Args:
        ref_length: 아이템 길이 (mm)
        ref_weight: 아이템 무게 (kg) — 모듈은 실제 무게, 패널은 1매 무게 기준
    """
    def score(tr: Truck) -> float:
        len_excess = (tr.max_length - ref_length) / max(ref_length, 1.0)
        wt_excess  = (tr.max_weight  - ref_weight)  / max(ref_weight,  1.0)
        return len_excess + wt_excess

    return min(ok_trucks, key=score)


# ---------------------------------------------------------------------------
# 모듈 — FFD (길이 내림차순, 혼적 허용)
# ---------------------------------------------------------------------------

def _pack_modules(
    modules: list[Module],
    trucks: list[Truck],
    road: RoadClass,
    spacing: SpacingParams,
    start_trip_no: int = 1,
) -> tuple[list[Trip], list[tuple[Module, str]]]:
    """1 트럭 = 1 모듈 (LH §2.7.1 표준).

    각 모듈을 실을 수 있는 트럭 중 용량이 가장 큰 트럭을 배정한다.
    모듈끼리 혼적하지 않으므로 모듈 수 = 회차 수.
    """
    if not modules:
        return [], []

    compat = _module_compatible_trucks(trucks)
    if not compat:
        return [], [(m, "모듈 운송 가능 트럭 없음 (lowbed/extendable 필요)") for m in modules]

    blocked: list[tuple[Module, str]] = []
    trips: list[Trip] = []
    next_no = start_trip_no

    for m in modules:
        ok_trucks = [
            tr for tr in compat
            if can_carry(m, tr, road).ok
            and m.width <= tr.max_width
            and m.height + tr.vehicle_height_offset <= tr.max_height
            and m.length <= tr.max_length - 2 * spacing.truck_edge_clearance_mm
        ]
        if not ok_trucks:
            blocked.append((m, "모듈 규격이 모든 트럭/도로 한도 초과"))
            continue

        best = _closest_fit_truck(ok_trucks, m.length, m.weight)
        trips.append(Trip(
            trip_no=next_no,
            truck=best,
            items=[m],
            wide_check=m.is_wide(),
            panels_per_row=1,
            n_layers=1,
        ))
        next_no += 1

    return trips, blocked


# ---------------------------------------------------------------------------
# 플로어 패널 — FFD (무게 내림차순, 혼적 허용, 적층 포함)
# ---------------------------------------------------------------------------

def _pack_floor_panels(
    panels: list[Panel],
    trucks: list[Truck],
    road: RoadClass,
    sp: SpacingParams,
    ds: DunnageSpec,
    start_trip_no: int,
) -> tuple[list[Trip], list[tuple[Panel, str]]]:
    """FFD 빈 패킹 — 무거운 순, 다른 사양도 같은 트럭에 혼적 가능."""
    if not panels:
        return [], []
    compat = _floor_panel_compatible_trucks(trucks)
    if not compat:
        return [], [(p, "플로어 패널 운송 가능 트럭 없음") for p in panels]

    blocked: list[tuple[Panel, str]] = []
    valid: list[tuple[Panel, list[Truck]]] = []
    for p in panels:
        ok = [tr for tr in compat
              if can_carry(p, tr, road).ok and p.width <= tr.max_width]
        if ok:
            valid.append((p, ok))
        else:
            blocked.append((p, "운송 가능 트럭 없음"))

    if not valid:
        return [], blocked

    # 무거운 순 정렬
    valid.sort(key=lambda x: x[0].weight, reverse=True)

    bins: list[dict] = []
    next_no = start_trip_no

    for p, ok_trucks in valid:
        placed = False

        for b in bins:
            if b["truck"] not in ok_trucks:
                continue
            tr = b["truck"]
            usable_len = tr.max_length - 2 * sp.truck_edge_clearance_mm
            if p.length > usable_len:
                continue
            # 적층 높이 체크: 이 패널 추가 시 총 층수
            ppr = max(b["panels_per_row"], 1)
            new_n = len(b["items"]) + 1
            new_layers = math.ceil(new_n / ppr)
            inner_h = tr.max_height - tr.vehicle_height_offset
            # 대표 두께(가장 두꺼운 패널)로 층 높이 계산
            max_thick = max(pi.thickness for pi in b["items"] + [p])
            stack_h = new_layers * max_thick + max(new_layers - 1, 0) * sp.dunnage_thickness_mm
            if stack_h > inner_h:
                continue
            # 무게 체크 (더니지 포함)
            layer_dun = _dunnage_weight_per_layer(tr.max_width, ds)
            new_dun = max(new_layers - 1, 0) * layer_dun
            if b["total_cargo"] + p.weight + new_dun > tr.max_weight:
                continue
            b["items"].append(p)
            b["total_cargo"] += p.weight
            b["dunnage_weight"] = new_dun
            b["n_layers"] = new_layers
            placed = True
            break

        if not placed:
            ok_for_new = [tr for tr in ok_trucks
                          if p.length <= tr.max_length - 2 * sp.truck_edge_clearance_mm]
            if not ok_for_new:
                blocked.append((p, "패널 길이가 트럭 유효 길이 초과"))
                continue
            best = _closest_fit_truck(ok_for_new, p.length, p.weight)
            # 이 패널 기준 per_row 계산
            _, ppr, _ = _max_floor_panels_per_truck(p, best, sp, ds)
            ppr = max(ppr, 1)
            bins.append({
                "truck": best,
                "items": [p],
                "total_cargo": p.weight,
                "dunnage_weight": 0.0,
                "panels_per_row": ppr,
                "n_layers": 1,
            })

    trips: list[Trip] = []
    for b in bins:
        trips.append(Trip(
            trip_no=next_no,
            truck=b["truck"],
            items=b["items"],
            panels_per_row=b["panels_per_row"],
            n_layers=b["n_layers"],
            dunnage_weight=b["dunnage_weight"],
        ))
        next_no += 1

    return trips, blocked


# ---------------------------------------------------------------------------
# 벽체 패널 — FFD (무게 내림차순, 눕혀서 적층 — 플로어 패널과 동일 방식)
# ---------------------------------------------------------------------------

def _pack_wall_panels(
    panels: list[Panel],
    trucks: list[Truck],
    road: RoadClass,
    sp: SpacingParams,
    ds: DunnageSpec,
    start_trip_no: int,
) -> tuple[list[Trip], list[tuple[Panel, str]]]:
    """FFD 빈 패킹 — 무거운 순, 눕혀서 적층 (플로어 패널과 동일 방식)."""
    if not panels:
        return [], []
    compat = _wall_panel_compatible_trucks(trucks)
    if not compat:
        return [], [(p, "벽체 패널 운송 가능 트럭 없음 (lowbed/extendable 필요)") for p in panels]

    blocked: list[tuple[Panel, str]] = []
    valid: list[tuple[Panel, list[Truck]]] = []
    for p in panels:
        ok = [tr for tr in compat
              if can_carry(p, tr, road).ok and p.width <= tr.max_width]
        if ok:
            valid.append((p, ok))
        else:
            blocked.append((p, "운송 가능 트럭 없음"))

    if not valid:
        return [], blocked

    # 무거운 순 정렬
    valid.sort(key=lambda x: x[0].weight, reverse=True)

    bins: list[dict] = []
    next_no = start_trip_no

    for p, ok_trucks in valid:
        placed = False

        for b in bins:
            if b["truck"] not in ok_trucks:
                continue
            tr = b["truck"]
            usable_len = tr.max_length - 2 * sp.truck_edge_clearance_mm
            if p.length > usable_len:
                continue
            ppr = max(b["panels_per_row"], 1)
            new_n = len(b["items"]) + 1
            new_layers = math.ceil(new_n / ppr)
            inner_h = tr.max_height - tr.vehicle_height_offset
            max_thick = max(pi.thickness for pi in b["items"] + [p])
            stack_h = new_layers * max_thick + max(new_layers - 1, 0) * sp.dunnage_thickness_mm
            if stack_h > inner_h:
                continue
            layer_dun = _dunnage_weight_per_layer(tr.max_width, ds)
            new_dun = max(new_layers - 1, 0) * layer_dun
            if b["total_cargo"] + p.weight + new_dun > tr.max_weight:
                continue
            b["items"].append(p)
            b["total_cargo"] += p.weight
            b["dunnage_weight"] = new_dun
            b["n_layers"] = new_layers
            placed = True
            break

        if not placed:
            ok_for_new = [tr for tr in ok_trucks
                          if p.length <= tr.max_length - 2 * sp.truck_edge_clearance_mm]
            if not ok_for_new:
                blocked.append((p, "벽체 패널 길이가 트럭 유효 길이 초과"))
                continue
            best = _closest_fit_truck(ok_for_new, p.length, p.weight)
            _, ppr, _ = _max_floor_panels_per_truck(p, best, sp, ds)
            ppr = max(ppr, 1)
            bins.append({
                "truck": best,
                "items": [p],
                "total_cargo": p.weight,
                "dunnage_weight": 0.0,
                "panels_per_row": ppr,
                "n_layers": 1,
            })

    trips: list[Trip] = []
    for b in bins:
        trips.append(Trip(
            trip_no=next_no,
            truck=b["truck"],
            items=b["items"],
            panels_per_row=b["panels_per_row"],
            n_layers=b["n_layers"],
            dunnage_weight=b["dunnage_weight"],
        ))
        next_no += 1

    return trips, blocked


# ---------------------------------------------------------------------------
# L자 패널 — FFD (길이 내림차순, 혼적 허용, 적층 불가)
# ---------------------------------------------------------------------------

def _pack_lshape_panels(
    panels: list[Panel],
    trucks: list[Truck],
    road: RoadClass,
    sp: SpacingParams,
    ds: DunnageSpec,
    start_trip_no: int,
) -> tuple[list[Trip], list[tuple[Panel, str]]]:
    """FFD 빈 패킹 — 길이 내림차순, 벽 부분이 위로 솟아 적층 불가."""
    if not panels:
        return [], []
    compat = _lshape_compatible_trucks(trucks)
    if not compat:
        return [], [(p, "L자 패널 운송 가능 트럭 없음 (lowbed/extendable 필요)") for p in panels]

    blocked: list[tuple[Panel, str]] = []
    valid: list[tuple[Panel, list[Truck]]] = []
    for p in panels:
        ok = [tr for tr in compat
              if can_carry(p, tr, road).ok and p.width <= tr.max_width]
        if ok:
            valid.append((p, ok))
        else:
            blocked.append((p, "운송 가능 트럭 없음"))

    if not valid:
        return [], blocked

    # 길이 내림차순 정렬
    valid.sort(key=lambda x: x[0].length, reverse=True)

    bins: list[dict] = []
    next_no = start_trip_no

    for p, ok_trucks in valid:
        placed = False

        for b in bins:
            if b["truck"] not in ok_trucks:
                continue
            tr = b["truck"]
            usable = tr.max_length - 2 * sp.truck_edge_clearance_mm
            gap = sp.panel_gap_mm if b["items"] else 0.0
            if b["used_length"] + gap + p.length > usable:
                continue
            if b["total_weight"] + p.weight > tr.max_weight:
                continue
            b["items"].append(p)
            b["used_length"] += gap + p.length
            b["total_weight"] += p.weight
            placed = True
            break

        if not placed:
            ok_for_new = [tr for tr in ok_trucks
                          if p.length <= tr.max_length - 2 * sp.truck_edge_clearance_mm]
            if not ok_for_new:
                blocked.append((p, "L자 패널 길이가 트럭 유효 길이 초과"))
                continue
            best = _closest_fit_truck(ok_for_new, p.length, p.weight)
            bins.append({
                "truck": best,
                "items": [p],
                "used_length": p.length,
                "total_weight": p.weight,
            })

    trips: list[Trip] = []
    for b in bins:
        trips.append(Trip(
            trip_no=next_no,
            truck=b["truck"],
            items=b["items"],
            panels_per_row=len(b["items"]),
            n_layers=1,
            dunnage_weight=0.0,
        ))
        next_no += 1

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
    """주어진 trip의 화물을 new_truck에 그대로 실을 수 있나 검사."""
    if not trip.items:
        return True, "(빈 회차)", trip

    sample = trip.items[0]

    # 1) 트럭 종류 호환성
    if trip.kind == "module":
        if new_truck.truck_type not in ("lowbed", "extendable"):
            return False, f"모듈은 lowbed 또는 extendable 트럭에만 적재 가능 (선택: {new_truck.truck_type})", None
    else:
        if isinstance(sample, Panel) and sample.kind == "wall":
            if new_truck.truck_type not in ("lowbed", "extendable"):
                return False, f"벽체 패널은 lowbed/extendable 트럭에만 적재 가능 (선택: {new_truck.truck_type})", None
        elif isinstance(sample, Panel) and sample.kind == "lshape":
            if new_truck.truck_type not in ("lowbed", "extendable"):
                return False, f"L자 패널은 lowbed/extendable 트럭만 적재 가능 (선택: {new_truck.truck_type})", None
        else:
            if new_truck.truck_type not in ("lowbed", "extendable"):
                return False, f"플로어 패널은 lowbed/extendable 트럭만 적재 가능 (선택: {new_truck.truck_type})", None

    # 2) 도로/트럭 4 조건 검사
    for item in trip.items:
        r = can_carry(item, new_truck, road)
        if not r.ok:
            return False, "; ".join(r.reasons) if r.reasons else "운송 불가", None

    n = len(trip.items)
    usable = new_truck.max_length - 2 * spacing.truck_edge_clearance_mm

    # 3) 모듈: 실제 총 길이 + 중량 검사
    if trip.kind == "module":
        total_len = sum(m.length for m in trip.items) + max(0, n - 1) * spacing.panel_gap_mm
        if total_len > usable:
            return False, f"모듈 총 길이 {total_len:.0f}mm > 트럭 유효길이 {usable:.0f}mm", None
        total_w = sum(m.weight for m in trip.items if isinstance(m, Module))
        if total_w > new_truck.max_weight:
            return False, f"총 중량 {total_w:.0f}kg > 트럭 적재한도 {new_truck.max_weight:.0f}kg", None
        new_trip = Trip(
            trip_no=trip.trip_no,
            truck=new_truck,
            items=list(trip.items),
            wide_check=any(isinstance(i, Module) and i.is_wide() for i in trip.items),
            panels_per_row=n,
            n_layers=1,
        )
        return True, "OK", new_trip

    # 4) 벽체 패널 (눕혀서 적층 — 플로어 패널과 동일 검사)
    if isinstance(sample, Panel) and sample.kind == "wall":
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

    # 5) L자 패널
    if isinstance(sample, Panel) and sample.kind == "lshape":
        max_n = _max_lshape_panels_per_truck(sample, new_truck, spacing)
        if n > max_n:
            return False, f"L자 패널 {n}매 적재 불가 (최대 {max_n}매)", None
        new_trip = Trip(
            trip_no=trip.trip_no,
            truck=new_truck,
            items=list(trip.items),
            panels_per_row=n,
            n_layers=1,
            dunnage_weight=0.0,
        )
        return True, "OK", new_trip

    # 6) 플로어 패널
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


def simulate_manual_trip(
    items: list[Item],
    truck: Truck,
    road: RoadClass,
    spacing: SpacingParams = SpacingParams(),
    dunnage: DunnageSpec = DunnageSpec(),
) -> tuple[bool, str, Trip | None]:
    """사용자가 직접 선택한 화물 + 트럭으로 1회차 시뮬레이션."""
    if not items:
        return False, "화물이 비어있음", None

    first = items[0]
    if isinstance(first, Module):
        if not all(isinstance(i, Module) for i in items):
            return False, "한 회차에 모듈과 패널을 섞을 수 없음", None
    else:
        if not all(isinstance(i, Panel) and i.kind == first.kind for i in items):
            return False, "한 회차에 다른 종류 패널을 섞을 수 없음 (플로어/벽체/L자)", None

    fake_trip = Trip(trip_no=999, truck=truck, items=list(items))
    return recheck_trip_with_truck(fake_trip, truck, road, spacing, dunnage)


def apply_truck_overrides(
    result: PackResult,
    overrides: dict,
    trucks: list[Truck],
    road: RoadClass,
    spacing: SpacingParams = SpacingParams(),
    dunnage: DunnageSpec = DunnageSpec(),
) -> tuple[PackResult, dict]:
    """사용자 트럭 override를 적용한 결과 반환."""
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

        ok, reason, new_trip = recheck_trip_with_truck(trip, new_truck, road, spacing, dunnage)
        if ok and new_trip is not None:
            new_trips.append(new_trip)
        else:
            errors[trip.trip_no] = reason
            new_trips.append(trip)

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
    """모듈·패널 리스트 → 운송 회차 산정 (FFD 빈 패킹)."""
    trips: list[Trip] = []
    blocked: list[tuple[Item, str]] = []
    next_no = 1

    # 1) 모듈 (FFD by 길이)
    mod_trips, mod_blocked = _pack_modules(modules, trucks, road, spacing, start_trip_no=next_no)
    trips.extend(mod_trips)
    blocked.extend(mod_blocked)
    next_no += len(mod_trips)

    # 2) 플로어 패널 (FFD by 무게, 적층)
    floor_panels = [p for p in panels if p.kind == "floor"]
    floor_trips, floor_blocked = _pack_floor_panels(
        floor_panels, trucks, road, spacing, dunnage, start_trip_no=next_no
    )
    trips.extend(floor_trips)
    blocked.extend(floor_blocked)
    next_no += len(floor_trips)

    # 3) 벽체 패널 (FFD by 두께, A-frame)
    wall_panels = [p for p in panels if p.kind == "wall"]
    wall_trips, wall_blocked = _pack_wall_panels(
        wall_panels, trucks, road, spacing, dunnage, start_trip_no=next_no
    )
    trips.extend(wall_trips)
    blocked.extend(wall_blocked)
    next_no += len(wall_trips)

    # 4) L자 패널 (FFD by 길이, 적층 불가)
    lshape_panels = [p for p in panels if p.kind == "lshape"]
    lshape_trips, lshape_blocked = _pack_lshape_panels(
        lshape_panels, trucks, road, spacing, dunnage, start_trip_no=next_no
    )
    trips.extend(lshape_trips)
    blocked.extend(lshape_blocked)

    return PackResult(trips=trips, blocked=blocked)
