import re
import json
import requests
import time


class LLMError(RuntimeError):
    pass


def _provider_ready(settings: dict) -> bool:
    p = settings.get("llm_provider")
    if p == "gemini":
        return bool(settings.get("gemini_api_key"))
    if p == "openai_compatible":
        base = settings.get("openai_base_url", "")
        is_local = "localhost" in base or "127.0.0.1" in base
        return bool(base and settings.get("openai_model") and (settings.get("openai_api_key") or is_local))
    return False


def _chain(settings: dict) -> list:
    """Engines to try in order: the selected one, the other built-in one, then the
    OpenAI-compatible entries of settings["fallback_providers"] (a JSON list of
    {"name", "base_url", "model", "api_key"}; api_key may be empty for keyless gateways)."""
    first = settings.get("llm_provider")
    if first not in ("gemini", "openai_compatible"):
        return []
    chain = []
    for p in (first, "openai_compatible" if first == "gemini" else "gemini"):
        s = {**settings, "llm_provider": p}
        if _provider_ready(s):
            chain.append(s)
    try:
        extra = json.loads(settings.get("fallback_providers") or "[]")
    except ValueError:
        extra = []
    for e in extra if isinstance(extra, list) else []:
        if isinstance(e, dict) and e.get("base_url") and e.get("model"):
            chain.append({**settings, "llm_provider": "openai_compatible", "openai_base_url": str(e["base_url"]),
                          "openai_model": str(e["model"]), "openai_api_key": str(e.get("api_key") or ""),
                          "_name": str(e.get("name") or ""),
                          "proxy_host": settings.get("proxy_host", ""),
                          "proxy_port": settings.get("proxy_port", ""),
                          "proxy_user": settings.get("proxy_user", ""),
                          "proxy_pass": settings.get("proxy_pass", "")})
    return chain


def llm_configured(settings: dict) -> bool:
    return bool(_chain(settings))


def _label(s: dict) -> str:
    if s["llm_provider"] == "gemini":
        return f"Gemini · {s['gemini_model']}"
    return f"{s['_name']} · {s['openai_model']}" if s.get("_name") else s["openai_model"]


def describe_llm(settings: dict) -> str:
    chain = _chain(settings)
    if not chain:
        return "Built-in heuristics (no AI key)"
    more = len(chain) - 1
    return _label(chain[0]) + (f" (+{more} fallback engine{'s' if more > 1 else ''})" if more else "")


def _parse_json(text: str):
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    try:
        return json.loads(text)
    except ValueError:
        m = re.search(r"\{.*\}", text, re.S)
        if m:
            return json.loads(m.group(0))
        raise LLMError("AI returned invalid JSON")


def _gemini(prompt: str, system: str, settings: dict) -> str:
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{settings['gemini_model']}:generateContent"
    body = {
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.4, "responseMimeType": "application/json"},
    }
    r = requests.post(url, json=body, headers={"x-goog-api-key": settings["gemini_api_key"]}, timeout=180)
    if r.status_code != 200:
        raise LLMError(f"Gemini HTTP {r.status_code}: {r.text[:300]}")
    data = r.json()
    try:
        return "".join(p.get("text", "") for p in data["candidates"][0]["content"]["parts"] if not p.get("thought"))
    except (KeyError, IndexError):
        raise LLMError(f"Gemini returned no content: {str(data)[:300]}")


def _openai_compatible(prompt: str, system: str, settings: dict) -> str:
    url = settings["openai_base_url"].rstrip("/") + "/chat/completions"
    headers = {"Content-Type": "application/json"}
    if settings.get("openai_api_key"):
        headers["Authorization"] = f"Bearer {settings['openai_api_key']}"
    body = {
        "model": settings["openai_model"],
        "temperature": 0.4,
        "response_format": {"type": "json_object"},
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
    }
    proxies = None
    if not settings.get("openai_api_key") and settings.get("proxy_host") and settings.get("proxy_port"):
        user = settings.get("proxy_user", "")
        pwd = settings.get("proxy_pass", "")
        if user and pwd:
            proxies = {
                "http": f"http://{user}:{pwd}@{settings['proxy_host']}:{settings['proxy_port']}",
                "https": f"http://{user}:{pwd}@{settings['proxy_host']}:{settings['proxy_port']}",
            }
    if proxies:
        print(f"[llm] Using proxy for keyless request: {settings['proxy_host']}:{settings['proxy_port']}")
    r = requests.post(url, json=body, headers=headers, proxies=proxies, timeout=180)
    if r.status_code == 400:
        # JSON mode unsupported, or the provider rejected its own output (Groq json_validate_failed):
        # retry as plain text and parse the JSON ourselves.
        body.pop("response_format")
        r = requests.post(url, json=body, headers=headers, proxies=proxies, timeout=180)
    if r.status_code != 200:
        raise LLMError(f"LLM HTTP {r.status_code}: {r.text[:300]}")
    return r.json()["choices"][0]["message"]["content"]


