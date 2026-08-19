from parser.model import SymbolNode, SymbolEdge
from hashlib import sha256
from typing import Optional


def extract_calls_from_node(node, code_bytes: bytes, lang_key: str) -> list[str]:
    """Helper to scan any AST subtree (or statement list) for function calls."""
    calls = set()
    CALL_TYPES = {
        "python": {"call"},
        "rust": {"call_expression"},
        "javascript": {"call_expression"},
    }
    target_types = CALL_TYPES.get(lang_key, {"call", "call_expression"})

    def _walk(curr_node):
        if curr_node.type in target_types:
            func_child = curr_node.child_by_field_name("function")
            if func_child:
                name = code_bytes[func_child.start_byte : func_child.end_byte].decode(
                    "utf-8", errors="ignore"
                )
                calls.add(name)
        for c in curr_node.children:
            _walk(c)

    _walk(node)
    return list(calls)


def build_symbol_edges(
    nodes: list["SymbolNode"],
    file_imports_map: Optional[dict[str, dict[str, str]]] = None,
) -> list[SymbolEdge]:
    """
    Pure logic pass: Builds structural (CONTAINS) and behavioral (CALLS) edges
    from in-memory SymbolNodes without executing DB operations.

    :param nodes: List of SymbolNode objects.
    :param file_imports_map: Mapping of file_path -> {imported_symbol: source_file_path}
    :return: List of resolved SymbolEdge objects.
    """
    if file_imports_map is None:
        file_imports_map = {}

    edges: list[SymbolEdge] = []

    # Fast in-memory lookup indexes
    # 1. (repo, path, symbol_name) -> symbol_id
    exact_location_index: dict[tuple[str, str, str], str] = {}
    # 2. (repo, symbol_name) -> list[symbol_id]
    global_name_index: dict[tuple[str, str], list[str]] = {}

    for node in nodes:
        exact_location_index[(node.repo, node.path, node.symbol)] = node.id

        repo_key = (node.repo, node.symbol)
        global_name_index.setdefault(repo_key, []).append(node.id)

    for node in nodes:
        # =========================================================================
        # 1. Structural Edges (Parent -> Child)
        # =========================================================================
        if node.parent_id:
            edges.append(
                SymbolEdge(
                    source_id=node.parent_id, target_id=node.id, edge_type="CONTAINS"
                )
            )

        # =========================================================================
        # 2. Behavioral Edges (Caller -> Callee)
        # =========================================================================
        if not node.calls:
            continue

        repo = node.repo
        path = node.path
        imports = file_imports_map.get(path, {})

        for call_name in node.calls:
            target_id = None

            # --- Tier 1: Same-File Match ---
            target_id = exact_location_index.get((repo, path, call_name))

            # --- Tier 2: Import-Assisted Match ---
            if not target_id and call_name in imports:
                imported_path = imports[call_name]
                target_id = exact_location_index.get((repo, imported_path, call_name))

            # --- Tier 3: Global Repo Fallback Match ---
            if not target_id:
                matches = global_name_index.get((repo, call_name), [])
                # Link unambiguously only if exactly 1 match exists globally
                if len(matches) == 1:
                    target_id = matches[0]

            # Append resolved call edge (avoiding self-loops)
            if target_id and target_id != node.id:
                edges.append(
                    SymbolEdge(
                        source_id=node.id, target_id=target_id, edge_type="CALLS"
                    )
                )

    return edges
