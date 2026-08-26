from sentence_transformers import SentenceTransformer
from parser.model import Symbol

model = SentenceTransformer("Qwen/Qwen3-Embedding-0.6B")


def embed_batch(texts: list) -> list[list[float]]:
    return model.encode(
        texts,
        batch_size=32,
        normalize_embeddings=True,
    ).tolist()


def query_embed(query: str):
    query_vector = model.encode(query, normalize_embeddings=True)
    return query_vector
