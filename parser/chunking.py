from tree_sitter_language_pack import Node, Tree
from parser.model import HOLDER_CATEGORIES
from parser.extract_metadata import extract_item_metadata
from pathlib import Path
import hashlib
from graph import extract_calls_from_node 
from parser.model import Symbol, SymbolNode


def extract_chunks(
    tree: Tree,
    code_bytes: bytes,
    lang_key: str,
    file_name: str,
    max_chunk_bytes: int = 2000,
) -> tuple[list[Symbol], list[SymbolNode]]:

    chunks = []
    nodes = []  # Our graph nodes

    relative_file_path = file_name.split("repo")[1] if "repo" in file_name else file_name
    repo_name = relative_file_path.split("/")[1] if len(relative_file_path.split("/")) > 1 else "default"
    repo_path = relative_file_path

    # Scope stack tracks parent SymbolNodes and their names as we recurse down
    def traverse(node: Node, scope_stack: list[dict] = None):
        if scope_stack is None:
            scope_stack = []

        metadata = extract_item_metadata(node, code_bytes, lang_key)

        if metadata is not None:
            category = metadata.get("category")
            node_type = metadata.get("ast_type", None)
            name_node = metadata.get("name", None)

            # Determine parent_id and parent path from scope stack
            parent_id = scope_stack[-1]["id"] if scope_stack else None
            parent_name = scope_stack[-1]["name"] if scope_stack else None

            # -------------------------------------------------------------
            # CASE A: Holder Categories (e.g. impl, module)
            # They don't become chunks, but they DO act as parent scopes!
            # -------------------------------------------------------------
            if node_type is not None and category in HOLDER_CATEGORIES:
                container_id = hashlib.sha256(
                    f"{repo_name}{repo_path}{name_node}{node.start_point[0] + 1}".encode(
                        "utf-8"
                    )
                ).hexdigest()

                virtual_node = SymbolNode(
                    id=container_id,
                    repo=repo_name,
                    path=repo_path,
                    symbol=name_node,
                    kind=node_type,
                    language=lang_key,
                    start_line=node.start_point[0] + 1,
                    end_line=node.end_point[0] + 1,
                    body="",
                    content_hash="",
                    parent_id=parent_id,
                    calls=[],
                )
                nodes.append(virtual_node)

                next_scope = scope_stack + [{"id": container_id, "name": name_node}]
                for child in node.children:
                    traverse(child, next_scope)
                return

            # -------------------------------------------------------------
            # CASE B: Regular Symbols (Functions, Classes, Methods)
            # -------------------------------------------------------------
            if node_type is not None and category not in HOLDER_CATEGORIES:
                node_bytes = code_bytes[node.start_byte : node.end_byte]
                doc = extract_leading_comment(node, code_bytes) or extract_docstring(
                    node, code_bytes
                )

                # Extract call expressions inside this node's body
                raw_calls = extract_calls_from_node(node, code_bytes, lang_key)

                if len(node_bytes) <= max_chunk_bytes:
                    body = node_bytes.decode("utf-8", errors="ignore")
                    
                    # Enriched semantic text representation for embeddings
                    calls_str = ", ".join(raw_calls) if raw_calls else "none"
                    parent_context = f"in {parent_name} " if parent_name else ""
                    text = f"FILE: {repo_path}\nSYMBOL: {parent_context}{name_node}\nCALLS: {calls_str}\nDOC: {doc or 'none'}\nBODY:\n{body}"
                    content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
                    
                    text_id = (
                        f"{repo_name}{repo_path}{name_node}{node.start_point[0] + 1}"
                    )
                    symbol_id = hashlib.sha256(text_id.encode("utf-8")).hexdigest()

                    # Existing Chunk
                    chunks.append(
                        Symbol(
                            id=symbol_id,
                            repo=repo_name,
                            symbol=name_node,
                            path=repo_path,
                            language=lang_key,
                            kind=node_type,
                            start_line=node.start_point[0] + 1,
                            end_line=node.end_point[0] + 1,
                            doc=doc,
                            body=body,
                            content_hash=content_hash,
                        )
                    )

                    # New Graph Node with parent_id & calls!
                    symbol_node = SymbolNode(
                        id=symbol_id,
                        repo=repo_name,
                        path=repo_path,
                        symbol=name_node,
                        kind=node_type,
                        language=lang_key,
                        start_line=node.start_point[0] + 1,
                        end_line=node.end_point[0] + 1,
                        body=body,
                        content_hash=content_hash,
                        doc=doc,
                        parent_id=parent_id,
                        calls=raw_calls,
                    )
                    nodes.append(symbol_node)

                    # If this is a class or container symbol, recurse into its children
                    if node_type in {"class", "class_definition", "struct"}:
                        next_scope = scope_stack + [{"id": symbol_id, "name": name_node}]
                        for child in node.children:
                            traverse(child, next_scope)
                    return

                # ---------------------------------------------------------
                # CASE C: Large Function Splitting (Overlapping Fragments)
                # ---------------------------------------------------------
                body_node = node.child_by_field_name("body")
                if body_node is not None:
                    statements = [c for c in body_node.children if c.is_named]
                    groups = []
                    current_group, current_size = [], 0
                    
                    # Sliding statement grouping with ~20% overlap of statements if possible
                    i = 0
                    while i < len(statements):
                        stmt = statements[i]
                        stmt_size = stmt.end_byte - stmt.start_byte
                        
                        if current_group and current_size + stmt_size > max_chunk_bytes:
                            groups.append(current_group)
                            # Overlap: keep the last 1 or 2 statements as context for the next chunk
                            overlap_count = min(1, len(current_group))
                            current_group = current_group[-overlap_count:]
                            current_size = sum(s.end_byte - s.start_byte for s in current_group)
                        
                        current_group.append(stmt)
                        current_size += stmt_size
                        i += 1

                    if current_group:
                        groups.append(current_group)

                    for group_idx, group in enumerate(groups):
                        _make_fragment(
                            group,
                            name_node,
                            node_type,
                            file_name,
                            lang_key,
                            code_bytes,
                            repo_name,
                            repo_path,
                            doc,
                            parent_id,
                            parent_name,
                            chunks,
                            nodes,
                            group_idx,
                        )
                    return

        # Default fallback traversal
        for child in node.children:
            traverse(child, scope_stack)

    traverse(tree.root_node)
    return chunks, nodes


