#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from engine.pipeline import Engine, generate_behavior_plan  # noqa: E402
from engine.trace_logger import to_markdown  # noqa: E402


def load_cases() -> list:
    path = ROOT / "cases" / "welcome_cases.json"
    return json.loads(path.read_text(encoding="utf-8"))


def run_one(request: dict, use_llm: bool, engine: Engine | None = None) -> dict:
    return generate_behavior_plan(request, use_llm=use_llm, engine=engine)


def write_outputs(result: dict, case_id: str, out_root: Path) -> None:
    persona = result["persona"]
    cases_dir = out_root / "cases"
    review_dir = out_root / "review"
    cases_dir.mkdir(parents=True, exist_ok=True)
    review_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{case_id}_{persona}"
    (cases_dir / f"{stem}.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    md = to_markdown(result)
    (review_dir / f"{stem}.md").write_text(md, encoding="utf-8")


def run_cases(use_llm: bool, personas: list[str], out_root: Path) -> None:
    cases = load_cases()
    engine = Engine(use_llm=use_llm)
    summary_rows = []
    for case in cases:
        for persona in personas:
            req = deepcopy(case["request"])
            req["persona"] = persona
            result = run_one(req, use_llm=use_llm, engine=engine)
            write_outputs(result, case["case_id"], out_root)
            final = result.get("final") or {}
            summary_rows.append(
                {
                    "case_id": case["case_id"],
                    "title": case.get("title"),
                    "persona": persona,
                    "call_type": final.get("behavior_call_type"),
                    "capabilities": final.get("selected_capabilities"),
                    "thinking_narrative": final.get("thinking_narrative"),
                    "action_plan": final.get("action_plan") or final.get("scenario_description"),
                    "spoken_reply": final.get("spoken_reply") or final.get("assistant_response"),
                }
            )
            print(
                f"[ok] {case['case_id']} {persona} -> {final.get('behavior_call_type')} {final.get('selected_capabilities')}"
            )

    def _cell(text: str, limit: int = 120) -> str:
        t = (text or "").replace("|", "/").replace("\n", " ").strip()
        if len(t) > limit:
            return t[: limit - 1] + "…"
        return t or "-"

    # summary markdown with required columns
    lines = ["# Welcome Cases Summary", ""]
    lines.append(
        "| Case | Title | Persona | 思考过程（性格决策自述） | 会怎么做（调用程度+先后顺序） | 到时候怎么说 |"
    )
    lines.append("|---|---|---|---|---|---|")
    for r in summary_rows:
        lines.append(
            f"| {r['case_id']} | {r['title']} | {r['persona']} | "
            f"{_cell(r.get('thinking_narrative'), 140)} | "
            f"{_cell(r.get('action_plan'), 140)} | "
            f"{_cell(r.get('spoken_reply'), 80)} |"
        )

    lines += ["", "## 全文展开", ""]
    for r in summary_rows:
        lines.append(f"### {r['case_id']} / {r['persona']} — {r['title']}")
        lines.append("")
        lines.append(f"- call_type: `{r['call_type']}`")
        lines.append(f"- capabilities: `{r['capabilities']}`")
        lines.append("")
        lines.append("**思考过程**")
        lines.append("")
        lines.append(r.get("thinking_narrative") or "（空）")
        lines.append("")
        lines.append("**会怎么做（调用程度+先后顺序）**")
        lines.append("")
        lines.append(r.get("action_plan") or "（空）")
        lines.append("")
        lines.append("**到时候怎么说**")
        lines.append("")
        lines.append(r.get("spoken_reply") or "（空）")
        lines.append("")

    # persona diff notes
    lines += ["", "## INFP vs ESTJ quick diffs", ""]
    by_case = {}
    for r in summary_rows:
        by_case.setdefault(r["case_id"], {})[r["persona"]] = r
    for cid, m in by_case.items():
        a, b = m.get("INFP"), m.get("ESTJ")
        if not a or not b:
            continue
        diff = []
        if a["call_type"] != b["call_type"]:
            diff.append(f"call_type {a['call_type']} vs {b['call_type']}")
        if a["capabilities"] != b["capabilities"]:
            diff.append(f"caps {a['capabilities']} vs {b['capabilities']}")
        if not diff:
            diff.append("调用集合相同，差异可能在执行模式/话术（见各 md）")
        lines.append(f"- {cid}: " + "; ".join(diff))

    (out_root / "review" / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote summary -> {out_root / 'review' / 'summary.md'}")


def main():
    parser = argparse.ArgumentParser(description="Personality behavior mapping engine")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p1 = sub.add_parser("run-one", help="Run a single JSON request file")
    p1.add_argument("request_json")
    p1.add_argument("--no-llm", action="store_true")
    p1.add_argument("--out", default=str(ROOT / "output"))

    p2 = sub.add_parser("run-cases", help="Run 10 welcome cases x personas")
    p2.add_argument("--no-llm", action="store_true")
    p2.add_argument("--personas", default="INFP,ESTJ")
    p2.add_argument("--out", default=str(ROOT / "output"))

    args = parser.parse_args()
    out_root = Path(args.out)

    if args.cmd == "run-one":
        req = json.loads(Path(args.request_json).read_text(encoding="utf-8"))
        result = run_one(req, use_llm=not args.no_llm)
        write_outputs(result, req.get("request_id", "one"), out_root)
        print(json.dumps(result["final"], ensure_ascii=False, indent=2))
    elif args.cmd == "run-cases":
        personas = [p.strip() for p in args.personas.split(",") if p.strip()]
        run_cases(use_llm=not args.no_llm, personas=personas, out_root=out_root)


if __name__ == "__main__":
    main()
