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
    Module, Panel, RoadClass, SpacingParams, Truck,
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
    used_length_mm: float = 0.0    # 실제 사용 길이 (화물 + 간격)
    usable_length_mm: float = 0.0  # 트럭 유효 적재 길이 (양끝 여유 제외)
    stacked_items: list = field(default_factory=list)
    # L자 패널 회차 전용: stacked_items[i] = items[i] 위에 올라간 Panel 또는 None

    @property
    def cargo_weight(self) -> float:
        base_w = sum(getattr(i, "weight", 0) for i in self.items)
        stacked_w = sum(
            getattr(s, "weight", 0) for s in self.stacked_items if s is not None
        )
        return base_w + stacked_w

    @property
    def total_weight(self) -> float:
        return self.cargo_weight

    @property
    def weight_utilization(self) -> float:
        """중량 기준 적재율 (%)."""
        if self.truck.max_weight <= 0:
            return 0.0
        return self.cargo_weight / self.truck.max_weight * 100.0

    @property
    def length_utilization(self) -> float:
        """길이 기준 적재율 (%)."""
        if self.usable_length_mm <= 0:
            return 0.0
        return self.used_length_mm / self.usable_length_mm * 100.0

    @property
    def utilization(self) -> float:
        """실질 적재율 = max(중량 기준, 길이 기준)."""
        return max(self.weight_utilization, self.length_utilization)

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
    panel: Panel, truck: Truck, sp: SpacingParams
) -> tuple[int, int, int]:
    """단일 사양 패널 1트럭 최대 (max_panels, per_row, n_layers).

    층간 간격 = panel_gap_mm (받침목 없음).
    """
    if panel.width > truck.max_width:
        return 0, 0, 0
    usable_len = truck.max_length - 2 * sp.truck_edge_clearance_mm
    if panel.length > usable_len:
        return 0, 0, 0
    ppr = max(int((usable_len + sp.panel_gap_mm) // (panel.length + sp.panel_gap_mm)), 1)
    inner_h = truck.max_height - truck.vehicle_height_offset
    if panel.thickness > inner_h:
        return 0, ppr, 0
    # 층간 간격 = panel_gap_mm
    nl = max(int((inner_h + sp.panel_gap_mm) // (panel.thickness + sp.panel_gap_mm)), 1)
    n_vol = ppr * nl
    if panel.weight <= 0:
        return n_vol, ppr, nl
    n_wt = math.floor(truck.max_weight / panel.weight)
    return min(n_vol, n_wt), ppr, nl


def _max_lshape_panels_per_truck(panel: Panel, truck: Truck, sp: SpacingParams) -> int:
    """단일 사양 L자 패널 1트럭 최대 매수 (기저 배치 기준, 적층 미포함)."""
    if panel.width > truck.max_width:
        return 0
    # L자 높이 = 바닥판(thickness) + 벽체(wall_height)
    if panel.thickness + panel.wall_height + truck.vehicle_height_offset > truck.max_height:
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
# L자 패널 적층 가능 여부 검사
# ---------------------------------------------------------------------------

def _can_stack_on_lshape(
    panel: Panel,
    lshape: Panel,
    truck: Truck,
    sp: SpacingParams,
) -> bool:
    """패널을 L자 패널 바닥판 위에 적층 가능한지 검사.

    적층 조건:
      ① 폭 — panel.width ≤ lshape.width − lshape.thickness − gap
             (L자 내벽 여유: 벽체 두께와 gap만큼 빼야 패널이 안쪽에 들어감)
      ② 길이 — panel.length ≤ lshape.length
               (L자 바닥판 길이를 넘을 수 없음)
      ③ 높이 — 차체 + L자 바닥판(thickness) + gap + 적층 패널 높이 ≤ truck.max_height
               L자 단독 높이(thickness+wall_height)도 함께 확인
    """
    # ① 폭 — 벽체 두께 + 수평 Gap(lshape_stack_gap_mm)을 빼야 적층 패널이 들어감
    avail_w = lshape.width - lshape.thickness - sp.lshape_stack_gap_mm
    if avail_w <= 0 or panel.width > avail_w:
        return False

    # ② 길이 — 트럭 유효 적재 길이 이내이면 OK (L자 개별 길이에 제한 없음)
    usable = truck.max_length - 2 * sp.truck_edge_clearance_mm
    if panel.length > usable:
        return False

    # ③ 높이
    inner_h = truck.max_height - truck.vehicle_height_offset
    L_h = lshape.thickness + lshape.wall_height          # L자 단독 점유 높이
    if panel.kind == "lshape":
        stk_h = lshape.thickness + sp.panel_gap_mm + panel.thickness + panel.wall_height
    else:
        stk_h = lshape.thickness + sp.panel_gap_mm + panel.thickness
    cargo_h = max(L_h, stk_h)
    return cargo_h <= inner_h


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
        usable = best.max_length - 2 * spacing.truck_edge_clearance_mm
        trips.append(Trip(
            trip_no=next_no,
            truck=best,
            items=[m],
            wide_check=m.is_wide(),
            panels_per_row=1,
            n_layers=1,
            used_length_mm=m.length,
            usable_length_mm=usable,
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
    start_trip_no: int,
) -> tuple[list[Trip], list[tuple[Panel, str]]]:
    """FFD 빈 패킹 — 무거운 순, 다른 사양도 같은 트럭에 혼적 가능. 층간 간격 = panel_gap_mm."""
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
            # 층간 간격 = panel_gap_mm
            stack_h = new_layers * max_thick + max(new_layers - 1, 0) * sp.panel_gap_mm
            if stack_h > inner_h:
                continue
            if b["total_cargo"] + p.weight > tr.max_weight:
                continue
            b["items"].append(p)
            b["total_cargo"] += p.weight
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
            _, ppr, _ = _max_floor_panels_per_truck(p, best, sp)
            ppr = max(ppr, 1)
            bins.append({
                "truck": best,
                "items": [p],
                "total_cargo": p.weight,
                "panels_per_row": ppr,
                "n_layers": 1,
            })

    trips: list[Trip] = []
    for b in bins:
        usable = b["truck"].max_length - 2 * sp.truck_edge_clearance_mm
        ppr = b["panels_per_row"]
        sample_len = b["items"][0].length if b["items"] else 0.0
        used_l = ppr * sample_len + max(0, ppr - 1) * sp.panel_gap_mm
        trips.append(Trip(
            trip_no=next_no,
            truck=b["truck"],
            items=b["items"],
            panels_per_row=ppr,
            n_layers=b["n_layers"],
            used_length_mm=used_l,
            usable_length_mm=usable,
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
    start_trip_no: int,
) -> tuple[list[Trip], list[tuple[Panel, str]]]:
    """FFD 빈 패킹 — 무거운 순, 눕혀서 적층 (플로어 패널과 동일 방식). 층간 간격 = panel_gap_mm."""
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
            stack_h = new_layers * max_thick + max(new_layers - 1, 0) * sp.panel_gap_mm
            if stack_h > inner_h:
                continue
            if b["total_cargo"] + p.weight > tr.max_weight:
                continue
            b["items"].append(p)
            b["total_cargo"] += p.weight
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
            _, ppr, _ = _max_floor_panels_per_truck(p, best, sp)
            ppr = max(ppr, 1)
            bins.append({
                "truck": best,
                "items": [p],
                "total_cargo": p.weight,
                "panels_per_row": ppr,
                "n_layers": 1,
            })

    trips: list[Trip] = []
    for b in bins:
        usable = b["truck"].max_length - 2 * sp.truck_edge_clearance_mm
        ppr = b["panels_per_row"]
        sample_len = b["items"][0].length if b["items"] else 0.0
        used_l = ppr * sample_len + max(0, ppr - 1) * sp.panel_gap_mm
        trips.append(Trip(
            trip_no=next_no,
            truck=b["truck"],
            items=b["items"],
            panels_per_row=ppr,
            n_layers=b["n_layers"],
            used_length_mm=used_l,
            usable_length_mm=usable,
        ))
        next_no += 1

    return trips, blocked


# ---------------------------------------------------------------------------
# L자 패널 — FFD (길이 내림차순, 혼적 허용, 적층 지원)
# ---------------------------------------------------------------------------

def _pack_lshape_panels(
    panels: list[Panel],
    trucks: list[Truck],
    road: RoadClass,
    sp: SpacingParams,
    start_trip_no: int,
    stacking_candidates: list[Panel] | None = None,
) -> tuple[list[Trip], list[tuple[Panel, str]], list[Panel]]:
    """L자 패널 FFD 빈 패킹 + 위에 적층 지원.

    L자 패널은 트럭 바닥에 길이 방향으로 나란히 배치하고,
    각 L자 패널 위에는 다른 패널(플로어/벽체 패널, 또는 더 작은 L자 패널)
    1매를 적층할 수 있습니다 (_can_stack_on_lshape 조건 충족 시).

    stacking_candidates — L자 빈 슬롯에 올릴 후보 패널 (플로어·벽체 패널 등).
                          적층에 성공한 패널은 제거하고 나머지를 반환합니다.

    Returns:
        trips              — 생성된 회차 목록
        blocked            — 배치 불가 L자 패널 목록
        remaining_candidates — 적층 배치에 실패한 stacking_candidates
    """
    sc = list(stacking_candidates) if stacking_candidates else []

    if not panels:
        return [], [], sc

    compat = _lshape_compatible_trucks(trucks)
    if not compat:
        return (
            [],
            [(p, "L자 패널 운송 가능 트럭 없음 (lowbed/extendable 필요)") for p in panels],
            sc,
        )

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
        return [], blocked, sc

    # 길이 내림차순 정렬 (FFD)
    valid.sort(key=lambda x: x[0].length, reverse=True)

    # 각 bin = { truck, base_items, stacked_items, used_length, total_weight }
    # base_items[i] ↔ stacked_items[i] (None이면 빈 슬롯)
    bins: list[dict] = []

    for p, ok_trucks in valid:
        placed = False

        # ① 기존 bin의 빈 슬롯에 적층 (L자 위에 L자 올리기)
        if p.kind == "lshape":
            for b in bins:
                if b["truck"] not in ok_trucks:
                    continue
                for i, (base_L, slot) in enumerate(
                    zip(b["base_items"], b["stacked_items"])
                ):
                    if slot is not None:
                        continue
                    if not _can_stack_on_lshape(p, base_L, b["truck"], sp):
                        continue
                    if b["total_weight"] + p.weight > b["truck"].max_weight:
                        continue
                    b["stacked_items"][i] = p
                    b["total_weight"] += p.weight
                    placed = True
                    break
                if placed:
                    break

        # ② 기존 bin 트럭 바닥에 나란히 추가
        if not placed:
            for b in bins:
                if b["truck"] not in ok_trucks:
                    continue
                tr = b["truck"]
                usable = tr.max_length - 2 * sp.truck_edge_clearance_mm
                gap = sp.panel_gap_mm if b["base_items"] else 0.0
                if b["used_length"] + gap + p.length > usable:
                    continue
                if p.width > tr.max_width:
                    continue
                if p.thickness + p.wall_height + tr.vehicle_height_offset > tr.max_height:
                    continue
                if b["total_weight"] + p.weight > tr.max_weight:
                    continue
                b["base_items"].append(p)
                b["stacked_items"].append(None)
                b["used_length"] += gap + p.length
                b["total_weight"] += p.weight
                placed = True
                break

        # ③ 새 bin 오픈
        if not placed:
            ok_for_new = [
                tr for tr in ok_trucks
                if (p.length <= tr.max_length - 2 * sp.truck_edge_clearance_mm
                    and p.width <= tr.max_width
                    and p.thickness + p.wall_height + tr.vehicle_height_offset
                    <= tr.max_height)
            ]
            if not ok_for_new:
                blocked.append((p, "L자 패널 길이/폭/높이가 트럭 한도 초과"))
                continue
            best = _closest_fit_truck(ok_for_new, p.length, p.weight)
            bins.append({
                "truck": best,
                "base_items": [p],
                "stacked_items": [None],
                "used_length": p.length,
                "total_weight": p.weight,
            })

    # ── stacking_candidates → L자 빈 슬롯에 배치 ───────────────────────────
    remaining_candidates: list[Panel] = []
    if sc:
        cands_sorted = sorted(sc, key=lambda x: x.weight, reverse=True)
        for cand in cands_sorted:
            placed = False
            for b in bins:
                for i, (base_L, slot) in enumerate(
                    zip(b["base_items"], b["stacked_items"])
                ):
                    if slot is not None:
                        continue
                    if not _can_stack_on_lshape(cand, base_L, b["truck"], sp):
                        continue
                    if b["total_weight"] + cand.weight > b["truck"].max_weight:
                        continue
                    b["stacked_items"][i] = cand
                    b["total_weight"] += cand.weight
                    placed = True
                    break
                if placed:
                    break
            if not placed:
                remaining_candidates.append(cand)

    # ── bin → Trip 변환 ──────────────────────────────────────────────────────
    trips: list[Trip] = []
    next_no = start_trip_no
    for b in bins:
        usable = b["truck"].max_length - 2 * sp.truck_edge_clearance_mm
        ppr = len(b["base_items"])
        has_stacked = any(s is not None for s in b["stacked_items"])
        trips.append(Trip(
            trip_no=next_no,
            truck=b["truck"],
            items=b["base_items"],
            panels_per_row=ppr,
            n_layers=2 if has_stacked else 1,
            used_length_mm=b["used_length"],
            usable_length_mm=usable,
            stacked_items=b["stacked_items"],
        ))
        next_no += 1

    return trips, blocked, remaining_candidates


# ---------------------------------------------------------------------------
# 트럭 교체 검사 — 사용자가 회차별로 트럭을 바꿀 때
# ---------------------------------------------------------------------------

def _panel_overcount_reason(
    n: int, max_n: int, ppr: int, nl: int,
    sample: Panel, new_truck: Truck, spacing: SpacingParams,
) -> str:
    """패널 적재 불가 원인을 진단해 사람이 읽기 쉬운 문자열로 반환.

    ppr·nl은 _max_floor_panels_per_truck 반환값을 그대로 전달해야 함.
    """
    inner_h = new_truck.max_height - new_truck.vehicle_height_offset
    usable_len = new_truck.max_length - 2 * spacing.truck_edge_clearance_mm

    # ── 원인 ①: 패널 폭 > 트럭 폭
    if sample.width > new_truck.max_width:
        return (
            f"❌ 패널 폭이 트럭 폭보다 넓습니다\n"
            f"  • 패널 폭 {sample.width:.0f}mm > 트럭 최대 폭 {new_truck.max_width:.0f}mm"
        )

    # ── 원인 ②: 패널 길이 > 트럭 유효 길이 (ppr=0)
    if ppr == 0 or sample.length > usable_len:
        return (
            f"❌ 트럭 적재 공간이 너무 짧습니다\n"
            f"  • 패널 길이 {sample.length:.0f}mm > 유효 적재 길이 {usable_len:.0f}mm\n"
            f"  • (트럭 {new_truck.max_length:.0f}mm − 양끝 여유 {spacing.truck_edge_clearance_mm:.0f}mm × 2)"
        )

    # ── 원인 ③: 패널 두께 > 내측 높이 (nl=0)
    if nl == 0 or sample.thickness > inner_h:
        return (
            f"❌ 패널 두께가 내측 높이를 초과합니다\n"
            f"  • 패널 두께 {sample.thickness:.0f}mm > 내측 높이 {inner_h:.0f}mm\n"
            f"  • (트럭 {new_truck.max_height:.0f}mm − 차체 {new_truck.vehicle_height_offset:.0f}mm)"
        )

    # ── 원인 ④: 높이 한도 vs 중량 한도
    stack_h = nl * sample.thickness + max(nl - 1, 0) * spacing.panel_gap_mm
    max_by_wt = math.floor(new_truck.max_weight / sample.weight) if sample.weight > 0 else n

    if max_n <= max_by_wt:
        # 높이(적층 단수)가 병목
        return (
            f"❌ 적층 높이 초과  ({n}매 요청 / 최대 {max_n}매)\n"
            f"  • 내측 높이 {inner_h:.0f}mm = 트럭 {new_truck.max_height:.0f}mm − 차체 {new_truck.vehicle_height_offset:.0f}mm\n"
            f"  • 최대 {nl}단 × {ppr}열 = {nl * ppr}매 "
            f"(높이 {stack_h:.0f}mm)"
        )
    else:
        # 중량이 병목
        total_req = n * sample.weight
        return (
            f"❌ 중량 초과  ({n}매 요청 / 최대 {max_n}매)\n"
            f"  • {n}매 × {sample.weight:.0f}kg/매 = {total_req:.0f}kg\n"
            f"  • 트럭 적재한도 {new_truck.max_weight:.0f}kg → 최대 {max_by_wt}매"
        )


def recheck_trip_with_truck(
    trip: Trip,
    new_truck: Truck,
    road: RoadClass,
    spacing: SpacingParams = SpacingParams(),
) -> tuple[bool, str, Trip | None]:
    """주어진 trip의 화물을 new_truck에 그대로 실을 수 있나 검사."""
    if not trip.items:
        return True, "(빈 회차)", trip

    sample = trip.items[0]

    # 1) 트럭 종류 호환성
    if trip.kind == "module":
        if new_truck.truck_type not in ("lowbed", "extendable"):
            return False, f"❌ 모듈은 lowbed / extendable 트럭에만 적재 가능\n  • 선택한 트럭 종류: {new_truck.truck_type}", None
    else:
        if new_truck.truck_type not in ("lowbed", "extendable"):
            kind_label = {"wall": "벽체 패널", "lshape": "L자 패널"}.get(
                sample.kind if isinstance(sample, Panel) else "", "플로어 패널"
            )
            return False, f"❌ {kind_label}은 lowbed / extendable 트럭에만 적재 가능\n  • 선택한 트럭 종류: {new_truck.truck_type}", None

    # 2) 도로/트럭 4 조건 검사
    for item in trip.items:
        r = can_carry(item, new_truck, road)
        if not r.ok:
            return False, "❌ 도로/트럭 한도 초과\n  • " + "\n  • ".join(r.reasons), None

    n = len(trip.items)
    usable = new_truck.max_length - 2 * spacing.truck_edge_clearance_mm

    # 3) 모듈: 길이 + 중량 수식 검사
    if trip.kind == "module":
        total_len = sum(m.length for m in trip.items) + max(0, n - 1) * spacing.panel_gap_mm
        if total_len > usable:
            return False, (
                f"❌ 길이 초과\n"
                f"  • 모듈 길이 합계 {total_len:.0f}mm > 유효 적재 길이 {usable:.0f}mm\n"
                f"  • (트럭 {new_truck.max_length:.0f}mm − 양끝 여유 {spacing.truck_edge_clearance_mm:.0f}mm×2)"
            ), None
        total_w = sum(m.weight for m in trip.items if isinstance(m, Module))
        if total_w > new_truck.max_weight:
            return False, (
                f"❌ 중량 초과\n"
                f"  • 모듈 무게 {total_w:.0f}kg > 트럭 적재한도 {new_truck.max_weight:.0f}kg"
            ), None
        used_l = sum(m.length for m in trip.items) + max(0, n - 1) * spacing.panel_gap_mm
        new_trip = Trip(
            trip_no=trip.trip_no,
            truck=new_truck,
            items=list(trip.items),
            wide_check=any(isinstance(i, Module) and i.is_wide() for i in trip.items),
            panels_per_row=n,
            n_layers=1,
            used_length_mm=used_l,
            usable_length_mm=usable,
        )
        return True, "OK", new_trip

    # 4) L자 패널
    if isinstance(sample, Panel) and sample.kind == "lshape":
        max_n = _max_lshape_panels_per_truck(sample, new_truck, spacing)
        if n > max_n:
            total_w = n * sample.weight
            return False, (
                f"❌ 적재 불가  ({n}매 요청 / 최대 {max_n}매)\n"
                f"  • {n}매 × {sample.weight:.0f}kg/매 = {total_w:.0f}kg  (한도 {new_truck.max_weight:.0f}kg)"
            ), None
        # 적층 패널도 새 트럭에서 가능한지 간단 검사
        for i, stk in enumerate(trip.stacked_items):
            if stk is not None and i < n:
                if not _can_stack_on_lshape(stk, trip.items[i], new_truck, spacing):
                    return False, (
                        f"❌ 적층 패널 '{stk.name}'이 새 트럭에서 L자 위 적층 조건을 만족하지 않습니다."
                    ), None
        used_l = n * sample.length + max(0, n - 1) * spacing.panel_gap_mm
        new_trip = Trip(
            trip_no=trip.trip_no,
            truck=new_truck,
            items=list(trip.items),
            panels_per_row=n,
            n_layers=trip.n_layers,
            used_length_mm=used_l,
            usable_length_mm=usable,
            stacked_items=list(trip.stacked_items),
        )
        return True, "OK", new_trip

    # 5) 플로어 패널 / 벽체 패널 (눕혀서 적층, 동일 로직)
    max_n, ppr, nl = _max_floor_panels_per_truck(sample, new_truck, spacing)
    if n > max_n:
        return False, _panel_overcount_reason(n, max_n, ppr, nl, sample, new_truck, spacing), None
    used_layers = math.ceil(n / ppr) if ppr > 0 else 1
    used_l = ppr * sample.length + max(0, ppr - 1) * spacing.panel_gap_mm
    new_trip = Trip(
        trip_no=trip.trip_no,
        truck=new_truck,
        items=list(trip.items),
        panels_per_row=ppr,
        n_layers=used_layers,
        used_length_mm=used_l,
        usable_length_mm=usable,
    )
    return True, "OK", new_trip


def simulate_manual_trip(
    items: list[Item],
    truck: Truck,
    road: RoadClass,
    spacing: SpacingParams = SpacingParams(),
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
    return recheck_trip_with_truck(fake_trip, truck, road, spacing)


def apply_truck_overrides(
    result: PackResult,
    overrides: dict,
    trucks: list[Truck],
    road: RoadClass,
    spacing: SpacingParams = SpacingParams(),
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

        ok, reason, new_trip = recheck_trip_with_truck(trip, new_truck, road, spacing)
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
) -> PackResult:
    """모듈·패널 리스트 → 운송 회차 산정 (FFD 빈 패킹).

    처리 순서:
      1) 모듈 (1트럭=1모듈)
      2) L자 패널 우선 배치 + 플로어·벽체 패널 적층 시도
      3) L자 위에 올리지 못한 플로어 패널 → 별도 트립
      4) L자 위에 올리지 못한 벽체 패널 → 별도 트립
    """
    trips: list[Trip] = []
    blocked: list[tuple[Item, str]] = []
    next_no = 1

    # 패널 종류 분류
    floor_panels = [p for p in panels if p.kind == "floor"]
    wall_panels  = [p for p in panels if p.kind == "wall"]
    lshape_panels = [p for p in panels if p.kind == "lshape"]

    # 1) 모듈 (1 트럭 = 1 모듈)
    mod_trips, mod_blocked = _pack_modules(
        modules, trucks, road, spacing, start_trip_no=next_no
    )
    trips.extend(mod_trips)
    blocked.extend(mod_blocked)
    next_no += len(mod_trips)

    # 2) L자 패널 (우선 배치) + 플로어·벽체 패널을 L자 빈 슬롯에 적층 시도
    stacking_candidates = floor_panels + wall_panels
    lshape_trips, lshape_blocked, remaining_stacking = _pack_lshape_panels(
        lshape_panels, trucks, road, spacing,
        start_trip_no=next_no,
        stacking_candidates=stacking_candidates if stacking_candidates else None,
    )
    trips.extend(lshape_trips)
    blocked.extend(lshape_blocked)
    next_no += len(lshape_trips)

    # 적층 배치에 실패한 패널 → 종류별 재분류
    remaining_floor = [p for p in remaining_stacking if p.kind == "floor"]
    remaining_wall  = [p for p in remaining_stacking if p.kind == "wall"]

    # 3) 플로어 패널 (FFD by 무게, 적층)
    floor_trips, floor_blocked = _pack_floor_panels(
        remaining_floor, trucks, road, spacing, start_trip_no=next_no
    )
    trips.extend(floor_trips)
    blocked.extend(floor_blocked)
    next_no += len(floor_trips)

    # 4) 벽체 패널 (FFD by 무게, 눕혀서 적층)
    wall_trips, wall_blocked = _pack_wall_panels(
        remaining_wall, trucks, road, spacing, start_trip_no=next_no
    )
    trips.extend(wall_trips)
    blocked.extend(wall_blocked)

    return PackResult(trips=trips, blocked=blocked)
