"""
app/main.py — FastAPI application for personal spam agent
"""
import uuid
import logging
from typing import List
from fastapi import FastAPI, Depends, HTTPException, Request, UploadFile, File
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.db.session  import get_db, engine
from app.db.models   import AuditLog, FeedbackLog, RetrainQueue
import app.db.models as _models   # ensure tables created
from app.db.crud     import (
    create_audit, get_audit_by_email_id,
    create_feedback, list_recent_audits,
)
from app.models.schemas import (
    TriageRequest, TriageResponse,
    FeedbackRequest, FeedbackResponse,
    AuditRecord, HealthResponse,
)
from app.parsing.email_parser  import parse_raw_email
from app.features.extractors   import extract_indicators, indicators_to_features
from app.ml.model              import classifier

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("main")

# Create all tables
_models.Base.metadata.create_all(bind=engine)

app = FastAPI(
    title="Personal Gmail Spam Detection Agent",
    description=(
        "Classifies emails as SPAM / HAM / UNCERTAIN using a local calibrated TF-IDF model. "
        "Connects to Gmail via IMAP. No paid services required."
    ),
    version="2.0.0",
)

import os as _os
_HERE = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
templates = Jinja2Templates(directory=_os.path.join(_HERE, "app", "templates"))


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_explanation(prob: float, action: str, features: list) -> str:
    feat_str = ", ".join(features) if features else "no notable signals"
    if action == "QUARANTINE":
        return f"High spam score ({prob*100:.1f}%). Signals: {feat_str}."
    elif action == "DELIVER":
        return f"Low spam score ({prob*100:.1f}%). Message appears safe."
    else:
        return f"Uncertain score ({prob*100:.1f}%). Signals: {feat_str}. Manually review."


def _run_triage(email_raw: str, db: Session) -> TriageResponse:
    """Core triage pipeline shared by /triage and /ui/triage_file."""
    parsed     = parse_raw_email(email_raw)
    indicators = extract_indicators(parsed)
    features   = indicators_to_features(indicators)
    email_id   = str(uuid.uuid4())

    # ML inference —— graceful degradation
    if not classifier.is_loaded:
        return TriageResponse(
            action="MODEL_NOT_LOADED",
            email_id=email_id,
            spam_probability=0.5,
            explanation="No model found. Run `python -m app.ml.train` first.",
            indicators=indicators,
            model_version="none",
            features=features,
        )

    result = classifier.predict(parsed["body_text"] + " " + parsed["subject"])
    prob   = result["spam_probability"]
    ver    = result["model_version"]

    # Thresholds: 0.90 / 0.10
    if prob >= 0.90:
        action = "QUARANTINE"
    elif prob <= 0.10:
        action = "DELIVER"
    else:
        action = "UNCERTAIN"

    explanation = _make_explanation(prob, action, features)

    # Audit log (non-blocking)
    create_audit(
        db=db,
        email_id=email_id,
        message_id=parsed.get("message_id"),
        sender=parsed.get("sender"),
        subject=parsed.get("subject"),
        spam_probability=prob,
        action=action,
        model_version=ver,
        indicators=indicators,
        explanation=explanation,
        header_hash=parsed.get("header_hash"),
        body_hash=parsed.get("body_hash"),
        body_text=parsed.get("body_text"),   # stored only if STORE_BODY_TEXT=true
    )

    return TriageResponse(
        action=action,
        email_id=email_id,
        spam_probability=prob,
        explanation=explanation,
        indicators=indicators,
        model_version=ver,
        features=features,
    )


# ── Endpoints ─────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    """Interactive web UI dashboard."""
    return templates.TemplateResponse(request, "index.html")


@app.get("/health", response_model=HealthResponse)
def health(db: Session = Depends(get_db)):
    """Returns model status and DB connectivity."""
    try:
        db.execute(_models.AuditLog.__table__.select().limit(1))
        db_status = "ok"
    except Exception:
        db_status = "error"

    return HealthResponse(
        status="ok",
        model_loaded=classifier.is_loaded,
        model_version=classifier.version if classifier.is_loaded else None,
        db=db_status,
    )


