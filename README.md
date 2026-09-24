# Code Search

Semantic code search with a call graph. It clones a Git repository, parses source files into symbols (functions, classes, structs and so on) with Tree-sitter, embeds each symbol with a local embedding model, and stores the vectors in Qdrant. Symbol metadata and a call graph go into SQLite. At query time it finds the closest symbols by vector similarity, then expands each hit one hop through the graph (parent, callees, callers) and formats the result as context for an LLM.

An incremental path re-indexes only the symbols that changed between two commits. A FastAPI webhook triggers it on GitHub pushes.

## How it works

```
                     ┌──────────────┐
 GitHub repo ──────► │  clone.py    │  clone / pull, list source files
                     └──────┬───────┘
                            ▼
                     ┌──────────────┐   Tree-sitter AST → symbol chunks
                     │  parser/     │   + SymbolNodes (parent, calls)
                     └──────┬───────┘
              ┌─────────────┴──────────────┐
              ▼                            ▼
   ┌────────────────────┐        ┌────────────────────┐
   │ indexing/embed.py  │        │ graph.py           │
   │ Qwen3-Embedding    │        │ CONTAINS / CALLS   │
   └─────────┬──────────┘        │ edge resolution    │
             ▼                   └─────────┬──────────┘
   ┌────────────────────┐                  ▼
   │ Qdrant (vectors)   │        ┌────────────────────┐
   └─────────┬──────────┘        │ SQLite (symbols +  │
             │                   │ symbol_edges)      │
             └──────────┬────────┴────────────────────┘
                        ▼
                ┌───────────────┐
                │ retrieval/    │  vector search → 1-hop graph expansion
                │               │  → Markdown context for an LLM
                └───────────────┘
```

### 1. Parsing and chunking ([parser/](parser/))

- [parser.py](parser/parser.py): detects the language and parses with `tree_sitter_language_pack`. `full_parser` returns `(chunks, symbol_nodes)` for one file. `batch_stream` groups chunks into batches of 32.
- [model.py](parser/model.py): the `Symbol`, `SymbolNode` and `SymbolEdge` dataclasses. It also holds `LANGUAGE_CONSTRUCT_MAP`, which maps each language to its AST node types (Rust, Python, TypeScript, JavaScript, Go, Java, C++). `HOLDER_CATEGORIES` (`module`, `impl`) are containers that become parents but not chunks.
- [extract_metadata.py](parser/extract_metadata.py): extracts symbol names. Most constructs use the `name` field. Rust `impl Trait for Type` and Go methods use relational templates. Declarations like `const X = 1` use a declarator lookup.
- [chunking.py](parser/chunking.py): walks the AST and keeps a scope stack to track parents.
  - A symbol that fits in 2000 bytes becomes one chunk.
  - A larger function is split into overlapping groups of statements, named `name [part N]`.
  - Doc text comes from leading comments or Python docstrings.
  - Each chunk records the calls made in its body.
  - IDs are a SHA-256 of repo, path, name and start line. `content_hash` is a SHA-256 of the enriched text and is used for change detection.

### 2. Graph ([graph.py](graph.py))

`build_symbol_edges` builds two edge types:

- `CONTAINS`: parent symbol → child symbol.
- `CALLS`: caller → callee, resolved in three tiers: same file, then import-assisted, then a global repo match only if the name is unique. Self-loops are dropped.

### 3. Indexing ([indexing/](indexing/))

- [embed.py](indexing/embed.py): `Qwen/Qwen3-Embedding-0.6B` through `sentence-transformers`, with normalized embeddings.
- [builder.py](indexing/builder.py): builds the embedding text (language, kind, name, docs, source).
- [qdrant.py](indexing/qdrant.py): creates the cosine-distance collection, upserts vectors, runs `semantic_search` and deletes by ID.
- [sqlite.py](indexing/sqlite.py): `symbols` and `symbol_edges` tables, with upsert, fetch, delete and one-hop graph queries.
- [index.py](indexing/index.py): `indexing(batch, embeddings)` writes to both stores.

### 4. Retrieval ([retrieval/](retrieval/))

- [retrieve.py](retrieval/retrieve.py): `retrieve_pipe(query, top_k=5, include_graph_context=True)` runs vector search in Qdrant, loads the hits from SQLite, adds parents, callees and callers, and sorts by score.
- [context.py](retrieval/context.py): `format_retrieval_context` renders the results as Markdown (location, docstring, code, call graph). `max_tokens_budget` is accepted but not enforced yet.

### 5. Incremental updates ([clone.py](clone.py) and [webhook.py](webhook.py))

