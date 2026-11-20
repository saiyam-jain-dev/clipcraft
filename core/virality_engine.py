import re
import math
import wave
import numpy as np
from .llm import complete_json, llm_configured, LLMError

STRONG = re.compile(
    r"\b(secret|never|always|mistake|truth|why|how to|stop|nobody|everyone|most people|actually|shocking|insane|"
    r"crazy|biggest|worst|best|wrong|lie|lied|million|billion|money|rich|broke|fail|failed|failure|success|"
    r"died|death|kill|fear|love|hate|regret|change(?:d)? my life|game ?changer|unbelievable|important|problem|"
    r"realized|discovered|the reason|here's|imagine|what if|listen|warning|dangerous|free|first time)\b", re.I)

# Emotional keywords from NeonClipping - for detecting high-impact moments
EMOTIONAL_KEYWORDS = re.compile(
    r"\b(oh my god|oh shit|oh no|oh wow|holy shit|what the hell|what the fuck|no way|are you kidding|"
    r"you're kidding|unbelievable|incredible|insane|crazy|this is insane|this is crazy|"
    r"run|help|police|stop|wait|look|watch this|check this out|listen to this|"
    r"bhaago|arre yaar|yaar|kya|matlab|pagal|bechara|chup kar|sach mein|"
    r"literally dying|i can't believe|this is it|the moment|that moment|"
    r"shocking|surprising|unexpected|sudden|boom|bang|explosion|"
    r"get out|get out of here|you won't believe|you have to see this)\b", re.I)
# Stream reactions and story openers. Bare "chat" is too common in VODs to count on its own.
REACTION = re.compile(
    r"\b(?:bro+|bruh|dude|nah|no way|hold on|what the|oh my god|oh my|holy(?: shit| crap| moly)?|"
    r"let'?s go|i can'?t(?: believe)?|are you (?:serious|kidding)|you(?:'re| are) (?:lying|capping)|"
    r"shut up|clip (?:this|that)|goated|cooked|clutch|deadass|no shot|ain'?t no way|"
    r"(?:he|she|they) did not|on god|unreal|ridiculous|wait what|yo+)\b", re.I)
LAUGH = re.compile(r"\b(?:ha(?:ha)+|he(?:he)+|lol+|lmao+|lmfao+|rofl)\b|\[laughter\]|\(laughter\)|\*laughs?\*", re.I)
STORY = re.compile(
    r"\b(?:one time|this one time|yesterday|last night|the other day|let me tell you|true story|"
    r"i remember when|back when|so basically|long story|story time|you will not believe|"
    r"this (?:guy|girl|dude|kid|man|woman)|so this (?:guy|dude|kid|man))\b", re.I)
BORING = re.compile(
    r"\b(?:subscribe|like and subscribe|follow me|link in|sponsor(?:ed)?|promo code|use code|"
    r"welcome back|thanks for watching|see you (?:next|in the next)|starting soon|be right back|"
    r"one second|my twitter|my instagram|join (?:the |my )?discord)\b", re.I)
DANGLING_END = re.compile(r"\b(?:and|but|because|so|or|if|when|that|which|to|of)$", re.I)
FILLER_START = re.compile(
    r"^(um+|uh+|er+|ah+|so|and|but|because|like|yeah|okay|ok|right|or|which|also|then|well|anyway|anyhow|"
    r"alright|all right|basically|literally|now|i mean|you know|let me|welcome|thanks|thank you|good morning|"
    r"good afternoon|good evening|hi everyone|hey everyone|hey guys|hello)\b", re.I)
REFERENTIAL_START = re.compile(r"^(he|she|they|it|this|that|these|those|him|her|them|there)\b", re.I)
WEAK_END = re.compile(r"^(yeah|yep|right|okay|ok|so|anyway|well|cool|nice|wow|hmm+|uh+)\b[.!?]*$", re.I)
STOPWORDS = set("""a an the and or but if then so of to in on at for with by from as is are was were be been being i you he she
it we they me him her us them my your his its our their this that these those there here what which who whom whose
when where why how all any both each few more most other some such no nor not only own same than too very can will
just don't do does did doing have has had having would could should im i'm it's that's you're we're they're gonna
like yeah um uh okay really know think going get got go one thing things lot kind sort mean right well also""".split())


def fmt_ts(sec: float) -> str:
    sec = int(sec)
    return f"{sec // 3600}:{sec % 3600 // 60:02d}:{sec % 60:02d}" if sec >= 3600 else f"{sec // 60}:{sec % 60:02d}"


# ----------------------------------------------------------------------------- audio energy

def audio_energy_profile(wav_path: str, offset: float = 0.0, hop: float = 0.5) -> dict:
    """Per-hop loudness (dB) z-scores. Spikes ~ laughter, shouting, applause, hype moments."""
    with wave.open(wav_path, "rb") as wf:
        sr = wf.getframerate()
        hop_n = int(sr * hop)
        db = []
        while True:
            raw = wf.readframes(hop_n * 240)
            if not raw:
                break
            x = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
            n = len(x) // hop_n
            if n:
                frames = x[: n * hop_n].reshape(n, hop_n)
                db.extend(20 * np.log10(np.sqrt((frames ** 2).mean(axis=1)) + 1e-6))
    db = np.array(db, dtype=np.float32)
    voiced = db[db > -50]
    mu, sd = (voiced.mean(), voiced.std() + 1e-6) if len(voiced) > 10 else (db.mean(), db.std() + 1e-6)
    return {"offset": offset, "hop": hop, "z": (db - mu) / sd, "audible": bool(len(voiced))}


