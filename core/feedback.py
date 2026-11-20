import json
import os
import time
from .config import DOWNLOADS_DIR
from . import learn

FEEDBACK_FILE = os.path.join(DOWNLOADS_DIR, "clip_feedback.jsonl")


def record_feedback(job_id: str, clip_id: str, rating: int, clip_data: dict):
    """Record a 1-5 star rating and refit the learned scoring weights."""
    features = learn.clip_features(clip_data)
    return learn.record(rating, features,
                        {"job_id": job_id, "clip_id": clip_id,
                         "layout": clip_data.get("layout"), "style": clip_data.get("caption_style")})


def get_feedback_stats() -> dict:
    return learn.refit()
