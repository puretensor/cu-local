#!/usr/bin/env python3
"""Render bench-results/*.json into assets/bench.svg (light + dark via prefers-color-scheme).

Each JSON: {"label": str, "slot": 1-4, "note": str, "results": [{"task", "seconds", "ok"}, ...]}
Bar = median seconds of the passing runs; the label says how many passed.
Usage: python3 scripts/render-bench.py [bench-results] [assets/bench.svg]
"""
import glob
import json
import os
import statistics
import sys
from xml.sax.saxutils import escape

SRC = sys.argv[1] if len(sys.argv) > 1 else "bench-results"
OUT = sys.argv[2] if len(sys.argv) > 2 else "assets/bench.svg"
TASKS = [("finder", "Finder: create and rename a folder"),
         ("calculator", "Calculator: 48271 × 9137"),
         ("textedit", "TextEdit: type and save a plain-text file")]
# Categorical slots, fixed order (validated adjacent-pair CVD-safe, light and dark).
LIGHT = {1: "#2a78d6", 2: "#eb6834", 3: "#1baf7a", 4: "#eda100"}
DARK = {1: "#3987e5", 2: "#d95926", 3: "#199e70", 4: "#c98500"}

series = []
for p in sorted(glob.glob(os.path.join(SRC, "*.json"))):
    d = json.load(open(p))
    if "slot" in d:
        series.append(d)
series.sort(key=lambda d: d["slot"])
if not series:
    sys.exit("no series in %s" % SRC)

def cell(d, task):
    runs = [r for r in d["results"] if r["task"] == task]
    ok = [r["seconds"] for r in runs if r["ok"]]
    return (statistics.median(ok) if ok else None), len(ok), len(runs)

vmax = max([c[0] for d in series for t, _ in TASKS for c in [cell(d, t)] if c[0]] or [60])
step = 30 if vmax > 90 else 15
axis_max = step * (int(vmax // step) + 1)

W, left, right = 1000, 40, 150
plot_w = W - left - right
bar_h, gap = 18, 2
group_h = len(series) * (bar_h + gap) - gap
legend_y = 92
top = 150
group_gap = 56
H = top + len(TASKS) * (group_h + group_gap) + 24

def x(v):
    return left + plot_w * v / axis_max

o = []
o.append('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 %d %d" width="%d" height="%d" role="img" aria-labelledby="bt bd">' % (W, H, W, H))
o.append('<title id="bt">Seconds per computer-use task, lower is better</title>')
desc = "; ".join("%s: %s" % (d["label"], ", ".join(
    "%s %s" % (t, ("%.0f s" % cell(d, t)[0]) if cell(d, t)[0] else "failed") for t, _ in TASKS)) for d in series)
o.append('<desc id="bd">%s</desc>' % escape(desc))
css = ["text{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif}",
       ".t1{fill:#1F2328}.t2{fill:#59636E}.grid{stroke:#D1D9E0}.base{stroke:#818B98}"]
css += [".s%d{fill:%s}" % (k, v) for k, v in LIGHT.items()]
css.append("@media (prefers-color-scheme: dark){.t1{fill:#E6EDF3}.t2{fill:#9198A1}.grid{stroke:#3D444D}.base{stroke:#6E7681}"
           + "".join(".s%d{fill:%s}" % (k, v) for k, v in DARK.items()) + "}")
o.append("<style>%s</style>" % "".join(css))

o.append('<text class="t1" x="%d" y="30" font-size="20" font-weight="600">Seconds per task, lower is better</text>' % left)
o.append('<text class="t2" x="%d" y="52" font-size="14">Median of passing runs on the same Mac. A label notes when some runs failed.</text>' % left)

lx = left
for d in series:
    o.append('<rect class="s%d" x="%d" y="%d" width="14" height="14" rx="3"/>' % (d["slot"], lx, legend_y - 11))
    o.append('<text class="t1" x="%d" y="%d" font-size="14">%s</text>' % (lx + 20, legend_y + 1, escape(d["label"])))
    lx += 44 + int(len(d["label"]) * 7.1)

bottom = top + len(TASKS) * (group_h + group_gap) - group_gap + 6
gy = top
for _ in TASKS:
    for v in range(step, axis_max + 1, step):
        o.append('<line class="grid" x1="%.1f" y1="%d" x2="%.1f" y2="%d" stroke-width="1"/>' % (x(v), gy - 4, x(v), gy + group_h + 4))
    gy += group_h + group_gap
for v in range(0, axis_max + 1, step):
    o.append('<text class="t2" x="%.1f" y="%d" font-size="13" text-anchor="middle">%d s</text>' % (x(v), bottom + 18, v))

y = top
for task, title in TASKS:
    o.append('<text class="t1" x="%d" y="%d" font-size="15" font-weight="600">%s</text>' % (left, y - 10, escape(title)))
    for d in series:
        med, ok, n = cell(d, task)
        if med:
            w = max(x(med) - left, 6)
            r = min(4, w / 2)
            o.append('<path class="s%d" d="M%d %d h%.1f a%.1f %.1f 0 0 1 %.1f %.1f v%.1f a%.1f %.1f 0 0 1 -%.1f %.1f h-%.1f z"><title>%s, %s: %.1f s (%d/%d passed)</title></path>' % (
                d["slot"], left, y, w - r, r, r, r, r, bar_h - 2 * r, r, r, r, r, w - r,
                escape(d["label"]), task, med, ok, n))
            label = "%.0f s" % med + ("" if ok == n else " (%d of %d passed)" % (ok, n))
            o.append('<text class="t1" x="%.1f" y="%d" font-size="13">%s</text>' % (left + w + 8, y + 13, label))
        else:
            o.append('<text class="t2" x="%d" y="%d" font-size="13">did not pass (0/%d)</text>' % (left + 8, y + 13, n))
        y += bar_h + gap
    o.append('<line class="base" x1="%d" y1="%d" x2="%d" y2="%d" stroke-width="1.5"/>' % (left, y - group_h - gap - 2, left, y))
    y += group_gap - gap
o.append("</svg>")
open(OUT, "w").write("\n".join(o) + "\n")
print("wrote %s (%d series, axis %d s)" % (OUT, len(series), axis_max))

# The same numbers as a table (the chart's text equivalent) between README markers.
def tokens(d):
    runs = [r for r in d["results"] if r.get("input_tokens")]
    return statistics.median(r["input_tokens"] for r in runs) if runs else None

rows = ["| Configuration | Finder | Calculator | TextEdit | Passed | Input tokens per task (median, mostly cache hits) |",
        "|---|---:|---:|---:|---:|---:|"]
for d in series:
    cells = []
    for t, _ in TASKS:
        med, ok, n = cell(d, t)
        cells.append("%.0f s" % med if med else "failed")
    passed = sum(1 for r in d["results"] if r["ok"])
    tk = tokens(d)
    rows.append("| %s | %s | %s | %s | %d/%d | %s |" % (
        d["label"], cells[0], cells[1], cells[2], passed, len(d["results"]), "{:,.0f}".format(tk) if tk else "n/a"))
table = "\n".join(rows)
readme = "README.md"
if os.path.exists(readme):
    s = open(readme).read()
    start, end = "<!-- bench-table -->", "<!-- /bench-table -->"
    if start in s and end in s:
        s = s[:s.index(start) + len(start)] + "\n" + table + "\n" + s[s.index(end):]
        open(readme, "w").write(s)
        print("updated README table")
print(table)