def raised_voice_profile(wav_path: str, offset: float = 0.0, hop: float = 0.5) -> dict:
    """
    Detects raised voice/shouting by analyzing energy in the 2-4 kHz band.
    Human shouting has strong energy in this frequency range (NeonClipping approach).
    """
    raised_scores = []
    try:
        with wave.open(wav_path, "rb") as wf:
            sr = wf.getframerate()
            hop_n = int(sr * hop)
            while True:
                raw = wf.readframes(hop_n * 240)
                if not raw:
                    break
                x = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
                n = len(x) // hop_n
                if not n:
                    continue
                frames = x[: n * hop_n].reshape(n, hop_n)
                fft = np.fft.rfft(frames, axis=1)
                freqs = np.fft.rfftfreq(hop_n, 1 / sr)
                band_mask = (freqs >= 2000) & (freqs <= 4000)
                band_energy = (np.abs(fft[:, band_mask]) ** 2).mean(axis=1)
                total_energy = (np.abs(fft) ** 2).mean(axis=1)
                raised_scores.extend((band_energy / (total_energy + 1e-10)).tolist())
    except (wave.Error, OSError):
        pass
    if not raised_scores:
        return {"offset": offset, "hop": hop, "z": np.array([]), "audible": False}
    raised_scores = np.array(raised_scores, dtype=np.float32)
    mu, sd = (raised_scores.mean(), raised_scores.std() + 1e-6) if len(raised_scores) > 10 else (raised_scores.mean(), 1.0)
    return {"offset": offset, "hop": hop, "z": (raised_scores - mu) / sd, "audible": bool(len(raised_scores))}


def _energy(profile, start, end):
    z = profile["z"]
    a = max(0, int((start - profile["offset"]) / profile["hop"]))
    b = min(len(z), int((end - profile["offset"]) / profile["hop"]))
    if b <= a:
        return 0.0, 0.0, 0.0
    w = z[a:b]
    hook = z[a:min(b, a + int(3 / profile["hop"]))]
    return float(w.mean()), float((w > 1.0).mean()), float(hook.mean()) if len(hook) else 0.0


# ----------------------------------------------------------------------------- shared helpers

def _clip_text(sents, i, j):
    return " ".join(s["text"] for s in sents[i:j + 1])


def _keywords(text, n=4):
    counts = {}
    for w in re.findall(r"[A-Za-z][A-Za-z']{3,}", text.lower()):
        if w not in STOPWORDS:
            counts[w] = counts.get(w, 0) + 1
    return [w for w, _ in sorted(counts.items(), key=lambda kv: -kv[1])[:n]]


def _hashtags(text):
    return ["#Shorts"] + ["#" + k.replace("'", "").capitalize() for k in _keywords(text, 4)]


DISCOURSE = re.compile(
    r"^(?:um+|uh+|er+|ah+|so|and|but|because|like|yeah|okay|ok|right|also|then|well|anyway|anyhow|"
    r"alright|all right|basically|literally|now|i mean|you know)[,:]?\s+", re.I)
CONTINUATION = re.compile(
    r"^(?:there(?:'s| is| are) also|another thing|the other|which |a less |the same |that kind|this kind|"
    r"today i'?m|i'?m a |my name is|i write |i know that)\b", re.I)


def _core_line(text):
    """Drop a leading 'Now,' / 'So,' so the real claim is what gets judged."""
    line = text.strip()
    for _ in range(3):
        nxt = DISCOURSE.sub("", line, count=1).strip()
        if nxt == line or len(nxt.split()) < 4:
            break
        line = nxt
    return line


_NOT_NAMES = {"The", "And", "But", "How", "Why", "What", "When", "Where", "This", "That", "Wait", "Just",
              "Look", "Now", "Well", "Yes", "Not", "For", "With", "From", "Into", "Then", "They", "There"}


def _named_thing(text):
    """A real capitalized name after the first letter, e.g. 'the Panic Monster'."""
    words = re.findall(r"\b[A-Z][a-zA-Z]{2,}\b", text.strip()[1:])
    return any(w not in _NOT_NAMES for w in words)


def _weak_open(text):
    """Throat-clearing and sentences that only make sense after the previous one."""
    first = _core_line(text)
    words = first.split()
    if not words:
        return True
    # If text is predominantly non-ASCII (e.g. Hindi / regional scripts), don't apply English filler heuristics
    is_non_ascii = len([c for c in first if ord(c) > 127]) > len(first) * 0.25
    if is_non_ascii:
        return len(words) < 2
    has_payload = bool(
        STRONG.search(first) or EMOTIONAL_KEYWORDS.search(first) or REACTION.search(first)
        or STORY.search(first) or LAUGH.search(first) or re.search(r"\d", first)
        or first.endswith("?") or _named_thing(text))
    if CONTINUATION.search(first):
        return True
    if REFERENTIAL_START.search(first) and not has_payload:
        return True
    if FILLER_START.search(first) and (len(words) <= 6 or not has_payload):
        return True
    return len(words) < 4 and not first.endswith("?")


def _tighten_range(sents, i, j, min_dur, max_dur):
    """Drop a weak greeting or a dangling 'yeah' so the clip opens on a hook and ends on a point."""
    n = len(sents)
    while i < j and _weak_open(sents[i]["text"]) and sents[j]["end"] - sents[i + 1]["start"] >= min_dur - 1:
        i += 1
    while j > i and WEAK_END.search(sents[j]["text"].strip()) and sents[j - 1]["end"] - sents[i]["start"] >= min_dur - 1:
        j -= 1
    while j > i and sents[j]["end"] - sents[i]["start"] > max_dur:
        j -= 1
    while j + 1 < n and sents[j]["end"] - sents[i]["start"] < min_dur and sents[j + 1]["end"] - sents[i]["start"] <= max_dur:
        j += 1
    return i, j


def _fit_bounds(sents, i, j, min_dur, max_dur):
    """Adjust sentence range so the clip lands inside [min_dur, max_dur], preferring a sentence-final ending."""
    n = len(sents)
    while j > i and sents[j]["end"] - sents[i]["start"] > max_dur:
        j -= 1
    while j + 1 < n and sents[j]["end"] - sents[i]["start"] < min_dur and sents[j + 1]["end"] - sents[i]["start"] <= max_dur:
        j += 1
    k = j
    while not sents[k]["terminal"] and k + 1 < n and k - j < 2 and sents[k + 1]["end"] - sents[i]["start"] <= max_dur:
        k += 1
    if sents[k]["terminal"]:
        j = k
    return _tighten_range(sents, i, j, min_dur, max_dur)


