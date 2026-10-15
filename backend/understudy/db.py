import os

from falkordb import FalkorDB


def connect(port: int | None = None) -> FalkorDB:
    return FalkorDB(
        host=os.environ.get("FALKORDB_HOST", "localhost"),
        port=port or int(os.environ.get("FALKORDB_PORT", "6379")),
    )


def drop(db: FalkorDB, name: str) -> None:
    if name in db.list_graphs():
        db.select_graph(name).delete()
