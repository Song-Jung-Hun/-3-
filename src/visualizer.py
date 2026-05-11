"""트럭 적재 시각화 — Top View + Rear View, 화물 종류별 분기.

화물 종류별 적재 자세:
  - 모듈: 길이방향 N매 1열 (Top), 폭방향 1매 + 모듈 높이 (Rear)
  - 플로어 패널: 1단 평면 N매 + 위로 적층 (Top = 1단, Rear = N단 적층)
  - 벽체 패널: 폭방향 두께 N매 줄짓기 (세움 자세, A-frame trailer)
  - L자 패널: 길이방향 N매 나란히 (눕혀서, 벽 부분이 위로 솟음)

FFD 혼적 대응:
  - 모듈/L자/벽체 패널: 서로 다른 사양의 아이템도 같은 트럭에 혼적 가능
  - 각 아이템의 실제 dimensions를 개별적으로 사용해 그림
"""
from __future__ import annotations

import plotly.express as px
import plotly.graph_objects as go

from .models import Module, Panel, SpacingParams, Truck
from .packer import Trip


SEAT_PALETTE = ["#f4d35e", "#a8dadc", "#bde0fe", "#cdb4db", "#ffafcc", "#fdc4b6"]
LAYER_PALETTE = ["#1d3557", "#e63946", "#2a9d8f", "#9d0208", "#003049", "#7209b7"]
MODULE_COLOR = "#1f77b4"
PALETTE_3D = [
    "#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd",
    "#8c564b", "#e377c2", "#7f7f7f", "#bcbd22", "#17becf",
]


# ---------------------------------------------------------------------------
# 트럭 외곽 그리기 (공통)
# ---------------------------------------------------------------------------

def _truck_outline_top(fig: go.Figure, truck: Truck) -> None:
    fig.add_shape(
        type="rect",
        x0=0, y0=0,
        x1=truck.max_length, y1=truck.max_width,
        line=dict(color="black", width=3),
        fillcolor="rgba(220,220,220,0.3)",
    )
    # 길이 치수 화살표 (적재함 아래)
    arrow_y = -200
    fig.add_annotation(
        x=truck.max_length, y=arrow_y, ax=0, ay=arrow_y,
        xref="x", yref="y", axref="x", ayref="y",
        showarrow=True, arrowhead=3, arrowsize=1.5, arrowwidth=2,
        arrowcolor="#0066cc",
    )
    fig.add_annotation(
        x=0, y=arrow_y, ax=truck.max_length, ay=arrow_y,
        xref="x", yref="y", axref="x", ayref="y",
        showarrow=True, arrowhead=3, arrowsize=1.5, arrowwidth=2,
        arrowcolor="#0066cc",
    )
    fig.add_annotation(
        x=truck.max_length / 2, y=arrow_y - 100,
        text=f"<b>길이 L = {int(truck.max_length):,} mm</b>",
        showarrow=False,
        font=dict(size=11, color="#0066cc"),
    )
    # 폭 치수 화살표 (적재함 오른쪽)
    arrow_x = truck.max_length + 250
    fig.add_annotation(
        x=arrow_x, y=truck.max_width, ax=arrow_x, ay=0,
        xref="x", yref="y", axref="x", ayref="y",
        showarrow=True, arrowhead=3, arrowsize=1.5, arrowwidth=2,
        arrowcolor="#cc6600",
    )
    fig.add_annotation(
        x=arrow_x, y=0, ax=arrow_x, ay=truck.max_width,
        xref="x", yref="y", axref="x", ayref="y",
        showarrow=True, arrowhead=3, arrowsize=1.5, arrowwidth=2,
        arrowcolor="#cc6600",
    )
    fig.add_annotation(
        x=arrow_x + 200, y=truck.max_width / 2,
        text=f"<b>폭 W</b><br>{int(truck.max_width):,}<br>mm",
        showarrow=False,
        font=dict(size=10, color="#cc6600"),
    )