def _headline(sents, i, j):
    """Title from the sharpest line in the clip, not whatever sentence happened to come first."""
    best, best_score = "", -10 ** 9
    for s in sents[i:j + 1]:
        line = s["text"].strip()
        if _weak_open(line) or WEAK_END.search(line):
            continue
        score = min(3, len(STRONG.findall(line))) * 8
        score += min(2, len(REACTION.findall(line))) * 10
        score += 12 if STORY.search(line) else 0
        score += 8 if LAUGH.search(line) else 0
        score += 14 if _named_thing(line) else 0
        score += 12 if line.endswith("?") else 0
        score += 8 if line.endswith("!") else 0
        score += 6 if re.search(r"\d", line) else 0
        score += 4 if re.search(r"\byou(r)?\b", line, re.I) else 0
        score -= 6 if len(line.split()) > 22 else 0
        if score > best_score:
            best, best_score = line, score
    if not best:
        for s in sents[i:j + 1]:
            line = s["text"].strip()
            if not _weak_open(line):
                best = line
                break
    if not best:
        best = sents[j]["text"].strip()
    stripped = re.sub(r"^(?:and|so|but|now|well|because)\b[,\s]+(?:(?:then|so)\b[,\s]+)?", "", best, flags=re.I)
    if len(stripped.split()) >= 4:
        best = stripped[0].upper() + stripped[1:]
    title = best if len(best) <= 72 else best[:69].rsplit(" ", 1)[0] + "..."
    caption = title if title.endswith((".", "?", "!")) else title.rstrip(",") + "."
    return title, caption


def _times(sents, words, i, j, max_dur, range_start=0.0, range_end=None):
    w0, w1 = sents[i]["w0"], sents[j]["w1"]
    start = max(range_start, words[w0]["start"])
    while w1 > w0 and words[w1]["end"] - start > max_dur:
        w1 -= 1
    end = min(words[w1]["end"], start + max_dur)
    if range_end is not None:
        end = min(end, range_end)
    budget = max(0.0, max_dur - (end - start))
    prev_end = words[w0 - 1]["end"] if w0 > 0 else range_start
    before = min(0.25, budget, max(0.0, start - max(range_start, prev_end)))
    start -= before
    budget -= before
    next_start = words[w1 + 1]["start"] if w1 + 1 < len(words) else end + 1.0
    end += min(0.45, budget, max(0.0, next_start - 0.02 - end))
    if range_end is not None:
        end = min(end, range_end)
    return start, end


def _select(cands, target, max_overlap=0.25):
    cands.sort(key=lambda c: c["virality_score"], reverse=True)
    chosen = []
    for c in cands:
        ok = True
        for s in chosen:
            inter = min(c["end_time"], s["end_time"]) - max(c["start_time"], s["start_time"])
            if inter > max_overlap * min(c["duration"], s["duration"]):
                ok = False
                break
        if ok:
            chosen.append(c)
            if len(chosen) >= target:
                break
    return chosen


def _make_clip(sents, words, i, j, max_dur, min_dur=0.0, range_start=0.0, range_end=None, **fields):
    start, end = _times(sents, words, i, j, max_dur, range_start, range_end)
    if end - start < min_dur - 1e-6 or end <= start:
        return None
    text = " ".join(w["word"] for w in words if start <= w["start"] and w["end"] <= end + 0.05)
    clip = {
        "start_time": start,
        "end_time": end,
        "duration": round(end - start, 1),
        "transcript": text,
        "hashtags": _hashtags(text),
    }
    clip.update({k: v for k, v in fields.items() if v not in (None, "", [])})
    return clip


# ----------------------------------------------------------------------------- heuristic scorer

def _combined_energy(energy, motion, start, end):
    parts = []
    if energy:
        parts.append(_energy(energy, start, end))
    if motion and len(motion.get("z", [])):
        parts.append(_energy(motion, start, end))
    if not parts:
        return 0.0, 0.0, 0.0
    return tuple(sum(p[k] for p in parts) / len(parts) for k in range(3))


def _heuristic_scores(sents, i, j, energy, prompt_terms, motion=None, raised_voice=None):
    first = _core_line(sents[i]["text"])
    opening = " ".join(_core_line(s["text"]) for s in sents[i:min(j + 1, i + 2)])
    text = _clip_text(sents, i, j)
    dur = max(1.0, sents[j]["end"] - sents[i]["start"])
    wc = len(text.split())
    mean_z, peak_frac, hook_z = _combined_energy(energy, motion, sents[i]["start"], sents[j]["end"])

    # Add raised voice detection
    raised_score = 0
    if raised_voice and len(raised_voice.get("z", [])):
        rv_z = raised_voice["z"]
        a = max(0, int((sents[i]["start"] - raised_voice["offset"]) / raised_voice["hop"]))
        b = min(len(rv_z), int((sents[j]["end"] - raised_voice["offset"]) / raised_voice["hop"]))
        if b > a:
            raised_score = float(rv_z[a:b].mean())

    hook = 50
    hook += 15 if first.endswith("?") else 0
    hook += min(2, len(STRONG.findall(opening))) * 9
    hook += min(2, len(EMOTIONAL_KEYWORDS.findall(text))) * 8  # Emotional keywords
    hook += 7 if re.search(r"\d", first) else 0
    hook += 7 if re.search(r"\byou(r)?\b", first, re.I) else 0
    hook += 6 if len(first.split()) <= 18 else (-4 if len(first.split()) > 28 else 0)
    hook += 12 if _named_thing(sents[i]["text"]) else 0
    hook += 8 if re.search(r"\b(but|turns out|one day|the problem|only thing|what if)\b", first, re.I) else 0
    hook -= 28 if _weak_open(sents[i]["text"]) else 0
    hook += max(-6, min(10, hook_z * 6))
    hook += max(-5, min(12, raised_score * 8))  # Add raised voice score

    pauses = sum(1 for a, b in zip(sents[i:j], sents[i + 1:j + 1]) if b["start"] - a["end"] > 1.5)
    flow = 55 + (20 if sents[j]["terminal"] else -10) + (10 if i == 0 or sents[i - 1]["terminal"] else -10) - pauses * 6

    content = [w for w in re.findall(r"[a-z']+", text.lower()) if w not in STOPWORDS]
    density = len(set(content)) / max(1, wc)
    value = 30 + density * 70 + min(15, len(STRONG.findall(text)) * 3) + min(9, len(re.findall(r"\d+", text)) * 3)

    wpm = wc / dur * 60
    trend = 50 + mean_z * 15 + peak_frac * 40 + (10 if 140 <= wpm <= 210 else -5) + raised_score * 5

    scores = {k: int(max(1, min(99, v))) for k, v in
              {"hook": hook, "flow": flow, "value": value, "trend": trend}.items()}
    # A short dies in the first three seconds. Hook outweighs a tidy middle.
    total = 0.48 * scores["hook"] + 0.18 * scores["flow"] + 0.2 * scores["value"] + 0.14 * scores["trend"]
    if prompt_terms:
        hits = sum(1 for t in prompt_terms if t in text.lower())
        total += 30 * hits / len(prompt_terms) if hits else -25
    return scores, int(max(1, min(99, round(total))))


