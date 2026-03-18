from embeddings.vertexai import build_vertexai_embedding

REGISTRY = {
    "vertexai": build_vertexai_embedding,
}


def get_embedding(config: dict):
    provider = config["embedding_provider"]
    builder = REGISTRY.get(provider)
    if builder is None:
        raise ValueError(f"不支援的 embedding provider: {provider}，可用: {list(REGISTRY)}")
    return builder(config)