def _truck_outline_rear(fig: go.Figure, truck: Truck) -> None:
    veh_h = truck.vehicle_height_offset
    # 차체
    fig.add_shape(
        type="rect",
        x0=0, y0=0,
        x1=truck.max_width, y1=veh_h,
        line=dict(color="dimgray", width=2),
        fillcolor="rgba(105,105,105,0.6)",
    )
    fig.add_annotation(
        x=truck.max_width / 2, y=veh_h / 2,
        text=f"<b>{truck.name}</b> 차체 ({int(veh_h)}mm)",
        showarrow=False, font=dict(size=10, color="white"),
    )
    # 적재함 한도
    fig.add_shape(
        type="rect",
        x0=0, y0=veh_h,
        x1=truck.max_width, y1=truck.max_height,
        line=dict(color="black", width=2, dash="dot"),
        fillcolor="rgba(220,220,220,0.2)",
    )
    # 폭 치수 화살표 (아래)
    arrow_y = -250
    fig.add_annotation(
        x=truck.max_width, y=arrow_y, ax=0, ay=arrow_y,
        xref="x", yref="y", axref="x", ayref="y",
        showarrow=True, arrowhead=3, arrowsize=1.5, arrowwidth=2,
        arrowcolor="#cc6600",
    )
    fig.add_annotation(
        x=0, y=arrow_y, ax=truck.max_width, ay=arrow_y,
        xref="x", yref="y", axref="x", ayref="y",
        showarrow=True, arrowhead=3, arrowsize=1.5, arrowwidth=2,
        arrowcolor="#cc6600",
    )
    fig.add_annotation(
        x=truck.max_width / 2, y=arrow_y - 200,
        text=f"<b>폭 W = {int(truck.max_width):,} mm</b>",
        showarrow=False, font=dict(size=11, color="#cc6600"),
    )
    # 높이 치수 화살표 (오른쪽)
    arrow_x = truck.max_width + 400
    fig.add_annotation(
        x=arrow_x, y=truck.max_height, ax=arrow_x, ay=0,
        xref="x", yref="y", axref="x", ayref="y",
        showarrow=True, arrowhead=3, arrowsize=1.5, arrowwidth=2,
        arrowcolor="#009933",
    )
    fig.add_annotation(
        x=arrow_x, y=0, ax=arrow_x, ay=truck.max_height,
        xref="x", yref="y", axref="x", ayref="y",
        showarrow=True, arrowhead=3, arrowsize=1.5, arrowwidth=2,
        arrowcolor="#009933",
    )
    fig.add_annotation(
        x=arrow_x + 350, y=truck.max_height / 2,
        text=f"<b>높이 H</b><br>{int(truck.max_height):,}<br>mm<br>(한도)",
        showarrow=False, font=dict(size=10, color="#009933"),
    )


def _edge_zones_top(fig: go.Figure, truck: Truck, edge: float) -> None:
    """Top View 양끝 결박 공간 (주황 점선 박스)."""
    if edge <= 0:
        return
    for x0, x1 in [(0, edge), (truck.max_length - edge, truck.max_length)]:
        fig.add_shape(
            type="rect",
            x0=x0, y0=0, x1=x1, y1=truck.max_width,
            line=dict(color="orange", width=1, dash="dash"),
            fillcolor="rgba(255,165,0,0.15)",
        )
    fig.add_annotation(
        x=edge / 2, y=truck.max_width / 2,
        text=f"결박<br>{int(edge)}mm", showarrow=False,
        font=dict(size=9, color="orange"),
    )
    fig.add_annotation(
        x=truck.max_length - edge / 2, y=truck.max_width / 2,
        text=f"결박<br>{int(edge)}mm", showarrow=False,
        font=dict(size=9, color="orange"),
    )


# ---------------------------------------------------------------------------
# Top View — 종류별
# ---------------------------------------------------------------------------