def _make_fragment(
    group,
    parent_name,
    node_type,
    file_name,
    lang_key,
    code_bytes,
    repo_name,
    repo_path,
    doc: str | None,
    parent_id: str | None,
    enclosing_name: str | None,
    chunks: list,
    nodes: list,
    group_idx: int,
):
    """One group of sibling statements -> one chunk + one SymbolNode, tagged with parent, calls, and fragment index."""
    start, end = group[0].start_byte, group[-1].end_byte
    body = code_bytes[start:end].decode("utf-8", errors="ignore")
    
    # 1. Collect calls strictly within the statement group
    fragment_calls = set()
    CALL_TYPES = {
        "python": {"call"},
        "rust": {"call_expression"},
        "javascript": {"call_expression"},
    }
    target_types = CALL_TYPES.get(lang_key, {"call", "call_expression"})

    def _walk_stmt(stmt_node):
        if stmt_node.type in target_types:
            func_child = stmt_node.child_by_field_name("function")
            if func_child:
                called_name = code_bytes[
                    func_child.start_byte : func_child.end_byte
                ].decode("utf-8", errors="ignore")
                fragment_calls.add(called_name)
        for child in stmt_node.children:
            _walk_stmt(child)

    for stmt in group:
        _walk_stmt(stmt)

    raw_calls = list(fragment_calls)
    calls_str = ", ".join(raw_calls) if raw_calls else "none"
    parent_context = f"in {enclosing_name} " if enclosing_name else ""
    symbol_display_name = f"{parent_name} [part {group_idx + 1}]"
    
    text = f"FILE: {repo_path}\nSYMBOL: {parent_context}{symbol_display_name}\nCALLS: {calls_str}\nDOC: {doc or 'none'}\nBODY:\n{body}"
    content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()

    text_id = f"{repo_name}{repo_path}{parent_name}{group[0].start_point[0] + 1}_{group_idx}"
    fragment_id = hashlib.sha256(text_id.encode("utf-8")).hexdigest()

    # 2. Build the vector chunk (Symbol)
    chunk = Symbol(
        id=fragment_id,
        repo=repo_name,
        symbol=symbol_display_name,
        path=repo_path,
        language=lang_key,
        kind=node_type,
        start_line=group[0].start_point[0] + 1,
        end_line=group[-1].end_point[0] + 1,
        doc=doc,
        body=body,
        content_hash=content_hash,
    )

    # 3. Build the graph node (SymbolNode)
    node = SymbolNode(
        id=fragment_id,
        repo=repo_name,
        path=repo_path,
        symbol=symbol_display_name,
        kind=node_type,
        language=lang_key,
        start_line=group[0].start_point[0] + 1,
        end_line=group[-1].end_point[0] + 1,
        body=body,
        content_hash=content_hash,
        doc=doc,
        parent_id=parent_id,
        calls=raw_calls,
    )

    chunks.append(chunk)
    nodes.append(node)


