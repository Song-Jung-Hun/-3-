"""모듈러 운송 시뮬레이터 — Streamlit 단일 페이지.

사용자가 모듈/플로어 패널/벽체 패널 종류 수를 먼저 선택하고,
종류별 사양·개수를 입력 → 운송 회차 산출 + 적재 도식 시각화.

실행:
    streamlit run app.py
"""
from __future__ import annotations

import pandas as pd
import plotly.express as px
import streamlit as st

from src.models import (
    Module, Panel, Section, SpacingParams,
    load_road_classes, load_sections, load_trucks,
)
from src.packer import apply_truck_overrides, pack_items
from src.visualizer import draw_3d_view, draw_rear_view, draw_top_view


# ---------------------------------------------------------------------------
# 페이지 설정 & 카탈로그 로딩
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="모듈러 운송 시뮬레이터",
    page_icon="🚚",
    layout="wide",
)


@st.cache_data
def _load_catalogs():
    return load_trucks(), load_road_classes(), load_sections()


trucks, roads, sections = _load_catalogs()
SECTION_BY_NAME = {s.name: s for s in sections}
SECTION_NAMES = [s.name for s in sections]


# ---------------------------------------------------------------------------
# 사이드바 — 도로 + 종류 수 + 적재 간격 + 트럭 카탈로그
# ---------------------------------------------------------------------------

with st.sidebar:
    st.title("⚙️ 시뮬레이션 설정")

    road_name = st.selectbox(
        "도로 등급",
        options=[r.name for r in roads],
        index=0,
        help="LH 매뉴얼 §2.7.1 표 기반.",
    )
    road = next(r for r in roads if r.name == road_name)

    st.caption(
        f"**{road.name}** 한도: 길이 {road.max_length:.0f}mm / 폭 {road.max_width:.0f}mm "
        f"/ 높이 {road.max_height:.0f}mm / 중량 {road.max_weight:.0f}kg"
    )

    st.divider()
    st.subheader("📋 종류 수 선택")
    n_module_kinds = st.number_input(
        "모듈 종류 수", min_value=0, max_value=10, value=1, step=1,
        help="예: 침실·거실·주방을 따로 입력하려면 3 선택",
    )
    n_floor_kinds = st.number_input(
        "플로어 패널 종류 수", min_value=0, max_value=10, value=1, step=1,
    )
    n_wall_kinds = st.number_input(
        "벽체 패널 종류 수", min_value=0, max_value=10, value=1, step=1,
    )
    n_lshape_kinds = st.number_input(
        "L자 패널 종류 수", min_value=0, max_value=10, value=0, step=1,
        help="바닥+벽이 ㄴ자로 합쳐진 패널",
    )

    st.divider()
    st.subheader("🛣 운송 거리")
    distance_km = st.number_input(
        "공장 → 현장 거리 (편도 km)",
        min_value=0.0, max_value=2000.0, value=50.0, step=5.0,
        help="향후 경제성 분석 시 거리 × 트럭 단가 = 운송비 산출에 사용.",
    )
    st.caption(f"왕복 거리 = **{distance_km * 2:.0f} km** (편도 × 2)")

    st.divider()
    st.subheader("📏 패널 적재 간격")
    panel_gap = st.slider(
        "패널 사이 Gap (mm)", 50, 200, 100, 10,
        help="패널과 패널 사이 간격. 결박벨트·지게차 포크 진입 공간. PCI MNL-122 권장 75~100mm.",
    )
    edge_clearance = st.slider(
        "트럭 양끝 여유 (mm)", 0, 500, 200, 50,
        help="결박 작업 공간. KOSHA 화물 결박 가이드 기준 150~200mm 권장.",
    )
    spacing = SpacingParams(
        panel_gap_mm=float(panel_gap),
        truck_edge_clearance_mm=float(edge_clearance),
    )

    st.divider()
    with st.expander(f"🚛 모듈러 운송 차량 카탈로그 ({len(trucks)}종)"):
        rows = []
        for t in trucks:
            roads_ok = []
            for r in roads:
                # 차량 자체 진입 가능 여부 = 길이·폭 한도. 높이·중량은 화물에 따라 가변.
                if t.max_length <= r.max_length and t.max_width <= r.max_width:
                    short = r.name.split(" ")[0]
                    roads_ok.append(short)
            rows.append(
                {
                    "차량": t.name,
                    "차종": t.truck_type,
                    "길이(mm)": t.max_length,
                    "폭(mm)": t.max_width,
                    "높이(mm)": t.max_height,
                    "적재(kg)": t.max_weight,
                    "운행 가능 도로": " / ".join(roads_ok) if roads_ok else "없음",
                }
            )
        df_trucks = pd.DataFrame(rows)
        st.dataframe(df_trucks, hide_index=True, width="stretch")
        st.caption(
            "**lowbed** = 저상 트레일러 (모듈·패널 전 종류, 눕혀서 적층) / "
            "**extendable** = 확장형 광폭 트레일러 (광폭 모듈·각종 패널, 광로 전용). "
            "차량 길이/폭/높이가 도로 한도를 넘으면 그 도로 진입 불가. "
            "출처: LH §2.7.1, 한국특장차, PCI MNL-122."
        )


