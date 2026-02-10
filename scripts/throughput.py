#!/usr/bin/env python3
import argparse
import csv
import math
from datetime import datetime, timezone
from pathlib import Path

import matplotlib.pyplot as plt


def parse_args():
    parser = argparse.ArgumentParser(description="绘制 Fig.3(c) 吞吐量曲线")
    parser.add_argument("--input", default="raw_data/tps.csv")
    parser.add_argument("--summary", default="raw_data/tps_summary.csv")
    parser.add_argument("--png-output", default="plots/fig3c.png")
    parser.add_argument("--tikz-output", default="plots/fig3c.tex")
    return parser.parse_args()


def load_rows(path: Path):
    rows = []
    with path.open("r", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            row["concurrency"] = int(row["concurrency"])
            row["message_throughput"] = float(row["message_throughput"])
            row["settlement_throughput"] = float(row["settlement_throughput"])
            row["completed_throughput"] = float(row["completed_throughput"])
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
        buckets.setdefault(row["concurrency"], []).append(row)

    summary = []
    for concurrency, values in sorted(buckets.items()):
        msg = [v["message_throughput"] for v in values]
        settle = [v["settlement_throughput"] for v in values]
        complete = [v["completed_throughput"] for v in values]
        summary.append(
            {
                "concurrency": concurrency,
                "n": len(values),
                "message_median": percentile(msg, 0.5),
                "message_p10": percentile(msg, 0.1),
                "message_p90": percentile(msg, 0.9),
                "settlement_median": percentile(settle, 0.5),
                "settlement_p10": percentile(settle, 0.1),
                "settlement_p90": percentile(settle, 0.9),
                "completed_median": percentile(complete, 0.5),
                "completed_p10": percentile(complete, 0.1),
                "completed_p90": percentile(complete, 0.9),
            }
        )
    return summary


def write_summary(summary, path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "concurrency",
                "n",
                "message_median",
                "message_p10",
                "message_p90",
                "settlement_median",
                "settlement_p10",
                "settlement_p90",
                "completed_median",
                "completed_p10",
                "completed_p90",
            ],
        )
        writer.writeheader()
        writer.writerows(summary)


def build_png(summary, output_path: Path):
    rows = sorted(summary, key=lambda r: r["concurrency"])
    x = [row["concurrency"] for row in rows]
    msg = [row["message_median"] for row in rows]
    settle = [row["settlement_median"] for row in rows]
    complete = [row["completed_median"] for row in rows]

    fig, ax = plt.subplots(figsize=(7.2, 4.2))
    ax.plot(
        x,
        msg,
        label="Message throughput (msg/s)",
        color="#4C8BB8",
        marker="o",
    )
    ax.plot(
        x,
        settle,
        label="Settlement throughput (tx/s)",
        color="#D77C5A",
        marker="s",
    )
    ax.plot(
        x,
        complete,
        label="Completed sessions (sessions/s)",
        color="#6AA84F",
        marker="^",
    )
    ax.set_xlabel("Concurrent Agents")
    ax.set_ylabel("Rate (msg/s, tx/s, sessions/s)")
    ax.legend(frameon=False)
    ax.grid(axis="y", linestyle="--", alpha=0.4)
    fig.tight_layout()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, dpi=300)

    meta = {"generated_at": datetime.now(timezone.utc).isoformat()}
    meta_path = output_path.parent / "fig3c_meta.json"
    with meta_path.open("w", encoding="utf-8") as handle:
        handle.write(json_dumps(meta))


def build_tikz(summary, output_path: Path):
    rows = sorted(summary, key=lambda r: r["concurrency"])
    x_vals = [row["concurrency"] for row in rows]
    msg = [row["message_median"] for row in rows]
    settle = [row["settlement_median"] for row in rows]
    complete = [row["completed_median"] for row in rows]

    def coords(values):
        return " ".join(f"({x},{y:.3f})" for x, y in zip(x_vals, values))

    caption = (
        "Fig. 3: Performance evaluation of the Agent-OSI prototype. (c) Throughput: "
        "message throughput (msg/s), settlement throughput (tx/s), and completed-session "
        "throughput (sessions/s) under increasing concurrency."
    )

    tikz = f"""% Auto-generated by throughput.py
\\begin{{figure}}[t]
\\centering
\\begin{{tikzpicture}}
\\begin{{axis}}[
  width=8.6cm,
  height=4.6cm,
  xlabel={{Concurrent Agents}},
  ylabel={{Rate (msg/s, tx/s, sessions/s)}},
  legend style={{at={{(0.5,1.05)}},anchor=south,legend columns=-1}},
  ymajorgrids,
  grid style={{dashed,gray!30}},
  tick label style={{font=\\footnotesize}},
  label style={{font=\\footnotesize}},
  legend style={{font=\\footnotesize}},
]
\\addplot+[mark=o, color={{rgb,255:red,76;green,139;blue,184}}] coordinates {{{coords(msg)}}};
\\addplot+[mark=s, color={{rgb,255:red,215;green,124;blue,90}}] coordinates {{{coords(settle)}}};
\\addplot+[mark=^, color={{rgb,255:red,106;green,168;blue,79}}] coordinates {{{coords(complete)}}};
\\legend{{Message throughput (msg/s), Settlement throughput (tx/s), Completed sessions (sessions/s)}}
\\end{{axis}}
\\end{{tikzpicture}}
\\caption{{{caption}}}
\\end{{figure}}
"""

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(tikz, encoding="utf-8")


def json_dumps(payload):
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
