"""
app/db/models.py — SQLAlchemy ORM models for personal spam agent
"""
import uuid
from datetime import datetime
from sqlalchemy import Column, String, Float, DateTime, Text, Boolean, JSON
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from .session import Base


def _uuid():
    return str(uuid.uuid4())


class AuditLog(Base):
    """One row per email classified."""
    __tablename__ = "audit_log"

    id            = Column(String, primary_key=True, default=_uuid)
    email_id      = Column(String, unique=True, index=True)   # our UUID
    message_id    = Column(String, index=True, nullable=True) # RFC-822 Message-ID
    sender        = Column(String, nullable=True)
    subject       = Column(String, nullable=True)
    timestamp     = Column(DateTime, default=datetime.utcnow)
    spam_probability = Column(Float)
    action        = Column(String)          # QUARANTINE | DELIVER | UNCERTAIN
    model_version = Column(String)
    indicators    = Column(JSON, nullable=True)  # dict of risk flags
    explanation   = Column(Text)
    header_hash   = Column(String, nullable=True)  # SHA-256 of raw headers
    body_hash     = Column(String, nullable=True)  # SHA-256 of body text
    body_text     = Column(Text, nullable=True)    # only if STORE_BODY_TEXT=true


class FeedbackLog(Base):
    """Analyst corrections."""
    __tablename__ = "feedback_log"

    id              = Column(String, primary_key=True, default=_uuid)
    email_id        = Column(String, index=True)
    corrected_label = Column(String)    # SPAM | HAM
    reason          = Column(Text, nullable=True)
    user_id         = Column(String, nullable=True)
    timestamp       = Column(DateTime, default=datetime.utcnow)


class RetrainQueue(Base):
    """Labeled samples queued for the next model retrain."""
    __tablename__ = "retrain_queue"

    id              = Column(String, primary_key=True, default=_uuid)
    email_id        = Column(String, index=True)
    label           = Column(String)    # SPAM | HAM
    body_text       = Column(Text)      # stored for retraining (regardless of STORE_BODY_TEXT)
    source          = Column(String)    # "feedback" | "seed"
    used_in_training = Column(Boolean, default=False)
    timestamp       = Column(DateTime, default=datetime.utcnow)
