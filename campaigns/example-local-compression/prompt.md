# Minimising delivery time for a compressed corpus

Find the zlib configuration that gets the benchmark corpus to a consumer in the least
total time on this machine, and explain what sets the limit.

Delivery time is the sum of two competing halves:

```
total_seconds = encode_seconds + compressed_bytes / (5 MB/s)
```

Compressing harder shrinks the payload, so the transmit half falls — but the encode half
rises, and past some point it rises faster than the transmit half falls. The best
configuration is where those two curves cross, and finding it is the run's purpose.
`total_seconds` is the number to minimise; every job returns both halves separately so
you can see which one you are trading against.

The run produces two things: the fastest configuration, and an account of what limits it.

## Fixed

The corpus (8 MB, rebuilt from a fixed seed each job) and the 5 MB/s bandwidth in the
cost model. These keep results comparable across jobs.

## Variable

`level`, `strategy`, `memlevel`, `windowlog`. `submit_local` describes each.

## Leads, roughly by expected payoff

These are hypotheses. Confirm one against your own results before drawing a conclusion
from it.

1. **Level.** The dominant control, and where the trade-off lives. Establish the shape
   of the curve before tuning anything else — the interesting region is wherever
   `encode_seconds` starts growing faster than `transmit_seconds` shrinks.
2. **Strategy.** `huffman_only` and `rle` skip most match-finding, so they encode fast
   and compress poorly. At this bandwidth that may or may not pay. Note that strategy
   interacts with level rather than acting independently of it.
3. **Window size.** `windowlog` bounds how distant a repeat can be matched. The corpus
   is built from a small vocabulary, so most matches are near — test whether the largest
   window earns its cost.
4. **memlevel.** Encoder working memory. Expect a smaller effect than the other three;
   worth one or two jobs once the rest is settled.

## Budget

One job at a time. Each takes a few seconds. Collect the outstanding result before
submitting work whose choice depends on it.

## Baseline

`level=6, strategy=default, memlevel=8, windowlog=15` is zlib's default. Measure it
first — it is the reference every later result is compared against.

## When to stop

Stop and write your conclusion when either holds:

- **You have a tested explanation.** You can say which half of the cost model binds and
  why, a prediction drawn from that held, and you can state what would have refuted it.
- **The leads are exhausted.** Report the best configuration, what was eliminated, and
  the evidence that eliminated it.

## Recording

Append one line per job to `results.jsonl` in the workspace: the arguments and the
returned metrics. Keep `LOGBOOK.md` as the running account — what you tried, what it
showed, what you concluded, and what you are doing next.

## Deliverable: SKILL.md

Write `SKILL.md` in the workspace: guidance for someone choosing compression settings
for a different corpus and a different bandwidth. Draft it after your first confirmed
finding and revise as more land.

Include how to measure, which settings mattered in order with the measurement showing
it, the best configuration found and its number, and what was ruled out with the
evidence that ruled it out. Every claim comes from a run you did. Say which parts are
specific to this corpus and bandwidth, and which you expect to carry over.
