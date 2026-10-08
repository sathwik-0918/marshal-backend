from dotenv import load_dotenv
load_dotenv()

import os
from fastapi import FastAPI, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from generation import requirement_extractor, timetable_solver, feedback_extractor
from schemas import AgentRequest, AgentResponse, GenerationSpec
from agent.graph import run_agent
from knowledge import store_manager, pdf_utils
from extraction import schedule_extractor
from schemas import AgentRequest, AgentResponse, GenerationSpec, FeedbackConstraint

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
    reference_entries = [r.model_dump(by_alias=True) for r in payload.reference_entries]
    return run_agent(
        payload.schedule_id, payload.schedule_name, activities, payload.message, reference_entries,
        timetable_spec=payload.timetable_spec, timetable_constraints=payload.timetable_constraints,
    )


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

@app.post("/schedule/extract")
async def extract_schedule(file: UploadFile = File(...)):
    content = await file.read()
    filename = file.filename or "document"

    try:
        if filename.lower().endswith(".pdf"):
            result = schedule_extractor.extract_from_pdf(content, filename)
        else:
            text = content.decode("utf-8", errors="ignore")
            if not text.strip():
                return {"success": False, "error": "No extractable text found in this file"}
            result = schedule_extractor.extract_from_text(text)
    except Exception as error:
        print(f"[schedule_extract] failed: {error}")
        return {"success": False, "error": "Couldn't read this document as a schedule. Try a clearer file, or enter activities manually."}

    return {"success": True, "extraction": result.model_dump()}

@app.post("/generate/timetable")
def generate_timetable(payload: dict):
    text = payload.get("requirement_text", "")
    if not text.strip():
        return {"success": False, "error": "No requirements provided"}
    try:
        spec, notes = requirement_extractor.extract_spec(text)
        result = timetable_solver.generate_options(spec)
        summary = timetable_solver.section_summary(spec)
        notes = notes + timetable_solver.fill_notes(summary)
    except Exception as error:
        print(f"[generate_timetable] failed: {error}")
        return {"success": False, "error": "Couldn't generate a timetable from this. Try stating weekly counts and durations more explicitly."}
    return {"success": True, "spec": spec.model_dump(), "extraction_notes": notes, "section_summary": summary, **result}

@app.post("/generate/refine")
def refine_timetable(payload: dict):
    try:
        spec = GenerationSpec.model_validate(payload.get("spec") or {})
        feedback_text = payload.get("feedback_text", "")
        rejected = payload.get("rejected_reference_entries", [])
        objective_mode = payload.get("objective_mode", "balanced")
        if not feedback_text.strip():
            return {"success": False, "error": "No feedback provided"}

        prior = [FeedbackConstraint.model_validate(c) for c in payload.get("prior_constraints", [])]
        subjects = sorted({s.subject_name for s in spec.sessions})
        faculty = sorted({f for s in spec.sessions for f in s.faculty})
        sections = sorted({s.section for s in spec.sessions if s.section})
        fb = feedback_extractor.extract_feedback(feedback_text, subjects, faculty, sections)

        solver_constraints = prior + fb.constraints
        result = timetable_solver.refine_with_feedback(spec, rejected, solver_constraints, objective_mode)
        if fb.unrecognized:
            result["unresolved_feedback"] = result.get("unresolved_feedback", []) + [fb.unrecognized]
        result["all_constraints"] = [c.model_dump() for c in solver_constraints if c.constraint_type != "unsupported"]
    except Exception as error:
        print(f"[refine_timetable] failed: {error}")
        return {"success": False, "error": "Couldn't refine this timetable from that feedback."}
    return {"success": True, **result}

@app.post("/generate/explain")
async def explain_timetable(payload: dict):
    try:
        spec = GenerationSpec.model_validate(payload.get("spec") or {})
        reference_entries = payload.get("reference_entries", [])
        target_entry = payload.get("target_entry")
        if not target_entry:
            return {"success": False, "error": "No entry specified"}
        explanation = timetable_solver.explain_placement(spec, reference_entries, target_entry, payload.get("objective_mode", "balanced"))
    except Exception as error:
        print(f"[explain_timetable] failed: {error}")
        return {"success": False, "error": "Couldn't explain this placement."}
    return {"success": True, "explanation": explanation}