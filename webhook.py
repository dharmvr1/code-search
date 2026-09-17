import hashlib
import hmac
import os
from pathlib import Path
from typing import List, Optional

from git import Repo
from fastapi import FastAPI, Header, HTTPException, Request, BackgroundTasks, status
from pydantic import BaseModel

# Secret configured in your GitHub Webhook settings
GITHUB_WEBHOOK_SECRET = os.getenv("GITHUB_WEBHOOK_SECRET", "super_secret_webhook_token")
LOCAL_REPOS_BASE_DIR = Path(os.getenv("LOCAL_REPOS_BASE_DIR", "/tmp/indexed_repos"))

app = FastAPI(title="Git Indexing Webhook Engine", version="1.0.0")

# =========================================================================
# 1. Payload Models
# =========================================================================
class CommitAuthor(BaseModel):
    name: str
    email: str

class Commit(BaseModel):
    id: str  # Commit SHA
    message: str
    timestamp: str
    author: CommitAuthor
    added: List[str] = []
    removed: List[str] = []
    modified: List[str] = []

class RepositoryInfo(BaseModel):
    name: str
    full_name: str
    clone_url: str

class PushWebhookPayload(BaseModel):
    ref: str
    before: str  # Old commit hash
    after: str   # New commit hash
    repository: RepositoryInfo
    commits: List[Commit] = []


# =========================================================================
# 2. HMAC Signature Verification Helper
# =========================================================================
def verify_github_signature(payload_body: bytes, secret: str, signature_header: Optional[str]):
    """
    Validates that the incoming request came from GitHub using HMAC SHA256.
    """
    if not signature_header:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="X-Hub-Signature-256 header is missing.",
        )

    hash_type, signature = signature_header.split("=")
    if hash_type != "sha256":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Unsupported signature hash algorithm.",
        )

    mac = hmac.new(secret.encode("utf-8"), msg=payload_body, digestmod=hashlib.sha256)
    expected_signature = mac.hexdigest()

    if not hmac.compare_digest(expected_signature, signature):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid webhook signature.",
        )


# =========================================================================
# 3. Background Task Runner: Sync Repo & Execute Diff Pipeline
# =========================================================================
def process_github_push_event(payload: PushWebhookPayload):
    """
    Executes the incremental re-indexing pipeline asynchronously.
    """
    repo_name = payload.repository.name
    dest_path = LOCAL_REPOS_BASE_DIR / repo_name

    print(f"[Webhook] Processing push event for repo: {repo_name} ({payload.before[:7]}..{payload.after[:7]})")

    # A. Ensure local repository exists or clone it
    if not dest_path.exists():
        print(f"[Webhook] Cloning repository {payload.repository.clone_url} to {dest_path}...")
        git_repo = Repo.clone_from(payload.repository.clone_url, dest_path)
    else:
        git_repo = Repo(dest_path)
        print(f"[Webhook] Fetching latest commits for {repo_name}...")
        git_repo.remotes.origin.fetch()

    # B. Checkout the updated commit
    git_repo.git.checkout(payload.after)

    # C. Get changed files using your existing function
    changed_relative_files = get_changed_file(
        destination_file_path=dest_path,
        old_commit_hash=payload.before,
        new_commit_hash=payload.after,
    )

    if not changed_relative_files:
        print("[Webhook] No changed files detected between commits. Skipping.")
        return

    print(f"[Webhook] Detected {len(changed_relative_files)} modified file(s): {changed_relative_files}")

    # D. Execute diff and update per modified file
    total_deletions = 0
    total_chunks = 0
    total_nodes = 0

    for rel_path in changed_relative_files:
        abs_file_path = str(dest_path / rel_path)

        # 1. Compute AST symbol diff
        symbols_to_delete, chunks_to_upsert, nodes_to_upsert = diff_symbols(
            repo=repo_name, file_path=abs_file_path
        )

        # 2. Apply updates to Qdrant & SQLite
        if symbols_to_delete or chunks_to_upsert or nodes_to_upsert:
            apply_symbol_changes(
                symbols_to_delete=symbols_to_delete,
                nodes_to_upsert=nodes_to_upsert,
                chunks_to_upsert=chunks_to_upsert,
                db_path="code_symbols.db",
            )

        total_deletions += len(symbols_to_delete)
        total_chunks += len(chunks_to_upsert)
        total_nodes += len(nodes_to_upsert)

    print(f"[Webhook] Re-indexing completed successfully for {repo_name}!")
    print(f"          Summary: Deleted {total_deletions} stale symbols | "
          f"Upserted {total_chunks} Qdrant vectors | Upserted {total_nodes} SQLite nodes.")


# =========================================================================
# 4. FastAPI Endpoint Definition
# =========================================================================
@app.post("/webhooks/github")
async def github_webhook_listener(
    request: Request,
    background_tasks: BackgroundTasks,
    x_github_event: Optional[str] = Header(None),
    x_hub_signature_256: Optional[str] = Header(None),
):
    """
    Main Webhook endpoint for GitHub events.
    Verifies payload signatures and schedules re-indexing in a background thread.
    """
    # 1. Read raw request body for HMAC verification
    raw_body = await request.body()
    verify_github_signature(raw_body, GITHUB_WEBHOOK_SECRET, x_hub_signature_256)

    # 2. Ignore non-push events (e.g., ping, issue_comment)
    if x_github_event == "ping":
        return {"status": "ok", "message": "Pong! Webhook successfully configured."}

    if x_github_event != "push":
        return {"status": "ignored", "reason": f"Event '{x_github_event}' is not handled."}

    # 3. Parse JSON payload
    payload_dict = await request.json()
    payload = PushWebhookPayload(**payload_dict)

    # 4. Offload processing to a background task (returns HTTP 202 instantly)
    background_tasks.add_task(process_github_push_event, payload)

    return {
        "status": "accepted",
        "message": f"Processing push event for {payload.repository.name} in background.",
        "commit_range": f"{payload.before[:7]}..{payload.after[:7]}",
    }