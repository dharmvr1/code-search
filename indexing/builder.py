from parser.model import Symbol


def build_embedding_text(symbol: Symbol) -> str:
    parts = []

    parts.append(f"Language: {symbol.language}")
    parts.append(f"Kind: {symbol.kind}")
    parts.append(f"Name: {symbol.symbol}")

    if symbol.doc:
        parts.append("")
        parts.append("Documentation:")
        parts.append(symbol.doc.strip())

    parts.append("")
    parts.append("Source Code:")
    parts.append(symbol.body.strip())

    return "\n".join(parts)


def build_embedding_batch(batch:list[Symbol]):
    batch_embed_text = []
    for symb in batch:
        batch_embed_text.append(build_embedding_text(symb))
    return batch_embed_text
        
    