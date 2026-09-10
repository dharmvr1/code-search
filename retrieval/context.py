from typing import Any, Dict


def format_symbol_for_llm(symbol) -> str:
    """Formats a single symbol node with file metadata."""
    return f"// File: {symbol.path} (Lines {symbol.start_line}-{symbol.end_line})\n// Kind: {symbol.kind}\n{symbol.body}"


def format_retrieval_context(
    retrieved_results: list[Dict[str, Any]], max_tokens_budget: int = 4000
) -> str:
    """
    Transforms enriched retrieval output (seed symbols + graph context)
    into a clean, token-efficient Markdown prompt block for an LLM.
    """
    if not retrieved_results:
        return "No relevant code context found."

    formatted_sections = []

    for idx, item in enumerate(retrieved_results, 1):
        seed = item["seed_symbol"]
        score = item.get("semantic_score", 0.0)
        graph = item.get("graph_context", {})

        parents = graph.get("parents", [])
        callees = graph.get("callees", [])
        callers = graph.get("callers", [])

        # --- SECTION HEADER ---
        section = [
            f"### RESULT {idx}: `{seed.symbol}` (Relevance Score: {score:.2f})",
            f"**Location:** `{seed.path}:{seed.start_line}-{seed.end_line}` | **Kind:** `{seed.kind}` | **Lang:** `{seed.language}`",
        ]

        # --- PARENT CONTAINER CONTEXT ---
        if parents:
            parent_info = ", ".join([f"`{p.symbol}` ({p.kind})" for p in parents])
            section.append(f"**Defined Inside:** {parent_info}")

        if seed.doc:
            section.append(f"**Docstring:**\n> {seed.doc.strip()}")

        # --- PRIMARY MATCH CODE BODY ---
        section.append("```" + f"{seed.language}")
        section.append(seed.body.strip())
        section.append("```")

        # --- GRAPH EXECUTION DEPENDENCIES (Compact Summaries) ---
        has_graph = callees or callers
        if has_graph:
            section.append("\n#### Connected Code Context (Graph Edges)")

        # Callees (What this function calls)
        if callees:
            section.append("* **Functions Called By This Symbol (`CALLS`):**")
            for callee in callees:
                summary = f"  - `{callee.symbol}()` in `{callee.path}`"
                if callee.doc:
                    # Compact docstring snippet
                    first_line_doc = callee.doc.strip().split("\n")[0]
                    summary += f': *"{first_line_doc}"*'
                section.append(summary)

        # Callers (What calls this function)
        if callers:
            section.append("* **Functions Calling This Symbol (`CALLED BY`):**")
            for caller in callers:
                section.append(
                    f"  - `{caller.symbol}()` in `{caller.path}` (Line {caller.start_line})"
                )

        section.append("\n" + "─" * 60)
        formatted_sections.append("\n".join(section))

    # Combine into a final prompt payload
    header = "## RELEVANT CODE CONTEXT & EXECUTION GRAPH\nUse the following extracted codebase symbols and dependency graph to answer the user request:\n\n"
    return header + "\n\n".join(formatted_sections)
