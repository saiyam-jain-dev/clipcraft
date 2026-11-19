from concurrent.futures import ThreadPoolExecutor
import re
import math
import wave
import numpy as np
from .llm import complete_json, complete_json_parallel, llm_configured, max_prompt_chars, LLMError

STRONG = re.compile(
    r"\b(secret|never|always|mistake|truth|why|how to|stop|nobody|everyone|most people|actually|shocking|insane|"
    r"crazy|biggest|worst|best|wrong|lie|lied|million|billion|money|rich|broke|fail|failed|failure|success|"
    r"died|death|kill|fear|love|hate|regret|change(?:d)? my life|game ?changer|unbelievable|important|problem|"
    r"realized|discovered|the reason|here's|imagine|what if|listen|warning|dangerous|free|first time)\b", re.I)
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
    has_payload = bool(STRONG.search(first) or re.search(r"\d", first) or first.endswith("?") or _named_thing(text))
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
        score += 14 if _named_thing(line) else 0
        score += 12 if line.endswith("?") else 0
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


def _heuristic_scores(sents, i, j, energy, prompt_terms, motion=None):
    first = _core_line(sents[i]["text"])
    opening = " ".join(_core_line(s["text"]) for s in sents[i:min(j + 1, i + 2)])
    text = _clip_text(sents, i, j)
    dur = max(1.0, sents[j]["end"] - sents[i]["start"])
    wc = len(text.split())
    mean_z, peak_frac, hook_z = _combined_energy(energy, motion, sents[i]["start"], sents[j]["end"])

    hook = 50
    hook += 15 if first.endswith("?") else 0
    hook += min(2, len(STRONG.findall(opening))) * 9
    hook += 7 if re.search(r"\d", first) else 0
    hook += 7 if re.search(r"\byou(r)?\b", first, re.I) else 0
    hook += 6 if len(first.split()) <= 18 else (-4 if len(first.split()) > 28 else 0)
    hook += 12 if _named_thing(sents[i]["text"]) else 0
    hook += 8 if re.search(r"\b(but|turns out|one day|the problem|only thing|what if)\b", first, re.I) else 0
    hook -= 28 if _weak_open(sents[i]["text"]) else 0
    hook += max(-6, min(10, hook_z * 6))

    pauses = sum(1 for a, b in zip(sents[i:j], sents[i + 1:j + 1]) if b["start"] - a["end"] > 1.5)
    flow = 55 + (20 if sents[j]["terminal"] else -10) + (10 if i == 0 or sents[i - 1]["terminal"] else -10) - pauses * 6

    content = [w for w in re.findall(r"[a-z']+", text.lower()) if w not in STOPWORDS]
    density = len(set(content)) / max(1, wc)
    value = 30 + density * 70 + min(15, len(STRONG.findall(text)) * 3) + min(9, len(re.findall(r"\d+", text)) * 3)

    wpm = wc / dur * 60
    trend = 50 + mean_z * 15 + peak_frac * 40 + (10 if 140 <= wpm <= 210 else -5)

    scores = {k: int(max(1, min(99, v))) for k, v in
              {"hook": hook, "flow": flow, "value": value, "trend": trend}.items()}
    # A short dies in the first three seconds. Hook outweighs a tidy middle.
    total = 0.48 * scores["hook"] + 0.18 * scores["flow"] + 0.2 * scores["value"] + 0.14 * scores["trend"]
    if prompt_terms:
        hits = sum(1 for t in prompt_terms if t in text.lower())
        total += 30 * hits / len(prompt_terms) if hits else -25
    return scores, int(max(1, min(99, round(total))))


def _heuristic_clips(sents, words, energy, min_dur, max_dur, target, prompt, range_start=0.0, range_end=None, motion=None):
    prompt_terms = [w for w in re.findall(r"[a-z']{3,}", (prompt or "").lower()) if w not in STOPWORDS]
    ideal = (min_dur + max_dur) / 2
    cands = []
    for i in range(len(sents)):
        if _weak_open(sents[i]["text"]):
            continue
        if i > 0 and not sents[i - 1]["terminal"] and not sents[i]["text"].strip().endswith("?"):
            continue
        best = None
        for j in range(i, len(sents)):
            dur = sents[j]["end"] - sents[i]["start"]
            if dur > max_dur + 4:
                break
            if dur < min_dur or not sents[j]["terminal"]:
                continue
            ii, jj = _tighten_range(sents, i, j, min_dur, max_dur)
            if _weak_open(sents[ii]["text"]) or sents[jj]["end"] - sents[ii]["start"] < min_dur - 1:
                continue
            if prompt_terms and not any(t in _clip_text(sents, ii, jj).lower() for t in prompt_terms):
                continue
            scores, total = _heuristic_scores(sents, ii, jj, energy, prompt_terms, motion)
            if scores["hook"] < 60:
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
                                     range_start, range_end, motion)
    return _select(chosen + extra, target)