# ---------------------------------------------------------------------------
# 입력 폼 헬퍼
# ---------------------------------------------------------------------------

MODULE_DEFAULTS = [
    ("침실 모듈", 3000, 6000, 3000, "SHS 200x200x8", "SHS 150x150x6", 0),
    ("거실 광폭모듈", 3400, 9000, 3000, "SHS 250x250x9", "SHS 200x200x8", 0),
    ("주방 모듈", 3000, 6000, 3000, "SHS 200x200x8", "SHS 150x150x6", 0),
    ("욕실 수직모듈", 2400, 3000, 3000, "SHS 175x175x8", "SHS 125x125x6", 0),
    ("기타 모듈", 3000, 6000, 3000, "SHS 200x200x8", "SHS 150x150x6", 0),
]

FLOOR_DEFAULTS = [
    ("거실 플로어", 3000, 9000, 200, "H 200x100x5.5x8", 0),
    ("주방 플로어", 3000, 6000, 200, "H 200x100x5.5x8", 0),
    ("기타 플로어", 3000, 9000, 200, "H 200x100x5.5x8", 0),
]

# 벽체 패널: (이름, 폭(층고·트럭폭방향), 길이(가로스팬·트럭길이방향), 두께, 보단면, 기둥단면, 수량)
# 폭(width) = 층고 → 트럭 폭 방향 (짧은 치수, 예: 2,800mm)
# 길이(length) = 가로 스팬 → 트럭 길이 방향 (긴 치수, 예: 9,000mm)
WALL_DEFAULTS = [
    ("세대간벽", 2800, 9000, 150, "C 150x75x6.5x10", "SHS 150x150x6", 0),
    ("외피벽체", 2800, 3000, 200, "C 200x80x7.5x11", "SHS 175x175x8", 0),
    ("기타 벽체", 2800, 6000, 150, "C 150x75x6.5x10", "SHS 150x150x6", 0),
]

# L자 패널: (이름, 폭, 바닥길이, 두께, 벽높이, 보단면, 기둥단면, 수량)
LSHAPE_DEFAULTS = [
    ("욕실 L자", 3000, 6000, 200, 2800, "H 200x100x5.5x8", "SHS 150x150x6", 0),
    ("주방 L자", 3000, 6000, 200, 2800, "H 200x100x5.5x8", "SHS 150x150x6", 0),
    ("기타 L자", 3000, 6000, 200, 2800, "H 200x100x5.5x8", "SHS 150x150x6", 0),
]


def _section_select(label: str, default_name: str, key: str) -> Section:
    """단면 선택 selectbox + 단위중량 표시."""
    try:
        idx = SECTION_NAMES.index(default_name)
    except ValueError:
        idx = 0
    chosen = st.selectbox(label, options=SECTION_NAMES, index=idx, key=key)
    sec = SECTION_BY_NAME[chosen]
    st.caption(f"  → {sec.weight_per_m} kg/m")
    return sec


