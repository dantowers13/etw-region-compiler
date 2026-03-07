"""etw-inspect — inspect pathfinding.esf and regions.esf structure."""
import click


@click.command()
@click.argument("esf_file", type=click.Path(exists=True))
@click.option("--region", help="Show details for a specific region name")
@click.option("--list-regions", is_flag=True, help="List all region names and cell IDs")
def cli(esf_file, region, list_regions):
    """Inspect an ETW ESF file — useful for Phase 0 reconnaissance."""
    raise NotImplementedError("Phase 0 tooling — implement after ESFReader is done")


if __name__ == "__main__":
    cli()
