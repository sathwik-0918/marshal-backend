import os
import faiss
import numpy as np
import pickle
from .embedding_pipeline import EmbeddingPipeline
from .text_utils import deep_clean_text


class FaissVectorStore:
    def __init__(self, persist_dir, model, chunk_size=800, chunk_overlap=150):
        self.persist_dir = persist_dir
        os.makedirs(self.persist_dir, exist_ok=True)
        self.index = None
        self.metadata = []
        self.model = model
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap

    def build_from_documents(self, documents):
        # Safe to call repeatedly on an already-loaded, non-empty store —
        # add_embeddings() below only initializes self.index if it's None,
        # otherwise it appends. That's what makes this also correct for
        # incremental ingestion, not just a first build.
        emb_pipe = EmbeddingPipeline(self.model, self.chunk_size, self.chunk_overlap)
        chunks = emb_pipe.chunk_documents(documents)
        embeddings, cleaned_chunks = emb_pipe.embed_chunks(chunks)
        metadatas = [{"text": c.page_content, "source": c.metadata.get("source", "Unknown")} for c in cleaned_chunks]
        if len(embeddings) > 0:
            self.add_embeddings(np.array(embeddings).astype("float32"), metadatas)
            self.save()
        return len(metadatas)

    def add_embeddings(self, embeddings, metadatas=None):
        dim = embeddings.shape[1]
        if self.index is None:
            self.index = faiss.IndexFlatL2(dim)
        self.index.add(embeddings)
        if metadatas is not None:
            self.metadata.extend(metadatas)

    def save(self):
        faiss.write_index(self.index, os.path.join(self.persist_dir, "faiss_index"))
        with open(os.path.join(self.persist_dir, "metadata.pkl"), "wb") as f:
            pickle.dump(self.metadata, f)

    def load(self):
        self.index = faiss.read_index(os.path.join(self.persist_dir, "faiss_index"))
        with open(os.path.join(self.persist_dir, "metadata.pkl"), "rb") as f:
            self.metadata = pickle.load(f)

    def search(self, query_embedding, top_k=5):
        if self.index is None or self.index.ntotal == 0:
            return []
        top_k = min(top_k, self.index.ntotal)
        distances, indices = self.index.search(query_embedding, top_k)
        results = []
        for idx, dist in zip(indices[0], distances[0]):
            if idx < 0 or idx >= len(self.metadata):
                continue
            meta = self.metadata[idx]
            if not meta.get("text", "").strip():
                continue
            results.append({"index": int(idx), "distance": float(dist), "metadata": meta})
        return results

    def query(self, query_text, top_k=5):
        cleaned_query = deep_clean_text(query_text)
        query_embedding = self.model.encode([cleaned_query], normalize_embeddings=True).astype("float32")
        return self.search(query_embedding, top_k)