def _module_form(idx: int) -> list[Module]:
    d = MODULE_DEFAULTS[idx] if idx < len(MODULE_DEFAULTS) else MODULE_DEFAULTS[-1]
    name = st.text_input("이름", value=d[0], key=f"mname_{idx}")
    width = st.number_input("폭 (mm)", 1000, 5000, d[1], 100, key=f"mw_{idx}")
    length = st.number_input("길이 (mm)", 2000, 20000, d[2], 100, key=f"ml_{idx}")
    height = st.number_input("높이 (mm)", 1500, 5000, d[3], 100, key=f"mh_{idx}")

    col_sec = _section_select("기둥", d[4], f"mcol_{idx}")
    beam_sec = _section_select("보", d[5], f"mbeam_{idx}")

    count = st.number_input("운송 수량 (EA)", 0, 500, d[6], 1, key=f"mc_{idx}")

    # 자동 무게 표시
    sample = Module(
        name="_preview",
        width=float(width), length=float(length), height=float(height),
        column_section=col_sec, beam_section=beam_sec,
    )
    st.info(f"🪶 자동 산출 무게: **{sample.weight:,.0f} kg/매**")

    return [
        Module(
            name=f"{name}-{j + 1}",
            width=float(width),
            length=float(length),
            height=float(height),
            column_section=col_sec,
            beam_section=beam_sec,
        )
        for j in range(int(count))
    ]


def _panel_form(idx: int, kind: str, defaults: list, key_prefix: str) -> list[Panel]:
    d = defaults[idx] if idx < len(defaults) else defaults[-1]
    name = st.text_input("이름", value=d[0], key=f"{key_prefix}name_{idx}")
    if kind == "wall":
        width  = st.number_input("층고 — 트럭 폭 방향 (mm)", 500,  6000, d[1], 100, key=f"{key_prefix}w_{idx}")
        length = st.number_input("가로 스팬 — 트럭 길이 방향 (mm)", 500, 20000, d[2], 100, key=f"{key_prefix}l_{idx}")
    else:
        width  = st.number_input("폭 (mm)", 500, 5000, d[1], 100, key=f"{key_prefix}w_{idx}")
        length = st.number_input("길이 (mm)", 1000, 20000, d[2], 100, key=f"{key_prefix}l_{idx}")
    thickness = st.number_input("두께 (mm)", 50, 1000, d[3], 10, key=f"{key_prefix}t_{idx}")

    beam_sec = _section_select("보", d[4], f"{key_prefix}beam_{idx}")
    col_sec = None
    extra_weight_kg = 0.0

    # 플로어 패널: 데크플레이트 + 콘크리트 180mm 슬래브 무게 자동 계산
    # 단위중량 350 kg/m² (강재 데크 ~10 kg/m² + 콘크리트 충전 ~340 kg/m²)
    FLOOR_SLAB_UNIT_WEIGHT = 350.0  # kg/m²
    if kind == "floor":
        slab_area_m2 = (float(width) / 1000.0) * (float(length) / 1000.0)
        extra_weight_kg = FLOOR_SLAB_UNIT_WEIGHT * slab_area_m2

    if kind == "wall":
        col_sec = _section_select("기둥", d[5], f"{key_prefix}col_{idx}")

        # 비내력벽 종류 선택 + 구성 안내
        NONBEARING_WALL_TYPES = {
            "내부 비내력벽": {
                "unit_weight": 30.0,
                "composition": [
                    "경량철골 스터드",
                    "석고보드 12.5mm (양면)",
                    "단열재 (유리면)",
                ],
            },
            "외부 비내력벽": {
                "unit_weight": 55.0,
                "composition": [
                    "경량철골 스터드",
                    "단열재 100mm",
                    "섬유시멘트판 (외장)",
                    "석고보드 12.5mm (내장)",
                ],
            },
        }
        st.markdown("**🧱 비내력벽 채움재**")
        sel_col, info_col = st.columns([1, 1.2])
        with sel_col:
            wall_nonbearing = st.radio(
                "비내력벽 종류",
                options=list(NONBEARING_WALL_TYPES.keys()),
                key=f"{key_prefix}wtype_{idx}",
                label_visibility="collapsed",
            )
        with info_col:
            wt = NONBEARING_WALL_TYPES[wall_nonbearing]
            st.caption(f"**{wall_nonbearing}** — {wt['unit_weight']} kg/m²")
            for comp in wt["composition"]:
                st.caption(f"  · {comp}")

        area_m2 = (float(width) / 1000.0) * (float(length) / 1000.0)
        extra_weight_kg = wt["unit_weight"] * area_m2

    count_default = d[-1]
    count = st.number_input("운송 수량 (EA)", 0, 500, count_default, 1, key=f"{key_prefix}c_{idx}")

    sample = Panel(
        name="_preview", kind=kind,
        width=float(width), length=float(length), thickness=float(thickness),
        beam_section=beam_sec, column_section=col_sec,
        extra_weight_kg=extra_weight_kg,
    )
    if kind == "wall":
        frame_w = sample.weight - extra_weight_kg
        st.info(
            f"🪶 자동 산출 무게: **{sample.weight:,.0f} kg/매**  "
            f"(구조 프레임 {frame_w:,.0f} kg + 비내력벽 {extra_weight_kg:,.0f} kg)"
        )
    elif kind == "floor":
        frame_w = sample.weight - extra_weight_kg
        st.info(
            f"🪶 자동 산출 무게: **{sample.weight:,.0f} kg/매**  "
            f"(구조 프레임 {frame_w:,.0f} kg + 슬래브 {extra_weight_kg:,.0f} kg  "
            f"※ 데크플레이트+콘크리트 180mm, 350 kg/m²)"
        )
    else:
        st.info(f"🪶 자동 산출 무게 (부재만): **{sample.weight:,.0f} kg/매**")

    return [
        Panel(
            name=f"{name}-{j + 1}",
            kind=kind,
            width=float(width),
            length=float(length),
            thickness=float(thickness),
            beam_section=beam_sec,
            column_section=col_sec,
            extra_weight_kg=extra_weight_kg,
        )
        for j in range(int(count))
    ]


