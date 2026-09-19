from dotenv import load_dotenv
load_dotenv()

import os
from fastapi import FastAPI, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from schemas import AgentRequest, AgentResponse
from agent.graph import run_agent
from knowledge import store_manager, pdf_utils

app = FastAPI(title="MARSHAL ML/Agent Service")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[os.getenv("ALLOWED_ORIGIN", "http://localhost:5000")],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/agent/process", response_model=AgentResponse)
def process_request(payload: AgentRequest):
    activities = [a.model_dump(by_alias=True) for a in payload.activities]
    return run_agent(payload.schedule_id, payload.schedule_name, activities, payload.message)


@app.post("/knowledge/ingest")
async def ingest_knowledge(schedule_id: str = Form(...), file: UploadFile = File(...)):
    content = await file.read()
    filename = file.filename or "document"

    if filename.lower().endswith(".pdf"):
        text = pdf_utils.extract_text(content, filename)
    else:
        text = content.decode("utf-8", errors="ignore")

    if not text.strip():
        return {"success": False, "error": "No extractable text found in this file"}

    chunks_added = store_manager.add_text_document(schedule_id, text, source_name=filename)
    return {"success": True, "chunksAdded": chunks_added, "filename": filename}