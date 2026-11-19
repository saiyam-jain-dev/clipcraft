import re
import textwrap


def sec_to_ass_time(seconds: float) -> str:
    c = max(0, int(round(seconds * 100)))
    return f"{c // 360000}:{c // 6000 % 60:02d}:{c // 100 % 60:02d}.{c % 100:02d}"


def _srt_time(seconds):
    ms = max(0, int(round(seconds * 1000)))
    return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"


def _escape(text):
    return str(text).replace("\\", "").replace("{", "(").replace("}", ")").replace("\n", " ").replace("\r", " ")


def _ass_color(hex_color):
    color = hex_color.lstrip("#")
    return f"&H00{color[4:6]}{color[2:4]}{color[0:2]}&"


def _chunks(words, max_chars=26, max_words=4):
    groups, cur = [], []
    for w in words:
        if cur and (w["start"] - cur[-1]["end"] > 0.7 or
                    len(" ".join(x["word"] for x in cur)) + len(w["word"]) > max_chars):
            groups.append(cur)
            cur = []
        cur.append(w)
        if len(cur) >= max_words or re.search(r"[.!?][\"']?$", w["word"]):
            groups.append(cur)
            cur = []
    if cur:
        groups.append(cur)
    return groups


def generate_karaoke_ass(words: list, clip_start: float, clip_end: float, ass_output_path: str,
                         style="bold", color="#c8ff64", enable_subtitles=True, brand_name="", title="") -> str:
    duration = clip_end - clip_start
    local = [{"word": _escape(w["word"]), "start": max(0.0, w["start"] - clip_start),
              "end": min(duration, w["end"] - clip_start)}
             for w in words if w["end"] > clip_start and w["start"] < clip_end and w["end"] > w["start"]]
    groups = _chunks(local)
    font, size, outline = ("Arial", 68, 5) if style == "bold" else ("Segoe UI", 58, 3) if style == "clean" else ("Arial", 52, 2)
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
ScaledBorderAndShadow: yes
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Caption,{font},{size},&H00FFFFFF,&H00FFFFFF,&H00000000,&H90000000,-1,0,0,0,100,100,0,0,1,{outline},2,2,75,75,440,1
Style: Brand,Arial,32,&H00FFFFFF,&H00FFFFFF,&H00000000,&HA0000000,0,0,0,0,100,100,0,0,3,12,0,7,60,60,100,1
Style: Title,Arial,62,&H00FFFFFF,&H00FFFFFF,&H00000000,&HA0000000,-1,0,0,0,100,100,0,0,1,5,2,8,90,90,210,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = []

    def add(a, b, name, text, layer=0):
        if b > a:
            lines.append(f"Dialogue: {layer},{sec_to_ass_time(a)},{sec_to_ass_time(b)},{name},,0,0,0,,{text}")

    if enable_subtitles:
        active_color = _ass_color(color)
        for gi, group in enumerate(groups):
            for i, word in enumerate(group):
                a = word["start"]
                next_t = group[i + 1]["start"] if i + 1 < len(group) else (
                    groups[gi + 1][0]["start"] if gi + 1 < len(groups) else duration)
                # Keep the line visible between adjacent words, but don't hang across long pauses.
                b = min(duration, next_t, max(word["end"], min(next_t, word["end"] + 0.25)))
                if style == "minimal":
                    text = " ".join(_escape(w["word"]) for w in group)
                else:
                    parts = []
                    for j, w in enumerate(group):
                        text = w["word"].upper() if style == "bold" else w["word"]
                        parts.append((r"{\c" + active_color + "}" + text + r"{\r}") if i == j else text)
                    text = " ".join(parts)
                add(a, b, "Caption", text)
    if brand_name:
        add(0, duration, "Brand", _escape(brand_name), 1)
    if title:
        headline = r"\N".join(textwrap.wrap(_escape(title)[:90], width=26))
        add(0, min(3.0, duration), "Title", headline, 1)
    with open(ass_output_path, "w", encoding="utf-8") as f:
        f.write(header + "\n".join(lines) + "\n")

    srt_path = ass_output_path.rsplit(".", 1)[0] + ".srt"
    with open(srt_path, "w", encoding="utf-8") as f:
        for i, group in enumerate(groups, start=1):
            f.write(f"{i}\n{_srt_time(group[0]['start'])} --> {_srt_time(group[-1]['end'])}\n")
            f.write(" ".join(w["word"] for w in group) + "\n\n")
    return ass_output_path