def _lshape_form(idx: int) -> list[Panel]:
    d = LSHAPE_DEFAULTS[idx] if idx < len(LSHAPE_DEFAULTS) else LSHAPE_DEFAULTS[-1]
    name = st.text_input("이름", value=d[0], key=f"lname_{idx}")
    width = st.number_input("폭 (mm)", 500, 5000, d[1], 100, key=f"lw_{idx}")
    length = st.number_input("바닥 길이 (mm)", 1000, 20000, d[2], 100, key=f"ll_{idx}")
    thickness = st.number_input("두께 (mm)", 50, 1000, d[3], 10, key=f"lt_{idx}")
    wall_height = st.number_input("벽 높이 (mm)", 500, 5000, d[4], 100, key=f"lwh_{idx}")

    beam_sec = _section_select("보", d[5], f"lbeam_{idx}")
    col_sec = _section_select("기둥", d[6], f"lcol_{idx}")

    count = st.number_input("운송 수량 (EA)", 0, 500, d[-1], 1, key=f"lc_{idx}")

    sample = Panel(
        name="_preview", kind="lshape",
        width=float(width), length=float(length), thickness=float(thickness),
        beam_section=beam_sec, column_section=col_sec,
        wall_height=float(wall_height),
    )
    st.info(f"🪶 자동 산출 무게 (부재만): **{sample.weight:,.0f} kg/매**")

    return [
        Panel(
            name=f"{name}-{j + 1}",
            kind="lshape",
            width=float(width),
            length=float(length),
            thickness=float(thickness),
            beam_section=beam_sec,
            column_section=col_sec,
            wall_height=float(wall_height),
        )
        for j in range(int(count))
    ]


# ---------------------------------------------------------------------------
# 메인 — 입력 폼
# ---------------------------------------------------------------------------

st.title("🚚 모듈러 운송 시뮬레이터")
st.markdown(
    "사이드바에서 **종류 수**와 **적재 간격**을 정한 뒤, "
    "아래에 종류별 사양·수량을 입력하세요. 단위는 **mm·kg**."
)

modules: list[Module] = []
panels: list[Panel] = []

if n_module_kinds > 0:
    st.subheader(f"📦 모듈 ({n_module_kinds}종)")
    cols = st.columns(min(int(n_module_kinds), 3))
    for i in range(int(n_module_kinds)):
        with cols[i % 3]:
            with st.expander(f"모듈 종류 {i + 1}", expanded=True):
                modules.extend(_module_form(i))

if n_floor_kinds > 0:
    st.subheader(f"⬜ 플로어 패널 ({n_floor_kinds}종)")
    cols = st.columns(min(int(n_floor_kinds), 3))
    for i in range(int(n_floor_kinds)):
        with cols[i % 3]:
            with st.expander(f"플로어 종류 {i + 1}", expanded=True):
                panels.extend(_panel_form(i, "floor", FLOOR_DEFAULTS, "f"))

