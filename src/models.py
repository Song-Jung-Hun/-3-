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
SectionType = Literal["SHS", "RHS", "H", "C", "L"]


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
    """수평/수직 모듈. 무게는 부재(기둥+보)에서 자동 계산."""

    name: str
    width: float                   # mm
    length: float                  # mm
    height: float                  # mm
    column_section: Section        # 4 모서리 기둥 단면
    beam_section: Section          # 천장보·바닥보 공통 단면

    @property
    def weight(self) -> float:
        """프레임 무게 = 4 기둥 + 4 천장보 + 4 바닥보."""
        col_total_m = (4 * self.height) / 1000.0
        beam_total_m = (4 * (self.width + self.length) * 2) / 1000.0  # 천장+바닥
        return (
            col_total_m * self.column_section.weight_per_m
            + beam_total_m * self.beam_section.weight_per_m
        )

    def is_wide(self) -> bool:
        """광폭 모듈 여부 (폭 3.0m 초과 → 확장형 광폭 트레일러 필요)."""
        return self.width > 3000.0


@dataclass(frozen=True)
class Panel:
    """플로어/벽체/L자 구조패널. 무게는 부재만 계산 (콘크리트 바닥판/벽체판 제외).

    플로어 패널: 보 4개(둘레) — 2×(폭+길이)
    벽체 패널: 보 2개(위·아래, 폭 방향) + 기둥 2개(양쪽, 길이 방향)
    L자 패널: 보 5개(바닥 먼변+꺾임+벽 윗변 = 3×폭, 바닥 양옆 = 2×길이) + 기둥 2개(벽 양쪽, wall_height)
    """

    name: str
    kind: PanelKind
    width: float                          # mm
    length: float                         # mm
    thickness: float                      # mm (콘크리트 바닥판/벽체판 두께, 무게 계산엔 미사용)
    beam_section: Section                 # 플로어: 둘레 보 / 벽체: 위·아래 보 / L자: 수평 보
    column_section: Section | None = None # 벽체·L자 패널 (양쪽 기둥)
    wall_height: float = 0.0             # L자 패널 전용 — 벽 부분 높이 (mm)

    @property
    def weight(self) -> float:
        if self.kind == "lshape" and self.column_section is not None:
            # L자 패널: 보 5개 + 기둥 2개
            beam_total_m = (3 * self.width + 2 * self.length) / 1000.0
            col_total_m = (2 * self.wall_height) / 1000.0
            return (beam_total_m * self.beam_section.weight_per_m
                    + col_total_m * self.column_section.weight_per_m)
        if self.kind == "wall" and self.column_section is not None:
            # 벽체 패널: 보 2개(위·아래, 폭 방향) + 기둥 2개(양쪽, 길이 방향)
            beam_total_m = (2 * self.width) / 1000.0
            col_total_m = (2 * self.length) / 1000.0
            return (beam_total_m * self.beam_section.weight_per_m
                    + col_total_m * self.column_section.weight_per_m)
        # 플로어 패널: 보 4개(둘레)
        beam_total_m = (2 * (self.width + self.length)) / 1000.0
        return beam_total_m * self.beam_section.weight_per_m


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


def load_sections(path: Path | None = None) -> list[Section]:
    """KS 표준 강재 단면 카탈로그 로드."""
    p = path or (DATA_DIR / "sections.json")
    with p.open("r", encoding="utf-8") as f:
        raw = json.load(f)
    return [Section(**s) for s in raw]
