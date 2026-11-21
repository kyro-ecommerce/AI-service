"""Evaluate the multi-turn chatbot on scripted Vietnamese dialogues.

Runs the real chat v2 pipeline over the active catalog with an in-memory
conversation store; nothing is written to the database. Default is
deterministic (parser only, no LLM). ``--live`` uses the configured LLM
provider and therefore spends its quota.

Metrics:
* intent accuracy and slot accuracy (expected slots after each turn);
* constraint satisfaction: share of returned cards that satisfy the turn's
  hard conditions when re-checked independently;
* grounding: share of replies in which every sentence passes the link/price
  check against the shown cards;
* card expectation: turns expected to return products (or not) that did;
* latency per turn.

    python scripts/evaluate_chat.py
    python scripts/evaluate_chat.py --live --output artifacts/evaluation/chat-live.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import statistics
import sys
import time
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

DIALOGUES = PROJECT_ROOT / "tests" / "fixtures" / "chat_dialogues_vi.json"


class _MemoryStore:
    def __init__(self) -> None:
        self.rows: dict = {}

    def load(self, conversation_id, *, user_id, session_id, now=None):
        from app.repositories.conversation_repository import LoadedConversation, new_conversation_id
        from app.schemas.conversation import ConversationState

        row = self.rows.get(conversation_id)
        if row is None:
            return LoadedConversation(new_conversation_id(), ConversationState())
        return LoadedConversation(conversation_id, row["state"], row["history"][-6:], is_new=False)

    def save(self, conversation, state, *, user_text, assistant_text, **_):
        row = self.rows.setdefault(conversation.conversation_id, {"history": []})
        row["state"] = state
        row["history"] += [("user", user_text), ("assistant", assistant_text)]
        return True


def _slot_matches(state, key: str, expected) -> bool:
    actual = getattr(state, key)
    if isinstance(expected, list):
        return sorted(x.casefold() for x in actual) == sorted(x.casefold() for x in expected)
    return actual == expected


def _sentences(text: str) -> list[str]:
    return [s for s in re.split(r"(?<=[.!?…])\s+|\n+", text) if s.strip()]


async def run(dialogues: list[dict], *, live: bool) -> dict:
    from app.services import chat_tools, chat_v2

    store = _MemoryStore()
    offline_llm = type("OfflineLLM", (), {"configured_provider": staticmethod(lambda: None)})
    patches = [
        patch("app.repositories.conversation_repository.load_conversation", new=store.load),
        patch("app.repositories.conversation_repository.save_turn", new=store.save),
        patch("app.services.recommendation_tracking.persist_served", return_value=True),
        patch("app.repositories.user_interaction_repository.record_user_interaction_in_new_session", return_value=True),
    ]
    if not live:
        patches += [
            patch("app.services.chat_v2.llm_client", new=offline_llm),
            patch("app.services.chat_nlu.llm_client", new=offline_llm),
        ]
    totals = {"intent": [], "slot": [], "constraint": [], "grounded": [], "cards": [], "latency_ms": []}
    failures = []
    for p in patches:
        p.start()
    try:
        for dialogue in dialogues:
            conversation_id = None
            for index, step in enumerate(dialogue["turns"]):
                started = time.perf_counter()
                plan = await chat_v2.prepare_turn(
                    step["say"], conversation_id=conversation_id, user_id=0,
                    session_id=f"eval-{dialogue['id']}", db=None, limit=4, http_client=None,
                )
                reply = "".join([c async for c in chat_v2.answer_chunks(plan, step["say"], None)])
                await asyncio.to_thread(
                    store.save, plan.conversation, plan.state, user_text=step["say"], assistant_text=reply
                )
                totals["latency_ms"].append((time.perf_counter() - started) * 1000)
                conversation_id = plan.conversation.conversation_id
                where = f"{dialogue['id']}#{index + 1}"

                ok_intent = plan.interpretation.intent == step["intent"]
                totals["intent"].append(ok_intent)
                if not ok_intent:
                    failures.append({"turn": where, "say": step["say"], "intent": plan.interpretation.intent, "expected": step["intent"]})
                for key, expected in step.get("slots", {}).items():
                    ok = _slot_matches(plan.state, key, expected)
                    totals["slot"].append(ok)
                    if not ok:
                        failures.append({"turn": where, "say": step["say"], "slot": key,
                                         "got": getattr(plan.state, key), "expected": expected})
                if plan.products and plan.interpretation.intent == "recommend":
                    eligible = chat_tools.eligible_products(plan.state.to_requirements(step["say"]),
                                                            plan.products).candidates
                    totals["constraint"] += [p.product_id in eligible for p in plan.products]
                totals["grounded"].append(all(chat_v2.sentence_ok(s, plan) for s in _sentences(reply)))
                if "cards" in step:
                    ok = bool(plan.products) == step["cards"]
                    totals["cards"].append(ok)
                    if not ok:
                        failures.append({"turn": where, "say": step["say"], "cards": len(plan.products), "expected_cards": step["cards"]})
    finally:
        for p in patches:
            p.stop()

    def rate(values):
        return round(sum(values) / len(values), 4) if values else None

    latencies = sorted(totals["latency_ms"])
    return {
        "dialogues": len(dialogues),
        "turns": len(totals["intent"]),
        "mode": "live_llm" if live else "parser_only",
        "intent_accuracy": rate(totals["intent"]),
        "slot_accuracy": rate(totals["slot"]),
        "slots_checked": len(totals["slot"]),
        "constraint_satisfaction": rate(totals["constraint"]),
        "cards_checked": len(totals["constraint"]),
        "grounded_reply_rate": rate(totals["grounded"]),
        "card_expectation_rate": rate(totals["cards"]),
        "latency_ms": {
            "median": round(statistics.median(latencies), 1) if latencies else None,
            "p95": round(latencies[min(len(latencies) - 1, int(0.95 * len(latencies)))], 1) if latencies else None,
        },
        "failures": failures,
        "note": "Scripted set written for this catalog; descriptive, not a benchmark.",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--live", action="store_true", help="use the configured LLM provider")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    dialogues = json.loads(DIALOGUES.read_text(encoding="utf-8"))["dialogues"]
    report = asyncio.run(run(dialogues, live=args.live))
    text = json.dumps(report, indent=2, ensure_ascii=False)
    print(text)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
