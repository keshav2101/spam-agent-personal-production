"""
app/db/crud.py — All database operations for personal spam agent
"""
import os
from datetime import datetime
from typing import Optional
from sqlalchemy.orm import Session

from .models import AuditLog, FeedbackLog, RetrainQueue

STORE_BODY = os.getenv("STORE_BODY_TEXT", "false").lower() == "true"


# ── Audit Log ─────────────────────────────────────────────────────────────────

def create_audit(
    db: Session,
    email_id: str,
    message_id: Optional[str],
    sender: Optional[str],
    subject: Optional[str],
    spam_probability: float,
    action: str,
    model_version: str,
    indicators: dict,
    explanation: str,
    header_hash: Optional[str],
    body_hash: Optional[str],
    body_text: Optional[str] = None,
) -> AuditLog:
    record = AuditLog(
        email_id=email_id,
        message_id=message_id,
        sender=sender,
        subject=subject,
        spam_probability=spam_probability,
        action=action,
        model_version=model_version,
        indicators=indicators,
        explanation=explanation,
        header_hash=header_hash,
        body_hash=body_hash,
        body_text=body_text if STORE_BODY else None,
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    return record


def get_audit_by_email_id(db: Session, email_id: str) -> Optional[AuditLog]:
    return db.query(AuditLog).filter(AuditLog.email_id == email_id).first()


def list_recent_audits(db: Session, limit: int = 50):
    return (
        db.query(AuditLog)
        .order_by(AuditLog.timestamp.desc())
        .limit(limit)
        .all()
    )


# ── Feedback Log ──────────────────────────────────────────────────────────────

def create_feedback(
    db: Session,
    email_id: str,
    corrected_label: str,
    reason: Optional[str],
    user_id: Optional[str],
) -> FeedbackLog:
    record = FeedbackLog(
        email_id=email_id,
        corrected_label=corrected_label,
        reason=reason,
        user_id=user_id,
    )
    db.add(record)
    db.commit()
    db.refresh(record)

    # Also add into retraining queue if we have body_text
    audit = db.query(AuditLog).filter(AuditLog.email_id == email_id).first()
    if audit and audit.body_text:
        enqueue_retrain(db, email_id, corrected_label, audit.body_text, source="feedback")

    return record


# ── Retrain Queue ──────────────────────────────────────────────────────────────

def enqueue_retrain(
    db: Session,
    email_id: str,
    label: str,
    body_text: str,
    source: str = "feedback",
) -> RetrainQueue:
    record = RetrainQueue(
        email_id=email_id,
        label=label,
        body_text=body_text,
        source=source,
        used_in_training=False,
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    return record


def get_untrained_samples(db: Session):
    return (
        db.query(RetrainQueue)
        .filter(RetrainQueue.used_in_training == False)
        .all()
    )


def mark_samples_trained(db: Session, ids: list[str]):
    db.query(RetrainQueue).filter(
        RetrainQueue.id.in_(ids)
    ).update({"used_in_training": True}, synchronize_session=False)
    db.commit()