def draw_top_view(trip: Trip, truck: Truck, sp: SpacingParams) -> go.Figure:
    fig = go.Figure()
    _truck_outline_top(fig, truck)
    edge = sp.truck_edge_clearance_mm
    gap = sp.panel_gap_mm

    if trip.kind == "module":
        # 모듈 N개 길이방향 1열
        _edge_zones_top(fig, truck, edge)
        cursor = edge
        for k, item in enumerate(trip.items):
            cy = (truck.max_width - item.width) / 2
            fig.add_shape(
                type="rect",
                x0=cursor, y0=cy,
                x1=cursor + item.length, y1=cy + item.width,
                line=dict(color=MODULE_COLOR, width=2),
                fillcolor=MODULE_COLOR, opacity=0.5,
            )
            fig.add_annotation(
                x=cursor + item.length / 2, y=cy + item.width / 2,
                text=f"<b>{item.name}</b><br>{int(item.weight)}kg",
                showarrow=False, font=dict(size=11),
            )
            cursor += item.length + gap

    elif trip.items and isinstance(trip.items[0], Panel) and trip.items[0].kind == "wall":
        # 벽체 패널 — 눕혀서 적층 (플로어 패널과 동일 방식)
        _edge_zones_top(fig, truck, edge)
        sample = trip.items[0]
        ppr = trip.panels_per_row
        n_to_draw = min(ppr, len(trip.items))
        cursor = edge
        cy = (truck.max_width - sample.width) / 2
        for k in range(n_to_draw):
            fig.add_shape(
                type="rect",
                x0=cursor, y0=cy,
                x1=cursor + sample.length, y1=cy + sample.width,
                line=dict(color="#8B0000", width=2),
                fillcolor=SEAT_PALETTE[k % len(SEAT_PALETTE)],
                opacity=0.7,
            )
            fig.add_annotation(
                x=cursor + sample.length / 2, y=cy + sample.width / 2,
                text=f"<b>1단 · 자리 {k + 1}</b><br>(길이 방향)",
                showarrow=False, font=dict(size=10),
            )
            if k < n_to_draw - 1 and gap > 0:
                fig.add_shape(
                    type="rect",
                    x0=cursor + sample.length, y0=cy,
                    x1=cursor + sample.length + gap, y1=cy + sample.width,
                    line=dict(color="brown", width=1),
                    fillcolor="rgba(139,69,19,0.4)",
                )
            cursor += sample.length + gap
        if trip.n_layers > 1:
            fig.add_annotation(
                x=truck.max_length / 2, y=truck.max_width + 250,
                text=(
                    f"※ 이 평면(1단)이 위로 <b>{trip.n_layers}단</b> 동일하게 적층됨 "
                    f"→ Rear View 참조"
                ),
                showarrow=False, font=dict(size=11, color="darkred"),
            )

    elif trip.items and isinstance(trip.items[0], Panel) and trip.items[0].kind == "lshape":
        # L자 패널 — 눕혀서 길이 방향 나란히 (벽 부분이 위로 솟음)
        _edge_zones_top(fig, truck, edge)
        cursor = edge
        n = len(trip.items)
        for k, item in enumerate(trip.items):
            cy = (truck.max_width - item.width) / 2
            # 바닥 부분 (ㄴ자 가로 bar)
            fig.add_shape(
                type="rect",
                x0=cursor, y0=cy,
                x1=cursor + item.length, y1=cy + item.width,
                line=dict(color="#8B4513", width=2),
                fillcolor=SEAT_PALETTE[k % len(SEAT_PALETTE)],
                opacity=0.7,
            )
            fig.add_annotation(
                x=cursor + item.length / 2, y=cy + item.width / 2,
                text=(
                    f"<b>{item.name}</b><br>"
                    f"{int(item.length)}×{int(item.width)}mm<br>"
                    f"벽↑{int(item.wall_height)}mm"
                ),
                showarrow=False, font=dict(size=9),
            )
            cursor += item.length + gap
        fig.add_annotation(
            x=truck.max_length / 2, y=truck.max_width + 250,
            text=(
                f"<b>L자 패널 {n}매</b> — 눕혀서 나란히 적재<br>"
                f"※ 벽 부분이 위로 솟음 → Rear View 참조"
            ),
            showarrow=False, font=dict(size=11, color="darkred"),
        )

    else:
        # 플로어 패널 — 1단 평면 (자리1, 자리2)
        _edge_zones_top(fig, truck, edge)
        sample = trip.items[0]
        ppr = trip.panels_per_row
        n_to_draw = min(ppr, len(trip.items))
        cursor = edge
        cy = (truck.max_width - sample.width) / 2
        for k in range(n_to_draw):
            fig.add_shape(
                type="rect",
                x0=cursor, y0=cy,
                x1=cursor + sample.length, y1=cy + sample.width,
                line=dict(color="#666", width=2),
                fillcolor=SEAT_PALETTE[k % len(SEAT_PALETTE)],
                opacity=0.7,
            )
            fig.add_annotation(
                x=cursor + sample.length / 2, y=cy + sample.width / 2,
                text=f"<b>1단 · 자리 {k + 1}</b><br>(길이 방향)",
                showarrow=False, font=dict(size=10),
            )
            if k < n_to_draw - 1 and gap > 0:
                fig.add_shape(
                    type="rect",
                    x0=cursor + sample.length, y0=cy,
                    x1=cursor + sample.length + gap, y1=cy + sample.width,
                    line=dict(color="brown", width=1),
                    fillcolor="rgba(139,69,19,0.4)",
                )
            cursor += sample.length + gap
        if trip.n_layers > 1:
            fig.add_annotation(
                x=truck.max_length / 2, y=truck.max_width + 250,
                text=(
                    f"※ 이 평면(1단)이 위로 <b>{trip.n_layers}단</b> 동일하게 적층됨 "
                    f"→ Rear View 참조"
                ),
                showarrow=False, font=dict(size=11, color="darkred"),
            )

    fig.update_layout(
        xaxis=dict(title="길이 (mm)", range=[-500, truck.max_length + 1200]),
        yaxis=dict(
            title="폭 (mm)",
            range=[-700, truck.max_width + 700],
            scaleanchor="x", scaleratio=1,
        ),
        height=450, showlegend=False,
        margin=dict(l=20, r=20, t=20, b=20),
    )
    return fig


