import os
from sentence_transformers import SentenceTransformer
from langchain_core.documents import Document
from .vector_store import FaissVectorStore

KNOWLEDGE_DIR = os.path.join(os.path.dirname(__file__), "..", "schedule_knowledge")

# Loaded ONCE, shared by every schedule's store.
_shared_model = SentenceTransformer("all-MiniLM-L6-v2")

_cache: dict = {}


def _schedule_dir(schedule_id: str) -> str:
    # This function IS the isolation boundary. Every store's files live
    # in a directory keyed only by this one schedule_id — there is no
    # code path anywhere that can read a different schedule's files
    # from here, because no other schedule_id ever enters this function.
    return os.path.join(KNOWLEDGE_DIR, schedule_id)


def get_store(schedule_id: str) -> FaissVectorStore:
    if schedule_id in _cache:
        return _cache[schedule_id]

    store = FaissVectorStore(persist_dir=_schedule_dir(schedule_id), model=_shared_model)
    index_path = os.path.join(store.persist_dir, "faiss_index")
    if os.path.exists(index_path):
        store.load()
    # else: left empty. A schedule with no uploaded documents yet is a
    # normal state, not an error — retrieve_node handles this below.

    _cache[schedule_id] = store
    return store


def add_text_document(schedule_id: str, text: str, source_name: str) -> int:
    store = get_store(schedule_id)  # same cached instance every later get_store() call returns
    doc = Document(page_content=text, metadata={"source": source_name})
    return store.build_from_documents([doc])  # mutates + saves in place