#!/usr/bin/env python
"""Minimal, self-contained demonstration of LLM-based information extraction.

Reads a small sample of Chinese listed-company executive biographies and
extracts each person's *political appointments* (government / party / people's
congress / consultative conference) as structured events.

This is a reduced copy of the production script, kept deliberately small so it
can be read end to end. The API key is NEVER stored in this file -- supply it
via the DEEPSEEK_API_KEY environment variable, a .env file, or --api-key.

Usage
-----
    export DEEPSEEK_API_KEY="sk-..."
    python code/extract_demo.py              # all 10 sample resumes -> output/
    python code/extract_demo.py --limit 3    # smoke test -> output/*.limit3.*

Outputs (written to output/)
    sample_extraction.csv    one row per extracted appointment
    sample_extraction.json   same content, nested by person
    usage.json               token counts, cache split, cost estimate
    run.log                  per-resume trace

A --limit run writes the same four files with a `.limit<N>` suffix, so a smoke
test can never overwrite the shipped reference output.
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests

# ---------------------------------------------------------------------------
# Paths (relative to the case-study root, so this runs from anywhere)
# ---------------------------------------------------------------------------
CASE_ROOT = Path(__file__).resolve().parent.parent
INPUT_CSV = CASE_ROOT / "data" / "sample_resumes.csv"
PROMPT_FILE = CASE_ROOT / "prompts" / "system_prompt.txt"
OUT_DIR = CASE_ROOT / "output"

# ---------------------------------------------------------------------------
# Model settings -- verified against https://api-docs.deepseek.com
# ---------------------------------------------------------------------------
# `deepseek-flash` is the current model name; `deepseek-v4-pro` is the larger
# one. The legacy name `deepseek-v4-flash` is still *accepted*, but its model
# has been retired and such requests are served by DeepSeek-V4.1-Flash anyway,
# so there is no reason to use the old name.
BASE_URL = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-flash")

# Thinking mode is ON by default, and it is expensive: it emits a chain of
# thought into reasoning_content, roughly 6x the output tokens for no gain on a
# pure extraction task. It has to be switched off explicitly -- see `THINKING`.
TEMPERATURE = 0.0          # right for extraction. NB: thinking mode ignores
                           # temperature, so this only bites in non-thinking mode
MAX_TOKENS = 10000         # a ceiling, not a target. One biography can hold
                           # dozens of appointments; the API maximum is 384K
TIMEOUT = 120
MAX_RETRIES = 6
USE_JSON_MODE = True       # falls back automatically if the model rejects it

# Published deepseek-flash prices, USD per 1M tokens. Off-peak is exactly half
# of peak. Peak = 01:00-04:00 and 06:00-10:00 UTC, Mon-Fri, excluding Chinese
# public holidays. Two things dominate the bill and neither is obvious:
#   * cached input is ~50x cheaper than uncached input, and
#   * output costs ~4x more than even an uncached input token.
PRICE_PER_MTOK = {
    "peak":     {"cache_hit": 0.006, "cache_miss": 0.30, "output": 1.20},
    "off_peak": {"cache_hit": 0.003, "cache_miss": 0.15, "output": 0.60},
}

# ---------------------------------------------------------------------------
# Controlled vocabularies (the extraction schema)
# ---------------------------------------------------------------------------
INST_TYPES = {"GOV", "PARTY", "NPC", "CPPCC", "OTHER"}
LEVELS = {"国家", "省", "地市", "县", "乡"}
LEVEL_RANK = {"国家": 1, "省": 2, "地市": 3, "县": 4, "乡": 5}
TENURE_TYPES = {"现任", "曾任", "历任", "挂职", "未知"}

# Bare province name -> full name with its administrative suffix.
PROVINCE_NORM = {
    "河北": "河北省", "山西": "山西省", "辽宁": "辽宁省", "吉林": "吉林省",
    "黑龙江": "黑龙江省", "江苏": "江苏省", "浙江": "浙江省", "安徽": "安徽省",
    "福建": "福建省", "江西": "江西省", "山东": "山东省", "河南": "河南省",
    "湖北": "湖北省", "湖南": "湖南省", "广东": "广东省", "海南": "海南省",
    "四川": "四川省", "贵州": "贵州省", "云南": "云南省", "陕西": "陕西省",
    "甘肃": "甘肃省", "青海": "青海省", "台湾": "台湾省",
    "内蒙古": "内蒙古自治区", "广西": "广西壮族自治区", "西藏": "西藏自治区",
    "宁夏": "宁夏回族自治区", "新疆": "新疆维吾尔自治区",
    "北京": "北京市", "上海": "上海市", "天津": "天津市", "重庆": "重庆市",
    "香港": "香港特别行政区", "澳门": "澳门特别行政区",
}
DIRECT_MUNICIPALITIES = {"北京市", "上海市", "天津市", "重庆市"}
_LOC_SUFFIXES = ("省", "市", "区", "县", "州", "盟", "旗", "乡", "镇")

# Deterministic noise filter applied AFTER the model returns: rank-only civil
# service grades are never leadership posts, whatever the model decides.
RANK_PATTERN = re.compile(r"科员|调研员|巡视员|办事员|处级|级秘书|级干部")

APPOINTMENT_FIELDS = [
    "inst_type", "level", "level_rank", "province", "city", "county",
    "location_raw", "org_std", "org_raw", "position_std", "position_raw",
    "is_representative", "session", "start_year", "end_year",
    "tenure_type", "is_concurrent",
]
OUTPUT_FIELDS = ["record_id", "PersonID", "Stkcd", "resume_version"] + APPOINTMENT_FIELDS + [
    "source_snippet", "method", "model",
]

USER_PROMPT_TMPL = "请抽取以下简历中的政治任职经历：\n\n{resume}"


# ---------------------------------------------------------------------------
# API key handling -- environment only, never hard-coded
# ---------------------------------------------------------------------------
def resolve_api_key(cli_key: str) -> tuple[str, str]:
    """Resolve the key from --api-key, then .env, then the environment.

    Returns the key and a label naming where it came from. Only the label is
    ever logged -- not even a fragment of the key itself, because these logs are
    written to `output/` and may end up committed to a public repo.
    """
    if cli_key:
        return cli_key.strip(), "--api-key"

    env_file = CASE_ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("DEEPSEEK_API_KEY=") and not line.startswith("#"):
                return line.split("=", 1)[1].strip().strip('"').strip("'"), ".env"

    key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if not key:
        sys.exit(
            "[error] No API key found.\n"
            "        Set it with:  export DEEPSEEK_API_KEY=\"sk-...\"\n"
            "        or copy .env.example to .env and fill it in."
        )
    return key, "DEEPSEEK_API_KEY env var"


# ---------------------------------------------------------------------------
# Cost accounting
# ---------------------------------------------------------------------------
def rate_period(now_utc: datetime) -> str:
    """Return 'peak' or 'off_peak' for DeepSeek's published price schedule.

    Peak runs 01:00-04:00 and 06:00-10:00 UTC, Monday to Friday. Chinese public
    holidays are billed off-peak too, but that calendar is not available here,
    so on a holiday this over-estimates -- the figure is an upper bound.
    """
    if now_utc.weekday() >= 5:          # Saturday or Sunday
        return "off_peak"
    return "peak" if (1 <= now_utc.hour < 4 or 6 <= now_utc.hour < 10) else "off_peak"


def estimate_cost(period: str, cache_hit: int, cache_miss: int, output: int) -> float:
    """Cost in USD, billed separately for cached and uncached input."""
    p = PRICE_PER_MTOK[period]
    return (cache_hit * p["cache_hit"]
            + cache_miss * p["cache_miss"]
            + output * p["output"]) / 1e6


# ---------------------------------------------------------------------------
# Normalisation -- the deterministic half of the pipeline
# ---------------------------------------------------------------------------
def _to_str(v: Any) -> Optional[str]:
    if v is None:
        return None
    s = str(v).strip()
    return s if s and s.lower() not in {"none", "null", "nan", "无", "不详", "未知"} else None


def _to_int(v: Any) -> Optional[int]:
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def _to_bool(v: Any) -> bool:
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return bool(v)
    return str(v).strip().lower() in {"true", "1", "是", "yes"}


def _strip_dup_suffix(v: Optional[str]) -> Optional[str]:
    """「山东省省」->「山东省」."""
    if not v:
        return None
    for sfx in _LOC_SUFFIXES:
        if v.endswith(sfx + sfx):
            return v[: -len(sfx)]
    return v or None


def norm_province(v: Optional[str]) -> Optional[str]:
    if not v:
        return None
    v = v.strip()
    return PROVINCE_NORM.get(v) or _strip_dup_suffix(v)


def normalize_appointment(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Coerce one model-emitted appointment into the fixed schema."""
    app: Dict[str, Any] = {}
    app["inst_type"] = _to_str(raw.get("inst_type")) or "OTHER"
    if app["inst_type"] not in INST_TYPES:
        app["inst_type"] = "OTHER"

    level = _to_str(raw.get("level"))
    app["level"] = level if level in LEVELS else None
    app["level_rank"] = _to_int(raw.get("level_rank"))
    if app["level_rank"] is None and app["level"] in LEVEL_RANK:
        app["level_rank"] = LEVEL_RANK[app["level"]]

    for k in ("location_raw", "org_std", "org_raw", "position_std", "position_raw", "session"):
        app[k] = _to_str(raw.get(k))

    app["province"] = norm_province(_to_str(raw.get("province")))
    app["city"] = _strip_dup_suffix(_to_str(raw.get("city")))
    app["county"] = _strip_dup_suffix(_to_str(raw.get("county")))
    # In a direct-administered municipality, a district is a county-level unit.
    if app["province"] in DIRECT_MUNICIPALITIES:
        if app["city"] and app["city"].endswith(("区", "县")):
            app["county"] = app["city"]
        app["city"] = app["province"]

    app["is_representative"] = _to_bool(raw.get("is_representative"))
    app["start_year"] = _to_int(raw.get("start_year"))
    app["end_year"] = _to_int(raw.get("end_year"))
    tenure = _to_str(raw.get("tenure_type"))
    app["tenure_type"] = tenure if tenure in TENURE_TYPES else "未知"
    app["is_concurrent"] = _to_bool(raw.get("is_concurrent"))
    return app