- `get_changed_file` diffs two commits.
- `diff_symbols` compares the stored symbols for a file against a fresh parse and returns the IDs to delete and the chunks and nodes to upsert (by `content_hash`).
- `apply_symbol_changes` deletes stale entries from Qdrant and SQLite, then embeds and upserts the changes.
- [webhook.py](webhook.py) exposes `POST /webhooks/github`. It verifies the `X-Hub-Signature-256` HMAC, answers `ping`, and runs the update for `push` events in a background task.

## Project layout

```
main.py            full indexing pipeline (entry point)
clone.py           clone/pull, file discovery, diffing, incremental indexing
graph.py           call extraction and edge building
webhook.py         FastAPI GitHub push webhook
config.py          COLLECTION_NAME, BATCH_SIZE
parser/            Tree-sitter parsing, chunking, models
indexing/          embeddings, Qdrant, SQLite
retrieval/         search and LLM context formatting
repo/              cloned repositories (default: rust-lang/rustup)
calculator.py, test.py, test.txt   scratch files used for testing
```

## Requirements

- Python 3.10 or newer (the code uses `X | None` type syntax)
- A Qdrant instance (Qdrant Cloud or self-hosted) and its API key
- Enough disk and memory to run the Qwen3-Embedding-0.6B model. It is downloaded from Hugging Face on first run.
- Packages (there is no `requirements.txt` yet):

```bash
pip install sentence-transformers qdrant-client python-dotenv gitpython \
            tree-sitter-language-pack fastapi uvicorn pydantic
```

## Configuration

Create a `.env` file in the project root:

```env
QDRANT_ENDPOINT=https://<your-cluster>.qdrant.io
QDRANT_API_KEY=<your-key>
```

For the webhook, set these as environment variables:

| Variable | Default | Purpose |
|---|---|---|
| `GITHUB_WEBHOOK_SECRET` | `super_secret_webhook_token` | Shared secret for signature checks. **Change it.** |
| `LOCAL_REPOS_BASE_DIR` | `/tmp/indexed_repos` | Where the webhook clones repos |

Other settings are constants in code: the collection name and batch size in [config.py](config.py), and the repo URL in [clone.py](clone.py) (`url = "https://github.com/rust-lang/rustup"`).

**Do not commit `.env`.** It contains your Qdrant key, and the project is not under version control yet. Add a `.gitignore` that excludes `.env`, `repo/` and `code_symbols.db`.

## Usage

### Index a repository

```bash
python main.py
```

This clones or updates the repo from `clone.py`, parses every supported file, embeds the symbols, writes vectors to Qdrant, and writes symbols and edges to `code_symbols.db`.

Supported extensions: `.py .rs .go .ts .tsx .js .java .cpp .c`. Ignored directories: `.git node_modules dist build target __pycache__`.

### Search

```python
from retrieval.retrieve import retrieve_pipe
from retrieval.context import format_retrieval_context

results = retrieve_pipe("how are toolchains installed?", top_k=5)
print(format_retrieval_context(results))
```

### Run the webhook

```bash
uvicorn webhook:app --host 0.0.0.0 --port 8000
```

Then add a GitHub webhook that:

- points to `https://<host>/webhooks/github`
- uses content type `application/json`
- uses the same secret as `GITHUB_WEBHOOK_SECRET`
- sends push events

## Known issues

These come from reading the code and I have not run the pipeline. They are worth fixing before relying on it:

- **Importing `clone.py` has side effects.** It runs `clone_repo(url)` at module level, so `import clone` (from `main.py`, `parser/parser.py` and others) clones or pulls the repo.
- **`webhook.py` is missing imports.** It calls `get_changed_file`, `diff_symbols` and `apply_symbol_changes` without importing them from `clone.py`.
- **Duplicate embedding model load.** `main.py` and `indexing/embed.py` both load `SentenceTransformer`. The one in `main.py` is unused.
- **Search results mismatch.** With `include_graph_context=False`, `retrieve_pipe` returns records keyed `symbol`, but `format_retrieval_context` expects `seed_symbol`.
- **Graph context is not per seed.** Parents, callees and callers are collected for all seeds together, so every result shows the same neighbours.
- **Two SQLite schemas.** `init_sqlite_db` (without `parent_id`) and `init_symbols_db` (with it) both exist. `sqlite_indexing` in `indexing/index.py` calls the old one and uses an `INSERT` that omits `parent_id`, which overwrites the parent link written by `save_symbol_nodes`. Use only `init_symbols_db`.
- **Incremental indexing path mismatch.** `diff_symbols` looks up stored symbols by repo-relative path, but receives absolute paths from the webhook.
- **Import-assisted call resolution is unused.** `file_imports_map` is always `{}`, so only same-file and unique-name matches work.
- **Webhook defaults.** The default secret is a placeholder, and signature parsing assumes a well-formed header.
- **No tests or dependency file.** `test.py` and `calculator.py` are sample inputs, not a test suite.
