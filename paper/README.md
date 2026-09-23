# Paper

`paper.tex` — IEEE conference format (`IEEEtran.cls` is in this directory, so
no TeX package installation is needed beyond a distribution).

## Building

```bash
tectonic -X compile paper.tex --outdir .      # what was used here; one pass
```

or, with a traditional distribution, run it **three times** so cross-references
and the bibliography settle:

```bash
pdflatex paper.tex && pdflatex paper.tex && pdflatex paper.tex
```

The bibliography is an inline `thebibliography` block, so there is no BibTeX
step.

## Current state of the build

8 pages, IEEE two-column (`[conference]{IEEEtran}` gives `\columnwidth` 252pt
against `\textwidth` 516pt). Zero overfull `\hbox` warnings, zero undefined
references, zero undefined citations. Two underfull boxes remain, both from
unbreakable identifiers in the bibliography; they are cosmetic. The other
warnings are XeTeX font-shape substitutions (`TU/ptm/b/n` → `bx`), which appear
for any IEEEtran document compiled with XeTeX.

## Sections

Abstract · I Introduction · II Literature Review · III Methodology ·
IV Implementation Status and Preliminary Validation · V Conclusion ·
References (25 entries, all cited).

Fig. 1 (`fig:arch`) is a full-width `figure*` drawn in TikZ: the offline band
that runs once per rule and contains the model, the build band that runs on
every compilation and contains none, and the rule library as the only channel
between them. Dashed boxes mark components that are designed but not
implemented, so the figure carries the project's scope honestly rather than
depicting a system that does not exist yet. Editing it needs no image tooling —
it is TikZ source inside `paper.tex`.

Section IV is not in the requested outline but is not optional: the
Methodology describes a system that is roughly 30% built, and presenting it
without stating which parts are measured would misrepresent the work. It is
also where every number lives.

## Keeping the numbers honest

Every figure in Section IV is printed by a script in `../experiments/` and
serialised to `../results/*.json`. Before editing any number in the paper, run:

```bash
cd .. && python3 -m experiments.run_all
```

and copy from the output. If a number in the paper is not reproduced by that
command, it does not belong in the paper.
