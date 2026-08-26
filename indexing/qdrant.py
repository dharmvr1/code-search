from qdrant_client import QdrantClient
from qdrant_client.models import (
    VectorParams,
    Distance,
    Batch,
    SearchParams,
    QueryResponse,
)
from dotenv import load_dotenv
import os
from parser.model import Symbol
from indexing.embed import query_embed, model

load_dotenv()

QDRANT_API_KEY = os.getenv("QDRANT_API_KEY")
QDRANT_ENDPOINT = os.getenv("QDRANT_ENDPOINT")

client = QdrantClient(url=QDRANT_ENDPOINT, api_key=QDRANT_API_KEY, cloud_inference=True)

EMBEDDING_DIM = model.get_embedding_dimension()


def qdrant_create_collection(collection_name: str):
    if not client.collection_exists(collection_name):
        client.create_collection(
            collection_name=collection_name,
            vectors_config=VectorParams(size=EMBEDDING_DIM, distance=Distance.COSINE),
        )
    return client


def qdrant_indexing(
    batch: list[Symbol],
    vectors: list,
    collection_name: str,
):

    id = []
    payload_symobls = []
    for symbol in batch:
        id.append(symbol.id)
        payload_symobls.append(
            payload={
                "repo": symbol.repo,
                "path": symbol.path,
                "language": symbol.language,
                "kind": symbol.kind,
                "symbol": symbol.symbol,
                "start_line": symbol.start_line,
                "end_line": symbol.end_line,
                "content_hash": symbol.content_hash,
            }
        )
    qdrant_create_collection(collection_name)
    client.upsert(
        collection_name=f"{collection_name}",
        points=Batch(
            ids=id,
            payloads=payload_symobls,
            vectors=vectors,
        ),
    )


def semantic_search(query: str, collection_name: str, top_k=10) -> QueryResponse:
    if not client.collection_exists(collection_name):
        raise RuntimeError("Collection NOT EXIST IN DB ")
    query_vector = query_embed(query)
    results = client.query_points(
        collection_name=collection_name,
        query=query_vector,
        limit=top_k,
        search_params=SearchParams(hnsw_ef=128, exact=False),
        with_payload=False,
    )
    return results


def qdrant_delete_ids(symbol_ids: list, collection_name: str):
    client.delete(collection_name=collection_name, points_selector=symbol_ids)
