# from tree_sitter import Language, Parser
from tree_sitter_language_pack import get_parser, detect_language, process, Tree

from parser.chunking import extract_chunks
from clone import iter_source_file
from pathlib import Path
from parser.model import SymbolNode


def parser(filename: str, code_bytes: bytes) -> Tree:

    lang_key = detect_language(path=filename)

    parser = get_parser(lang_key)

    tree = parser.parse(code_bytes)
    return tree, lang_key


def full_parser(file_path: Path):
    """Parses an individual file and returns both vector chunks and graph nodes."""
    with open(file_path, "rb") as file:
        code_bytes = file.read()

    tree, lang_key = parser(filename=str(file_path), code_bytes=code_bytes)

    chunks, symbol_nodes = extract_chunks(
        tree, code_bytes=code_bytes, lang_key=lang_key, file_name=str(file_path)
    )

    # Return a tuple of lists for this specific file
    return chunks, symbol_nodes


def batch_stream(chunk_stream, batch_size: int = 32):
    # this take all chunks and make batch of it
    batch = []
    for chunk in chunk_stream:
        batch.append(chunk)
        if len(batch) == batch_size:
            yield batch
            batch = []

    if batch:
        yield batch
