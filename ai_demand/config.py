"""Shared settings.

Score thresholds are the first assignments in this module. Tune the verdict
here and nowhere else.
"""

from datetime import date
from typing import NamedTuple

# Average of the four Spot interruption buckets (r = 0..4).
SPOT_HIGH_BUCKET = 3
SPOT_LOW_BUCKET = 1

# Vast median day-over-day change, as a fraction. Strict: exactly ±3% scores 0.
VAST_DOD_UP = 0.03
VAST_DOD_DOWN = -0.03

# vLLM last_day versus its 7-day moving average, as a fraction.
VLLM_VS_MA_UP = 0.05
VLLM_VS_MA_DOWN = -0.05

# PJM overnight mean versus the 7-night average, as a fraction.
PJM_VS_MA_UP = 0.01
PJM_VS_MA_DOWN = -0.01

# OpenRouter daily total tokens versus the 7-day moving average, as a fraction.
OR_VS_MA_UP = 0.05
OR_VS_MA_DOWN = -0.05

# Hugging Face is collected for the sheet and is not part of the verdict.
HF_VS_MA_UP = 0.10
HF_VS_MA_DOWN = -0.10

# Rentable-machine count versus the previous day. The score sign is flipped:
# fewer machines (a drop past this band) is +1, more machines is -1.
VAST_AVAIL_UP = 0.05
VAST_AVAIL_DOWN = -0.05

# Sum of the signals that actually had data.
VERDICT_STRONG_AT = 2
VERDICT_SOFTENING_AT = -2

WINDOW_DAYS = 7

# Network. Each source retries this many times, then yields to the others.
HTTP_RETRIES = 3
HTTP_TIMEOUT = (5, 20)
HTTP_RETRY_SLEEP_SECONDS = 1.0
USER_AGENT = "ai-demand-tracker/1.0"

SPOT_URL = "https://spot-bid-advisor.s3.amazonaws.com/spot-advisor-data.json"
VAST_PUBLIC_URL = "https://storage.googleapis.com/vast-public-gpu-pricing/gpu-pricing-public.json"
VAST_BUNDLES_URL = "https://console.vast.ai/api/v0/bundles/"
PYPI_RECENT_URL = "https://pypistats.org/api/packages/vllm/recent"
PYPI_OVERALL_URL = "https://pypistats.org/api/packages/vllm/overall"
EIA_URL = "https://api.eia.gov/v2/electricity/rto/region-data/data/"
OPENROUTER_RANKINGS_URL = "https://openrouter.ai/api/v1/datasets/rankings-daily"
# Days requested, ending yesterday UTC, so the window covers a 7-day average.
OPENROUTER_LOOKBACK_DAYS = 10
HUGGINGFACE_MODEL_URL = "https://huggingface.co/api/models/{repo_id}"

# Official flagship repos. gpt2 is intentionally excluded. Each id was checked
# against the public model API on 2026-11-15.
HF_BASKET = (
    "meta-llama/Llama-3.1-8B-Instruct",
    "meta-llama/Llama-3.3-70B-Instruct",
    "Qwen/Qwen2.5-7B-Instruct",
    "Qwen/Qwen3-14B",
    "Qwen/Qwen3-0.6B",
    "deepseek-ai/DeepSeek-V3.2",
    "deepseek-ai/DeepSeek-R1",
    "openai/gpt-oss-20b",
    "openai/gpt-oss-120b",
    "mistralai/Mistral-7B-Instruct-v0.3",
    "mistralai/Mistral-Small-3.2-24B-Instruct-2506",
    "google/gemma-3-4b-it",
    "google/gemma-3-27b-it",
)

# Latest hourly rows to request: 10 days covers 7 nights plus a lag buffer.
PJM_HOURLY_LENGTH = 240

SHEET_URL = "https://docs.google.com/spreadsheets/d/1YV2uuLIqz7WhhuznL0yOGSBVkljz0M46WCt2vk7Wj5A/edit"

# CSV numeric precision. Prices keep 4 decimal places; averages keep 2.
PRICE_DECIMALS = 4
AVERAGE_DECIMALS = 2

BACKFILL_START = date(2026, 11, 2)
BACKFILL_END = date(2026, 11, 14)

# Fallback search used only when the public pricing feed fails.
# Per-GPU price is dph_total / num_gpus.
VAST_BUNDLE_QUERY = {
    "gpu_name": {"in": ["H100 SXM", "H200", "B200"]},
    "rentable": {"eq": True},
    "type": "on-demand",
    "limit": 500,
    "order": [["dph_total", "asc"]],
}


class SpotTarget(NamedTuple):
    region: str
    instance: str
    column: str
    display: str


SPOT_INSTANCES: tuple[SpotTarget, ...] = (
    SpotTarget("us-east-1", "p5.48xlarge", "spot_use1_p5", "us-east-1 p5 (H100)"),
    SpotTarget("us-east-1", "p4d.24xlarge", "spot_use1_p4d", "us-east-1 p4d (A100)"),
    SpotTarget("us-west-2", "p5.48xlarge", "spot_usw2_p5", "us-west-2 p5 (H100)"),
    SpotTarget("us-west-2", "p4d.24xlarge", "spot_usw2_p4d", "us-west-2 p4d (A100)"),
)

SPOT_COLUMNS: tuple[str, ...] = tuple(target.column for target in SPOT_INSTANCES)

# Public-feed slug -> short key used in column names and the score.
VAST_SLUGS = {
    "H100-SXM": "h100",
    "H200": "h200",
    "B200": "b200",
}

# bundles/ gpu_name -> the same short key.
VAST_BUNDLE_NAMES = {
    "H100 SXM": "h100",
    "H200": "h200",
    "B200": "b200",
}

VAST_DISPLAY = (
    ("h100", "H100"),
    ("h200", "H200"),
    ("b200", "B200"),
)

# Keys that contribute a Vast price signal. B200 is reported, not scored.
VAST_SCORED_KEYS = ("h100", "h200")

DEFAULT_BUCKET_LABELS = {
    0: "<5%",
    1: "5-10%",
    2: "10-15%",
    3: "15-20%",
    4: ">20%",
}

HISTORY_COLUMNS = (
    "date_kst",
    "spot_use1_p5",
    "spot_use1_p4d",
    "spot_usw2_p5",
    "spot_usw2_p4d",
    "vast_h100_min",
    "vast_h100_median",
    "vast_h200_min",
    "vast_h200_median",
    "vast_b200_min",
    "vast_b200_median",
    "vllm_last_day",
    "vllm_7d_ma",
    "pjm_overnight_mw",
    "pjm_overnight_7d_ma",
    "overall",
    "notes",
    "or_total_tokens",
    "or_top5_share",
    "or_top1_model",
    "hf_basket_30d",
    "hf_basket_alltime",
    "hf_implied_daily",
    "vast_h100_avail",
    "vast_h200_avail",
    "vast_b200_avail",
)

HISTORY_HEADER = ",".join(HISTORY_COLUMNS)
