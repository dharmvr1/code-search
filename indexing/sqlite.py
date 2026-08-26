import sqlite3
from parser.model import Symbol, SymbolEdge, SymbolNode
from pathlib import Path

DB_PATH = "code_symbols.db"


def init_sqlite_db(db_path: str = DB_PATH):
    """
    Creates the table for storing code symbols using your updated schema.
    """
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS symbols (
            id TEXT PRIMARY KEY,
            repo TEXT NOT NULL,
            path TEXT NOT NULL,
            language TEXT NOT NULL,
            kind TEXT NOT NULL,
            symbol TEXT NOT NULL,
            start_line INTEGER NOT NULL,
            end_line INTEGER NOT NULL,
            body TEXT NOT NULL,
            doc TEXT,
            content_hash TEXT NOT NULL
        );
    """)

    conn.commit()
    conn.close()


def sqlite_indexing(batch: list[Symbol], db_path: str = DB_PATH):
    """
    Bulk inserts a batch of Symbol objects into the SQLite database.
    Assumes your Symbol model has been updated with .body and .doc properties.
    """
    init_sqlite_db(db_path)

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # Extract the 11 fields to match the new schema
    data_to_insert = [
        (
            str(symbol.id),
            symbol.repo,
            symbol.path,
            symbol.language,
            symbol.kind,
            symbol.symbol,
            symbol.start_line,
            symbol.end_line,
            symbol.body,  # Added body
            symbol.doc,  # Added doc
            symbol.content_hash,
        )
        for symbol in batch
    ]

    # 11 columns require 11 placeholders (?)
    cursor.executemany(
        """
        INSERT OR REPLACE INTO symbols (
            id, repo, path, language, kind, symbol, 
            start_line, end_line, body, doc, content_hash
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """,
        data_to_insert,
    )

    conn.commit()
    conn.close()


def fetch_symbols_by_ids(ids: list[str], db_path: str = DB_PATH) -> list[Symbol]:
    """
    Fetches the full metadata, including body and docs, for a list of IDs
    and returns them as strongly-typed Symbol objects.
    """
    if not ids:
        return []

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    placeholders = ",".join("?" * len(ids))
    query = f"SELECT * FROM symbols WHERE id IN ({placeholders})"

    cursor.execute(query, ids)
    rows = cursor.fetchall()

    conn.close()

    # Unpack the row dictionary directly into the Symbol constructor
    return [Symbol(**dict(row)) for row in rows]


def fetch_symbol_by_path(repo: str, file_path: str, db_path: str = DB_PATH):
    """Gets all currently indexed symbols for a specific file."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    cursor.execute(
        "SELECT * FROM symbols WHERE repo = ? AND path = ?", (repo, file_path)
    )
    rows = cursor.fetchall()
    conn.close()

    return [Symbol(**dict(row)) for row in rows]


def delete_symbol_by_ids(symbol_ids: list):
    print(f"Deleting {len(symbol_ids)} removed symbols from")
    # Delete from Qdrant
    # client.delete(collection_name="my_codebase", points_selector=symbols_to_delete)
    # Delete from SQLite
    conn = sqlite3.connect("code_symbols.db")
    placeholders = ",".join("?" * len(symbol_ids))
    conn.execute(f"DELETE FROM symbols WHERE id IN ({placeholders})", symbol_ids)
    conn.commit()
    conn.close()


def save_symbol_edges(edges: list[SymbolEdge], db_path: str = DB_PATH):
    """
    Persistence pass: Performs batch insertion of SymbolEdge objects into SQLite.
    """
    if not edges:
        return

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    edge_rows = [(e.source_id, e.target_id, e.edge_type) for e in edges]

    cursor.executemany(
        """
        INSERT OR IGNORE INTO symbol_edges (source_id, target_id, edge_type)
        VALUES (?, ?, ?)
        """,
        edge_rows,
    )

    conn.commit()
    conn.close()
    print(f"Successfully saved {len(edges)} symbol edges to SQLite.")