def _heuristic_clips(sents, words, energy, min_dur, max_dur, target, prompt, range_start=0.0, range_end=None, motion=None, raised_voice=None):
    prompt_terms = [w for w in re.findall(r"[a-z']{3,}", (prompt or "").lower()) if w not in STOPWORDS]
    ideal = (min_dur + max_dur) / 2
    terminal_count = sum(1 for s in sents if s.get("terminal"))
    strict_terminal = terminal_count >= len(sents) * 0.15
    cands = []
    for i in range(len(sents)):
        if _weak_open(sents[i]["text"]):
            if i + 1 < len(sents) and not _weak_open(sents[i + 1]["text"]):
                i += 1
            else:
                continue
        if strict_terminal and i > 0 and not sents[i - 1]["terminal"] and not sents[i]["text"].strip().endswith("?"):
            continue
        best = None
        for j in range(i, len(sents)):
            dur = sents[j]["end"] - sents[i]["start"]
            if dur > max_dur + 4:
                break
            if dur < min_dur:
                continue
            if strict_terminal and not sents[j]["terminal"]:
                continue
            ii, jj = _tighten_range(sents, i, j, min_dur, max_dur)
            if _weak_open(sents[ii]["text"]) or sents[jj]["end"] - sents[ii]["start"] < min_dur - 1:
                continue
            if prompt_terms and not any(t in _clip_text(sents, ii, jj).lower() for t in prompt_terms):
                continue
            scores, total = _heuristic_scores(sents, ii, jj, energy, prompt_terms, motion, raised_voice)
            if scores["hook"] < 55:
                continue
            total -= abs((sents[jj]["end"] - sents[ii]["start"]) - ideal) / ideal * 6
            if not best or total > best[0]:
                best = (total, ii, jj, scores)
        if best:
            total, ii, jj, scores = best
            title, caption = _headline(sents, ii, jj)
            first = sents[ii]["text"].strip()
            clip = _make_clip(sents, words, ii, jj, max_dur, min_dur, range_start, range_end,
                              title=title, hook=first[:140], caption=caption,
                              virality_score=int(max(1, min(99, round(total)))), scores=scores,
                              reason=_reason(scores))
            if clip:
                cands.append(clip)
    chosen = _select(cands, target)
    if len(chosen) >= target:
        return chosen
    # A strict hook gate can leave a short video thin. Fill the rest from the next-best windows.
    extra = _heuristic_clips_relaxed(sents, words, energy, min_dur, max_dur, target, prompt_terms, ideal,
                                     range_start, range_end, motion, raised_voice)
    return _select(chosen + extra, target)


def _heuristic_clips_relaxed(sents, words, energy, min_dur, max_dur, target, prompt_terms, ideal,
                             range_start, range_end, motion=None, raised_voice=None):
    terminal_count = sum(1 for s in sents if s.get("terminal"))
    strict_terminal = terminal_count >= len(sents) * 0.15
    cands = []
    for i in range(len(sents)):
        if _weak_open(sents[i]["text"]):
            if i + 1 < len(sents) and not _weak_open(sents[i + 1]["text"]):
                i += 1
            else:
                continue
        if strict_terminal and i > 0 and not sents[i - 1]["terminal"]:
            continue
        best = None
        for j in range(i, len(sents)):
            dur = sents[j]["end"] - sents[i]["start"]
            if dur > max_dur:
                break
            if dur < min_dur:
                continue
            if strict_terminal and not sents[j]["terminal"]:
                continue
            ii, jj = _tighten_range(sents, i, j, min_dur, max_dur)
            if _weak_open(sents[ii]["text"]):
                continue
            scores, total = _heuristic_scores(sents, ii, jj, energy, prompt_terms, motion, raised_voice)
            if scores["hook"] < 50:
                continue
            total -= abs(dur - ideal) / ideal * 6
            if not best or total > best[0]:
                best = (total, ii, jj, scores)
        if best:
            total, ii, jj, scores = best
            title, caption = _headline(sents, ii, jj)
            clip = _make_clip(sents, words, ii, jj, max_dur, min_dur, range_start, range_end,
                              title=title, hook=sents[ii]["text"].strip()[:140], caption=caption,
                              virality_score=int(max(1, min(99, round(total)))), scores=scores,
                              reason=_reason(scores))
            if clip:
                cands.append(clip)
    return _select(cands, target)


def _reason(scores):
    best = max(scores, key=scores.get)
    return {
        "hook": "Opens with a strong curiosity hook that stops the scroll.",
        "flow": "Self-contained story with a clean beginning and ending.",
        "value": "Dense with concrete, quotable insight.",
        "trend": "High-energy delivery with emotional peaks in the audio.",
    }[best]