def parse_json(content: str) -> Optional[Dict[str, Any]]:
    """Tolerate markdown fences and stray prose around the JSON object."""
    if not content:
        return None
    text = content.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            return obj
    except json.JSONDecodeError:
        pass
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if m:
        try:
            obj = json.loads(m.group(0))
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            pass
    return None


# ---------------------------------------------------------------------------
# API client
# ---------------------------------------------------------------------------
class DeepSeekClient:
    def __init__(self, api_key: str, system_prompt: str, logger: logging.Logger) -> None:
        self.api_key = api_key
        self.system_prompt = system_prompt
        self.url = BASE_URL.rstrip("/") + "/chat/completions"
        self.logger = logger

    def extract(self, resume: str) -> Tuple[Optional[List[Dict[str, Any]]], Dict[str, int]]:
        """Return (normalised appointments, usage). On failure, (None, {})."""
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload: Dict[str, Any] = {
            "model": MODEL,
            "messages": [
                {"role": "system", "content": self.system_prompt},
                {"role": "user", "content": USER_PROMPT_TMPL.format(resume=resume)},
            ],
            "temperature": TEMPERATURE,
            "max_tokens": MAX_TOKENS,
            # Thinking mode defaults to ON. Left on, the model spends its budget
            # on reasoning_content instead of the answer: measured at 355 extra
            # reasoning tokens against 71 answer tokens on the same input, and
            # it truncates the JSON (finish_reason=length). Must be switched off.
            # Equivalent alternative: "reasoning_effort": "none".
            "thinking": {"type": "disabled"},
        }
        if USE_JSON_MODE:
            payload["response_format"] = {"type": "json_object"}

        for attempt in range(MAX_RETRIES):
            try:
                resp = requests.post(self.url, headers=headers, json=payload, timeout=TIMEOUT)
            except (requests.exceptions.Timeout, requests.exceptions.RequestException) as e:
                self._backoff(f"network error: {type(e).__name__}", attempt)
                continue

            if resp.status_code == 200:
                data = resp.json()
                content = data["choices"][0]["message"]["content"]
                usage = data.get("usage") or {}
                obj = parse_json(content)
                if obj is None:
                    self.logger.warning("could not parse JSON; retrying")
                    payload.pop("response_format", None)
                    self._backoff("JSON parse failure", attempt)
                    continue
                apps = [normalize_appointment(a)
                        for a in obj.get("appointments", [])
                        if isinstance(a, dict)]
                return apps, usage

            if resp.status_code == 401:
                sys.exit("[error] HTTP 401: invalid API key.")
            if resp.status_code == 402:
                sys.exit("[error] HTTP 402: insufficient balance.")
            if resp.status_code == 429:
                self._backoff("rate limited (429)", attempt, base=2.0)
                continue
            if resp.status_code >= 500:
                self._backoff(f"server error {resp.status_code}", attempt, base=2.0)
                continue
            if resp.status_code == 400 and "response_format" in payload:
                self.logger.info("model rejected JSON mode; retrying without it")
                payload.pop("response_format", None)
                self._backoff("json mode fallback", attempt)
                continue
            self.logger.error("HTTP %s (not retryable): %s", resp.status_code, resp.text[:200])
            return None, {}

        return None, {}

    def _backoff(self, msg: str, attempt: int, base: float = 1.0) -> None:
        wait = min(base * (2 ** attempt) + 0.5, 60.0)
        self.logger.warning("%s; retry %d/%d in %.1fs", msg, attempt + 1, MAX_RETRIES, wait)
        time.sleep(wait)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> None:
    ap = argparse.ArgumentParser(description="LLM extraction demo on the sample resumes")
    ap.add_argument("--limit", type=int, default=0, help="only process the first N resumes")
    ap.add_argument("--api-key", default="", help="override the API key (prefer the env var)")
    args = ap.parse_args()

    api_key, key_source = resolve_api_key(args.api_key)

    # A --limit run is a smoke test, so it writes to suffixed files and can
    # never clobber the shipped reference output. Only a full run updates
    # output/sample_extraction.* -- and even that is a deliberate overwrite.
    suffix = f".limit{args.limit}" if args.limit else ""
    out_csv = OUT_DIR / f"sample_extraction{suffix}.csv"
    out_json = OUT_DIR / f"sample_extraction{suffix}.json"
    usage_json = OUT_DIR / f"usage{suffix}.json"
    log_file = OUT_DIR / f"run{suffix}.log"

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.StreamHandler(sys.stdout),
                  logging.FileHandler(log_file, encoding="utf-8", mode="w")],
    )
    log = logging.getLogger("extract_demo")

    system_prompt = PROMPT_FILE.read_text(encoding="utf-8").strip()
    with INPUT_CSV.open("r", encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    if args.limit:
        rows = rows[: args.limit]

    log.info("model=%s key_source=%s key_len=%d resumes=%d prompt_chars=%d",
             MODEL, key_source, len(api_key), len(rows), len(system_prompt))

    client = DeepSeekClient(api_key, system_prompt, log)
    all_rows: List[Dict[str, Any]] = []
    nested: List[Dict[str, Any]] = []
    in_tok = out_tok = hit_tok = miss_tok = 0
    n_failed = 0
    t0 = time.time()

    for i, row in enumerate(rows, start=1):
        pid = row["PersonID"]
        resume = (row.get("D0801c") or "").strip()
        log.info("[%d/%d] %s (%d chars) ...", i, len(rows), pid, len(resume))
        apps, usage = client.extract(resume)

        if apps is None:
            n_failed += 1
            log.error("[%d/%d] %s | FAILED", i, len(rows), pid)
            continue

        # The API splits prompt tokens into cached and uncached, billed ~50x
        # apart, so track them separately rather than lumping them together.
        in_tok += usage.get("prompt_tokens", 0)
        out_tok += usage.get("completion_tokens", 0)
        hit_tok += usage.get("prompt_cache_hit_tokens", 0)
        miss_tok += usage.get("prompt_cache_miss_tokens", 0)

        # Deterministic post-filter: drop non-target types and rank-only grades.
        kept = [a for a in apps
                if a.get("inst_type") != "OTHER"
                and not RANK_PATTERN.search(a.get("position_std") or "")]
        dropped = len(apps) - len(kept)

        for idx, app in enumerate(kept):
            rec = {
                "record_id": f"{pid}_{idx}",
                "PersonID": pid,
                "Stkcd": row.get("Stkcd", ""),
                "resume_version": row.get("resume_version", ""),
                "source_snippet": resume,
                "method": "LLM",
                "model": MODEL,
            }
            rec.update({f: app.get(f) for f in APPOINTMENT_FIELDS})
            all_rows.append(rec)

        nested.append({
            "case_id": pid,
            "stock_code": row.get("Stkcd", ""),
            "resume": resume,
            "n_appointments": len(kept),
            "n_dropped_by_postfilter": dropped,
            "appointments": kept,
        })
        log.info("[%d/%d] %s | %d appointments (%d dropped) | in=%d out=%d tok",
                 i, len(rows), pid, len(kept), dropped,
                 usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0))
        time.sleep(0.1)

    with out_csv.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=OUTPUT_FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows(all_rows)

    out_json.write_text(json.dumps(nested, ensure_ascii=False, indent=2), encoding="utf-8")

    elapsed = time.time() - t0
    period = rate_period(datetime.now(timezone.utc))
    cost = estimate_cost(period, hit_tok, miss_tok, out_tok)
    # Fall back to the plain prompt total if the API did not report the split.
    if hit_tok + miss_tok == 0:
        miss_tok = in_tok
        cost = estimate_cost(period, 0, in_tok, out_tok)
    usage_rec = {
        "model": MODEL,
        "n_resumes": len(rows),
        "n_failed": n_failed,
        "n_appointments": len(all_rows),
        "prompt_tokens": in_tok,
        "prompt_cache_hit_tokens": hit_tok,
        "prompt_cache_miss_tokens": miss_tok,
        "prompt_cache_hit_rate": round(hit_tok / in_tok, 3) if in_tok else 0.0,
        "completion_tokens": out_tok,
        "elapsed_seconds": round(elapsed, 1),
        "seconds_per_resume": round(elapsed / max(len(rows), 1), 2),
        "rate_period_at_run": period,
        "estimated_cost_usd": round(cost, 6),
        "price_per_mtok_usd": PRICE_PER_MTOK[period],
    }
    usage_json.write_text(json.dumps(usage_rec, ensure_ascii=False, indent=2), encoding="utf-8")

    log.info("done: %d appointments from %d resumes in %.1fs | in=%d (cache hit %.0f%%) "
             "out=%d tok | %s rates | ~$%.4f",
             len(all_rows), len(rows), elapsed, in_tok,
             100 * hit_tok / in_tok if in_tok else 0, out_tok, period, cost)
    log.info("wrote %s / %s / %s", out_csv.name, out_json.name, usage_json.name)


if __name__ == "__main__":
    main()
