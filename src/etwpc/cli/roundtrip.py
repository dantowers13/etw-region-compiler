"""
etw-roundtrip — the round-trip fidelity gate test (ETWPC-26).

Read an ESF file, write it back unmodified, binary-diff the output.
Must be byte-for-byte identical before any in-game testing is valid.
"""
import sys
import tempfile
from pathlib import Path

import click
from rich.console import Console


@click.command()
@click.argument("esf_file", type=click.Path(exists=True))
@click.option("--output", "-o", default=None, help="Write round-tripped file here instead of a temp file")
@click.option("--verbose", "-v", is_flag=True, help="Show first differing bytes")
def cli(esf_file: str, output: str | None, verbose: bool) -> None:
    """Round-trip an ESF file: read → write → binary diff.  Must be byte-perfect."""
    from etwpc.io.esf_reader import ESFReader
    from etwpc.io.esf_writer import ESFWriter

    console = Console()
    src = Path(esf_file)
    console.print(f"[bold]Round-trip test:[/bold] {src}")

    # ── Read ──────────────────────────────────────────────────────────────────
    console.print("  Reading...", end=" ")
    try:
        reader = ESFReader(src)
        root   = reader.read_root()
        console.print("[green]OK[/green]")
    except Exception as exc:
        console.print(f"[red]FAIL[/red] — {exc}")
        sys.exit(1)

    # ── Write ─────────────────────────────────────────────────────────────────
    console.print("  Writing...", end=" ")
    try:
        writer = ESFWriter(root, reader.tag_names, reader.timestamp)
        result = writer.to_bytes()
        console.print("[green]OK[/green]")
    except Exception as exc:
        console.print(f"[red]FAIL[/red] — {exc}")
        sys.exit(1)

    # ── Optionally save ───────────────────────────────────────────────────────
    if output:
        Path(output).write_bytes(result)
        console.print(f"  Written to: {output}")

    # ── Binary diff ───────────────────────────────────────────────────────────
    original = src.read_bytes()
    if result == original:
        console.print(f"[bold green]PASS[/bold green] — 0 bytes differ ({len(original):,} bytes total)")
        sys.exit(0)

    # Find first difference
    n_diff = sum(a != b for a, b in zip(original, result))
    size_diff = len(result) - len(original)
    console.print(
        f"[bold red]FAIL[/bold red] — {n_diff} bytes differ, "
        f"size delta {size_diff:+d} bytes "
        f"(original {len(original):,}, output {len(result):,})"
    )

    if verbose:
        for i, (a, b) in enumerate(zip(original, result)):
            if a != b:
                ctx_start = max(0, i - 8)
                console.print(f"  First diff at offset {i:#x}:")
                console.print(f"    original: {original[ctx_start:i+9].hex()}")
                console.print(f"    output:   {result[ctx_start:i+9].hex()}")
                break

    sys.exit(1)


if __name__ == "__main__":
    cli()
