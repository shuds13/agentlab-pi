# How this run works

## Work in cycles

A cycle spans several turns: you submit, end your turn, and resume when jobs finish.
Keep a cycle open until you have interpreted its data, then write it up and open the
next. Each cycle builds on the conclusion of the last, so a reader can follow what you
are pursuing and why.

1. **Observe.** What is known going in — data so far, the last cycle's conclusion.
2. **Hypothesize.** One concrete, falsifiable statement, naming the mechanism you think
   is responsible.
3. **Predict.** Write down what each planned job will show if the hypothesis holds, and
   what result would refute it, before you submit.
4. **Experiment.** Submit. Vary one thing per job, so a difference is attributable.
5. **Interpret.** What the data says.
6. **Close.** Write the cycle into both journals, then call `cycle_done` with what it
   established, before opening the next one.
   Then ask whether this run's goal is met. If it is, call `goal_met` with what
   settles it instead of opening another cycle.

Keep each step to a short block. A refuted hypothesis is a good cycle.

## Records

All in the shared area.

- **`results.jsonl`** — one JSON object per line, appended as each result lands. Correct
  an entry by appending a new line describing the change.
- **`LOGBOOK.md`** — your memory across runs, append-only, terse. What you tried, what it
  showed, what you concluded, which lines of enquiry are closed. You start each run with
  no memory of the last one, so anything not written here is lost. One line per cycle
  pointing at its journal section; the write-up itself goes in the journal.
- **`JOURNAL.md`** — the readable record. Append a section per cycle: the five steps, the
  figures with captions, and pointers to the `results.jsonl` lines behind each claim.
- **`JOURNAL.tex`** — the same record as one accumulating LaTeX document, so the run has
  a typeset report at any point.

Write the journal entry when a cycle's experiment finishes, not at the end of the run.

## Figures

A figure is an image file, not a description of one. Plot with matplotlib through `Bash`,
save under `journal/figures/` in the shared area, and reference it from both journal
files with a brief caption saying what it shows. Keep the plotting script beside its
figure so it can be redrawn when more data lands.

## The LaTeX journal

Create `JOURNAL.tex` on your first cycle if it does not exist, with this preamble:

```latex
\documentclass[11pt,a4paper]{article}
\usepackage{graphicx}
\usepackage{booktabs}
\usepackage[margin=1in]{geometry}
\usepackage{hyperref}
\graphicspath{{journal/figures/}}
\title{<campaign name>}
\date{\today}
\begin{document}
\maketitle
\end{document}
```

Then append one `\section` per cycle immediately before `\end{document}`, mirroring the
`JOURNAL.md` entry and using `\includegraphics` for its figures. Never rewrite an earlier
section, and never touch the preamble after creating it.

It must stay compilable. Run `pdflatex -interaction=nonstopmode JOURNAL.tex` in the
shared area after each append, and fix any error before continuing — a journal that no
longer builds is the one thing here you cannot leave for later. If `pdflatex` is not
installed, say so once in `LOGBOOK.md` and keep `JOURNAL.tex` correct anyway.

## Ending

Append a closing summary to `LOGBOOK.md`: the outcome, what explains it, what was
eliminated, and what is worth trying next.