def fallback_line_chunking(code_bytes: bytes, max_bytes: int, overlap_bytes: int = 200) -> list[dict]:
    """Fallback sliding-window chunker with overlap for plain text or unsupported ASTs."""
    lines = code_bytes.decode("utf-8", errors="ignore").splitlines()
    chunks = []
    current_chunk = []
    current_size = 0

    for i, line in enumerate(lines):
        line_bytes = len(line.encode("utf-8"))
        if current_size + line_bytes > max_bytes and current_chunk:
            chunks.append(
                {
                    "type": "fallback_lines",
                    "content": "\n".join(current_chunk),
                }
            )
            # Retain overlap lines from the end of the current chunk
            overlap_chunk = []
            overlap_size = 0
            for prev_line in reversed(current_chunk):
                sz = len(prev_line.encode("utf-8"))
                if overlap_size + sz > overlap_bytes:
                    break
                overlap_chunk.insert(0, prev_line)
                overlap_size += sz
            
            current_chunk = overlap_chunk
            current_size = overlap_size

        current_chunk.append(line)
        current_size += line_bytes

    if current_chunk:
        chunks.append(
            {
                "type": "fallback_lines",
                "content": "\n".join(current_chunk),
            }
        )

    return chunks


COMMENT_NODE_TYPES = {"comment", "line_comment", "block_comment"}


def extract_leading_comment(node, code_bytes: bytes) -> str | None:
    """Walks backward through preceding siblings, collecting contiguous
    comment lines sitting directly above the node."""
    comments = []
    sibling = node.prev_sibling
    while sibling is not None and sibling.type in COMMENT_NODE_TYPES:
        comments.insert(
            0,
            code_bytes[sibling.start_byte : sibling.end_byte].decode(
                "utf-8", errors="ignore"
            ),
        )
        sibling = sibling.prev_sibling
    return "\n".join(comments) if comments else None


def extract_docstring(node, code_bytes: bytes) -> str | None:
    """Python-style: the first statement inside the body is a bare string literal."""
    body_node = node.child_by_field_name("body")
    if body_node is None:
        return None

    first = next((c for c in body_node.children if c.is_named), None)
    if first is None:
        return None

    string_node = first if first.type == "string" else None

    if string_node is None and first.type == "expression_statement" and first.children:
        candidate = first.children[0]
        if candidate.type == "string":
            string_node = candidate

    if string_node is None:
        return None

    return (
        code_bytes[string_node.start_byte : string_node.end_byte]
        .decode("utf-8", errors="ignore")
        .strip("\"'")
    )