def _energy_clips(energy, duration, min_dur, max_dur, target, range_start, motion=None):
    """For gameplay / music / sports with little speech: pick the loudest, most eventful stretches."""
    if not energy or not len(energy["z"]) or not energy.get("audible", True):
        return []
    hop = energy["hop"]
    z = energy["z"]
    win = int(((min_dur + max_dur) / 2) / hop)
    if len(z) < win:
        win = len(z)
    kernel = np.ones(win) / win
    smooth = np.convolve(z, kernel, mode="valid") + 0.6 * np.convolve((z > 1.0).astype(float), kernel, mode="valid")
    cands = []
    for a in np.argsort(-smooth)[: max(50, target * 20)]:
        start = energy["offset"] + a * hop
        end = min(start + win * hop, energy["offset"] + duration)
        score = int(max(1, min(99, 60 + smooth[a] * 20)))
        cands.append({
            "start_time": round(start, 2), "end_time": round(end, 2), "duration": round(end - start, 1),
            "title": f"Highlight at {fmt_ts(start)}", "hook": "", "transcript": "",
            "virality_score": score, "scores": {"hook": score, "flow": 60, "value": 50, "trend": score},
            "reason": "Peak action: the loudest or most visually active stretch.",
            "hashtags": ["#Shorts", "#Highlights", "#Viral"],
        })
    return _select(cands, target, max_overlap=0.0)


# ----------------------------------------------------------------------------- moment miner
# Local pass finds candidate windows in one walk of the transcript. The model only
# ranks that shortlist, so an 8-10 hour VOD is not sent through dozens of full-transcript calls.

RANK_SYSTEM = (
    "You are an expert short-form video editor. Choose moments people would willingly watch and share: "
    "a complete story with a payoff, a surprising reveal, a sharp argument, a useful explanation, "
    "a funny exchange, or a high-stakes reaction. Judge the actual content, not keyword density. "
    "Reject filler, sponsorships, greetings, context-dependent fragments, repeated topics, and empty hype. "
    "Return only valid JSON."
)

RANK_PROMPT = """These candidate moments are already cut from a long video. Pick the {n} best standalone Shorts.

Rules:
- Stay inside that candidate's sentence range. You may shrink start_sentence..end_sentence. Do not jump outside it.
- The clip must land between {min_dur} and {max_dur} seconds.
- The first line has to work in the first 3 seconds. The last line has to land (punchline, reaction, conclusion), not trail off.
- Prefer a self-contained idea with enough setup and payoff. Avoid moments that need unseen context, repeat the same point, or rely only on shouting or a viral-sounding phrase.
- Favor specific, surprising, emotionally clear, useful, or funny moments. Treat the local score as a hint, not proof that the moment is good.
- Rewrite the title as a scroll-stopping YouTube title under 60 characters. Do not paste the first sentence.
- Score honestly. A 90 is a clip people would stitch. Most candidates belong between 50 and 78. Do not score everything above 85.
- Pick distinct moments across the full timeline when quality is close. Skip sponsors, intros, outros, and near-duplicates.
{prompt_rule}
Return JSON, best first:
{{"clips": [
  {{"id": "c0", "start_sentence": 12, "end_sentence": 18, "title": "Scroll-stopping title", "hook": "opening line", "reason": "why a clipping channel would post this", "scores": {{"hook": 84, "flow": 76, "value": 80, "trend": 74}}, "virality_score": 79, "hashtags": ["#Shorts"], "caption": "One or two sentences."}}
]}}

CANDIDATES:
{body}"""

def _prompt_terms(prompt):
    return [w for w in re.findall(r"[a-z']{3,}", (prompt or "").lower()) if w not in STOPWORDS]


def _sent_pack(sentence):
    text = sentence["text"].strip()
    low = text.lower()
    words = low.split()
    reaction = len(REACTION.findall(low))
    laugh = len(LAUGH.findall(low))
    story = 1 if STORY.search(low) else 0
    strong = len(STRONG.findall(low))
    emotion = len(EMOTIONAL_KEYWORDS.findall(low))
    question = text.endswith("?")
    bang = text.endswith("!")
    number = bool(re.search(r"\d", text))
    you = bool(re.search(r"\byou(?:r|'re)?\b", low))
    name = _named_thing(text)
    weak = _weak_open(text)
    anchor = 0
    anchor += 3 * min(reaction, 2) + 3 * story + 2 * min(strong, 2) + 2 * min(emotion, 2) + 2 * laugh
    anchor += 2 if question else 0
    anchor += 1 if bang or number or you or name else 0
    anchor -= 3 if weak else 0
    return {
        "low": low, "wc": len(words), "reaction": reaction, "laugh": laugh, "story": story,
        "strong": strong, "emotion": emotion, "q": question, "bang": bang, "num": number,
        "you": you, "name": name, "weak": weak, "anchor": anchor,
        "dangling": bool(DANGLING_END.search(low)),
        "boring": bool(BORING.search(low)),
    }


def _end_quality(pack):
    score = 0
    if pack["dangling"]:
        score -= 5
    if pack["q"] or pack["bang"]:
        score += 3
    if pack["reaction"] or pack["laugh"] or pack["story"]:
        score += 3
    if pack["wc"] <= 14:
        score += 1
    if pack["weak"]:
        score -= 2
    if pack["boring"]:
        score -= 3
    return score


def _best_end(sents, packs, i, j, min_dur, max_dur):
    """Prefer a punchline near j over a sentence that trails off."""
    n = len(sents)
    start = sents[i]["start"]
    best_j, best_q = j, -10 ** 9
    for k in range(max(i, j - 2), min(n, j + 3)):
        dur = sents[k]["end"] - start
        if dur < min_dur - 1 or dur > max_dur + 0.4:
            continue
        quality = _end_quality(packs[k]) + (1 if sents[k].get("terminal") else 0)
        if quality > best_q:
            best_j, best_q = k, quality
    return best_j