if n_wall_kinds > 0:
    st.subheader(f"🟧 벽체 패널 ({n_wall_kinds}종)")
    cols = st.columns(min(int(n_wall_kinds), 3))
    for i in range(int(n_wall_kinds)):
        with cols[i % 3]:
            with st.expander(f"벽체 종류 {i + 1}", expanded=True):
                panels.extend(_panel_form(i, "wall", WALL_DEFAULTS, "w"))

if n_lshape_kinds > 0:
    st.subheader(f"🔲 L자 패널 ({n_lshape_kinds}종)")
    cols = st.columns(min(int(n_lshape_kinds), 3))
    for i in range(int(n_lshape_kinds)):
        with cols[i % 3]:
            with st.expander(f"L자 종류 {i + 1}", expanded=True):
                panels.extend(_lshape_form(i))


# ---------------------------------------------------------------------------
# 결과
# ---------------------------------------------------------------------------

st.divider()

if not modules and not panels:
    st.info(
        "📝 **시작하려면 위 입력 폼에서 운송할 모듈/패널의 수량을 입력해 주세요.**\n\n"
        "1. 모듈/플로어 패널/벽체 패널 종류별로 expander를 펼치고\n"
        "2. **운송 수량 (EA)** 칸에 1 이상 입력\n"
        "3. 입력 즉시 자동 계산되어 운송 결과·시각화·3D 미리보기가 표시됩니다."
    )
    st.stop()

raw_result = pack_items(modules, panels, trucks, road, spacing)

# ---------------------------------------------------------------------------
# 회차별 화물차 선택 UI — 사용자가 자동 결과를 직접 수정 가능
# ---------------------------------------------------------------------------

if "truck_overrides" not in st.session_state:
    st.session_state.truck_overrides = {}

# 현재 결과의 trip_no 들로 stale entry 정리
valid_trip_nos = {t.trip_no for t in raw_result.trips}
st.session_state.truck_overrides = {
    k: v for k, v in st.session_state.truck_overrides.items() if k in valid_trip_nos
}

with st.expander("🔄 회차별 화물차 선택 (선택 사항)", expanded=False):
    st.caption(
        "기본은 알고리즘이 자동 선택한 트럭이 사용됩니다. "
        "원하는 회차의 트럭을 직접 다른 종류로 바꾸면 그 트럭으로 적재 가능한지 자동 검사됩니다. "
        "❌이 뜨면 그 트럭에는 못 실어요 — 원래 트럭이 그대로 유지됩니다."
    )
    truck_names = ["(자동)"] + [t.name for t in trucks]
    for trip in raw_result.trips:
        c1, c2, c3 = st.columns([1.5, 2.5, 3])
        c1.markdown(f"**회차 {trip.trip_no}**")
        c2.caption(f"자동 선택: {trip.truck.name}")
        cur_choice = st.session_state.truck_overrides.get(trip.trip_no, "(자동)")
        try:
            cur_idx = truck_names.index(cur_choice)
        except ValueError:
            cur_idx = 0
        new_choice = c3.selectbox(
            "사용할 트럭",
            options=truck_names,
            index=cur_idx,
            key=f"override_select_{trip.trip_no}",
            label_visibility="collapsed",
        )
        if new_choice == "(자동)":
            st.session_state.truck_overrides.pop(trip.trip_no, None)
        else:
            st.session_state.truck_overrides[trip.trip_no] = new_choice

# 사용자 override 적용
result, override_errors = apply_truck_overrides(
    raw_result, st.session_state.truck_overrides, trucks, road, spacing
)

if override_errors:
    st.warning("⚠ 일부 회차의 트럭 변경이 적용되지 않았습니다. 원래 트럭이 유지됩니다.")
    for no, reason in override_errors.items():
        lines = reason.split("\n")
        header = lines[0]                               # ❌ ... 한 줄
        bullets = [l.strip().lstrip("•").strip() for l in lines[1:] if l.strip()]
        md_bullets = "\n".join(f"- {b}" for b in bullets)
        st.error(f"**회차 {no}** &nbsp; {header}\n\n{md_bullets}")

st.subheader("📊 운송 결과")

