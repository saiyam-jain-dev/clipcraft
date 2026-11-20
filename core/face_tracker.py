import os
import cv2
import numpy as np
from .config import YUNET_MODEL_PATH


def init_face_detector():
    if os.path.isfile(YUNET_MODEL_PATH) and os.path.getsize(YUNET_MODEL_PATH) > 10000:
        try:
            return "yunet", cv2.FaceDetectorYN.create(YUNET_MODEL_PATH, "", (640, 360), 0.75, 0.3, 100)
        except cv2.error:
            pass
    return "haar", cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")


def analyze_video_speakers(video_path: str, start_sec: float, end_sec: float, sample_interval=0.5,
                           progress_callback=None) -> dict:
    cap = cv2.VideoCapture(video_path)
    width, height = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    if not width or not height:
        cap.release()
        raise RuntimeError("Cannot decode video for reframing.")
    det_type, detector = init_face_detector()
    samples, last_hist = [], None
    t = start_sec
    try:
        while t < end_sec:
            cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
            ok, frame = cap.read()
            if not ok:
                break
            scale = min(1.0, 640 / width)
            small = cv2.resize(frame, (int(width * scale), int(height * scale)))
            gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
            hist = cv2.calcHist([gray], [0], None, [32], [0, 256])
            cv2.normalize(hist, hist)
            cut = last_hist is not None and cv2.compareHist(last_hist, hist, cv2.HISTCMP_BHATTACHARYYA) > 0.5
            last_hist = hist
            boxes = []
            if det_type == "yunet":
                detector.setInputSize((small.shape[1], small.shape[0]))
                _, found = detector.detect(small)
                if found is not None:
                    boxes = [tuple(float(x) / scale for x in f[:4]) for f in found]
            else:
                found = detector.detectMultiScale(gray, 1.1, 5, minSize=(24, 24))
                boxes = [tuple(float(x) / scale for x in b) for b in found]
            boxes.sort(key=lambda b: b[2] * b[3], reverse=True)
            samples.append({"t": round(t - start_sec, 3), "faces": boxes, "cut": bool(cut)})
            if progress_callback:
                progress_callback((t - start_sec) / max(0.1, end_sec - start_sec) * 100)
            t += sample_interval
    finally:
        cap.release()

    face_samples = [s for s in samples if s["faces"]]
    # Split only when two distinct faces are present across most of the video (> 65%),
    # separated by at least 35% of the frame width (e.g. podcast dual interview setup).
    dual = sum(1 for s in face_samples if len(s["faces"]) >= 2
               and abs(s["faces"][0][0] - s["faces"][1][0]) > width * 0.35)
    framed = sum(1 for s in face_samples if s["faces"][0][2] >= width * 0.04)
    face_ratio = framed / len(samples) if samples else 0.0
    # In auto mode, prefer 'fill' (focused vertical face tracking) or 'fit' (clean letterbox with blurred bg)
    # unless two distinct separated podcast speakers are persistently present (> 65% of total frames).
    selected = ("split" if len(samples) and dual / len(samples) >= 0.65 and face_ratio >= 0.5
                else "fill" if face_ratio >= 0.35 else "fit")
    return {"mode": selected, "face_ratio": round(face_ratio, 2), "frame_width": width, "frame_height": height,
            "samples": samples}


def activity_profile(video_path: str, start_sec: float, end_sec: float, hop: float = 1.0) -> dict:
    """Frame-difference z-scores. Spikes are cuts, action, and camera moves — useful when speech is thin."""
    cap = cv2.VideoCapture(video_path)
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)) or 1
    values = []
    prev = None
    t = start_sec
    try:
        while t < end_sec:
            cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
            ok, frame = cap.read()
            if not ok:
                break
            scale = min(1.0, 160 / width)
            small = cv2.cvtColor(cv2.resize(frame, (max(2, int(frame.shape[1] * scale)), max(2, int(frame.shape[0] * scale)))),
                                 cv2.COLOR_BGR2GRAY)
            if prev is not None and prev.shape == small.shape:
                values.append(float(np.mean(cv2.absdiff(small, prev))))
            else:
                values.append(0.0)
            prev = small
            t += hop
    finally:
        cap.release()
    arr = np.array(values, dtype=np.float32)
    if len(arr) < 3:
        return {"offset": start_sec, "hop": hop, "z": arr, "audible": False}
    mu, sd = float(arr.mean()), float(arr.std() + 1e-6)
    return {"offset": start_sec, "hop": hop, "z": (arr - mu) / sd, "audible": True}


def crop_trajectory(info: dict, crop_w: int, crop_h: int, which="primary") -> list:
    w, h = info["frame_width"], info["frame_height"]
    center = {"primary": 0.5, "left": 0.25, "right": 0.75}[which] * w
    cx, cy = center, h / 2
    points = []
    last_face_time = -10
    for sample in info["samples"]:
        faces = sample["faces"]
        face = None
        if faces:
            if which == "left":
                # Only consider faces strictly on the left half of the video frame
                left_faces = [f for f in faces if (f[0] + f[2] / 2) < w * 0.52]
                face = min(left_faces, key=lambda b: b[0] + b[2] / 2) if left_faces else None
            elif which == "right":
                # Only consider faces strictly on the right half of the video frame
                right_faces = [f for f in faces if (f[0] + f[2] / 2) > w * 0.48]
                face = max(right_faces, key=lambda b: b[0] + b[2] / 2) if right_faces else None
            else:
                face = faces[0]

        if face is not None:
            tx = face[0] + face[2] / 2
            ty = face[1] + face[3] / 2 + crop_h * 0.18
            last_face_time = sample["t"]
        elif sample["cut"] or sample["t"] - last_face_time > 3:
            tx, ty = center, h / 2
        else:
            tx, ty = cx, cy

        if sample["cut"] or not points:
            cx, cy = tx, ty
        else:
            # A dead zone prevents jitter from small face-detection changes; larger moves ease into the new crop.
            if abs(tx - cx) > crop_w * 0.08:
                cx += (tx - cx) * 0.4
            if abs(ty - cy) > crop_h * 0.08:
                cy += (ty - cy) * 0.4
        x = int(max(0, min(w - crop_w, cx - crop_w / 2))) // 2 * 2
        y = int(max(0, min(h - crop_h, cy - crop_h / 2))) // 2 * 2
        points.append((sample["t"], x, y, sample["cut"]))
    return points or [(0.0, int(max(0, (w - crop_w) / 2)), int(max(0, (h - crop_h) / 2)), False)]
