# AI 인프라 수요 일일 트래커

매일 아침 네 가지 공개 지표를 모아 `data/history.csv`에 한 줄을 추가하고, 같은 내용의 짧은 요약을 텔레그램으로 보냅니다. 시트 반영은 이 저장소가 하지 않습니다. 다른 작업이 아침마다 CSV의 최신 행을 구글 시트에 복사합니다.

시트: https://docs.google.com/spreadsheets/d/1YV2uuLIqz7WhhuznL0yOGSBVkljz0M46WCt2vk7Wj5A/edit

## 실행 시각

GitHub Actions `AI 수요 일일 수집`이 매일 07:45 KST에 돕니다. 크론은 `45 22 * * *` (UTC)입니다. 한국은 서머타임을 쓰지 않으므로 UTC 22:45는 항상 KST 07:45입니다.

같은 워크플로는 수동 실행(`workflow_dispatch`)도 받습니다.

## 시크릿

저장소 **Settings → Secrets and variables → Actions**에 아래 이름으로 넣습니다. 텔레그램 시크릿 이름은 기존 봇과 같습니다.

| 이름 | 없으면 |
| --- | --- |
| `BOT_TOKEN` | 메시지를 보내지 않고 워크플로 로그에만 출력 |
| `CHAT_ID` | 위와 같음. 둘 중 하나만 없어도 전송하지 않음 |
| `EIA_API_KEY` | PJM만 `데이터 없음`으로 남고 나머지 수집은 계속됨. 키는 [EIA Open Data](https://www.eia.gov/opendata/register.php)에서 발급 |

워크플로 잡에는 `contents: write`가 있어서, 드라이런이 아닐 때 `data/history.csv`를 `ai-demand-bot` 이름으로 `main`에 커밋합니다. 커밋 메시지에는 `[skip ci]`가 들어 있습니다. push 전에 `git pull --rebase origin main`을 합니다. `main`이 액션의 push를 막도록 보호되어 있으면 커밋 단계가 실패합니다.

## 드라이런

메시지만 확인하고 CSV와 텔레그램은 건드리지 않습니다.

GitHub에서:

1. **Actions → AI 수요 일일 수집 → Run workflow**
2. `dry_run`을 체크
3. Run workflow
4. 잡 로그에 한글 요약이 출력됩니다. `data/history.csv`는 커밋되지 않고 텔레그램도 가지 않습니다.

로컬에서:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m ai_demand --dry-run
```

시크릿 없이 드라이런이 아닌 실행을 하면 메시지는 표준 출력으로 나오고, CSV는 기록되며, PJM은 `EIA_API_KEY`가 없어 비어 있습니다. 종료 코드는 0입니다.

```bash
python -m ai_demand
```

한 출처가 실패해도 나머지는 기록합니다. 네 출처가 모두 실패하거나 CSV를 쓰지 못할 때만 종료 코드가 1입니다. 텔레그램 전송 실패는 경고만 남기고 수집 자체는 성공으로 봅니다.

## 지표

임계값은 `ai_demand/config.py` 맨 위의 상수입니다.

### 1. AWS Spot 중단률

<https://spot-bid-advisor.s3.amazonaws.com/spot-advisor-data.json>

`spot_advisor[리전]['Linux'][인스턴스]['r']`는 중단 구간 번호입니다. `ranges`의 라벨은 0 `<5%`, 1 `5-10%`, 2 `10-15%`, 3 `15-20%`, 4 `>20%`입니다.

| 대상 | CSV 열 |
| --- | --- |
| us-east-1 `p5.48xlarge` (H100) | `spot_use1_p5` |
| us-east-1 `p4d.24xlarge` (A100) | `spot_use1_p4d` |
| us-west-2 `p5.48xlarge` (H100) | `spot_usw2_p5` |
| us-west-2 `p4d.24xlarge` (A100) | `spot_usw2_p4d` |

CSV에는 `r` 원값(0–4)을 넣고, 메시지에는 `{r+1}단계 ({라벨})`로 보여 줍니다. 직전 CSV 행의 같은 칸과 비교해 화살표를 붙입니다. `r`가 오르면 ↑(중단 증가), 내리면 ↓, 같으면 → 이고 괄호 안은 `전일 동일`입니다. 값이 달라졌으면 `전일 N단계`로 적습니다. 7일 전 날짜 또는 그 이전에서 가장 가까운 행에 값이 있으면 `7일전 N단계`를 쉼표로 이어 붙입니다. 그 칸이 비어 있으면 그 비교만 생략하고, 더 오래된 행으로 내려가지 않습니다. 둘 다 없으면 괄호 전체를 생략합니다. 백필 행의 Spot 칸은 비어 있으므로 7일 비교는 실측이 쌓인 뒤에 나타납니다. 종합 점수는 메시지 화살표가 아니라 네 개 `r` 평균이 같은 직전 행보다 올랐는지를 그대로 사용합니다.

### 2. Vast.ai 가격

공개 피드: <https://storage.googleapis.com/vast-public-gpu-pricing/gpu-pricing-public.json>

슬러그는 `H100-SXM`, `H200`, `B200`입니다. 메시지에는 H100 SXM을 H100으로 적습니다. 각 GPU의 `current.min`, `current.median`($/시간)을 기록합니다.

전일 대비는 피드의 `daily[]`가 아니라 **오늘 `current.median`과 직전 CSV 행의 median**을 비교합니다. 그래야 다음 날부터 실시간 중간값끼리 이어집니다. 백필 다음 첫 실측일은 일별 중간값과 실시간 중간값을 비교하므로 등락이 커 보일 수 있습니다. 그 행의 `notes`에는 `vast: dod vs backfill`이 붙습니다.

공개 피드가 실패하면 <https://console.vast.ai/api/v0/bundles/> 의 온디맨드 호가를 쓰고, GPU당 가격은 `dph_total / num_gpus`입니다. 이때 notes는 `vast: bundles fallback`입니다.

Data: Vast.ai

Vast.ai 공개 가격 피드는 출처를 밝히면 표시와 인용을 허용합니다. 이 저장소는 원본 피드 전체가 아니라 일일 최저·중간값만 저장합니다. 대량 수집, 재배포, 인덱스·벤치마크·파생 제품으로의 이용은 별도 데이터 라이선스(`data@vast.ai`)가 필요하다는 문구가 피드에 있습니다.

### 3. vLLM PyPI

- 전일 다운로드: <https://pypistats.org/api/packages/vllm/recent> 의 `last_day`
- 7일 평균: <https://pypistats.org/api/packages/vllm/overall> 에서 `category`가 `without_mirrors`인 일별 값의 최근 7일 평균. 그 7일은 빈틈없이 이어져 있어야 하고, 가장 최근 날을 포함합니다.

집계는 대략 하루 늦습니다. `last_day`와 overall 최신 값이 다르면 notes에 `vllm: last_day mismatch`가 붙습니다.

### 4. PJM 야간 기저부하

EIA v2 `electricity/rto/region-data`, `respondent=PJM`, `type=D`, `frequency=hourly`. `period`는 UTC 시각(`YYYY-MM-DDTHH`)이라 `America/New_York`으로 바꿉니다.

야간 기저부하는 현지 **00:00 이상 05:00 미만** 다섯 시간의 수요(MW) 평균입니다. 다섯 시간이 모두 있는 밤만 완전한 밤으로 치고, 그중 가장 최근 밤을 씁니다. 직전 비교는 그 앞의 완전한 밤이고, 7일 평균은 최근 완전한 7밤(당일 밤 포함)의 평균입니다. 봄철 서머타임으로 새벽 2시가 없으면 그 밤은 불완전으로 건너뜁니다.

키가 없거나 호출이 실패하거나 완전한 밤이 없으면 PJM 칸만 비우고 수집은 계속합니다.

## 종합 판정

데이터가 있는 신호만 더합니다. 결측 신호는 합계에 포함하지 않습니다.

| 신호 | +1 | -1 |
| --- | --- | --- |
| Spot 4개 `r`의 평균 | 평균 ≥ 3 **또는** 전일 CSV 평균보다 상승 | 평균 ≤ 1 **또는** 전일보다 하락 |
| Vast H100 median 전일 대비 | > +3% | < −3% |
| Vast H200 median 전일 대비 | > +3% | < −3% |
| vLLM `last_day` 대 7일 평균 | > +5% | < −5% |
| PJM 야간 평균 대 7밤 평균 | > +1% | < −1% |

B200은 메시지와 CSV에만 나오고 점수에는 넣지 않습니다. 경계값(정확히 +3%, +5%, +1%)은 0점입니다.

Spot은 신호 하나입니다. 위 두 조건이 동시에 서로 다른 방향이면 상쇄되어 0이고, 같은 방향이 겹쳐도 +1 또는 -1을 넘지 않습니다. 전일 Spot 값이 없으면(백필 행) 수준만 봅니다. 평균이 1과 3 사이이고 방향이 없으면 0입니다. H100은 한가하고 A100은 빡빡한 날처럼 네 값이 갈리면 평균이 가운데로 모여 0이 될 수 있습니다.

합이 2 이상이면 `Strong`, -2 이하면 `Softening`, 그 외에는 `Stable`입니다.

## CSV

`data/history.csv` 헤더는 아래 순서를 유지합니다. 빈 칸은 결측입니다. 같은 `date_kst`가 있으면 그 행을 교체합니다.

```
date_kst,spot_use1_p5,spot_use1_p4d,spot_usw2_p5,spot_usw2_p4d,vast_h100_min,vast_h100_median,vast_h200_min,vast_h200_median,vast_b200_min,vast_b200_median,vllm_last_day,vllm_7d_ma,pjm_overnight_mw,pjm_overnight_7d_ma,overall,notes
```

`date_kst`는 수집 시점의 한국 날짜입니다. 가격은 소수점 넷째 자리, 평균은 둘째 자리까지 반올림한 뒤 끝의 0을 뺍니다. `notes`는 세미콜론으로 이은 짧은 오류·정보입니다. 정상이면 비어 있습니다.

`2026-11-02`부터 `2026-11-14`는 백필입니다. Vast `daily[]` median과 pypistats `without_mirrors`의 그날 다운로드·7일 평균만 채우고 나머지 칸은 비웁니다. notes는 `backfill`입니다.

```bash
python -m ai_demand --backfill
```

백필은 그 날짜만 다시 쓰고 텔레그램을 보내지 않습니다. 일별 이력은 공개 피드에만 있으므로 피드가 실패하면 백필도 실패합니다.

## 테스트

```bash
pip install -r requirements-dev.txt
python -m pytest
```

파싱, 점수, CSV 교체, 메시지 형식을 검증합니다. 테스트는 네트워크를 호출하지 않습니다.