# ---------------------------------------------------------------------------
# Rear View — 종류별
# ---------------------------------------------------------------------------

def draw_rear_view(trip: Trip, truck: Truck, sp: SpacingParams) -> go.Figure:
    fig = go.Figure()
    _truck_outline_rear(fig, truck)
    veh_h = truck.vehicle_height_offset
    gap = sp.panel_gap_mm   # 층간 간격도 동일 gap 사용 (받침목 없음)
    edge = sp.truck_edge_clearance_mm

    if trip.kind == "module":
        # 모듈 N매 길이 방향 1열 — 가장 큰 모듈 기준으로 단면 도식
        tallest = max(trip.items, key=lambda i: i.height)
        cx = (truck.max_width - tallest.width) / 2
        fig.add_shape(
            type="rect",
            x0=cx, y0=veh_h,
            x1=cx + tallest.width, y1=veh_h + tallest.height,
            line=dict(color=MODULE_COLOR, width=2),
            fillcolor=MODULE_COLOR, opacity=0.5,
        )
        # 모듈이 여러 사양 혼적인지 확인
        names = list(dict.fromkeys(i.name for i in trip.items))  # 순서 보존 deduplicate
        name_str = " + ".join(names) if len(names) > 1 else names[0]
        fig.add_annotation(
            x=cx + tallest.width / 2, y=veh_h + tallest.height / 2,
            text=(
                f"<b>{name_str}</b><br>"
                f"폭 {int(tallest.width)} × 높이 {int(tallest.height)}mm<br>"
                f"※ {len(trip.items)}매가 길이 방향 1열로 적재"
            ),
            showarrow=False, font=dict(size=10),
        )

    elif trip.items and isinstance(trip.items[0], Panel) and trip.items[0].kind == "wall":
        # 벽체 패널 — 눕혀서 적층 (플로어 패널과 동일 방식)
        sample = trip.items[0]
        ppr = trip.panels_per_row
        n_total = len(trip.items)
        used_layers = (n_total + ppr - 1) // ppr
        cx = (truck.max_width - sample.width) / 2
        cursor_y = veh_h
        for layer in range(used_layers):
            in_layer = min(ppr, n_total - layer * ppr)
            pos = ""
            if used_layers > 1:
                pos = " (아래)" if layer == 0 else (" (위)" if layer == used_layers - 1 else "")
            fig.add_shape(
                type="rect",
                x0=cx, y0=cursor_y,
                x1=cx + sample.width,
                y1=cursor_y + sample.thickness,
                line=dict(color="#8B0000", width=2),
                fillcolor=LAYER_PALETTE[layer % len(LAYER_PALETTE)],
                opacity=0.75,
            )
            fig.add_annotation(
                x=cx + sample.width / 2,
                y=cursor_y + sample.thickness / 2,
                text=f"<b>{layer + 1}단{pos}</b> · {in_layer}매",
                showarrow=False, font=dict(size=9, color="white"),
            )
            cursor_y += sample.thickness + gap   # 층간 gap (받침목 없음)
        total_h = used_layers * sample.thickness + (used_layers - 1) * gap
        fig.add_annotation(
            x=truck.max_width / 2, y=truck.max_height + 250,
            text=(
                f"<b>벽체 패널 총 {used_layers}단 적층</b> "
                f"(높이 {int(total_h)}mm) | "
                f"각 단 {ppr}매 → 총 {n_total}매"
            ),
            showarrow=False, font=dict(size=10, color="darkred"),
        )

    elif trip.items and isinstance(trip.items[0], Panel) and trip.items[0].kind == "lshape":
        # L자 패널 단면 (ㄴ자) — 첫 번째 패널 기준 대표 단면 도식
        WALL_THICK_VIS = 150  # 벽 두께 시각화용 추정값 (mm)
        sample = trip.items[0]
        cx = (truck.max_width - sample.width) / 2
        # 바닥 부분 (가로 bar)
        fig.add_shape(
            type="rect",
            x0=cx, y0=veh_h,
            x1=cx + sample.width, y1=veh_h + sample.thickness,
            line=dict(color="#8B4513", width=2),
            fillcolor="#DEB887",
            opacity=0.85,
        )
        # 벽 부분 (세로 bar, 왼쪽 끝에서 위로 솟음)
        fig.add_shape(
            type="rect",
            x0=cx, y0=veh_h + sample.thickness,
            x1=cx + WALL_THICK_VIS, y1=veh_h + sample.thickness + sample.wall_height,
            line=dict(color="#8B4513", width=2),
            fillcolor="#A0522D",
            opacity=0.85,
        )
        total_h = sample.thickness + sample.wall_height
        fig.add_annotation(
            x=truck.max_width / 2, y=veh_h + total_h + 350,
            text=(
                f"<b>L자 단면 (ㄴ자) — {len(trip.items)}매</b><br>"
                f"바닥부: 폭 {int(sample.width)}mm × 두께 {int(sample.thickness)}mm<br>"
                f"벽부: 높이 {int(sample.wall_height)}mm (위로 솟음)<br>"
                f"총 운송 높이: {int(total_h)}mm"
            ),
            showarrow=False, font=dict(size=10, color="darkred"),
        )

    else:
        # 플로어 패널 — 폭방향 1매 + 위로 N단 적층
        sample = trip.items[0]
        ppr = trip.panels_per_row
        n_total = len(trip.items)
        used_layers = (n_total + ppr - 1) // ppr
        cx = (truck.max_width - sample.width) / 2
        cursor_y = veh_h
        for layer in range(used_layers):
            in_layer = min(ppr, n_total - layer * ppr)
            pos = ""
            if used_layers > 1:
                if layer == 0:
                    pos = " (가장 아래)"
                elif layer == used_layers - 1:
                    pos = " (가장 위)"
            fig.add_shape(
                type="rect",
                x0=cx, y0=cursor_y,
                x1=cx + sample.width,
                y1=cursor_y + sample.thickness,
                line=dict(color="black", width=2),
                fillcolor=LAYER_PALETTE[layer % len(LAYER_PALETTE)],
                opacity=0.75,
            )
            fig.add_annotation(
                x=cx + sample.width / 2,
                y=cursor_y + sample.thickness / 2,
                text=f"<b>{layer + 1}단{pos}</b> · {in_layer}매",
                showarrow=False, font=dict(size=9, color="white"),
            )
            cursor_y += sample.thickness + gap   # 층간 gap (받침목 없음)
        total_h = used_layers * sample.thickness + (used_layers - 1) * gap
        fig.add_annotation(
            x=truck.max_width / 2, y=truck.max_height + 250,
            text=(
                f"<b>총 {used_layers}단 적층</b> "
                f"(높이 {int(total_h)}mm) | "
                f"각 단 {ppr}매 → 총 {n_total}매"
            ),
            showarrow=False, font=dict(size=10, color="darkred"),
        )

    fig.update_layout(
        xaxis=dict(title="폭 (mm)", range=[-300, truck.max_width + 1200]),
        yaxis=dict(
            title="높이 (mm)",
            range=[-700, truck.max_height + 500],
            scaleanchor="x", scaleratio=1,
        ),
        height=550, showlegend=False,
        margin=dict(l=20, r=20, t=20, b=20),
    )
    return fig


