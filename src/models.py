"""모듈러 운송 시뮬레이터 데이터 모델.

단위 규약: 길이는 mm, 중량은 kg.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal


PanelKind = Literal["floor", "wall"]
TruckType = Literal["lowbed", "extendable", "aframe"]


@dataclass(frozen=True)
class Module:
    """수평/수직 모듈."""

    name: str
    width: float   # mm
    length: float  # mm
    height: float  # mm
    weight: float  # kg

    def is_wide(self) -> bool:
        """광폭 모듈 여부 (폭 3.0m 초과 → 확장형 광폭 트레일러 필요)."""
        return self.width > 3000.0


@dataclass(frozen=True)
class Panel:
    """플로어/벽체 구조패널."""

    name: str
    kind: PanelKind   # "floor" or "wall"
    width: float      # mm
    length: float     # mm
    thickness: float  # mm
    weight: float     # kg


@dataclass(frozen=True)
class Truck:
    """모듈러 운송 차량.

    truck_type별 적재 정책:
      - lowbed: 저상 트레일러. 모듈·플로어 패널 가능. 폭 3.0m 이하.
      - extendable: 확장형 광폭 트레일러. 광폭 모듈 가능. 폭 3.4m. 광폭 운송 허가 필요.
      - aframe: A-frame 트레일러. 벽체 패널 세워서만.
    """

    name: str
    truck_type: TruckType
    max_length: float
    max_width: float
    max_height: float
    max_weight: float
    vehicle_height_offset: float = 700.0  # 차체 높이 (저상 평균 0.7m)


@dataclass(frozen=True)
class SpacingParams:
    """패널 적재 간격 표준값 (mm).

    근거:
      - PCI MNL-122 §6: 운송·하역 시 더니지 권장
      - KOSHA 화물 결박 가이드: 결박 작업 공간 확보
      - 본 자료: references/05_PCI_운송더니지_가이드.md
    """

    panel_gap_mm: float = 100.0
    truck_edge_clearance_mm: float = 200.0
    dunnage_thickness_mm: float = 100.0


@dataclass(frozen=True)
class DunnageSpec:
    """더니지 사양 — 무게 산출용.

    근거: references/07_목재더니지_밀도_무게.md
    """

    density_kg_per_m3: float = 500.0      # 소나무 건조 평균
    cross_section_mm: float = 100.0       # 100×100mm 각재
    pieces_per_layer: int = 3             # 양 끝 + 중간


@dataclass(frozen=True)
class RoadClass:
    """도로 등급."""

    name: str
    max_length: float
    max_width: float
    max_height: float
    max_weight: float


# ---------------------------------------------------------------------------
# JSON 로더
# ---------------------------------------------------------------------------

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def load_trucks(path: Path | None = None) -> list[Truck]:
    p = path or (DATA_DIR / "trucks.json")
    with p.open("r", encoding="utf-8") as f:
        raw = json.load(f)
    return [Truck(**t) for t in raw]


def load_road_classes(path: Path | None = None) -> list[RoadClass]:
    p = path or (DATA_DIR / "road_limits.json")
    with p.open("r", encoding="utf-8") as f:
        raw = json.load(f)
    return [RoadClass(**r) for r in raw]