def _window_scores(sents, packs, i, j, energy, motion, raised_voice, prompt_terms, focus):
    opening = packs[i]
    if opening["weak"] and i + 1 <= j and not packs[i + 1]["weak"]:
        opening = packs[i + 1]
    last = packs[j]
    window = packs[i:j + 1]
    text = " ".join(p["low"] for p in window)
    wc = sum(p["wc"] for p in window) or 1
    dur = max(1.0, sents[j]["end"] - sents[i]["start"])
    mean_z, peak_frac, hook_z = _combined_energy(energy, motion, sents[i]["start"], sents[j]["end"])
    raised = 0.0
    if raised_voice and len(raised_voice.get("z", [])):
        z = raised_voice["z"]
        a = max(0, int((sents[i]["start"] - raised_voice["offset"]) / raised_voice["hop"]))
        b = min(len(z), int((sents[j]["end"] - raised_voice["offset"]) / raised_voice["hop"]))
        if b > a:
            raised = float(z[a:b].mean())
    laughs = sum(p["laugh"] for p in window)
    reactions = sum(p["reaction"] for p in window)
    stories = sum(p["story"] for p in window)

    hook = 44
    hook += 14 if opening["q"] else 0
    hook += min(18, opening["reaction"] * 8 + opening["story"] * 10)
    hook += min(12, opening["strong"] * 5 + opening["emotion"] * 4)
    hook += 6 if opening["num"] else 0
    hook += 5 if opening["you"] else 0
    hook += 6 if opening["name"] else 0
    hook += 4 if opening["laugh"] else 0
    hook += 5 if opening["wc"] <= 16 else (-6 if opening["wc"] > 32 else 0)
    hook -= 20 if opening["weak"] else 0
    hook -= 16 if opening["boring"] and not opening["story"] else 0
    hook += max(-6, min(12, hook_z * 5))
    hook += max(-4, min(10, raised * 4))

    flow = 46
    flow += 14 if sents[j].get("terminal") or last["bang"] or last["q"] else -6
    flow += 10 if last["reaction"] or last["laugh"] or last["story"] else 0
    flow -= 16 if last["dangling"] else 0
    flow -= 10 if opening["weak"] else 0
    if re.search(r"\b(but|until|then|suddenly|turns out|except)\b", text):
        flow += 8
    pauses = sum(1 for a, b in zip(sents[i:j], sents[i + 1:j + 1]) if b["start"] - a["end"] > 1.6)
    flow -= pauses * 5

    content = [w for w in re.findall(r"[a-z']+", text) if w not in STOPWORDS]
    density = len(set(content)) / wc
    value = 32 + density * 46 + min(16, reactions * 3) + min(14, laughs * 5) + stories * 8
    value += min(10, sum(p["strong"] for p in window) * 2)
    if any(p["boring"] for p in window) and laughs == 0 and stories == 0 and reactions == 0:
        value -= 18

    wpm = wc / dur * 60
    trend = 42 + mean_z * 12 + peak_frac * 28 + laughs * 6 + max(-4, min(10, raised * 4))
    trend += 8 if 130 <= wpm <= 220 else (-4 if wpm < 70 else 0)
    if focus == "highlights":
        trend += 8

    scores = {k: int(max(1, min(99, round(v)))) for k, v in
              {"hook": hook, "flow": flow, "value": value, "trend": trend}.items()}
    if focus == "story":
        weights = (0.28, 0.34, 0.26, 0.12)
    elif focus == "highlights":
        weights = (0.30, 0.14, 0.16, 0.40)
    else:
        weights = (0.40, 0.24, 0.22, 0.14)
    total = sum(w * scores[k] for w, k in zip(weights, ("hook", "flow", "value", "trend")))
    if prompt_terms:
        hits = sum(1 for t in prompt_terms if t in text)
        total += 16 * hits / len(prompt_terms) if hits else -10
    # Setup then payoff beats a flat read of the same length.
    if (stories or reactions) and (last["laugh"] or last["reaction"] or last["q"] or last["bang"]):
        total += 4
    return scores, int(max(1, min(99, round(total))))


def _moment_reason(scores, packs, i, j):
    window = packs[i:j + 1]
    if sum(p["laugh"] for p in window) and scores["trend"] >= scores["value"]:
        return "A reaction or laugh lands, which is what gets clipped off a long stream."
    if sum(p["story"] for p in window) and scores["flow"] >= 60:
        return "A story with a beginning and a payoff, so it stands alone as a short."
    if sum(p["reaction"] for p in window) >= 2:
        return "A back-and-forth reaction that would stop a scroll in the first few seconds."
    best = max(scores, key=scores.get)
    return {
        "hook": "Opens on a line that works in the first three seconds.",
        "flow": "Starts on a hook and ends on a point, without the rest of the VOD.",
        "value": "Quotable enough that a clipping channel would post it on its own.",
        "trend": "Energy peaks the way a highlight does, not like flat small talk.",
    }[best]


def _overlaps(a, b, max_overlap=0.22):
    inter = min(a["end_time"], b["end_time"]) - max(a["start_time"], b["start_time"])
    if inter <= 0:
        return False
    return inter > max_overlap * min(a["duration"], b["duration"])


def _select_spread(cands, target, duration, max_overlap=0.22):
    """Highest scores first, kept apart so one hot minute doesn't eat the whole batch."""
    pool = sorted(cands, key=lambda c: c.get("virality_score", 0), reverse=True)
    if not pool or target <= 0:
        return []
    span = duration if duration and duration > 0 else max(c["end_time"] for c in pool)
    gap = max(40.0, span / max(target * 2.5, 1))

    def ok(clip, min_gap):
        mid = (clip["start_time"] + clip["end_time"]) / 2
        for kept in chosen:
            if _overlaps(clip, kept, max_overlap):
                return False
            if min_gap:
                other = (kept["start_time"] + kept["end_time"]) / 2
                if abs(mid - other) < min_gap and clip["virality_score"] < kept["virality_score"] + 8:
                    return False
        return True

    chosen = []
    for clip in pool:
        if len(chosen) >= target:
            break
        if ok(clip, gap):
            chosen.append(clip)
    if len(chosen) < target:
        for clip in pool:
            if len(chosen) >= target:
                break
            if clip not in chosen and ok(clip, 0):
                chosen.append(clip)
    return chosen


def _thin_candidates(clips, limit, bucket_sec):
    """One or two windows per slice of the video, best score first."""
    kept, counts = [], {}
    for clip in sorted(clips, key=lambda c: c["virality_score"], reverse=True):
        mid = (clip["start_time"] + clip["end_time"]) / 2
        bucket = int(mid / max(bucket_sec, 1))
        if counts.get(bucket, 0) >= 1:
            continue
        counts[bucket] = counts.get(bucket, 0) + 1
        kept.append(clip)
        if len(kept) >= limit:
            break
    return kept


