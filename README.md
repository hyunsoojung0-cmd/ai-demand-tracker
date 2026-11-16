# AI 인프라 수요 일일 트래커

매일 아침 지표를 모아 `data/history.csv`에 한 줄을 추가합니다. 텔레그램에는 핵심 다섯 지표의 숫자와 해설을 보내고, 이어서 같은 다섯 지표의 차트를 앨범으로 보냅니다. vLLM과 Hugging Face는 시트용으로 CSV에만 남고 메시지·점수·차트에는 넣지 않습니다. 시트 반영은 이 저장소가 하지 않습니다. 다른 작업이 아침마다 CSV의 최신 행을 구글 시트에 복사합니다.

시트: https://docs.google.com/spreadsheets/d/1YV2uuLIqz7WhhuznL0yOGSBVkljz0M46WCt2vk7Wj5A/edit

## 실행 시각

GitHub Actions `AI 수요 일일 수집`이 매일 07:45 KST에 돕니다. 크론은 `45 22 * * *` (UTC)입니다. 한국은 서머타임을 쓰지 않으므로 UTC 22:45는 항상 KST 07:45입니다.

GitHub가 그 시각의 실행을 늦추면 같은 워크플로가 09:15 KST(`15 0 * * *` UTC)에 한 번 더 돕니다. 이 보조 실행만, 방금 `main`에서 받은 `data/history.csv`에 오늘(KST) `date_kst` 행이 있는지 먼저 봅니다. 행이 있으면 수집과 텔레그램을 하지 않고 로그에 `already collected, skipping`을 남깁니다. 07:45 크론과 수동 실행은 그 행이 있어도 항상 수집합니다.

두 크론이 겹치면 `ai-demand-daily` 동시성 그룹에서 나중 실행이 앞 실행이 끝날 때까지 기다립니다. 진행 중인 실행은 취소하지 않습니다. 늦어진 07:45 실행이 행을 `main`에 올린 뒤, 09:15 실행이 그 행을 보고 건너뜁니다.

같은 워크플로는 수동 실행(`workflow_dispatch`)도 받습니다. 수동 실행은 `--fallback`을 붙이지 않습니다.

## 시크릿

저장소 **Settings → Secrets and variables → Actions**에 아래 이름으로 넣습니다. 텔레그램 시크릿 이름은 기존 봇과 같습니다.

