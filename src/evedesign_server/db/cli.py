import click
from evedesign_server.db.engine import create_all

@click.group()
def cli():
    pass


@cli.command("create-all")
def create_all_cmd():
    create_all()


if __name__ == "__main__":
    cli()