# ---------------------------------------------------------------------------
# 3D View — Mesh3d로 입체 적재 도식
# ---------------------------------------------------------------------------

def _box_mesh(
    x0: float, y0: float, z0: float,
    x1: float, y1: float, z1: float,
    color: str, opacity: float, name: str = "",
    show_edges: bool = True,
) -> list[go.Mesh3d | go.Scatter3d]:
    """8 꼭짓점 직육면체 + 외곽선. (Mesh, edges) 리스트 반환."""
    mesh = go.Mesh3d(
        x=[x0, x1, x1, x0, x0, x1, x1, x0],
        y=[y0, y0, y1, y1, y0, y0, y1, y1],
        z=[z0, z0, z0, z0, z1, z1, z1, z1],
        i=[7, 0, 0, 0, 4, 4, 6, 6, 4, 0, 3, 2],
        j=[3, 4, 1, 2, 5, 6, 5, 2, 0, 1, 6, 3],
        k=[0, 7, 2, 3, 6, 7, 1, 1, 5, 5, 7, 6],
        color=color,
        opacity=opacity,
        flatshading=True,
        showlegend=False,
        hovertext=name,
        hoverinfo="text" if name else "skip",
    )
    traces: list = [mesh]
    if show_edges:
        # 12 모서리
        ex, ey, ez = [], [], []
        edges = [
            ((x0, y0, z0), (x1, y0, z0)),
            ((x1, y0, z0), (x1, y1, z0)),
            ((x1, y1, z0), (x0, y1, z0)),
            ((x0, y1, z0), (x0, y0, z0)),
            ((x0, y0, z1), (x1, y0, z1)),
            ((x1, y0, z1), (x1, y1, z1)),
            ((x1, y1, z1), (x0, y1, z1)),
            ((x0, y1, z1), (x0, y0, z1)),
            ((x0, y0, z0), (x0, y0, z1)),
            ((x1, y0, z0), (x1, y0, z1)),
            ((x1, y1, z0), (x1, y1, z1)),
            ((x0, y1, z0), (x0, y1, z1)),
        ]
        for (p0, p1) in edges:
            ex += [p0[0], p1[0], None]
            ey += [p0[1], p1[1], None]
            ez += [p0[2], p1[2], None]
        traces.append(go.Scatter3d(
            x=ex, y=ey, z=ez,
            mode="lines",
            line=dict(color="rgba(0,0,0,0.6)", width=2),
            showlegend=False,
            hoverinfo="skip",
        ))
    return traces