| 이름 | 없으면 |
| --- | --- |
| `BOT_TOKEN` | 메시지를 보내지 않고 워크플로 로그에만 출력 |
| `CHAT_ID` | 위와 같음. 둘 중 하나만 없어도 전송하지 않음 |
| `EIA_API_KEY` | PJM만 `데이터 없음`으로 남고 나머지 수집은 계속됨. 키는 [EIA Open Data](https://www.eia.gov/opendata/register.php)에서 발급 |
| `OPENROUTER_API_KEY` | OpenRouter 토큰 사용량만 `데이터 없음 (OPENROUTER_API_KEY 없음)`. 추론에 쓰는 키와 같습니다 |
| `HF_TOKEN` | 없어도 Hugging Face 공개 API로 수집합니다. 있으면 `Authorization` 헤더로만 보냅니다 |

워크플로 잡에는 `contents: write`가 있어서, 드라이런이 아닐 때 `data/history.csv`를 `ai-demand-bot` 이름으로 `main`에 커밋합니다. 커밋 메시지에는 `[skip ci]`가 들어 있습니다. push 전에 `git pull --rebase origin main`을 합니다. `main`이 액션의 push를 막도록 보호되어 있으면 커밋 단계가 실패합니다.

## 드라이런

메시지만 확인하고 CSV와 텔레그램은 건드리지 않습니다.

GitHub에서:

1. **Actions → AI 수요 일일 수집 → Run workflow**
2. `dry_run`을 체크
3. Run workflow
4. 잡 로그에 한글 요약이 출력됩니다. `data/history.csv`는 커밋되지 않고 텔레그램도 가지 않습니다. 차트 PNG는 `daily-charts` 아티팩트로 올라갑니다.

로컬에서:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m ai_demand --dry-run
```

시크릿 없이 드라이런이 아닌 실행을 하면 메시지는 표준 출력으로 나오고, CSV는 기록되며, PJM과 OpenRouter는 키가 없어 비어 있습니다. Hugging Face는 키 없이 수집됩니다. 종료 코드는 0입니다.

```bash
python -m ai_demand
```

한 출처가 실패해도 나머지는 기록합니다. 여섯 출처가 모두 실패하거나 CSV를 쓰지 못할 때만 종료 코드가 1입니다. 텔레그램 전송 실패는 경고만 남기고 수집 자체는 성공으로 봅니다.

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

### 3. Vast.ai 대여 가능 머신

키 없는 공개 검색입니다. `GET https://console.vast.ai/api/v0/bundles/`에 `rentable=true`, `type=on-demand`, GPU 이름 `H100 SXM`·`H200`·`B200`을 넣고, 응답의 `machine_id`를 GPU별로 세어 `vast_h100_avail`, `vast_h200_avail`, `vast_b200_avail`에 정수로 저장합니다. 같은 머신의 호가 여러 개는 한 대로 칩니다. 그 GPU 호가가 없으면 0입니다. `truncated`가 참이면 잘린 숫자를 저장하지 않고 세 칸을 비웁니다. 호출이 실패해도 가격 수집은 계속됩니다. 과거 검색 결과가 없으므로 백필하지 않고, 오늘부터 쌓입니다.

메시지에는 `H100 13대`처럼 적습니다. 전일이 있으면 증감을 붙입니다. 해설은 가격이 오르고 대수가 줄면 공급이 빠듯한 수요, 가격이 내리고 대수가 늘면 공급이 풀리는 쪽으로 적습니다.

### 4. vLLM PyPI

- 전일 다운로드: <https://pypistats.org/api/packages/vllm/recent> 의 `last_day`
- 7일 평균: <https://pypistats.org/api/packages/vllm/overall> 에서 `category`가 `without_mirrors`인 일별 값의 최근 7일 평균. 그 7일은 빈틈없이 이어져 있어야 하고, 가장 최근 날을 포함합니다.

집계는 대략 하루 늦습니다. `last_day`와 overall 최신 값이 다르면 notes에 `vllm: last_day mismatch`가 붙습니다. 이 값은 CSV에만 저장하고 텔레그램과 점수에는 넣지 않습니다.

### 5. PJM 야간 기저부하

EIA v2 `electricity/rto/region-data`, `respondent=PJM`, `type=D`, `frequency=hourly`. `period`는 UTC 시각(`YYYY-MM-DDTHH`)이라 `America/New_York`으로 바꿉니다.

야간 기저부하는 현지 **00:00 이상 05:00 미만** 다섯 시간의 수요(MW) 평균입니다. 다섯 시간이 모두 있는 밤만 완전한 밤으로 치고, 그중 가장 최근 밤을 씁니다. 직전 비교는 그 앞의 완전한 밤이고, 7일 평균은 최근 완전한 7밤(당일 밤 포함)의 평균입니다. 봄철 서머타임으로 새벽 2시가 없으면 그 밤은 불완전으로 건너뜁니다.

키가 없거나 호출이 실패하거나 완전한 밤이 없으면 PJM 칸만 비우고 수집은 계속합니다.

### 6. OpenRouter 토큰

<https://openrouter.ai/api/v1/datasets/rankings-daily>

`Authorization: Bearer`에 `OPENROUTER_API_KEY`를 넣습니다. 키가 없으면 이 항목만 비웁니다. UTC 하루는 상위 50개 모델과 `other` 한 줄로 오고, `total_tokens`는 문자열입니다. 오늘(UTC)은 아직 끝나지 않았으므로 그보다 이전인 마지막 완성일을 씁니다. 전일 대비와 7일 평균은 같은 응답의 최근 약 10일에서 계산하고 CSV에 의존하지 않습니다.

`or_total_tokens`는 `other`를 포함한 합계입니다. `or_top5_share`는 상위 5개 모델 토큰을 그날 합계로 나눈 비율(0–1)입니다. `or_top1_model`은 `other`를 뺀 1위 슬러그입니다. 메시지에는 합계를 십억(B) 단위로 보여주고 데이터 날짜를 `11/14 UTC`처럼 적습니다.

Source: OpenRouter (CC BY 4.0)

데이터셋은 CC BY 4.0입니다. 재사용할 때는 OpenRouter를 출처로 밝혀야 합니다. 문서: <https://openrouter.ai/docs/api/api-reference/datasets/daily-token-totals-for-top-50-models>. 순위 원본은 <https://openrouter.ai/rankings>입니다.

### 7. Hugging Face 오픈 모델 다운로드

고정된 공식 플래그십 13개입니다. `gpt2`는 넣지 않습니다. 목록은 `ai_demand/config.py`의 `HF_BASKET`입니다. 모델마다 `https://huggingface.co/api/models/{id}?expand[]=downloads&expand[]=downloadsAllTime`를 호출합니다. `downloads`는 최근 30일, `downloadsAllTime`은 누적입니다. `HF_TOKEN`이 있으면 그 헤더만 추가합니다.

`hf_basket_30d`와 `hf_basket_alltime`은 바스켓 합입니다. 일일 다운로드는 오늘 누적에서 직전 CSV 행의 `hf_basket_alltime`을 뺀 값이고, 처음에는 비웁니다. 일부 모델이 실패하면 30일 합은 성공한 모델만 더하고 notes에 남기며, 누적 합 `hf_basket_alltime`은 비워서 일일 시계열이 부분 합과 섞이지 않게 합니다. 전부 실패하면 이 항목만 비웁니다. Hugging Face는 CSV에만 두고 텔레그램과 점수에는 넣지 않습니다.

## 차트

텍스트를 보낸 뒤 같은 다섯 지표를 PNG 앨범으로 보냅니다. 최근 90일이고, 빈 날은 선을 끊습니다. `data/history.csv`와 `data/backfill_6m.csv`를 날짜로 합치며, 일일 파일의 값이 있으면 그 값을 씁니다.

1. OpenRouter 일일 토큰과 7일 평균. 단위는 조(T)입니다.
2. Vast.ai H100·H200·B200 중간 가격. 백필 일일 중간값에서 실시간 중간값으로 이어집니다.
3. Vast.ai 대여 가능 머신. 관측이 2일 이상일 때만 그립니다.
4. AWS Spot 중단률 1–5단계. 네 줄을 계단 그래프로 그리고, 2일 이상일 때만 그립니다.
5. PJM 야간 기저부하와 7밤 평균.

각 선의 오른쪽 끝에 최신 값을 적습니다. 한글은 워크플로에서 `fonts-nanum`을 설치해 쓰고, 글꼴이 없으면 영어 라벨로 그립니다. 차트를 만들거나 보내는 데 실패해도 텍스트 메시지는 그대로 둡니다. 드라이런은 `charts/*.png`를 아티팩트 `daily-charts`로 올리고 텔레그램으로는 보내지 않습니다. 09:15 보조 실행이 이미 수집된 날을 건너뛰면 차트도 만들지 않습니다.

## 종합 판정

메시지와 같은 다섯 지표만 점수를 냅니다. 데이터가 있는 신호만 더합니다. 결측 신호는 합계에 포함하지 않습니다. vLLM과 Hugging Face는 수집만 하고 점수에 넣지 않습니다.

| 신호 | +1 | -1 |
| --- | --- | --- |
| OpenRouter 전체 토큰 대 7일 평균 | > +5% | < −5% |
| Vast 중간가. H100·H200 전일 대비의 평균. B200은 제외 | > +3% | < −3% |
| Vast 대여 가능 대수 합의 전일 대비 | 감소가 5%를 넘음 | 증가가 5%를 넘음 |
| Spot 4개 `r`의 평균 | 평균 ≥ 3 **또는** 전일 CSV 평균보다 상승 | 평균 ≤ 1 **또는** 전일보다 하락 |
| PJM 야간 평균 대 7밤 평균 | > +1% | < −1% |

대여 가능 대수는 H100·H200·B200을 더합니다. 어제와 오늘이 둘 다 있는 GPU만 합에 넣습니다. 대수가 줄면 공급이 빠듯한 쪽으로 +1, 늘면 −1입니다. 첫날처럼 전일 값이 없으면 이 신호는 빼니다. B200 가격은 메시지와 CSV에만 나오고 가격 점수에는 넣지 않습니다. 경계값(정확히 +3%, +5%, +1%)은 0점입니다.

Spot은 신호 하나입니다. 위 두 조건이 동시에 서로 다른 방향이면 상쇄되어 0이고, 같은 방향이 겹쳐도 +1 또는 -1을 넘지 않습니다. 전일 Spot 값이 없으면(백필 행) 수준만 봅니다. 평균이 1과 3 사이이고 방향이 없으면 0입니다. H100은 한가하고 A100은 빡빡한 날처럼 네 값이 갈리면 평균이 가운데로 모여 0이 될 수 있습니다.

합이 2 이상이면 `Strong`, -2 이하면 `Softening`, 그 외에는 `Stable`입니다.

종합 줄 아래에는 규칙으로 만든 짧은 해설이 붙습니다. 판단 근거는 강세·중립·약세 개수이고, 주요 변동은 전일 대비가 큰 항목입니다. Vast를 백필 일별 중간값과 비교한 날에는 `백필 일별값과 비교`를 붙이고, 그 등락은 해설의 가격 방향에서 뺀 뒤 30일 추이를 봅니다. OpenRouter 하루 변동이 15% 이상이면 `하루 변동이 큼`을 붙입니다. 추이는 `history.csv`와 `backfill_6m.csv`를 날짜로 합친 뒤(같은 칸에 값이 있으면 일일 파일이 이기고, 빈 칸은 백필을 지우지 않음) 7일 평균을 30일 전·90일 전과 비교합니다. 재료가 없는 문장은 생략합니다.

## CSV

`data/history.csv` 헤더는 아래 순서를 유지합니다. 빈 칸은 결측입니다. 같은 `date_kst`가 있으면 그 행을 교체합니다.

```
date_kst,spot_use1_p5,spot_use1_p4d,spot_usw2_p5,spot_usw2_p4d,vast_h100_min,vast_h100_median,vast_h200_min,vast_h200_median,vast_b200_min,vast_b200_median,vllm_last_day,vllm_7d_ma,pjm_overnight_mw,pjm_overnight_7d_ma,overall,notes,or_total_tokens,or_top5_share,or_top1_model,hf_basket_30d,hf_basket_alltime,hf_implied_daily,vast_h100_avail,vast_h200_avail,vast_b200_avail
```

`date_kst`는 수집 시점의 한국 날짜입니다. 가격과 `or_top5_share`는 소수점 넷째 자리, 평균은 둘째 자리까지 반올림한 뒤 끝의 0을 뺍니다. `notes`는 세미콜론으로 이은 짧은 오류·정보입니다. 정상이면 비어 있습니다. OpenRouter, Hugging Face, Vast 가용 대수 열은 헤더 끝에만 붙입니다. 예전 행은 그 칸이 비어 있습니다. `vast_*_avail`은 그날 대여 가능한 머신 수이고, 백필하지 않습니다.

`2026-11-02`부터 `2026-11-14`는 백필입니다. Vast `daily[]` median과 pypistats `without_mirrors`의 그날 다운로드·7일 평균만 채우고 나머지 칸은 비웁니다. notes는 `backfill`입니다.

```bash
python -m ai_demand --backfill
```

백필은 그 날짜만 다시 쓰고 텔레그램을 보내지 않습니다. 일별 이력은 공개 피드에만 있으므로 피드가 실패하면 백필도 실패합니다.

## 6개월 백필

Actions의 **6개월 백필** 워크플로는 수동 실행만 됩니다. `data/history.csv`는 바꾸지 않고 `data/backfill_6m.csv`만 `main`에 커밋합니다. 헤더는 일일 CSV와 같고, 모든 행의 notes는 `backfill6m`입니다. 시작일 기본값은 `2026-05-15`이고, 종료일을 비우면 어제(KST)입니다. 시크릿은 `EIA_API_KEY`와 `OPENROUTER_API_KEY`를 그대로 씁니다.

각 출처가 실제로 주는 과거만 채웁니다. 없는 값은 만들지 않습니다.

| 열 | 과거 데이터 |
| --- | --- |
| vLLM `vllm_last_day`, `vllm_7d_ma` | pypistats `/overall`의 `without_mirrors`. 2026-11-15 기준 2026-05-18부터 2026-11-14까지 181일이 빈틈없이 있습니다. 7일 평균은 그날 포함 7일이 모두 있을 때만 채웁니다 |
| Vast `vast_*_median` | 공개 피드 `daily[]` 중간값만. 최저가는 비웁니다. 2026-11-15 기준 피드는 최근 90일(2026-08-17~2026-11-14)만 보관합니다 |
| PJM `pjm_overnight_mw`, `pjm_overnight_7d_ma` | EIA 시간별 PJM 수요. 일일 수집과 같이 `length=240`, 내림차순, 같은 facet으로 최대 7일씩 요청합니다. 한 주가 시간 초과되어도 그 주만 건너뛰고 나머지는 남깁니다. `start`/`end`는 `YYYY-MM-DDTHH`입니다. 야간은 미동부 00:00–05:00 평균이고, 7밤 평균은 그날까지의 완전한 밤 7개입니다. 키가 없으면 비웁니다 |
| OpenRouter `or_total_tokens`, `or_top5_share`, `or_top1_model` | `rankings-daily`. 한 요청은 최대 366일이라 더 길면 나눠 요청합니다. 키가 없으면 비웁니다. UTC 기준 아직 끝나지 않은 오늘은 뺍니다 |
| Spot, Hugging Face, Vast 가용 대수, `overall`, Vast 최저가 | 공개 이력이 없으므로 항상 비웁니다. 가용 대수는 일일 수집이 쌓인 뒤부터 CSV에 있습니다 |

```bash
python -m ai_demand.backfill6m --start 2026-05-15
```

## 테스트

```bash
pip install -r requirements-dev.txt
python -m pytest
```

파싱, 점수, CSV 교체, 메시지 형식을 검증합니다. 테스트는 네트워크를 호출하지 않습니다.