# Engines that fail at the transport/HTTP level are skipped for a while: a dead gateway
# would otherwise burn a full timeout on every transcript chunk of every job.
_engine_cooldown: dict = {}
_COOLDOWN_SECONDS = 600
_RATE_LIMIT_COOLDOWN_SECONDS = 30  # shorter cooldown for 429 so key rotation is snappy


def _is_rate_limit(e: Exception) -> bool:
    return (isinstance(e, LLMError) and "429" in str(e)) or (isinstance(e, LLMError) and "rate_limit" in str(e).lower())


def _transport_error(e: Exception) -> bool:
    return isinstance(e, requests.RequestException) or (isinstance(e, LLMError) and "HTTP" in str(e))


def complete_json(prompt: str, system: str, settings: dict, log=None) -> dict:
    log = log or (lambda msg, level="info": None)
    errors = []
    chain = _chain(settings)
    active = [s for s in chain if time.monotonic() >= _engine_cooldown.get(_label(s), 0)]
    for s in active or chain:
        call = _gemini if s["llm_provider"] == "gemini" else _openai_compatible
        started = time.monotonic()
        log(f"Asking {_label(s)} for moments ({len(prompt):,} characters of transcript)...")
        try:
            data = _parse_json(call(prompt, system, s))
            log(f"{_label(s)} answered in {time.monotonic() - started:.0f} s")
            return data
        except (LLMError, requests.RequestException, ValueError, KeyError, IndexError, TypeError) as e:
            if _transport_error(e):
                cooldown = _RATE_LIMIT_COOLDOWN_SECONDS if _is_rate_limit(e) else _COOLDOWN_SECONDS
                _engine_cooldown[_label(s)] = time.monotonic() + cooldown
                log(f"{_label(s)} skipped for {cooldown // 60} min after failing", "warn")
            errors.append(f"{_label(s)}: {str(e)[:140]}")
            print(f"[llm] {errors[-1]}")
            log(f"{_label(s)} failed after {time.monotonic() - started:.0f} s: {str(e)[:300]}", "error")
    raise LLMError(" | ".join(errors) or "No AI engine is configured")


def complete_json_parallel(prompts, system, settings, log=None):
    """Run multiple prompts in parallel across all available engines for speed."""
    from concurrent.futures import ThreadPoolExecutor, as_completed
    log = log or (lambda msg, level="info": None)
    chain = _chain(settings)
    if not chain:
        raise LLMError("No AI engine is configured")
    active = [s for s in chain if time.monotonic() >= _engine_cooldown.get(_label(s), 0)]
    engines = active or chain
    results = [None] * len(prompts)
    errors = []

    def ask_item(idx: int, engine_idx: int):
        s = engines[engine_idx % len(engines)]
        call = _gemini if s["llm_provider"] == "gemini" else _openai_compatible
        started = time.monotonic()
        prompt = prompts[idx]
        try:
            data = _parse_json(call(prompt, system, s))
            log(f"{_label(s)} answered chunk {idx + 1}/{len(prompts)} in {time.monotonic() - started:.0f} s")
            return idx, data
        except (LLMError, requests.RequestException, ValueError, KeyError, IndexError, TypeError) as e:
            if _transport_error(e):
                cooldown = _RATE_LIMIT_COOLDOWN_SECONDS if _is_rate_limit(e) else _COOLDOWN_SECONDS
                _engine_cooldown[_label(s)] = time.monotonic() + cooldown
                log(f"{_label(s)} skipped for {cooldown // 60} min after failing", "warn")
            err = f"chunk {idx + 1} via {_label(s)}: {str(e)[:120]}"
            errors.append(err)
            print(f"[llm] {err}")
            log(f"{_label(s)} failed chunk {idx + 1} after {time.monotonic() - started:.0f} s: {str(e)[:200]}", "error")
            return idx, None

    with ThreadPoolExecutor(max_workers=min(len(engines) * 2, len(prompts), 16)) as pool:
        futures = {pool.submit(ask_item, i, i): i for i in range(len(prompts))}
        for fut in as_completed(futures):
            idx, data = fut.result()
            if data is not None:
                results[idx] = data

    if all(r is None for r in results):
        raise LLMError("All chunks failed: " + " | ".join(errors[:5]))
    return results


def max_prompt_chars(settings: dict) -> int:
    # One size for every engine, because a chunk sized for Gemini's 1M-token window is rejected by the
    # fallbacks (OVH caps at 131k tokens) and trips free-tier per-minute limits. Chunks run in parallel.
    return 100_000
