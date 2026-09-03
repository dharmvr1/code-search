from parser.parser import batch_stream, generate_repo_chunks

from pathlib import Path
from indexing.builder import build_embedding_batch
from indexing.embed import embed_batch
from sentence_transformers import SentenceTransformer
from indexing.index import indexing
from parser.parser import full_parser
from clone import iter_source_file
from parser.model import SymbolNode
from graph import build_symbol_edges
from indexing.sqlite import init_symbols_db, save_symbol_edges, save_symbol_nodes

model = SentenceTransformer("Qwen/Qwen3-Embedding-0.6B")


folder_repo = "/repo/"
root_folder = Path.cwd()


def main():
    folder_path = Path(f"{root_folder}{folder_repo}")

    # 1. Initialize SQLite database tables
    db_path = "code_symbols.db"
    init_symbols_db(db_path)

    all_repo_nodes: list[SymbolNode] = []

    def chunk_generator():
        nonlocal all_repo_nodes
        for file_path in iter_source_file(folder_path):
            chunks, symbol_nodes = full_parser(file_path)
            all_repo_nodes.extend(symbol_nodes)

            # Yield individual chunks to feed your embedding batcher
            for chunk in chunks:
                yield chunk

    # 2. Process Vector Chunks for Vector Search / Qdrant
    batch_chunks = batch_stream(chunk_generator())

    for batch in batch_chunks:
        text_batch = build_embedding_batch(batch)
        embeddings = embed_batch(text_batch)
        indexing(batch, embeddings)

    # 3. Process Code Graph for SQLite (Runs once all nodes exist)
    print(f"Indexing {len(all_repo_nodes)} symbol nodes into SQLite...")

    # Save Symbol Nodes
    save_symbol_nodes(all_repo_nodes, db_path=db_path)

    # Build and Save Graph Edges
    # (Pass file_imports_map if you have extracted import aliases)
    edges = build_symbol_edges(all_repo_nodes, file_imports_map={})
    save_symbol_edges(edges, db_path=db_path)

    print(
        "Pipeline completed successfully: Embeddings and Symbol Graph are fully indexed!"
    )


if __name__ == "__main__":
    main()
