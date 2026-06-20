from rich.console import Console

# Shared width for the bordered interactive surfaces (welcome panel, prompt box, …)
# so every full-width element lines up to the same left/right edges.
BOX_WIDTH = 92

console = Console()
error_console = Console(stderr=True)