@app.post("/triage", response_model=TriageResponse)
def triage_email(request: TriageRequest, db: Session = Depends(get_db)):
    """
    Classifies a raw email.
    Returns action (QUARANTINE / DELIVER / UNCERTAIN / MODEL_NOT_LOADED),
    probability, explanation, and risk indicators.
    """
    return _run_triage(request.email_raw, db)


@app.post("/ui/triage_file", response_model=TriageResponse)
async def triage_file(
    file: UploadFile = File(...),
    db:   Session    = Depends(get_db),
):
    """Accepts .eml / .txt / .pdf / .json file upload and triages it."""
    import io, json as _json
    import PyPDF2

    content  = await file.read()
    filename = file.filename.lower()

    try:
        if filename.endswith(".pdf"):
            reader   = PyPDF2.PdfReader(io.BytesIO(content))
            raw_text = "\n".join(page.extract_text() or "" for page in reader.pages)
        elif filename.endswith(".json"):
            raw_text = _json.dumps(_json.loads(content.decode("utf-8")), indent=2)
        else:
            raw_text = content.decode("utf-8", errors="ignore")
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"File parse error: {e}")

    if not raw_text.strip():
        raise HTTPException(status_code=400, detail="Uploaded file is empty or unreadable.")

    return _run_triage(raw_text, db)


@app.post("/feedback", response_model=FeedbackResponse)
def submit_feedback(request: FeedbackRequest, db: Session = Depends(get_db)):
    """
    Records analyst correction and adds the email to the retrain queue.
    """
    audit = get_audit_by_email_id(db, request.email_id)
    if not audit:
        raise HTTPException(status_code=404, detail=f"email_id {request.email_id} not found in audit log.")

    create_feedback(
        db=db,
        email_id=request.email_id,
        corrected_label=request.corrected_label.upper(),
        reason=request.reason,
        user_id=request.user_id,
    )
    return FeedbackResponse(
        status="feedback logged and queued for retraining",
        email_id=request.email_id,
    )


@app.get("/audit/stats")
def audit_stats(db: Session = Depends(get_db)):
    """Returns aggregate counts per action for dashboard stats."""
    rows = (
        db.query(AuditLog.action, func.count(AuditLog.id).label("count"))
        .group_by(AuditLog.action)
        .all()
    )
    counts = {r.action: r.count for r in rows}
    return {
        "total":       sum(counts.values()),
        "quarantine":  counts.get("QUARANTINE", 0),
        "deliver":     counts.get("DELIVER", 0),
        "uncertain":   counts.get("UNCERTAIN", 0),
    }


@app.get("/audit", response_model=List[AuditRecord])
def list_audits(limit: int = 50, db: Session = Depends(get_db)):
    """Lists the most recent audit records."""
    records = list_recent_audits(db, limit=limit)
    return [
        AuditRecord(
            email_id=r.email_id,
            message_id=r.message_id,
            sender=r.sender,
            subject=r.subject,
            timestamp=r.timestamp.isoformat(),
            spam_probability=r.spam_probability,
            action=r.action,
            model_version=r.model_version,
            indicators=r.indicators,
            explanation=r.explanation,
            header_hash=r.header_hash,
            body_hash=r.body_hash,
        )
        for r in records
    ]


@app.get("/audit/{email_id}", response_model=AuditRecord)
def get_audit(email_id: str, db: Session = Depends(get_db)):
    """Retrieves the full audit record for a classified email."""
    record = get_audit_by_email_id(db, email_id)
    if not record:
        raise HTTPException(status_code=404, detail="Audit record not found.")
    return AuditRecord(
        email_id=record.email_id,
        message_id=record.message_id,
        sender=record.sender,
        subject=record.subject,
        timestamp=record.timestamp.isoformat(),
        spam_probability=record.spam_probability,
        action=record.action,
        model_version=record.model_version,
        indicators=record.indicators,
        explanation=record.explanation,
        header_hash=record.header_hash,
        body_hash=record.body_hash,
    )
