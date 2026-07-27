"""Make sibling preprocessing packages importable by direct runner paths."""

from pathlib import Path
import sys


def configure_runner_import_path() -> None:
    """Add the runner and Neo4j package directories to ``sys.path``."""
    runner_directory = Path(__file__).resolve().parent
    neo4j_directory = runner_directory.parent
    for directory in [runner_directory, neo4j_directory]:
        directory_text = str(directory)
        if directory_text not in sys.path:
            sys.path.insert(0, directory_text)


configure_runner_import_path()
