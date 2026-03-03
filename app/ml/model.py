"""
app/ml/model.py — Calibrated spam classifier loader
"""
import os
import glob
import logging
import joblib
from typing import Optional, Dict, Any

logger = logging.getLogger("ml.model")

MODEL_REGISTRY = os.getenv("MODEL_REGISTRY", "model_registry")


class SpamClassifier:
    """
    Wraps a calibrated sklearn pipeline loaded from model_registry/.
    Thread-safe for read; call reload() after retraining.
    """

    def __init__(self):
        self._pipeline = None
        self._version  = "none"
        self.load_latest()

    def load_latest(self):
        """Loads the most recently saved model from model_registry/."""
        pattern = os.path.join(MODEL_REGISTRY, "model_v*.pkl")
        files   = sorted(glob.glob(pattern))
        if not files:
            logger.warning(
                f"No model found in {MODEL_REGISTRY}/. "
                "Run `python -m app.ml.train` to train the initial model."
            )
            self._pipeline = None
            self._version  = "none"
            return

        latest = files[-1]
        try:
            self._pipeline = joblib.load(latest)
            self._version  = os.path.basename(latest).replace(".pkl", "")
            logger.info(f"Model loaded: {self._version}")
        except Exception as e:
            logger.error(f"Failed to load {latest}: {e}")
            self._pipeline = None
            self._version  = "none"

    @property
    def is_loaded(self) -> bool:
        return self._pipeline is not None

    @property
    def version(self) -> str:
        return self._version

    def predict(self, text: str) -> Dict[str, Any]:
        """
        Returns {prob, label, model_version} or raises RuntimeError if not loaded.
        """
        if not self.is_loaded:
            raise RuntimeError("MODEL_NOT_LOADED")

        prob  = float(self._pipeline.predict_proba([text])[0][1])
        label = "SPAM" if prob >= 0.5 else "HAM"
        return {
            "spam_probability": prob,
            "label":            label,
            "model_version":    self._version,
        }


# Module-level singleton — imported by main.py and imap_watcher.py
classifier = SpamClassifier()
