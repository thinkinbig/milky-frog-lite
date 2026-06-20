# Separate human output by stream

Milky Frog writes normal human-readable results (including diagnostics) to stdout and errors to stderr, while `--json` emits only machine-readable JSON on stdout. Styled output respects `NO_COLOR`, preserving a polished Terminal UI without breaking pipes, redirection, or automation.
