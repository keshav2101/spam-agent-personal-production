"""
app/ml/train.py — Train / retrain the calibrated spam classifier
─────────────────────────────────────────────────────────────────
Usage:
    python -m app.ml.train

Seed corpus layout (download instructions in README):
    data/
    ├── spam/   ← one .txt file per spam email
    └── ham/    ← one .txt file per ham email

Also reads NEW labeled rows from the retrain_queue DB table
and marks them as used after training.

Output:
    model_registry/model_vYYYYMMDD_HHMMSS.pkl
"""
import os
import pathlib
import logging
import joblib
from datetime import datetime
from sklearn.pipeline        import Pipeline
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.svm             import LinearSVC
from sklearn.calibration     import CalibratedClassifierCV
from sklearn.model_selection import StratifiedKFold, cross_val_score
from sklearn.utils           import shuffle as sk_shuffle

from app.db.session import SessionLocal
from app.db.crud    import get_untrained_samples, mark_samples_trained

# Ensure DB tables exist (important when running train before first API start)
from app.db.session import engine, Base
import app.db.models  # noqa: F401 — registers all ORM models
Base.metadata.create_all(bind=engine)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("train")

DATA_SPAM      = os.getenv("DATA_SPAM",      "data/spam")
DATA_HAM       = os.getenv("DATA_HAM",       "data/ham")
MODEL_REGISTRY = os.getenv("MODEL_REGISTRY", "model_registry")


def _load_corpus_dir(folder: str, label: int) -> tuple[list, list]:
    """Reads all files from a folder (any extension / no extension)."""
    texts, labels = [], []
    for path in pathlib.Path(folder).rglob("*"):
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
            if text.strip():
                texts.append(text)
                labels.append(label)
        except Exception:
            pass
    logger.info(f"  Loaded {len(texts)} samples from {folder}")
    return texts, labels


def train():
    # ── 1. Load seed corpus ──────────────────────────────────────────────────
    spam_texts, spam_labels = _load_corpus_dir(DATA_SPAM, 1)
    ham_texts,  ham_labels  = _load_corpus_dir(DATA_HAM,  0)

    texts  = spam_texts  + ham_texts
    labels = spam_labels + ham_labels

    if not texts:
        logger.error(
            "No seed data found. Create data/spam/ and data/ham/ with .txt files.\n"
            "See README for SpamAssassin download instructions."
        )
        return

    # ── 2. Augment with DB feedback samples ──────────────────────────────────
    db = SessionLocal()
    try:
        samples = get_untrained_samples(db)
        db_ids  = []
        for s in samples:
            if s.body_text:
                texts.append(s.body_text)
                labels.append(1 if s.label == "SPAM" else 0)
                db_ids.append(s.id)
        if db_ids:
            logger.info(f"  Added {len(db_ids)} samples from feedback DB")
    finally:
        db.close()

    texts, labels = sk_shuffle(texts, labels, random_state=42)
    logger.info(f"Total training samples: {len(texts)}")

    # ── 3. Build calibrated pipeline ─────────────────────────────────────────
    vectorizer = TfidfVectorizer(
        analyzer="char_wb",     # character n-grams (robust to obfuscation)
        ngram_range=(2, 5),
        max_features=100_000,
        sublinear_tf=True,
    )

    # Word n-grams merged via FeatureUnion would be ideal for production;
    # char n-grams alone work well and avoid multi-vectorizer complexity here.
    base_clf = LinearSVC(C=0.5, max_iter=5000)

    calibrated = CalibratedClassifierCV(base_clf, cv=StratifiedKFold(n_splits=5))

    pipeline = Pipeline([
        ("tfidf", vectorizer),
        ("clf",   calibrated),
    ])

    # ── 4. Cross-validation ───────────────────────────────────────────────────
    logger.info("Running 5-fold cross-validation…")
    cv_scores = cross_val_score(
        pipeline, texts, labels,
        cv=StratifiedKFold(n_splits=5, shuffle=True, random_state=42),
        scoring="f1",
        n_jobs=-1,
    )
    logger.info(f"CV F1: {cv_scores.mean():.4f} ± {cv_scores.std():.4f}")

    # ── 5. Final fit on all data ──────────────────────────────────────────────
    pipeline.fit(texts, labels)

    # ── 6. Save to model registry ─────────────────────────────────────────────
    os.makedirs(MODEL_REGISTRY, exist_ok=True)
    version   = datetime.utcnow().strftime("v%Y%m%d_%H%M%S")
    out_path  = os.path.join(MODEL_REGISTRY, f"model_{version}.pkl")
    joblib.dump(pipeline, out_path)
    logger.info(f"✓ Model saved: {out_path}")

    # ── 7. Mark DB samples as trained ────────────────────────────────────────
    if db_ids:
        db = SessionLocal()
        try:
            mark_samples_trained(db, db_ids)
        finally:
            db.close()

    logger.info("Training complete. Reload the API server to pick up the new model.")
    return out_path


if __name__ == "__main__":
    train()
