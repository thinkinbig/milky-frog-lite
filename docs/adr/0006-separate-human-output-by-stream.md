# Separate human output by stream

Milky Frog writes normal human-readable results to stdout and errors or diagnostics to stderr, while `--json` emits only machine-readable JSON on stdout. Styled output is enabled only for an interactive terminal and respects `NO_COLOR`, preserving a polished Terminal UI without breaking pipes, redirection, or automation.