def _truck_outline_3d(fig: go.Figure, truck: Truck) -> None:
    """트럭 차체(채움) + 적재함 외곽(점선) 3D."""
    veh_h = truck.vehicle_height_offset
    # 차체
    for tr in _box_mesh(
        0, 0, 0,
        truck.max_length, truck.max_width, veh_h,
        "#666", 0.5, f"{truck.name} 차체",
    ):
        fig.add_trace(tr)
    # 적재함 한도 (점선 외곽만)
    ex, ey, ez = [], [], []
    z0, z1 = veh_h, truck.max_height
    L, W = truck.max_length, truck.max_width
    # 위 사각 + 수직 모서리만 (아래는 차체 윗면과 겹침)
    edges = [
        ((0, 0, z1), (L, 0, z1)),
        ((L, 0, z1), (L, W, z1)),
        ((L, W, z1), (0, W, z1)),
        ((0, W, z1), (0, 0, z1)),
        ((0, 0, z0), (0, 0, z1)),
        ((L, 0, z0), (L, 0, z1)),
        ((L, W, z0), (L, W, z1)),
        ((0, W, z0), (0, W, z1)),
    ]
    for p0, p1 in edges:
        ex += [p0[0], p1[0], None]
        ey += [p0[1], p1[1], None]
        ez += [p0[2], p1[2], None]
    fig.add_trace(go.Scatter3d(
        x=ex, y=ey, z=ez,
        mode="lines",
        line=dict(color="black", dash="dash", width=3),
        showlegend=False,
        hoverinfo="skip",
        name="적재 한도",
    ))


