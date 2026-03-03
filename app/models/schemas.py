"""
app/models/schemas.py — Pydantic request/response schemas
"""
from typing import Optional, Dict, Any, List
from pydantic import BaseModel


class TriageRequest(BaseModel):
    email_raw: str


class TriageResponse(BaseModel):
    action: str               # QUARANTINE | DELIVER | UNCERTAIN | MODEL_NOT_LOADED
    email_id: str
    spam_probability: float
    explanation: str
    indicators: Dict[str, Any]
    model_version: str
    features: List[str]


class FeedbackRequest(BaseModel):
    email_id: str
    corrected_label: str      # SPAM | HAM
    reason: Optional[str] = None
    user_id: Optional[str] = None


class FeedbackResponse(BaseModel):
    status: str
    email_id: str


class AuditRecord(BaseModel):
    email_id: str
    message_id: Optional[str]
    sender: Optional[str]
    subject: Optional[str]
    timestamp: str
    spam_probability: float
    action: str
    model_version: str
    indicators: Optional[Dict[str, Any]]
    explanation: str
    header_hash: Optional[str]
    body_hash: Optional[str]

    class Config:
        from_attributes = True


class HealthResponse(BaseModel):
    status: str
    model_loaded: bool
    model_version: Optional[str]
    db: str
