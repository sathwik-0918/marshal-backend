from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_core.documents import Document
from .text_utils import deep_clean_text


class EmbeddingPipeline:
    def __init__(self, model, chunk_size=800, chunk_overlap=150):
        # model is a pre-loaded SentenceTransformer, shared across every
        # schedule — see store_manager.py for where it's loaded once.
        self.model = model
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap

    def chunk_documents(self, documents):
        splitter = RecursiveCharacterTextSplitter(
            chunk_size=self.chunk_size,
            chunk_overlap=self.chunk_overlap,
            length_function=len,
            separators=["\n\n", "\n", ". ", "? ", "! ", "; ", ", ", " ", ""],
        )
        return splitter.split_documents(documents)

    def clean_chunks(self, chunks):
        cleaned = []
        for chunk in chunks:
            cleaned_text = deep_clean_text(chunk.page_content)
            if len(cleaned_text.strip()) < 30:
                continue
            cleaned.append(Document(page_content=cleaned_text, metadata=chunk.metadata))
        return cleaned

    def embed_chunks(self, chunks):
        cleaned_chunks = self.clean_chunks(chunks)
        texts = [c.page_content for c in cleaned_chunks]
        embeddings = self.model.encode(texts, batch_size=64, normalize_embeddings=True)
        return embeddings, cleaned_chunks