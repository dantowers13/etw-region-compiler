"""
etw-region-add — main CLI entry point.
Implement per ETWPC-24.
"""
import click


@click.command()
@click.option("--pathfinding", required=True, type=click.Path(exists=True), help="Path to pathfinding.esf")
@click.option("--regions",     required=True, type=click.Path(exists=True), help="Path to regions.esf")
@click.option("--map-tga",     required=True, type=click.Path(exists=True), help="Path to map_regions.tga")
@click.option("--new-region",  required=True, help="Name for the new region (e.g. jutland)")
@click.option("--new-colour",  required=True, help="Unique RGB colour for new region, e.g. '255,128,64'")
@click.option("--parent",      required=True, help="Name of the parent region being split")
@click.option("--new-cell-id", type=int, default=None, help="cell_id for new region (auto-assigned if omitted)")
@click.option("--output-dir",  default="./mod_output", help="Directory for modified output files")
@click.option("--dry-run",     is_flag=True, help="Run all stages but write no files")
@click.option("--verbose",     is_flag=True, help="Verbose output")
def cli(pathfinding, regions, map_tga, new_region, new_colour, parent,
        new_cell_id, output_dir, dry_run, verbose):
    """Add a new region to the ETW campaign map by splitting an existing region."""
    raise NotImplementedError("ETWPC-24: implement pipeline integration")


if __name__ == "__main__":
    cli()
