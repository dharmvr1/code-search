from pathlib import Path
from git import Git, Repo
import os
from indexing.sqlite import (
    fetch_symbol_by_path,
    delete_symbol_graph_by_ids,
    save_symbol_nodes,
    save_symbol_edges,
)
from graph import build_symbol_edges
from parser.parser import full_parser, batch_stream
from indexing.qdrant import qdrant_delete_ids
from config import COLLECTION_NAME
from indexing.embed import embed_batch
from indexing.builder import build_embedding_batch
from indexing.index import indexing
from parser.model import SymbolNode, Symbol

SUPPORTED = {
    ".py",
    ".rs",
    ".go",
    ".ts",
    ".tsx",
    ".js",
    ".java",
    ".cpp",
    ".c",
}


IGNORE = {
    ".git",
    "node_modules",
    "dist",
    "build",
    "target",
    "__pycache__",
}

git = Git()


url = "https://github.com/rust-lang/rustup"


def clone_repo(url: str):
    REPO_ROOT = Path("repo")
    full_path = Path(os.getcwd() + f"/{REPO_ROOT}")
    full_path.mkdir(exist_ok=True)

    repo_name = url.rstrip("/").split("/")[-1].replace(".git", "")
    destination = Path(f"{full_path}/{repo_name}")

    if not destination.exists():
        print(f"cloning repo {url}")

        repo = Repo.clone_from(url=url, to_path=destination)

        new_commit = repo.head.commit.hexsha
        print("done")
        return destination, None, new_commit

    print(f"{repo_name} already cloned.")
    repo = Repo(destination)

    old_commit = repo.head.commit.hexsha

    origin = repo.remotes.origin
    origin.pull()
    new_commit = repo.head.commit.hexsha

    return destination, old_commit, new_commit


def iter_source_file(root_path: str | Path):
    if not isinstance(root_path, Path):
        root_path = Path(root_path)

    for path in root_path.rglob("*"):

        if not path.is_file():

            continue

        if any(part in IGNORE for part in path.parts):

            continue

        if path.suffix in SUPPORTED:

            yield path


destination, old_commit, new_commit = clone_repo(url)


def get_changed_file(
    destination_file_path: Path, old_commit_hash: str, new_commit_hash: str
):
    if not old_commit_hash:
        return
    repo = Repo(destination_file_path)
    old_commit = repo.commit(old_commit_hash)
    new_commit = repo.commit(new_commit_hash)

    differ = old_commit.diff(new_commit)

    files = set()
    for change in differ:
        files.add(change.a_path or change.b_path)

    return list(files)


from pathlib import Path


def diff_symbols(repo: str, file_path: str):
    """
    Diffs both vector chunks (Symbol) and graph nodes (SymbolNode) for a changed file.

    Returns:
        - symbols_to_delete: List of symbol IDs no longer present in the file
        - chunks_to_upsert: List of modified/added vector Symbol chunks
        - nodes_to_upsert: List of modified/added graph SymbolNode objects
    """
    # 1. Fetch existing symbols from SQLite for this file path
    old_symbols = fetch_symbol_by_path(repo=repo, file_path=file_path)
    old_map = {sym.id: sym for sym in old_symbols}

    new_chunks: list[Symbol] = []
    new_nodes: list[SymbolNode] = []

    # 2. Parse file if it still exists (if deleted, new_chunks and new_nodes stay empty)
    path_obj = Path(file_path)
    if path_obj.exists():
        new_chunks, new_nodes = full_parser(path_obj)

    new_chunks_map = {sym.id: sym for sym in new_chunks}
    new_nodes_map = {node.id: node for node in new_nodes}

    symbols_to_delete = []
    chunks_to_upsert = []
    nodes_to_upsert = []

    # 3. Detect Deletions: Symbols that existed previously but are missing now
    for old_id in old_map:
        if old_id not in new_chunks_map and old_id not in new_nodes_map:
            symbols_to_delete.append(old_id)

    # 4. Detect Vector Chunk Additions & Modifications (for Qdrant)
    for chunk_id, chunk in new_chunks_map.items():
        if chunk_id not in old_map:
            # Brand new chunk
            chunks_to_upsert.append(chunk)
        elif old_map[chunk_id].content_hash != chunk.content_hash:
            # Code content changed
            chunks_to_upsert.append(chunk)

    # 5. Detect Graph Node Additions & Modifications (for SQLite Graph)
    for node_id, node in new_nodes_map.items():
        if node_id not in old_map:
            nodes_to_upsert.append(node)
        elif old_map[node_id].content_hash != node.content_hash:
            nodes_to_upsert.append(node)

    return symbols_to_delete, chunks_to_upsert, nodes_to_upsert


def apply_symbol_changes(
    symbols_to_delete: list[str],
    nodes_to_upsert: list[SymbolNode],
    chunks_to_upsert: list[Symbol],
    file_imports_map: dict = None,
    db_path: str = "code_symbols.db",
):
    if file_imports_map is None:
        file_imports_map = {}

    # =========================================================================
    # A. Handle Deletions (Qdrant Vectors + SQLite Graph)
    # =========================================================================
    if symbols_to_delete:
        print(
            f"Deleting {len(symbols_to_delete)} stale symbols from Qdrant and Graph..."
        )

        # 1. Delete vectors from Qdrant
        qdrant_delete_ids(symbols_to_delete, collection_name=COLLECTION_NAME)

        # 2. Delete nodes and cascade-delete edges from SQLite
        delete_symbol_graph_by_ids(symbols_to_delete, db_path=db_path)

    # =========================================================================
    # B. Handle Upserts (Additions & Modifications)
    # =========================================================================
    if chunks_to_upsert:
        print(
            f"Embedding and upserting {len(chunks_to_upsert)} vector chunks to Qdrant..."
        )
        symbol_batch = batch_stream(chunks_to_upsert)

        for batch in symbol_batch:
            text_batch = build_embedding_batch(batch)
            embeddings = embed_batch(text_batch)
            indexing(batch, embeddings)

    if nodes_to_upsert:
        print(f"Upserting {len(nodes_to_upsert)} symbol nodes to SQLite Graph...")

        # 1. Save / Update Symbol Nodes in SQLite
        save_symbol_nodes(nodes_to_upsert, db_path=db_path)

        # 2. Re-resolve and Save Edges for newly upserted nodes
        new_edges = build_symbol_edges(
            nodes_to_upsert, file_imports_map=file_imports_map
        )
        save_symbol_edges(new_edges, db_path=db_path)


def incremental_indexing(repo_path: str, old_commit_hash: str, new_commit_hash: str):

    repo_name = repo_path.split("/")[-1]

    changed_files = get_changed_file(
        repo_path, old_commit_hash=old_commit_hash, new_commit_hash=new_commit_hash
    )

    for file in changed_files:
        symbols_to_delete, chunks_to_upsert, nodes_to_upsert = diff_symbols(
            repo_name, file
        )
        apply_symbol_changes(
            symbols_to_delete=symbols_to_delete,
            nodes_to_upsert=nodes_to_upsert,
            chunks_to_upsert=chunks_to_upsert,
            file_imports_map={},
        )
