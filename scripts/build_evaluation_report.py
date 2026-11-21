"""Run every offline evaluator and print one Markdown summary.

    python scripts/build_evaluation_report.py --output artifacts/evaluation/latest

Runs, in order: search (50 labelled queries), chat (scripted dialogues, parser
only, so no LLM quota), recommendation offline evaluation on stored
interactions and on 200 simulated users, online metrics from served lists,
and the catalog quality audit. Each raw JSON is saved next to summary.md.
Numbers are descriptive for this catalog, not a benchmark.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNS = {
    "search": ["scripts/evaluate_search.py", "--k", "5"],
    "chat": ["scripts/evaluate_chat.py"],
    "recsys_stored": ["scripts/evaluate_recsys.py", "--k", "5"],
    "recsys_simulated": ["scripts/evaluate_recsys.py", "--k", "5", "--simulate", "200"],
    "online": ["scripts/report_recommendation_metrics.py", "--days", "30"],
    "catalog": ["scripts/audit_catalog_quality.py"],
}


def run(name: str, args: list[str]) -> dict | None:
    proc = subprocess.run(
        [sys.executable, *args], cwd=ROOT, capture_output=True, text=True, encoding="utf-8"
    )
    if proc.returncode != 0:
        print(f"[{name}] failed: {proc.stderr.strip().splitlines()[-1:]}", file=sys.stderr)
        return None
    return json.loads(proc.stdout)


def _f(value) -> str:
    return "–" if value is None else f"{value:.3f}"


def summarise(results: dict[str, dict | None]) -> str:
    lines = [f"# Evaluation summary ({datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC)", ""]
    if search := results.get("search"):
        lines += [f"## Search ({search['queries']} queries, k={search['k']})", "",
                  "| mode | recall@k | nDCG@k | MRR |", "|---|---|---|---|"]
        for mode, data in search["modes"].items():
            o = data["overall"]
            lines.append(f"| {mode} | {_f(o['recall@k'])} | {_f(o['ndcg@k'])} | {_f(o['mrr'])} |")
        lines.append("")
    if chat := results.get("chat"):
        lines += [f"## Chat ({chat['dialogues']} dialogues, {chat['turns']} turns, {chat['mode']})", "",
                  "| intent | slots | constraints | grounded | cards | p95 ms |", "|---|---|---|---|---|---|",
                  f"| {_f(chat['intent_accuracy'])} | {_f(chat['slot_accuracy'])} | "
                  f"{_f(chat['constraint_satisfaction'])} | {_f(chat['grounded_reply_rate'])} | "
                  f"{_f(chat['card_expectation_rate'])} | {chat['latency_ms']['p95']} |",
                  f"\nFailures: {len(chat['failures'])}", ""]
    for key, title in (("recsys_stored", "stored interactions"), ("recsys_simulated", "simulated users")):
        rec = results.get(key)
        if not rec:
            continue
        actors = rec["dataset"].get("eligible_actors", {})
        lines += [f"## Recommendations, {title} (next-item actors: {actors.get('next_item')})", "",
                  "| model | nDCG@5 (95% CI) | hit@5 | coverage |", "|---|---|---|---|"]
        for name, model in rec["models"].items():
            if model.get("status") != "ok":
                lines.append(f"| {name} | {model.get('status')} | | |")
                continue
            m = model["metrics"]
            ci = m["ndcg@k"]["ci95"]
            lines.append(f"| {name} | {_f(m['ndcg@k']['mean'])} ({_f(ci[0])}–{_f(ci[1])}) | "
                         f"{_f(m['hit_rate@k']['mean'])} | {_f(model['catalog_coverage'])} |")
        lines.append("")
    if online := results.get("online"):
        lines += [f"## Online (since {online['since'][:10]}, {online['attribution']})", "",
                  "| surface | strategy | lists | impressions | clicks | CTR |", "|---|---|---|---|---|---|"]
        for g in online["groups"]:
            lines.append(f"| {g['surface']} | {g['strategy']} | {g['served_lists']} | {g['impressions']} | "
                         f"{g['clicks']} | {_f(g['ctr'])} |")
        lines.append("")
    if catalog := results.get("catalog"):
        lines += ["## Catalog quality", "",
                  f"- active products: {catalog['active_products']}",
                  f"- price suspects: {[p['product_id'] for p in catalog['price_suspects']]}",
                  f"- devices without RAM data: {[p['product_id'] for p in catalog['devices_without_ram_data']]}",
                  ""]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--output", type=Path, help="directory for raw JSON and summary.md")
    parser.add_argument("--only", nargs="*", choices=sorted(RUNS), help="run a subset")
    args = parser.parse_args()
    results = {name: run(name, cmd) for name, cmd in RUNS.items() if not args.only or name in args.only}
    summary = summarise(results)
    print(summary)
    if args.output:
        args.output.mkdir(parents=True, exist_ok=True)
        for name, data in results.items():
            if data is not None:
                (args.output / f"{name}.json").write_text(
                    json.dumps(data, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8")
        (args.output / "summary.md").write_text(summary + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
