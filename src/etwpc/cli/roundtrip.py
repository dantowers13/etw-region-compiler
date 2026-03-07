"""
etw-roundtrip — the round-trip fidelity test.
Read an ESF file, write it back, verify byte-for-byte identity.
This is the non-negotiable gate test (ETWPC-26).
"""
import sys
import click


@click.command()
@click.argument("esf_file", type=click.Path(exists=True))
@click.option("--output", default=None, help="Write round-tripped file here (default: temp file)")
def cli(esf_file, output):
    """Round-trip an ESF file: read → write → binary diff. Must be byte-perfect."""
    from rich.console import Console
    console = Console()
    console.print(f"[bold]Round-trip test:[/bold] {esf_file}")
    console.print("[yellow]ESFReader not yet implemented (ETWPC-11)[/yellow]")
    sys.exit(1)


if __name__ == "__main__":
    cli()