def draw_3d_view(trip: Trip, truck: Truck, sp: SpacingParams) -> go.Figure:
    """3D 미리보기 — 트럭 + 화물 입체 도식."""
    fig = go.Figure()
    _truck_outline_3d(fig, truck)
    veh_h = truck.vehicle_height_offset
    edge = sp.truck_edge_clearance_mm
    gap = sp.panel_gap_mm

    if not trip.items:
        pass  # 트럭만

    elif trip.kind == "module":
        # 모듈 N매 길이 방향 1열, 폭 가운데
        cursor = edge
        for k, item in enumerate(trip.items):
            cy = (truck.max_width - item.width) / 2
            color = PALETTE_3D[k % len(PALETTE_3D)]
            for tr in _box_mesh(
                cursor, cy, veh_h,
                cursor + item.length, cy + item.width, veh_h + item.height,
                color, 0.65,
                f"{item.name} ({int(item.weight)}kg)",
            ):
                fig.add_trace(tr)
            cursor += item.length + gap

    elif isinstance(trip.items[0], Panel) and trip.items[0].kind == "wall":
        # 벽체 패널 — 눕혀서 적층 (플로어 패널과 동일 방식)
        sample = trip.items[0]
        ppr = trip.panels_per_row
        n_total = len(trip.items)
        used_layers = (n_total + ppr - 1) // ppr if ppr > 0 else 1
        cy = (truck.max_width - sample.width) / 2
        cursor_z = veh_h
        for layer in range(used_layers):
            in_layer = min(ppr, n_total - layer * ppr)
            cursor_x = edge
            color = PALETTE_3D[layer % len(PALETTE_3D)]
            for k in range(in_layer):
                idx = layer * ppr + k
                item = trip.items[idx]
                for tr in _box_mesh(
                    cursor_x, cy, cursor_z,
                    cursor_x + sample.length,
                    cy + sample.width,
                    cursor_z + sample.thickness,
                    color, 0.7,
                    f"{item.name} ({layer + 1}단 자리{k + 1})",
                ):
                    fig.add_trace(tr)
                cursor_x += sample.length + gap
            cursor_z += sample.thickness + gap   # 층간 gap (받침목 없음)

    elif isinstance(trip.items[0], Panel) and trip.items[0].kind == "lshape":
        # L자 패널 — 눕혀서 나란히, 바닥 박스 + 벽 박스
        WALL_THICK_VIS = 150  # 벽 두께 시각화용 추정값 (mm)
        cursor = edge
        for k, item in enumerate(trip.items):
            cy = (truck.max_width - item.width) / 2
            color = PALETTE_3D[k % len(PALETTE_3D)]
            # 바닥 박스 (눕혀진 부분)
            for tr in _box_mesh(
                cursor, cy, veh_h,
                cursor + item.length, cy + item.width, veh_h + item.thickness,
                color, 0.70,
                f"{item.name} 바닥부 ({int(item.weight)}kg)",
            ):
                fig.add_trace(tr)
            # 벽 박스 (한쪽 끝에서 위로 솟음)
            for tr in _box_mesh(
                cursor, cy, veh_h + item.thickness,
                cursor + item.length, cy + WALL_THICK_VIS,
                veh_h + item.thickness + item.wall_height,
                "#A0522D", 0.65,
                f"{item.name} 벽부 (높이 {int(item.wall_height)}mm)",
            ):
                fig.add_trace(tr)
            cursor += item.length + gap

    else:
        # 플로어 패널 — 1단 ppr매 × n_layers 단 적층
        sample = trip.items[0]
        ppr = trip.panels_per_row
        n_total = len(trip.items)
        used_layers = (n_total + ppr - 1) // ppr if ppr > 0 else 1
        cy = (truck.max_width - sample.width) / 2
        cursor_z = veh_h
        for layer in range(used_layers):
            in_layer = min(ppr, n_total - layer * ppr)
            cursor_x = edge
            color = PALETTE_3D[layer % len(PALETTE_3D)]
            for k in range(in_layer):
                idx = layer * ppr + k
                item = trip.items[idx]
                for tr in _box_mesh(
                    cursor_x, cy, cursor_z,
                    cursor_x + sample.length,
                    cy + sample.width,
                    cursor_z + sample.thickness,
                    color, 0.7,
                    f"{item.name} ({layer+1}단 자리{k+1})",
                ):
                    fig.add_trace(tr)
                cursor_x += sample.length + gap
            cursor_z += sample.thickness + gap   # 층간 gap (받침목 없음)

    fig.update_layout(
        scene=dict(
            xaxis_title="길이 (mm)",
            yaxis_title="폭 (mm)",
            zaxis_title="높이 (mm)",
            aspectmode="data",
            camera=dict(eye=dict(x=1.6, y=1.4, z=0.9)),
        ),
        height=600,
        margin=dict(l=0, r=0, t=0, b=0),
        showlegend=False,
    )
    return fig
