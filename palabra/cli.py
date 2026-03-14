"""palabra CLI — protect Word comment anchors."""

import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table

from palabra.protect import protect_file, LOG_FILE, LOG_DIR

console = Console()


@click.group()
def main():
    """palabra — protect Word comment anchors from silent deletion."""
    pass


@main.command()
@click.argument("filepath", type=click.Path(exists=True))
def protect(filepath):
    """Protect comment anchors in a .docx file."""
    filepath = os.path.abspath(filepath)
    try:
        total, newly_wrapped, already_protected = protect_file(filepath)
    except Exception as e:
        console.print(f"[bold red]Error:[/bold red] {e}")
        sys.exit(1)

    console.print(f"[bold green]Done.[/bold green] {total} anchor(s) found, "
                  f"{newly_wrapped} newly wrapped, {already_protected} already protected.")


@main.command()
@click.argument("folder", type=click.Path(exists=True, file_okay=False))
def watch(folder):
    """Watch a folder for .docx changes and auto-protect."""
    from watchdog.observers import Observer
    from watchdog.events import FileSystemEventHandler

    folder = os.path.abspath(folder)

    class DocxHandler(FileSystemEventHandler):
        def on_modified(self, event):
            if event.is_directory:
                return
            if not event.src_path.endswith(".docx"):
                return
            # Skip temp files Word creates
            basename = os.path.basename(event.src_path)
            if basename.startswith("~$") or basename.startswith(".~"):
                return

            ts = datetime.now().strftime("%H:%M:%S")
            try:
                total, newly_wrapped, already_protected = protect_file(event.src_path)
                if newly_wrapped > 0:
                    console.print(
                        f"[dim][{ts}][/dim] protected {newly_wrapped} comment anchor(s) "
                        f"in [bold]{basename}[/bold]"
                    )
                else:
                    console.print(
                        f"[dim][{ts}][/dim] skipped {basename} — all anchors already protected"
                    )
            except Exception as e:
                console.print(f"[dim][{ts}][/dim] [red]error processing {basename}: {e}[/red]")

    observer = Observer()
    observer.schedule(DocxHandler(), folder, recursive=True)
    observer.start()

    console.print(f"[bold]palabra[/bold] watching {folder} — ctrl+c to stop")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
    observer.join()


@main.command()
@click.argument("filepath", type=click.Path(exists=True))
def history(filepath):
    """Show comment history from the sidecar snapshot."""
    filepath = os.path.abspath(filepath)
    sidecar_path = os.path.splitext(filepath)[0] + "-comments-snapshot.json"

    if not os.path.isfile(sidecar_path):
        console.print(
            f"[yellow]No snapshot found.[/yellow] Run [bold]palabra protect {filepath}[/bold] first."
        )
        sys.exit(1)

    with open(sidecar_path) as f:
        data = json.load(f)

    if not data:
        console.print("[yellow]No comments found in snapshot.[/yellow]")
        return

    table = Table(title="Comment History")
    table.add_column("Author", style="cyan")
    table.add_column("Date", style="green")
    table.add_column("Anchor Text", style="white")
    table.add_column("Comment Text", style="yellow")

    for entry in data:
        table.add_row(
            entry.get("author", ""),
            entry.get("date", ""),
            entry.get("anchor_text", ""),
            entry.get("comment_text", ""),
        )

    console.print(table)


@main.command()
def log():
    """Show recent activity log."""
    if not LOG_FILE.is_file():
        console.print("[yellow]No activity log found yet.[/yellow]")
        return

    with open(LOG_FILE) as f:
        lines = f.readlines()

    last_50 = lines[-50:]
    if not last_50:
        console.print("[yellow]Activity log is empty.[/yellow]")
        return

    table = Table(title="Activity Log (last 50)")
    table.add_column("Entry", style="white")

    for line in last_50:
        table.add_row(line.strip())

    console.print(table)
