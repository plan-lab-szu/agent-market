#!/usr/bin/env python3
import argparse
import csv
import math
from datetime import datetime, timezone
from pathlib import Path

import matplotlib.pyplot as plt


def parse_args():
    parser = argparse.ArgumentParser(description="生成 Fig.3(b) 延迟图")
    parser.add_argument("--input", default="raw_data/latency.csv")
    parser.add_argument("--summary", default="raw_data/latency_summary.csv")
    parser.add_argument("--png-output", default="plots/fig3b.png")
    parser.add_argument("--tikz-output", default="plots/fig3b.tex")
    return parser.parse_args()


def load_rows(path: Path):
    rows = []
    with path.open("r", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            row["messaging_ms"] = float(row["messaging_ms"])
            row["settlement_ms"] = float(row["settlement_ms"])
            row["execution_ms"] = float(row["execution_ms"])
            rows.append(row)
    return rows


def percentile(values, p):
    if not values:
        return 0.0
    values = sorted(values)
    k = (len(values) - 1) * p
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return values[int(k)]
    d0 = values[f] * (c - k)
    d1 = values[c] * (k - f)
    return d0 + d1


def summarize(rows):
    buckets = {}
    for row in rows:
        buckets.setdefault(row["workload"], []).append(row)

    summary = []
    for workload, values in buckets.items():
        messaging = [v["messaging_ms"] for v in values]
        settlement = [v["settlement_ms"] for v in values]
        execution = [v["execution_ms"] for v in values]
        summary.append(
            {
                "workload": workload,
                "n": len(values),
                "messaging_median": percentile(messaging, 0.5),
                "messaging_p10": percentile(messaging, 0.1),
                "messaging_p90": percentile(messaging, 0.9),
                "settlement_median": percentile(settlement, 0.5),
                "settlement_p10": percentile(settlement, 0.1),
                "settlement_p90": percentile(settlement, 0.9),
                "execution_median": percentile(execution, 0.5),
                "execution_p10": percentile(execution, 0.1),
                "execution_p90": percentile(execution, 0.9),
            }
        )
    return summary


def write_summary(summary, path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "workload",
                "n",
                "messaging_median",
                "messaging_p10",
                "messaging_p90",
                "settlement_median",
                "settlement_p10",
                "settlement_p90",
                "execution_median",
                "execution_p10",
                "execution_p90",
            ],
        )
        writer.writeheader()
        writer.writerows(summary)


def build_png(summary, output_path: Path):
    order = ["light", "pipeline", "genai"]
    label_map = {"light": "Light", "pipeline": "Pipeline", "genai": "GenAI"}
    data = {item["workload"]: item for item in summary}

    def value(workload, key):
        return data.get(workload, {}).get(key, 0.0)

    messaging = [value(w, "messaging_median") for w in order]
    settlement = [value(w, "settlement_median") for w in order]
    execution = [value(w, "execution_median") for w in order]

    x = range(len(order))
    fig, ax = plt.subplots(figsize=(7.2, 4.2))
    ax.bar(x, messaging, label="Messaging", color="#4C8BB8")
    ax.bar(
        x,
        settlement,
        bottom=messaging,
        label="Settlement",
        color="#D77C5A",
    )
    ax.bar(
        x,
        execution,
        bottom=[m + s for m, s in zip(messaging, settlement)],
        label="Execution",
        color="#6AA84F",
    )
    ax.set_ylabel("Time (ms)")
    ax.set_xticks(list(x))
    ax.set_xticklabels([label_map[w] for w in order])
    ax.legend(frameon=False)
    ax.grid(axis="y", linestyle="--", alpha=0.4)
    fig.tight_layout()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, dpi=300)

    meta = {"generated_at": datetime.now(timezone.utc).isoformat()}
    meta_path = output_path.parent / "fig3b_meta.json"
    meta_path.write_text(json_dump(meta), encoding="utf-8")


def build_tikz(summary, output_path: Path):
    order = ["light", "pipeline", "genai"]
    label_map = {"light": "Light", "pipeline": "Pipeline", "genai": "GenAI"}
    data = {item["workload"]: item for item in summary}

    def value(workload, key):
        return data.get(workload, {}).get(key, 0.0)

    messaging = [value(w, "messaging_median") for w in order]
    settlement = [value(w, "settlement_median") for w in order]
    execution = [value(w, "execution_median") for w in order]

    coords = {
        "messaging": " ".join(
            f"({label_map[w]},{value:.3f})" for w, value in zip(order, messaging)
        ),
        "settlement": " ".join(
            f"({label_map[w]},{value:.3f})" for w, value in zip(order, settlement)
        ),
        "execution": " ".join(
            f"({label_map[w]},{value:.3f})" for w, value in zip(order, execution)
        ),
    }

    caption = (
        "Fig. 3: Performance evaluation of the Agent-OSI prototype. (b) Latency: "
        "breakdown of end-to-end time across three workloads (Light (no-gen), "
        "Pipeline (K-step), and GenAI (image/LLM)). Execution includes IPFS upload."
    )

    tikz = f"""% Auto-generated by latency.py
\\begin{{figure}}[t]
\\centering
\\begin{{tikzpicture}}
\\begin{{axis}}[
  ybar stacked,
  bar width=9pt,
  width=8.6cm,
  height=4.6cm,
  symbolic x coords={{{",".join(label_map[w] for w in order)}}},
  xtick=data,
  ylabel={{Time (ms)}},
  legend style={{at={{(0.5,1.05)}},anchor=south,legend columns=-1}},
  ymin=0,
  ymajorgrids,
  grid style={{dashed,gray!30}},
  tick label style={{font=\\footnotesize}},
  label style={{font=\\footnotesize}},
  legend style={{font=\\footnotesize}},
]
\\addplot+[fill={{rgb,255:red,76;green,139;blue,184}}] coordinates {{{coords["messaging"]}}};
\\addplot+[fill={{rgb,255:red,215;green,124;blue,90}}] coordinates {{{coords["settlement"]}}};
\\addplot+[fill={{rgb,255:red,106;green,168;blue,79}}] coordinates {{{coords["execution"]}}};
\\legend{{Messaging, Settlement, Execution (incl. IPFS)}}
\\end{{axis}}
\\end{{tikzpicture}}
\\caption{{{caption}}}
\\end{{figure}}
"""

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(tikz, encoding="utf-8")


def json_dump(payload):
    import json

    return json.dumps(payload, ensure_ascii=False, indent=2)


def main():
    args = parse_args()
    root = Path(__file__).resolve().parents[1]
    input_path = root / args.input
    summary_path = root / args.summary
    png_path = root / args.png_output
    tikz_path = root / args.tikz_output

    rows = load_rows(input_path)
    summary = summarize(rows)
    write_summary(summary, summary_path)
    build_png(summary, png_path)
    build_tikz(summary, tikz_path)


if __name__ == "__main__":
    main()
