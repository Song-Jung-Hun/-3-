# 모듈러 운송 시뮬레이터

사용자가 모듈·플로어 패널·벽체 패널의 사양과 개수를 입력하면 트럭 운송 회차를 자동 산출.

## 빠른 시작

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
streamlit run app.py
```

## 입력

- **모듈**: 폭 / 길이 / 높이 / 중량 + 개수
- **플로어 패널**: 폭 / 길이 / 두께 / 중량 + 개수
- **벽체 패널**: 폭 / 길이 / 두께 / 중량 + 개수
- **도로 등급**: 광로 / 일반도로 / 이면도로
- **트럭 카탈로그**: 25t·15t·11t 카고 (`data/trucks.json`에서 자유 수정)

## 출력

- 모듈/패널/총 회차 수, 평균 적재율
- 회차별 적재 내역 표 (트럭, 아이템, 총중량, 적재율)
- 회차별 적재율 막대그래프
- 회차 1개 선택 → 트럭 적재 박스 도식 (Top View)
- 운송 불가 아이템과 사유 (도로·트럭 한도 초과 시)

## 폴더 구조

```
.
├── app.py                # Streamlit 진입점
├── requirements.txt
├── data\
│   ├── trucks.json           # 3종 트럭 카탈로그 (25t·15t·11t)
│   └── road_limits.json      # 도로 3 등급
├── src\
│   ├── models.py         # Module / Panel / Truck / RoadClass
│   ├── limits.py         # 트럭 적재 한도 검사기 (4 조건)
│   └── packer.py         # 1D 중량 First Fit Decreasing (회차 산정)
└── tests\
    └── test_can_carry.py
```

## 단위 규약

길이는 **mm**, 중량은 **kg**으로 통일.

## 알고리즘

- **모듈**: 1 트럭 = 1 모듈 (LH §2.7.1 표준). 적재율이 가장 높은 트럭이 자동 선택됨.
- **패널**: 무거운 순으로 정렬 → 가장 큰 트럭(25t)에 차곡차곡 채워서 한도 초과 시 다음 트럭으로 (First Fit Decreasing).