mc_card, fc_card, total_card, util_card, dist_card = st.columns(5)
mc_card.metric("모듈 회차", f"{result.module_trips} 회")
fc_card.metric("패널 회차", f"{result.panel_trips} 회")
total_card.metric("**총 회차**", f"{result.total_trips} 회")
util_card.metric("평균 적재율", f"{result.avg_utilization:.1f}%")
total_distance = result.total_trips * distance_km * 2
dist_card.metric(
    "총 운송거리",
    f"{total_distance:,.0f} km",
    help=f"왕복 {distance_km*2:.0f}km × {result.total_trips}회차",
)

if result.blocked:
    st.error(f"⚠ 운송 불가 아이템 {len(result.blocked)}개 — 도로 등급 올리거나 모듈 분할 필요")
    with st.expander("불가 사유 보기"):
        for item, reason in result.blocked:
            st.write(f"- **{item.name}**: {reason}")


# 회차 표
st.markdown("### 🚛 트럭 운송 회차별 적재 내역")
st.caption("회차 = 트럭 1대가 공장→현장으로 1번 운반하는 것.")
trip_rows = []
for trip in result.trips:
    item_names = ", ".join(i.name for i in trip.items)
    extra = ""
    if trip.kind == "panel":
        sample = trip.items[0] if trip.items else None
        if sample and sample.kind == "wall":
            extra = f"{trip.panels_per_row}열 × {trip.n_layers}단 (눕혀서 적층)"
        elif sample and sample.kind == "lshape":
            extra = f"L자 {trip.panels_per_row}매 나란히 (적층 불가)"
        else:
            extra = f"{trip.panels_per_row}열 × {trip.n_layers}단"
    elif trip.kind == "module":
        extra = f"{len(trip.items)}매/대 (1열)"
    # 어느 항목이 결정인지 표시
    w_util = round(trip.weight_utilization, 1)
    l_util = round(trip.length_utilization, 1)
    binding = "중량↑" if w_util >= l_util else "길이↑"
    trip_rows.append(
        {
            "회차": trip.trip_no,
            "차량": trip.truck.name,
            "종류": "모듈" if trip.kind == "module" else "패널",
            "수량": len(trip.items),
            "적재배치": extra,
            "아이템": item_names,
            "화물중량(kg)": int(trip.cargo_weight),
            "총중량(kg)": int(trip.total_weight),
            "중량 적재율(%)": w_util,
            "길이 적재율(%)": l_util,
            "결정인자": binding,
            "광폭검토": "✓" if trip.wide_check else "",
        }
    )
st.dataframe(pd.DataFrame(trip_rows), use_container_width=True, hide_index=True)


# 적재율 막대그래프 — 중량 / 길이 두 축 비교
if result.trips:
    util_rows = []
    for t in result.trips:
        # x축 라벨에 종류 포함 → 회차마다 항상 "중량 / 길이" 2개 막대가 같은 위치에 옴
        x_label = f"{t.trip_no}회({('모듈' if t.kind == 'module' else '패널')})"
        util_rows.append(
            {"회차": x_label, "적재율(%)": round(t.weight_utilization, 1), "구분": "중량"}
        )
        util_rows.append(
            {"회차": x_label, "적재율(%)": round(t.length_utilization, 1), "구분": "길이"}
        )
    df_util = pd.DataFrame(util_rows)
    fig_util = px.bar(
        df_util,
        x="회차",
        y="적재율(%)",
        color="구분",
        barmode="group",
        title="회차별 적재율 — 중량 vs 길이",
        text="적재율(%)",
        color_discrete_map={
            "중량": "#4C72B0",
            "길이": "#55A868",
        },
    )
    fig_util.update_traces(texttemplate="%{text:.1f}%", textposition="outside")
    fig_util.update_layout(yaxis_range=[0, 115])
    st.plotly_chart(fig_util, use_container_width=True, key="util_chart")


# ---------------------------------------------------------------------------
# 적재 시각화 — Top View + Rear View
# ---------------------------------------------------------------------------

st.markdown("### 🎨 트럭 적재 시각화")
st.caption(
    f"패널 사이 Gap **{int(spacing.panel_gap_mm)}mm**, "
    f"양끝 여유 **{int(spacing.truck_edge_clearance_mm)}mm** 적용. "
    "벽체 패널은 플로어 패널과 같이 눕혀서 적층 운송."
)

