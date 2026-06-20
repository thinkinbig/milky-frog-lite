from __future__ import annotations

from rich.table import Table
from rich.text import Text

from milky_frog.diagnostics import CheckStatus, Diagnostic
from milky_frog.ui.presenter._base import _Surface

_CHECK_STYLES = {
    CheckStatus.PASS: "bold green",
    CheckStatus.WARN: "bold yellow",
    CheckStatus.FAIL: "bold red",
}


class _DiagnosticsSurface(_Surface):
    def diagnostics(self, diagnostics: tuple[Diagnostic, ...]) -> None:
        table = Table(title="Milky Frog doctor", header_style="bold")
        table.add_column("Status", no_wrap=True)
        table.add_column("Check")
        table.add_column("Value")
        for diagnostic in diagnostics:
            status = Text(diagnostic.status, style=_CHECK_STYLES[diagnostic.status])
            table.add_row(status, diagnostic.name, diagnostic.value)
        self.out.print(table)

        failed = sum(item.status is CheckStatus.FAIL for item in diagnostics)
        warned = sum(item.status is CheckStatus.WARN for item in diagnostics)
        if failed:
            self.out.print(
                Text(f"Doctor found {failed} failure(s) and {warned} warning(s).", style="red")
            )
        elif warned:
            self.out.print(Text(f"Doctor passed with {warned} warning(s).", style="yellow"))
        else:
            self.out.print(Text("Doctor passed.", style="green"))