def _mine_candidates(sents, words, energy, min_dur, max_dur, target, prompt, range_start, range_end,
                     motion, raised_voice, focus, duration):
    """O(n) windows around hooks. An 8-hour transcript stays under a second."""
    n = len(sents)
    if n == 0 or max_dur < 8:
        return []
    packs = [_sent_pack(s) for s in sents]
    terms = _prompt_terms(prompt)
    if focus == "story":
        lengths = [min(max_dur, max(min_dur, (min_dur + max_dur) * 0.62)), max_dur]
    else:
        mid = (min_dur + max_dur) / 2
        lengths = [max(min_dur, mid * 0.8), min(max_dur, max(mid, min_dur + 6))]
        if max_dur - lengths[-1] > 8:
            lengths.append(max_dur)
    stride = 1 if n < 900 else 2 if n < 2800 else 8 if n < 8000 else 12
    starts = set(range(0, n, stride))
    for idx, pack in enumerate(packs):
        if pack["anchor"] >= 3 or pack["laugh"] or pack["story"]:
            starts.add(idx)

    # For multi-hour VODs, score only the strongest openings in each short
    # time bucket. This keeps coverage across the stream while avoiding tens
    # of thousands of near-duplicate overlapping windows.
    if n > 2800:
        bucket_starts = {}
        for idx in starts:
            bucket = int(max(0.0, sents[idx]["start"] - range_start) / 45.0)
            pack = packs[idx]
            quality = pack["anchor"] + 4 * pack["laugh"] + 4 * pack["story"]
            choices = bucket_starts.setdefault(bucket, [])
            choices.append((quality, idx))
            if len(choices) > 2:
                choices.sort(key=lambda item: (-item[0], item[1]))
                del choices[2:]
        starts = {idx for choices in bucket_starts.values() for _, idx in choices}

    def collect(min_anchor):
        found = {}
        for i0 in sorted(starts):
            i = i0
            if packs[i]["weak"] and i + 1 < n and packs[i + 1]["anchor"] > packs[i]["anchor"]:
                i += 1
            j = i
            hit = [False] * len(lengths)
            while j < n:
                dur = sents[j]["end"] - sents[i]["start"]
                if dur > max_dur + 4:
                    break
                if dur + 0.3 >= min_dur:
                    for t_i, want in enumerate(lengths):
                        if hit[t_i] or dur + 2 < want:
                            continue
                        nxt = sents[j + 1]["end"] - sents[i]["start"] if j + 1 < n else dur + 99
                        if dur + 1 < want and nxt <= max_dur + 1 and abs(dur - want) > 6:
                            continue
                        hit[t_i] = True
                        jj = _best_end(sents, packs, i, j, min_dur, max_dur)
                        ii = i
                        if (packs[ii]["weak"] and ii < jj and not packs[ii + 1]["weak"]
                                and sents[jj]["end"] - sents[ii + 1]["start"] >= min_dur - 1):
                            ii += 1
                        if sents[jj]["end"] - sents[ii]["start"] < min_dur - 1:
                            continue
                        if max(p["anchor"] for p in packs[ii:jj + 1]) < min_anchor and sum(
                                p["laugh"] for p in packs[ii:jj + 1]) == 0:
                            continue
                        scores, total = _window_scores(
                            sents, packs, ii, jj, energy, motion, raised_voice, terms, focus)
                        prev = found.get((ii, jj))
                        if prev is None or total > prev[0]:
                            found[(ii, jj)] = (total, scores)
                j += 1
        return found

    found = collect(2)
    if len(found) < max(12, target * 2):
        found = collect(0) or found

    clips = []
    for (ii, jj), (total, scores) in found.items():
        title, caption = _headline(sents, ii, jj)
        clip = _make_clip(
            sents, words, ii, jj, max_dur, min_dur, range_start, range_end,
            title=title, hook=sents[ii]["text"].strip()[:140], caption=caption,
            virality_score=total, scores=scores, reason=_moment_reason(scores, packs, ii, jj))
        if not clip:
            continue
        clip["_i"] = ii
        clip["_j"] = jj
        clips.append(clip)
    bucket = max(25.0, (min_dur + max_dur) / 2)
    return _thin_candidates(clips, max(48, target * 8), bucket)


