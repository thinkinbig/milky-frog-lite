"""Read-noise pilot: SWE-bench context-efficiency benchmark for the Harness.

Three stages, wired only through the typed artifacts in ``schema`` — never raw
dicts:

    sample  (SWE-bench Verified -> tasks)   python -m evals.read_noise.sample
    run     (tasks -> per-Run read records) python -m evals.read_noise.run
    score   (records -> footprint / waste)  python -m evals.read_noise.score

Design and rationale: ``docs/evals/read-noise-pilot-design.md``.
"""