def _heuristic_clips_relaxed(sents, words, energy, min_dur, max_dur, target, prompt_terms, ideal,
                             range_start, range_end, motion=None):
    cands = []
    for i in range(len(sents)):
        if _weak_open(sents[i]["text"]):
            continue
        if i > 0 and not sents[i - 1]["terminal"]:
            continue
        best = None
        for j in range(i, len(sents)):
            dur = sents[j]["end"] - sents[i]["start"]
            if dur > max_dur:
                break
            if dur < min_dur or not sents[j]["terminal"]:
                continue
            ii, jj = _tighten_range(sents, i, j, min_dur, max_dur)
            if _weak_open(sents[ii]["text"]):
                continue
            scores, total = _heuristic_scores(sents, ii, jj, energy, prompt_terms, motion)
            if scores["hook"] < 55:
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


# ----------------------------------------------------------------------------- LLM picker

SYSTEM = (
    "You are the world's best short-form video editor (TikTok, YouTube Shorts, Reels). You find the moments in "
    "long videos that will go viral as standalone vertical clips. You only return JSON."
)

PROMPT = """Below is a transcript split into numbered sentences: [index] (timestamp) text.

Pick the {n} BEST standalone clips for short-form video. Each clip is a contiguous range of sentences.

Requirements:
- Duration between {min_dur} and {max_dur} seconds (use the timestamps; the clip runs from the start of start_sentence to the end of end_sentence).
- HOOK: the first sentence must grab attention within 3 seconds (bold claim, question, surprising fact, conflict, emotion). Never start on filler ("so", "and", "um") or on a sentence that depends on earlier context ("he said that...", "this is why...").
- FLOW: the clip makes complete sense to someone who never saw the full video, and ends on a satisfying payoff / punchline / conclusion, never mid-thought.
- VALUE: insight, story, humour, controversy, or emotion that makes people watch to the end, comment and share.
- Prefer variety: different topics, no overlapping ranges.
{prompt_rule}
Score each clip 0-100 on hook, flow, value, trend (how well it fits what is currently popular on short-form platforms), and give an overall virality_score 0-100. Be honest and spread the scores; not everything is 95.

Return JSON exactly in this shape, ordered best first:
{{"clips": [{{"start_sentence": 12, "end_sentence": 19, "title": "Punchy title under 60 chars", "hook": "the opening line, verbatim", "reason": "one sentence on why this will perform", "scores": {{"hook": 90, "flow": 85, "value": 80, "trend": 75}}, "virality_score": 86, "hashtags": ["#Shorts", "#Topic", "#Topic2"], "caption": "Social caption, 1-2 sentences"}}]}}

TRANSCRIPT:
{transcript}"""


def _llm_clips(sents, words, energy, min_dur, max_dur, target, prompt, settings, log, range_start=0.0, range_end=None, motion=None):
    lines = [f"[{s['idx']}] ({fmt_ts(s['start'])}) {s['text']}" for s in sents]
    limit = max_prompt_chars(settings)
    chunks, cur, size = [], [], 0
    for line in lines:
        if cur and size + len(line) > limit:
            chunks.append(cur)
            cur, size = cur[-15:], sum(len(x) for x in cur[-15:])
        cur.append(line)
        size += len(line) + 1
    chunks.append(cur)

    prompt_rule = (f"- The user specifically wants: \"{prompt.strip()}\". Only pick moments matching this request.\n"
                   if prompt and prompt.strip() else "")

    prompts = []
    for ci, chunk in enumerate(chunks):
        n = min(20, max(3, math.ceil(target * 1.6 / len(chunks)) + 1))
        prompts.append(PROMPT.format(n=n, min_dur=min_dur, max_dur=max_dur, prompt_rule=prompt_rule,
                                      transcript="\n".join(chunk)))

    if len(chunks) == 1:
        log(f"Asking AI for the best moments...")
        data = complete_json(prompts[0], SYSTEM, settings, log=log)
        raw = data.get("clips") if isinstance(data, dict) else data
        if not isinstance(raw, list):
            raise LLMError("AI returned an invalid clips list.")
    else:
        log(f"Asking AI for the best moments ({len(chunks)} chunks, {len(prompts)} prompts)...")
        results = complete_json_parallel(prompts, SYSTEM, settings, log=log)
        raw = []
        for ci, data in enumerate(results):
            if data is None:
                log(f"Skipped chunk {ci + 1}/{len(chunks)}: no AI engine answered for it", "warn")
                continue
            entries = data.get("clips") if isinstance(data, dict) else data
            if isinstance(entries, list):
                raw.extend(entries)
    if not raw:
        raise LLMError("No AI engine returned any moments.")

    prompt_terms = [w for w in re.findall(r"[a-z']{3,}", (prompt or "").lower()) if w not in STOPWORDS]
    cands = []
    for c in raw:
        if not isinstance(c, dict):
            continue
        try:
            i, j = int(c["start_sentence"]), int(c["end_sentence"])
        except (KeyError, TypeError, ValueError, OverflowError):
            continue
        if not (0 <= i < len(sents)) or j < i:
            continue
        i, j = _fit_bounds(sents, i, min(j, len(sents) - 1), min_dur, max_dur)
        if _weak_open(sents[i]["text"]):
            continue
        llm_scores = c.get("scores") if isinstance(c.get("scores"), dict) else {}
        h_scores, h_total = _heuristic_scores(sents, i, j, energy, prompt_terms, motion)
        scores = {}
        for k in h_scores:
            try:
                value = float(llm_scores.get(k, h_scores[k]))
                if not math.isfinite(value):
                    value = h_scores[k]
            except (TypeError, ValueError):
                value = h_scores[k]
            scores[k] = int(max(1, min(99, value)))
        try:
            llm_total = float(c.get("virality_score", sum(scores.values()) / 4))
            if not math.isfinite(llm_total):
                llm_total = sum(scores.values()) / 4
        except (TypeError, ValueError, OverflowError):
            llm_total = sum(scores.values()) / 4
        # Mostly trust the editor, but let measured audio energy nudge ties.
        total = int(max(1, min(99, round(0.85 * llm_total + 0.15 * h_total))))
        raw_tags = c.get("hashtags") if isinstance(c.get("hashtags"), list) else []
        tags = [str(t)[:60] if str(t).startswith("#") else f"#{str(t)[:59]}" for t in raw_tags][:6]
        clip = _make_clip(sents, words, i, j, max_dur, min_dur, range_start, range_end,
                          title=str(c.get("title", ""))[:90] or sents[i]["text"][:60],
                          hook=str(c.get("hook", ""))[:200] or sents[i]["text"][:140],
                          reason=str(c.get("reason", ""))[:300], caption=str(c.get("caption", ""))[:500],
                          virality_score=total, scores=scores, hashtags=tags or None)
        if clip:
            cands.append(clip)
    return _select(cands, target)