def _shortlist(cands, k, duration):
    """Show the model the whole VOD, not forty cuts of the same argument."""
    if len(cands) <= k:
        return list(cands)
    slices = 8
    cap = max(2, k // slices + 1)
    span = max(duration or 0, max(c["end_time"] for c in cands), 1)
    picked, counts = [], [0] * slices
    ranked = sorted(cands, key=lambda c: c["virality_score"], reverse=True)
    for clip in ranked:
        mid = (clip["start_time"] + clip["end_time"]) / 2
        bucket = min(slices - 1, int(mid / span * slices))
        if counts[bucket] >= cap:
            continue
        counts[bucket] += 1
        picked.append(clip)
        if len(picked) >= k:
            return picked
    have = {id(c) for c in picked}
    for clip in ranked:
        if id(clip) in have:
            continue
        picked.append(clip)
        if len(picked) >= k:
            break
    return picked


def _clamp_score(value, fallback):
    try:
        value = float(value)
        if not math.isfinite(value):
            return fallback
    except (TypeError, ValueError):
        return fallback
    return int(max(1, min(99, round(value))))


def _rank_mined(cands, sents, words, min_dur, max_dur, target, prompt, settings, log,
                range_start, range_end, duration):
    short = _shortlist(cands, min(36, max(target * 4, 12)), duration or 0)
    by_id = {}
    blocks = []
    for n, clip in enumerate(short):
        cid = f"c{n}"
        by_id[cid] = clip
        i, j = clip["_i"], clip["_j"]
        body = _clip_text(sents, i, j)
        if len(body) > 460:
            body = body[:210].rsplit(" ", 1)[0] + " … " + body[-210:].split(" ", 1)[-1]
        blocks.append(
            f"[{cid}] {fmt_ts(clip['start_time'])}–{fmt_ts(clip['end_time'])} "
            f"sentences {i}-{j} local {clip['virality_score']}\n"
            f"OPEN: {sents[i]['text'][:180]}\n"
            f"END: {sents[j]['text'][:180]}\n"
            f"TEXT: {body}"
        )
    prompt_rule = ""
    if prompt and prompt.strip():
        prompt_rule = f'- The user specifically wants: "{prompt.strip()}". Prefer moments that match. Skip the rest.\n'
    text = RANK_PROMPT.format(
        n=target, min_dur=int(min_dur), max_dur=int(max_dur), prompt_rule=prompt_rule, body="\n\n".join(blocks))
    log(f"Ranking {len(short)} candidate moments in one pass...")
    # Keep the ranker on the user's critical path brief; free endpoints often
    # accept a request but never return before a local ranker can finish.
    data = complete_json(text, RANK_SYSTEM, settings, log=log, budget=18)
    raw = data.get("clips") if isinstance(data, dict) else data
    if not isinstance(raw, list):
        raise LLMError("AI returned an invalid clips list.")

    ranked = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        base = by_id.get(str(item.get("id", "")).strip().lower())
        if not base:
            continue
        try:
            i, j = int(item["start_sentence"]), int(item["end_sentence"])
        except (KeyError, TypeError, ValueError, OverflowError):
            i, j = base["_i"], base["_j"]
        i = min(max(i, base["_i"]), base["_j"])
        j = min(max(j, i), base["_j"])
        j = min(j, len(sents) - 1)
        if j < i:
            continue
        i, j = _fit_bounds(sents, i, j, min_dur, max_dur)
        i = min(max(i, base["_i"]), base["_j"])
        j = min(max(j, i), base["_j"])
        local_scores = base["scores"] if base else {"hook": 60, "flow": 60, "value": 60, "trend": 60}
        local_total = base["virality_score"] if base else 60
        llm_scores = item.get("scores") if isinstance(item.get("scores"), dict) else {}
        scores = {k: _clamp_score(llm_scores.get(k), local_scores.get(k, 60)) for k in ("hook", "flow", "value", "trend")}
        if _weak_open(sents[i]["text"]):
            scores["hook"] = max(40, scores["hook"] - 10)
        llm_total = _clamp_score(item.get("virality_score"), local_total)
        total = int(max(1, min(99, round(0.7 * llm_total + 0.3 * local_total))))
        raw_tags = item.get("hashtags") if isinstance(item.get("hashtags"), list) else []
        tags = [str(t)[:60] if str(t).startswith("#") else f"#{str(t)[:59]}" for t in raw_tags][:6]
        clip = _make_clip(
            sents, words, i, j, max_dur, min_dur, range_start, range_end,
            title=str(item.get("title", ""))[:90] or (base or {}).get("title") or sents[i]["text"][:60],
            hook=str(item.get("hook", ""))[:200] or sents[i]["text"][:140],
            reason=str(item.get("reason", ""))[:300], caption=str(item.get("caption", ""))[:500],
            virality_score=total, scores=scores, hashtags=tags or None)
        if clip:
            ranked.append(clip)
    if not ranked:
        raise LLMError("AI did not return any usable moments.")
    return _select_spread(ranked, target, duration or 0)


# ----------------------------------------------------------------------------- entry point

def find_viral_moments(sents, words, energy, settings, min_dur=20, max_dur=60, target=5, prompt="",
                       duration=0.0, range_start=0.0, motion=None, focus="auto", log=lambda m: None,
                       raised_voice=None) -> dict:
    """Returns {"clips": [...], "engine": str, "warning": str|None}"""
    speech_minutes = max(1e-6, duration / 60)
    speech_rate = len(words) / speech_minutes
    sparse_speech = speech_rate < 25
    prefer_speech = focus != "highlights"
    end = range_start + duration if duration else None

    def _active(profile):
        return profile if profile and len(profile.get("z", [])) and profile.get("audible", True) else None

    warning = None
    clips = []
    engine = "moments"
    mined = []
    if sents and speech_rate >= 8:
        mined = _mine_candidates(
            sents, words, energy, min_dur, max_dur, target, prompt, range_start, end,
            motion, raised_voice, focus, duration)

    if mined and prefer_speech and llm_configured(settings):
        try:
            clips = _rank_mined(
                mined, sents, words, min_dur, max_dur, target, prompt, settings, log,
                range_start, end, duration)
            engine = "ai"
        except (LLMError, ValueError, KeyError, OSError) as e:
            warning = f"AI ranker failed, used the local moment scores instead ({str(e)[:160]})"
            try:
                log(warning, "warn")
            except TypeError:
                log(warning)
            clips = []
        if not clips and not warning:
            warning = "AI found no valid moments; used the local moment scores instead."

    if prefer_speech and len(clips) < target and mined:
        clips = _select_spread(clips + mined, target, duration)

    if not clips and (prompt or "").strip() and prefer_speech and speech_rate >= 25:
        raise ValueError("No moments matched your prompt. Try simpler keywords or enable an AI provider in Settings.")

    action_profile = _active(motion) or _active(energy)
    if focus == "highlights":
        action = _energy_clips(action_profile, duration, min_dur, max_dur, target, range_start) if action_profile else []
        clips = _select_spread(mined + action, target, duration)
        engine = "highlights"
        warning = "Highlight mode mixes spoken moments with the loudest stretches."
    elif len(clips) < target and (sparse_speech or len(clips) < max(1, target // 2)):
        extra = _energy_clips(action_profile, duration, min_dur, max_dur, target, range_start) if action_profile else []
        if extra:
            clips = _select_spread(clips + extra, target, duration)
        if clips and sparse_speech and engine == "moments":
            warning = "Little speech detected, so some clips follow audio and visual peaks."
        elif not clips:
            engine = "visual" if _active(motion) else "audio"
            if sparse_speech:
                warning = "Little or no speech detected, so clips follow audio and visual action."

    ordered = []
    for clip in sorted(clips, key=lambda c: c["start_time"]):
        ordered.append({k: v for k, v in clip.items() if not str(k).startswith("_")})
    for k, clip in enumerate(ordered):
        clip["id"] = f"clip_{k + 1}"
    return {"clips": ordered, "engine": engine, "warning": warning}