def init_symbols_db(db_path: str = DB_PATH):
    """Creates the symbols and symbol_edges tables with indexes if they don't exist."""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    cursor.executescript("""
    CREATE TABLE IF NOT EXISTS symbols (
        id TEXT PRIMARY KEY,
        repo TEXT NOT NULL,
        path TEXT NOT NULL,
        symbol TEXT NOT NULL,
        kind TEXT NOT NULL,
        language TEXT NOT NULL,
        start_line INTEGER NOT NULL,
        end_line INTEGER NOT NULL,
        body TEXT NOT NULL,
        content_hash TEXT NOT NULL,
        parent_id TEXT,
        doc TEXT,
        FOREIGN KEY (parent_id) REFERENCES symbols(id) ON DELETE SET NULL
    );

    CREATE INDEX IF NOT EXISTS idx_symbols_lookup ON symbols (repo, path, symbol);
    CREATE INDEX IF NOT EXISTS idx_symbols_name ON symbols (repo, symbol);

    CREATE TABLE IF NOT EXISTS symbol_edges (
        source_id TEXT NOT NULL,
        target_id TEXT NOT NULL,
        edge_type TEXT NOT NULL,
        PRIMARY KEY (source_id, target_id, edge_type),
        FOREIGN KEY (source_id) REFERENCES symbols(id) ON DELETE CASCADE,
        FOREIGN KEY (target_id) REFERENCES symbols(id) ON DELETE CASCADE
    );

    CREATE INDEX IF NOT EXISTS idx_edges_outgoing ON symbol_edges (source_id, edge_type);
    CREATE INDEX IF NOT EXISTS idx_edges_incoming ON symbol_edges (target_id, edge_type);
    """)

    conn.commit()
    conn.close()


def save_symbol_nodes(nodes: list[SymbolNode], db_path: str = "code_symbols.db"):
    """
    Persistence pass: Performs batch upsert of SymbolNode objects into SQLite.
    """
    if not nodes:
        return

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # Convert SymbolNode dataclass objects to tuple rows
    node_rows = [
        (
            n.id,
            n.repo,
            n.path,
            n.symbol,
            n.kind,
            n.language,
            n.start_line,
            n.end_line,
            n.body,
            n.content_hash,
            n.parent_id,
            n.doc,
        )
        for n in nodes
    ]

    cursor.executemany(
        """
        INSERT OR REPLACE INTO symbols (
            id, repo, path, symbol, kind, language,
            start_line, end_line, body, content_hash,
            parent_id, doc
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        node_rows,
    )

    conn.commit()
    conn.close()
    print(f"Successfully saved {len(nodes)} symbol nodes to SQLite.")


def delete_symbol_graph_by_ids(symbol_ids: list[str], db_path: str = "code_symbols.db"):
    """
    Deletes symbol nodes and cleans up associated outgoing and incoming edges.
    """
    if not symbol_ids:
        return

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # Enable Foreign Keys to ensure ON DELETE CASCADE removes edges automatically
    cursor.execute("PRAGMA foreign_keys = ON;")

    # Batch delete symbols (Cascade removes edges where source_id or target_id matches)
    placeholders = ",".join(["?"] * len(symbol_ids))

    cursor.execute(
        f"DELETE FROM symbol_edges WHERE source_id IN ({placeholders}) OR target_id IN ({placeholders});",
        symbol_ids + symbol_ids,
    )
    cursor.execute(f"DELETE FROM symbols WHERE id IN ({placeholders});", symbol_ids)

    conn.commit()
    conn.close()


def fetch_graph_context_by_ids(
    seed_ids: list[str], db_path: str = DB_PATH, max_hops: int = 1
) -> dict:
    """
    Given seed symbol IDs, traverses outgoing (callees) and incoming (callers/parents)
    edges up to max_hops to gather context nodes.
    """
    if not seed_ids:
        return {"seed_ids": [], "callee_ids": [], "caller_ids": [], "parent_ids": []}

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    placeholders = ",".join(["?"] * len(seed_ids))

    # 1. Get Parent Class/Module IDs (CONTAINS edges)
    cursor.execute(
        f"""
        SELECT source_id FROM symbol_edges 
        WHERE target_id IN ({placeholders}) AND edge_type = 'CONTAINS'
        """,
        seed_ids,
    )
    parent_ids = [row[0] for row in cursor.fetchall()]

    # 2. Get Callees (Functions called BY these seed nodes)
    cursor.execute(
        f"""
        SELECT target_id FROM symbol_edges 
        WHERE source_id IN ({placeholders}) AND edge_type = 'CALLS'
        """,
        seed_ids,
    )
    callee_ids = [row[0] for row in cursor.fetchall()]

    # 3. Get Callers (Functions that CALL these seed nodes)
    cursor.execute(
        f"""
        SELECT source_id FROM symbol_edges 
        WHERE target_id IN ({placeholders}) AND edge_type = 'CALLS'
        """,
        seed_ids,
    )
    caller_ids = [row[0] for row in cursor.fetchall()]

    conn.close()

    return {
        "seed_ids": seed_ids,
        "parent_ids": list(set(parent_ids)),
        "callee_ids": list(set(callee_ids)),
        "caller_ids": list(set(caller_ids)),
    }
