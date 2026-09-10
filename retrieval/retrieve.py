from indexing.qdrant import semantic_search
from indexing.sqlite import fetch_symbols_by_ids, fetch_graph_context_by_ids
from config import COLLECTION_NAME


def retrieve_pipe(query: str, top_k: int = 5, include_graph_context: bool = True):
    """
    1. Vector Search: Finds top-k semantically relevant 'seed' symbols from Qdrant.
    2. Graph Traversal: Pulls parents, callees, and callers for each seed symbol.
    3. Context Assembly: Combines seed results with structural graph context.
    """
    # Step 1: Semantic Search in Qdrant
    results = semantic_search(query, top_k=top_k, collection_name=COLLECTION_NAME)

    if not results or not results.points:
        return []

    # Map scores and collect unique seed IDs
    scores_map = {str(point.id): point.score for point in results.points}
    seed_ids = list(scores_map.keys())

    # Step 2: Fetch Seed Symbol records from SQLite
    seed_symbols = fetch_symbols_by_ids(seed_ids)

    if not include_graph_context:
        # Standard vector-only fallback
        records = [
            {
                "symbol": sym,
                "semantic_score": scores_map.get(sym.id, 0.0),
                "relation": "seed",
            }
            for sym in seed_symbols
        ]
        records.sort(key=lambda x: x["semantic_score"], reverse=True)
        return records

    # Step 3: Graph Context Traversal (1-Hop Expansion)
    graph_map = fetch_graph_context_by_ids(seed_ids)

    # Collect additional node IDs while avoiding duplicates
    additional_ids = set(
        graph_map["parent_ids"] + graph_map["callee_ids"] + graph_map["caller_ids"]
    ) - set(seed_ids)

    context_symbols = (
        fetch_symbols_by_ids(list(additional_ids)) if additional_ids else []
    )
    context_lookup = {sym.id: sym for sym in context_symbols}

    # Step 4: Assemble Structured Context
    enriched_results = []

    for seed_sym in seed_symbols:
        s_id = seed_sym.id

        # Resolve related graph nodes for this seed
        parents = [
            context_lookup[p_id]
            for p_id in graph_map["parent_ids"]
            if p_id in context_lookup
        ]
        callees = [
            context_lookup[c_id]
            for c_id in graph_map["callee_ids"]
            if c_id in context_lookup
        ]
        callers = [
            context_lookup[cl_id]
            for cl_id in graph_map["caller_ids"]
            if cl_id in context_lookup
        ]

        enriched_results.append(
            {
                "seed_symbol": seed_sym,
                "semantic_score": scores_map.get(s_id, 0.0),
                "graph_context": {
                    "parents": parents,  # Structural container (e.g. Class / Impl)
                    "callees": callees,  # What this function calls
                    "callers": callers,  # What calls this function
                },
            }
        )

    # Sort results by vector similarity score
    enriched_results.sort(key=lambda x: x["semantic_score"], reverse=True)

    return enriched_results