# ----------------------------------------------------------------------------- entry point

def find_viral_moments(sents, words, energy, settings, min_dur=20, max_dur=60, target=5, prompt="",
                       duration=0.0, range_start=0.0, motion=None, focus="auto", log=lambda m: None) -> dict:
    """Returns {"clips": [...], "engine": str, "warning": str|None}"""
    speech_minutes = max(1e-6, duration / 60)
    sparse_speech = len(words) / speech_minutes < 25 or focus == "highlights"
    prefer_speech = focus != "highlights"

    warning = None
    clips = []
    engine = "heuristic"
    end = range_start + duration if duration else None
    if sents and prefer_speech and not (focus == "highlights") and llm_configured(settings) and len(words) / speech_minutes >= 25:
        try:
            clips = _llm_clips(sents, words, energy, min_dur, max_dur, target, prompt, settings, log,
                               range_start, end, motion)
            engine = "ai"
        except (LLMError, ValueError, KeyError, OSError) as e:
            warning = f"AI picker failed, used built-in scoring instead ({str(e)[:160]})"
            log(warning)
            clips = []
        if not clips and not warning:
            warning = "AI found no valid moments; used built-in scoring instead."
    if not clips and sents and prefer_speech and len(words) / max(1e-6, duration / 60) >= 25:
        clips = _heuristic_clips(sents, words, energy, min_dur, max_dur, target, prompt,
                                 range_start, end, motion)
        engine = "heuristic"
    if not clips and prompt.strip() and prefer_speech and len(words) / max(1e-6, duration / 60) >= 25:
        raise ValueError("No moments matched your prompt. Try simpler keywords or enable an AI provider in Settings.")
    def _active(profile):
        return profile if profile and len(profile.get("z", [])) and profile.get("audible", True) else None

    action_profile = _active(motion) or _active(energy)
    if focus == "highlights" and sents and len(words) / max(1e-6, duration / 60) >= 25:
        spoken = _heuristic_clips(sents, words, energy, min_dur, max_dur, target, prompt, range_start, end, motion)
        action = _energy_clips(action_profile, duration, min_dur, max_dur, target, range_start) if action_profile else []
        clips = _select(spoken + action, target)
        engine = "highlights"
        warning = "Highlight mode mixes strong lines with the most active stretches."
    elif not clips:
        clips = _energy_clips(action_profile, duration, min_dur, max_dur, target, range_start) if action_profile else []
        engine = "visual" if _active(motion) else "audio"
        if sparse_speech and focus != "highlights":
            warning = "Little or no speech detected, so clips follow audio and visual action."

    for k, c in enumerate(sorted(clips, key=lambda c: c["start_time"])):
        c["id"] = f"clip_{k + 1}"
    return {"clips": clips, "engine": engine, "warning": warning}
