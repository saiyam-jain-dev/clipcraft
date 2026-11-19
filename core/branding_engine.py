import os
import subprocess
from .config import FFMPEG_EXE, DEFAULT_BRAND_NAME

def apply_channel_branding(video_path: str, output_path: str, brand_name: str = DEFAULT_BRAND_NAME, position: str = "top_left") -> str:
    """
    Overlays a clean, modern translucent social watermark pill badge onto the vertical short.
    Positions: 'top_left', 'top_right', 'side_left'
    """
    if not brand_name:
        cmd = [FFMPEG_EXE, "-y", "-i", video_path, "-c", "copy", output_path]
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
        return output_path

    # Clean brand text
    clean_brand = brand_name.replace("'", "").replace(":", "").strip()
    badge_text = f"yt / {clean_brand}"

    if position == "top_left":
        pos_expr = "x=60:y=100"
    elif position == "top_right":
        pos_expr = "x=w-tw-60:y=100"
    else:
        pos_expr = "x=60:y=h/2"

    vf = f"drawtext=text='{badge_text}':fontsize=32:fontcolor=white:box=1:boxcolor=black@0.55:boxborderw=14:{pos_expr}"

    cmd = [
        FFMPEG_EXE,
        "-y",
        "-i", video_path,
        "-vf", vf,
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "19",
        "-c:a", "copy",
        output_path
    ]
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
    return output_path
