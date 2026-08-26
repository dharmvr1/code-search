from indexing.sqlite import sqlite_indexing
from indexing.qdrant import qdrant_indexing
from config import COLLECTION_NAME


def indexing(batch, embeddings):
    sqlite_indexing(batch)
    qdrant_indexing(
        batch,
        embeddings,
        collection_name=COLLECTION_NAME,
    )