with st.expander("🎨 색깔·박스 의미 (범례)", expanded=False):
    leg_col1, leg_col2, leg_col3 = st.columns(3)
    with leg_col1:
        st.markdown(
            "**검은 굵은 선** → 트럭 적재함 외곽\n\n"
            "**주황 점선 박스** → 양끝 결박 공간 (벨트·후크 위치)"
        )
    with leg_col2:
        st.markdown(
            "**노랑·연파랑 등 연한 톤** → Top View 자리 구분 (1단 안의 자리)\n\n"
            "**짙은 파랑·빨강 등** → Rear/3D View 적층 단 구분"
        )
    with leg_col3:
        st.markdown(
            "**짙은 톤 (네이비·빨강)** → Rear View 적층 단 구분\n\n"
            "**파란색 큰 박스** → 모듈 1매"
        )

trip_options = [
    f"회차 {t.trip_no} ({t.truck.name}, {int(t.total_weight)}kg)"
    for t in result.trips
]

if trip_options:
    sel_idx = st.selectbox(
        "회차 선택",
        options=range(len(trip_options)),
        format_func=lambda i: trip_options[i],
    )
    sel_trip = result.trips[sel_idx]
    truck = sel_trip.truck

    # 트럭 사양 정보 박스
    spec_col1, spec_col2, spec_col3, spec_col4, spec_col5 = st.columns(5)
    spec_col1.metric("현재 트럭", truck.name.split(" (")[0])
    spec_col2.metric("길이 L", f"{int(truck.max_length):,} mm")
    spec_col3.metric("폭 W", f"{int(truck.max_width):,} mm")
    spec_col4.metric("높이 H", f"{int(truck.max_height):,} mm")
    spec_col5.metric("적재 한도", f"{int(truck.max_weight):,} kg")

    # 회차 종류 안내
    if sel_trip.kind == "module":
        st.info(
            "🚛 **모듈 회차** — 저상/광폭 트레일러에 모듈 N개 1열 적재. "
            "Top View는 길이방향 배치, Rear View는 폭방향 1매 + 모듈 높이."
        )
    elif sel_trip.items and sel_trip.items[0].kind == "wall":
        st.info(
            "🟧 **벽체 패널 회차** — 패널을 **눕혀서 적층**. "
            "플로어 패널과 동일하게 저상/광폭 트레일러에 길이 방향으로 나란히, 위로 N단 쌓아서 운송."
        )
    elif sel_trip.items and sel_trip.items[0].kind == "lshape":
        st.info(
            "🔲 **L자 패널 회차** — 바닥+벽 ㄴ자 패널을 **눕혀서 길이 방향 나란히**. "
            "벽 부분이 위로 솟아 적층 불가. Top View = 평면 배치, Rear View = ㄴ자 단면."
        )
    else:
        st.info(
            "⬜ **플로어 패널 회차** — 패널을 눕혀서 적층. "
            "Top View = 1단의 평면(자리1·자리2), Rear View = 위로 N단 적층된 단면."
        )

    tab_2d, tab_3d = st.tabs(["📐 2D 도식 (Top + Rear)", "🎲 3D 미리보기"])

    with tab_2d:
        view_col1, view_col2 = st.columns(2)
        with view_col1:
            st.markdown("**📐 Top View (평면도)**")
            st.plotly_chart(
                draw_top_view(sel_trip, truck, spacing),
                width="stretch",
                key=f"auto_top_{sel_trip.trip_no}",
            )
        with view_col2:
            st.markdown("**📐 Rear View (뒷면도)**")
            st.plotly_chart(
                draw_rear_view(sel_trip, truck, spacing),
                width="stretch",
                key=f"auto_rear_{sel_trip.trip_no}",
            )

    with tab_3d:
        st.caption(
            "마우스로 **드래그=회전 / 휠=확대축소 / 우클릭+드래그=이동**. "
            "박스 위에 마우스를 올리면 아이템 이름·중량이 표시됩니다."
        )
        st.plotly_chart(
            draw_3d_view(sel_trip, truck, spacing),
            width="stretch",
            key=f"auto_3d_{sel_trip.trip_no}",
        )



# Footer
st.divider()
st.caption(
    f"입력 카탈로그: 차량 {len(trucks)}종, 도로 {len(roads)}등급. "
    "기본 데이터 `data/*.json` — 메모장으로 수정 가능. "
    "참고 자료: `references/` 폴더 (LH §2.7.1 / PCI MNL-122 / KOSHA / 한국특장차)."
)
