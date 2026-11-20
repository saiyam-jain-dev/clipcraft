"""Feature weights + a feedback loop (ported from NeonClipping's learn.py).

Every clip carries a `features` dict with values in 0..1: hook, flow, value,
trend (the editor scores / 100), energy (loudness peak), motion, flash, strobe,
cuts (measured signals). Score = weighted mean of the features that exist.

Star ratings (1-5) are stored locally in downloads/feedback.jsonl. With enough
ratings the weights are fitted by ridge regression pulled back toward the
defaults, blended in gradually (alpha = n/60, max 0.85). Nothing is uploaded.
"""
import json
import time
from pathlib import Path

import numpy as np

from .config import DOWNLOADS_DIR

FEATURES = ["hook", "flow", "value", "trend", "energy", "motion", "flash", "strobe", "cuts", "shout"]
DEFAULT = {"hook": .15, "flow": .09, "value": .14, "trend": .09,
           "energy": .13, "motion": .13, "flash": .07, "strobe": .05, "cuts": .07, "shout": .08}
MIN_RATINGS = 8

FEEDBACK_FILE = Path(DOWNLOADS_DIR) / "feedback.jsonl"
WEIGHTS_FILE = Path(DOWNLOADS_DIR) / "weights.json"


def load_weights() -> dict:
    try:
        w = json.loads(WEIGHTS_FILE.read_text(encoding="utf-8"))
        return {k: float(w.get(k, DEFAULT[k])) for k in FEATURES}
    except (OSError, ValueError):
        return dict(DEFAULT)


def score(features: dict, weights: dict = None) -> float:
    """Weighted mean over the features that exist (missing ones do not count as zero)."""
    weights = weights or DEFAULT
    num = den = 0.0
    for k in FEATURES:
        v = features.get(k)
        if v is None:
            continue
        num += weights[k] * float(v)
        den += weights[k]
    return num / den if den else 0.0


def ratings() -> list:
    out = []
    if FEEDBACK_FILE.exists():
        for line in FEEDBACK_FILE.read_text(encoding="utf-8").splitlines():
            try:
                out.append(json.loads(line))
            except ValueError:
                pass
    return out


def clip_features(clip: dict) -> dict:
    """Best-effort 0..1 feature vector for a clip, from stored features or its scores."""
    feats = dict(clip.get("features") or {})
    scores = clip.get("scores") or {}
    for k in ("hook", "flow", "value", "trend"):
        if feats.get(k) is None and scores.get(k) is not None:
            try:
                feats[k] = max(0.0, min(1.0, float(scores[k]) / 100.0))
            except (TypeError, ValueError):
                pass
    if feats.get("virality") is None and clip.get("virality_score") is not None:
        try:
            feats["virality"] = max(0.0, min(1.0, float(clip["virality_score"]) / 100.0))
        except (TypeError, ValueError):
            pass
    return {k: v for k, v in feats.items() if isinstance(v, (int, float))}


def record(rating: int, features: dict, extra: dict = None) -> dict:
    row = {"t": int(time.time()), "rating": int(rating),
           "features": {k: float(v) for k, v in features.items()}, **(extra or {})}
    FEEDBACK_FILE.parent.mkdir(parents=True, exist_ok=True)
    with FEEDBACK_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return refit()


def refit() -> dict:
    """Ridge regression of rating on features, shrunk toward DEFAULT. Saves weights.json."""
    rows = [r for r in ratings() if r.get("features")]
    n = len(rows)
    summary = {"ratings": n, "weights": dict(DEFAULT), "fitted": False, "insight": []}
    if n >= MIN_RATINGS:
        X = np.array([[float(r["features"].get(k) or 0.0) for k in FEATURES] for r in rows])
        y = np.array([(r["rating"] - 3) / 2.0 for r in rows])      # -1..1
        corr = {}
        for j, k in enumerate(FEATURES):
            if X[:, j].std() > 1e-6 and y.std() > 1e-6:
                corr[k] = float(np.corrcoef(X[:, j], y)[0, 1])
        w0 = np.array([DEFAULT[k] for k in FEATURES])
        lam = 2.0
        Xc = X - X.mean(axis=0)
        A = Xc.T @ Xc + lam * np.eye(len(FEATURES))
        b = Xc.T @ y + lam * w0
        wf = np.clip(np.linalg.solve(A, b), 0.01, None)
        wf = wf / wf.sum()
        alpha = min(0.85, n / 60.0)
        w = (1 - alpha) * w0 + alpha * wf
        w = w / w.sum()
        summary.update(weights={k: round(float(v), 4) for k, v in zip(FEATURES, w)},
                       fitted=True, alpha=round(alpha, 2))
        ranked = sorted(corr.items(), key=lambda kv: -abs(kv[1]))[:3]
        summary["insight"] = [f"{k}: {'higher' if c > 0 else 'lower'} = better rated ({c:+.2f})"
                              for k, c in ranked]
    WEIGHTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    WEIGHTS_FILE.write_text(json.dumps(summary["weights"]), encoding="utf-8")
    return summary
