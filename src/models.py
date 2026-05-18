"""모듈러 운송 시뮬레이터 데이터 모델.

단위 규약: 길이는 mm, 중량은 kg.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal


PanelKind = Literal["floor", "wall", "lshape"]
TruckType = Literal["lowbed", "extendable", "aframe"]
SectionType = Literal["SHS", "RHS", "H", "C", "L", "CFT"]


@dataclass(frozen=True)
class Section:
    """KS 표준 강재 단면.

    근거: KS D 3568 (각형강관), KS D 3502/3503 (H형강·L형강), KS D 3530 (C형강).
    weight_per_m = 단면적(mm²) × 강재 밀도(7,850 kg/m³) × 1m / 10⁹.
    """

    name: str                # 예: "SHS 200x200x8"
    section_type: SectionType
    width: float             # mm
    height: float            # mm
    thickness: float         # mm (SHS/RHS는 두께, H/C는 웨브 두께, L은 변 두께)
    weight_per_m: float      # kg/m
    flange_thickness: float = 0.0  # H/C형강 플랜지 두께 (그 외는 0)


@dataclass(frozen=True)
class Module:
    """수평/수직 모듈. 무게는 부재(기둥+보) + 슬래브 + 비내력벽에서 자동 계산."""

    name: str
    width: float                   # mm
    length: float                  # mm
    height: float                  # mm
    column_section: Section        # 4 모서리 기둥 단면
    beam_section: Section          # 천장보·바닥보 공통 단면
    extra_weight_kg: float = 0.0   # 슬래브 + 비내력벽 합산 추가 중량 (kg)

    @property
    def weight(self) -> float:
        """총 무게 = 프레임(4기둥+8보) + 슬래브 + 비내력벽."""
        col_total_m = (4 * self.height) / 1000.0
        beam_total_m = (4 * (self.width + self.length) * 2) / 1000.0  # 천장+바닥
        frame_w = (
            col_total_m * self.column_section.weight_per_m
            + beam_total_m * self.beam_section.weight_per_m
        )
        return frame_w + self.extra_weight_kg

    def is_wide(self) -> bool:
        """광폭 모듈 여부 (폭 3.0m 초과 → 확장형 광폭 트레일러 필요)."""
        return self.width > 3000.0


@dataclass(frozen=True)
class Panel:
    """플로어/벽체/L자 구조패널.

    플로어 패널: 보 4개(둘레) — 2×(폭+길이)
    벽체 패널: 보 2개(위·아래, 폭 방향) + 기둥 2개(양쪽, 길이 방향)
    L자 패널: 보 5개(바닥 먼변+꺾임+벽 윗변 = 3×폭, 바닥 양옆 = 2×길이) + 기둥 2개(벽 양쪽, wall_height)
    extra_weight_kg: 비내력벽 채움재(단열재·마감재 등) 추가 중량 (kg/매, 기본 0)
    """

    name: str
    kind: PanelKind
    width: float                          # mm
    length: float                         # mm
    thickness: float                      # mm (플로어: 슬래브 두께 / 벽체·L자: 벽체 두께)
    beam_section: Section                 # 플로어: 둘레 보 / 벽체: 위·아래 보 / L자: 수평 보
    column_section: Section | None = None # 벽체·L자 패널 (양쪽 기둥)
    wall_height: float = 0.0             # L자 패널 전용 — 벽 부분 높이 (mm)
    extra_weight_kg: float = 0.0         # 비내력벽 채움재 추가 중량 (kg/매)

    @property
    def weight(self) -> float:
        if self.kind == "lshape" and self.column_section is not None:
            # L자 패널: 보 5개 + 기둥 2개
            beam_total_m = (3 * self.width + 2 * self.length) / 1000.0
            col_total_m = (2 * self.wall_height) / 1000.0
            frame_w = (beam_total_m * self.beam_section.weight_per_m
                       + col_total_m * self.column_section.weight_per_m)
            return frame_w + self.extra_weight_kg
        if self.kind == "wall" and self.column_section is not None:
            # 벽체 패널: 보 2개(위·아래, 스팬=길이 방향) + 기둥 2개(양쪽, 층고=폭 방향)
            beam_total_m = (2 * self.length) / 1000.0  # 위·아래 보 = 2 × 가로 스팬
            col_total_m = (2 * self.width) / 1000.0    # 양쪽 기둥 = 2 × 층고
            frame_w = (beam_total_m * self.beam_section.weight_per_m
                       + col_total_m * self.column_section.weight_per_m)
            return frame_w + self.extra_weight_kg
        # 플로어 패널: 보 4개(둘레)
        beam_total_m = (2 * (self.width + self.length)) / 1000.0
        return beam_total_m * self.beam_section.weight_per_m + self.extra_weight_kg


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
    hourly_rate_krw: float = 0.0          # 시간당 기계경비 (원/hr). lowbed만 적용. 출처: 대한건설협회 건설기계 경비산출표 2025, 코드 2702


@dataclass(frozen=True)
class SpacingParams:
    """패널 적재 간격 (mm).

    panel_gap_mm            — 패널 사이 수평·수직 간격 (같은 열 내, 적층 층 사이)
    truck_edge_clearance_mm — 트럭 양끝 결박 여유
    lshape_stack_gap_mm     — L자 패널 벽체와 적층 패널 사이 수평 간격
    """

    panel_gap_mm: float = 100.0
    truck_edge_clearance_mm: float = 200.0
    lshape_stack_gap_mm: float = 100.0


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


def load_sections(path: Path | None = None) -> list[Section]:
    """KS 표준 강재 단면 카탈로그 로드."""
    p = path or (DATA_DIR / "sections.json")
    with p.open("r", encoding="utf-8") as f:
        raw = json.load(f)
    return [Section(**s) for s in raw]
