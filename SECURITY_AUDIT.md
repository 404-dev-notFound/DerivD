# Security Audit Report — Financial Content Intelligence Pipeline

**Audit Date**: 2026-05-07
**Auditor**: Claude Security Review Agent
**Repository**: https://github.com/404-dev-notFound/DerivD
**Status**: ⚠️ CONDITIONAL PASS (after critical key rotation)
**Security Score**: 6.5 / 10

---

## Executive Summary

The pipeline is well-structured with several genuine security controls — SSRF blocking, Pydantic schema enforcement, prompt-injection stripping, hallucination guards, and a correctly gitignored `.env`. However, three systemic weaknesses bring the risk level to **HIGH** until resolved:

1. **A live API key exists in `.env`** (the file is gitignored, but sits on OneDrive — cloud-synced storage)
2. **The REST API has no authentication by default** and no rate limiting
3. **Dependencies are significantly outdated** with no automated audit process

The code demonstrates clear security awareness. Every guard that is present is code-enforced, not aspirational documentation.

---

## ⛔ STOP — Critical Action Required Before Reading Further

**Finding S-1: Live API key on a cloud-synced drive**

File: `.env`, line 3
```
OPENROUTER_API_KEY=sk-or-v1-6658b5843...
```

The `.env` is correctly gitignored and was never committed to git history. However, the project root is inside `OneDrive\Desktop\DerivD`, meaning this live credential is synced to Microsoft's servers.

**Action: Rotate the key immediately at https://openrouter.ai/keys**
Update `.env` with the new value after rotation.

---

## Findings by Category

### 1. Secrets Management

| ID | Severity | Status | Finding |
|----|----------|--------|---------|
| S-1 | CRITICAL | ❌ FAIL | Live API key in `.env` on OneDrive-synced path |
| S-2 | HIGH | ⚠️ WARN | `API_SECRET_KEY` defaults to empty string — auth silently bypassed |
| S-3 | — | ✅ PASS | `.env` correctly listed in `.gitignore` |
| S-4 | — | ✅ PASS | `.env.example` uses placeholder values only |
| S-5 | — | ✅ PASS | `config.json` contains no credentials |
| S-6 | — | ✅ PASS | API key read via `os.environ["OPENROUTER_API_KEY"]` — raises `KeyError` if missing |

**S-2 Detail** — `api.py` lines 61–76:
```python
_API_SECRET = os.environ.get("API_SECRET_KEY", "")
if not _API_SECRET:
    logger.warning("[AUTH] API_SECRET_KEY not set — endpoints unprotected.")
    return   # auth silently skipped
```
The default in `.env.example` is `API_SECRET_KEY=` (empty). Any caller who reaches the server can trigger a real LLM pipeline run and incur API costs.

---

### 2. API Authentication & Authorization

| ID | Severity | Status | Finding |
|----|----------|--------|---------|
| A-1 | HIGH | ❌ FAIL | Auth guard bypassed by default when `API_SECRET_KEY` is not set |
| A-2 | HIGH | ❌ FAIL | No rate limiting on any endpoint |
| A-3 | MEDIUM | ⚠️ WARN | `GET /pipeline/logs` unauthenticated — leaks internal paths and error messages |
| A-4 | MEDIUM | ⚠️ WARN | `GET /llm-calls` unauthenticated — leaks model names, token counts, prompt hashes |
| A-5 | — | ✅ PASS | CORS not set to `*` wildcard; methods restricted to GET/POST |

**A-2 Detail** — No `slowapi` or `limits` in `requirements.txt`. `POST /pipeline/run` can be called in a tight loop with zero authentication, draining OpenRouter credits. Zero-barrier financial DoS.

**Remediation for A-2:**
```bash
pip install slowapi
```
```python
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter

@app.post("/pipeline/run", ...)
@limiter.limit("3/minute")
def run_pipeline(request: Request, ...):
    ...
```

---

### 3. SSRF (Server-Side Request Forgery)

| ID | Severity | Status | Finding |
|----|----------|--------|---------|
| SSRF-1 | — | ✅ PASS | `_validate_url()` blocks non-http/https schemes (`file://`, `ftp://`, etc.) |
| SSRF-2 | — | ✅ PASS | Private/loopback/link-local IPs blocked via Python `ipaddress` module |
| SSRF-3 | MEDIUM | ⚠️ WARN | DNS rebinding not mitigated — host resolves again at fetch time |
| SSRF-4 | MEDIUM | ⚠️ WARN | `http://` (non-TLS) sources allowed — MITM injection vector |

**SSRF-3 Detail** — `utils/content_utils.py` lines 42–57. URL validation checks hostname at parse time, but `requests.get()` performs a fresh DNS lookup. An attacker controlling DNS for a whitelisted domain can rebind to `127.0.0.1` between validation and the actual TCP connection. Mitigated by resolving once and binding the IP, or using `ssrf-protect`.

**SSRF-4 Remediation** — `Stages/SOURCES_LOADED.py` line 29, change:
```python
# Current:
if not isinstance(s, str) or not s.startswith("http"):
# Fix:
if not isinstance(s, str) or not s.startswith("https://"):
    raise ValueError(f"Sources must use HTTPS. Got: {s!r}")
```

---

### 4. Prompt Injection

| ID | Severity | Status | Finding |
|----|----------|--------|---------|
| PI-1 | — | ✅ PASS | `sanitise_text()` strips lines starting with injection prefixes |
| PI-2 | MEDIUM | ⚠️ WARN | Injection pattern is line-start only — inline injections not caught |
| PI-3 | MEDIUM | ⚠️ WARN | `source_url` and `source_name` embedded in LLM prompts without sanitisation |
| PI-4 | — | ✅ PASS | Anti-hallucination rules injected into every system prompt |
| PI-5 | — | ✅ PASS | Code-enforced content-id and span validation post-LLM |

**PI-2 Detail** — `utils/content_utils.py` lines 36–39:
```python
_INJECTION_PATTERN = re.compile(
    r"^\s*(ignore|disregard|forget|...)",
    re.IGNORECASE | re.MULTILINE,
)
```
The `^` anchor only matches line beginnings. `"Here is news: IGNORE PREVIOUS INSTRUCTIONS..."` on one line is not caught.

**PI-3 Detail** — `Stages/ENTITIES_EXTRACTED.py` line 115:
```python
f"source_url: {item['source_url']}\n"
```
A URL like `https://evil.com/news?q=IGNORE+PREVIOUS+INSTRUCTIONS` passes through unsanitised into the LLM user prompt. Same issue in `ENTITY_SENTIMENT_SCORED.py` line 168.

---

### 5. Path Traversal

| ID | Severity | Status | Finding |
|----|----------|--------|---------|
| PT-1 | — | ✅ PASS | `GET /reports/{name}` uses `Path.resolve()` + prefix check |
| PT-2 | MEDIUM | ⚠️ WARN | Prefix check uses `str.startswith()` — edge case bypass on some paths |
| PT-3 | — | ✅ PASS | Artifact names are whitelisted, not user-derived paths |

**PT-2 Remediation** — `api.py` lines 303–306:
```python
# Current (fragile — /app/reports_evil passes startswith("/app/reports")):
if not str(path).startswith(str(reports_root)):
# Fix (Python 3.9+):
if not path.is_relative_to(reports_root):
    raise HTTPException(status_code=400, detail="Invalid report name")
```

---

### 6. Input Validation

| ID | Severity | Status | Finding |
|----|----------|--------|---------|
| IV-1 | — | ✅ PASS | Pydantic models enforce schema on all LLM outputs |
| IV-2 | — | ✅ PASS | `sources.json` validated for URL format and non-empty list |
| IV-3 | — | ✅ PASS | `resolution_confidence` constrained to `[0.0, 1.0]` via `Field(ge=0.0, le=1.0)` |
| IV-4 | — | ✅ PASS | API `lines` query param bounded `ge=1, le=2000` |
| IV-5 | LOW | ⚠️ WARN | `startswith("http")` in source loader accepts HTTP |

---

### 7. Information Disclosure via Error Handling

| ID | Severity | Status | Finding |
|----|----------|--------|---------|
| IE-1 | MEDIUM | ⚠️ WARN | `str(exc)` stored in state returned by unauthenticated `GET /pipeline/status` |
| IE-2 | LOW | ⚠️ WARN | `_load_artifact()` returns raw JSON parse error detail to caller |
| IE-3 | — | ✅ PASS | Fetch errors sanitised to `type(e).__name__` only (no stack trace) |
| IE-4 | — | ✅ PASS | Pydantic warnings include entity IDs but not raw web content |

**IE-1 Remediation** — `api.py` line 190:
```python
# Current:
_set_state(status="failed", error=str(exc), ...)
# Fix:
_set_state(status="failed", error="Pipeline failed — check pipeline.log for details.", ...)
```

---

### 8. Dependency Security

| ID | Severity | Status | Package | Pinned | Latest |
|----|----------|--------|---------|--------|--------|
| D-1 | HIGH | ❌ FAIL | `fastapi` | 0.110.3 | 0.136.1 |
| D-2 | MEDIUM | ⚠️ WARN | `httpx` | 0.27.0 | 0.28.1 |
| D-3 | MEDIUM | ⚠️ WARN | `requests` | 2.31.0 | 2.33.1 |
| D-4 | MEDIUM | ⚠️ WARN | `pydantic` | 2.7.1 | 2.12.5 |
| D-5 | HIGH | ❌ FAIL | — | — | No `pip-audit` or Dependabot configured |

**D-1 Detail** — `fastapi==0.110.3` is 26 minor versions behind current. No disclosed critical CVEs at time of audit, but the gap increases exposure to unpatched Starlette dependency vulnerabilities that ship in newer FastAPI versions.

**D-5 Detail** — No `.github/workflows/` directory exists. No automated CVE scanning on dependencies. A newly disclosed `requests` or `httpx` vulnerability would not be automatically detected.

---

### 9. Logging Security

| ID | Severity | Status | Finding |
|----|----------|--------|---------|
| L-1 | — | ✅ PASS | API key never appears in any log file |
| L-2 | — | ✅ PASS | `llm_calls.jsonl` stores prompt hashes, not raw prompts |
| L-3 | LOW | ⚠️ WARN | `pipeline.log` readable unauthenticated via `GET /pipeline/logs` |
| L-4 | — | ✅ PASS | Fetch errors logged as `type(e).__name__` only |

---

### 10. Concurrency / Race Conditions

| ID | Severity | Status | Finding |
|----|----------|--------|---------|
| RC-1 | — | ✅ PASS | `_state` dict protected by `threading.Lock()` |
| RC-2 | MEDIUM | ⚠️ WARN | Pipeline running check is not TOCTOU-safe |
| RC-3 | LOW | ⚠️ WARN | `llm_calls.jsonl` append without file lock — concurrent runs can interleave |

**RC-2 Detail** — `api.py` lines 239–246:
```python
state = _get_state()           # lock released here
if state["status"] == "running":
    raise HTTPException(...)   # race window between here...
thread = threading.Thread(...)
thread.start()                 # ...and here
```
Two simultaneous `POST /pipeline/run` requests can both read `status=idle` and both start threads. Fix: hold `_lock` across the check and the thread start.

```python
# Atomic check-and-start
with _lock:
    if _state["status"] == "running":
        raise HTTPException(status_code=409, ...)
    _state["status"] = "running"
thread = threading.Thread(target=_run_pipeline, daemon=True)
thread.start()
```

---

### 11. Security Headers

| ID | Severity | Status | Header |
|----|----------|--------|--------|
| SH-1 | MEDIUM | ❌ FAIL | `X-Content-Type-Options: nosniff` missing |
| SH-2 | MEDIUM | ❌ FAIL | `X-Frame-Options: DENY` missing |
| SH-3 | MEDIUM | ❌ FAIL | `Content-Security-Policy` missing |
| SH-4 | LOW | ❌ FAIL | `Strict-Transport-Security` missing |

**Remediation** — Add to `api.py`:
```python
@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Content-Security-Policy"] = "default-src 'none'"
    response.headers["Strict-Transport-Security"] = "max-age=31536000"
    return response
```

---

## Security Strengths

These controls are genuine, code-enforced, and worth preserving:

| Strength | Implementation | File |
|----------|---------------|------|
| SSRF blocking | `ipaddress` module + `_BLOCKED_HOSTS` | `utils/content_utils.py:42` |
| Pydantic schema enforcement | All LLM outputs validated before write | `utils/models.py` |
| Hallucination guard | Content-ID + span overlap checks post-LLM | `utils/llm_client.py:284` |
| Prompt injection stripping | `sanitise_text()` applied pre-LLM | `utils/content_utils.py:60` |
| Fetch error containment | `type(e).__name__` only in logs | `utils/content_utils.py:84` |
| Stage-order enforcement | `advance()` assertions prevent bypass | `run_pipeline.py:49` |
| Secret-free config | `config.json` contains no credentials | `config.json` |
| Safe `.gitignore` | `.env` excluded from all commits | `.gitignore:2` |

---

## Remediation Priority

### Priority 1 — Do Immediately (before any deployment)

| # | Action |
|---|--------|
| 1 | Rotate OpenRouter API key at https://openrouter.ai/keys |
| 2 | Add rate limiting to `POST /pipeline/run` (add `slowapi`) |
| 3 | Make pipeline start check atomic (TOCTOU fix in `api.py`) |

### Priority 2 — Fix Before Production

| # | Action |
|---|--------|
| 4 | Require `API_SECRET_KEY` at server startup — fail fast if missing |
| 5 | Add security headers middleware to `api.py` |
| 6 | Replace `str.startswith()` path check with `path.is_relative_to()` |
| 7 | Update outdated dependencies (`fastapi`, `httpx`, `requests`, `pydantic`) |

### Priority 3 — Next Iteration

| # | Action |
|---|--------|
| 8 | Improve prompt injection pattern to match inline (not just line-start) |
| 9 | Sanitise `source_url`/`source_name` before embedding in LLM prompts |
| 10 | Enforce HTTPS-only sources in `SOURCES_LOADED.py` |
| 11 | Add `pip-audit` to developer workflow or GitHub Actions |
| 12 | Sanitise `str(exc)` in pipeline status endpoint |
| 13 | Add `GET /pipeline/logs` and `GET /llm-calls` behind auth |

---

## OWASP Top 10 Compliance

| OWASP Category | Status | Notes |
|----------------|--------|-------|
| A01 Broken Access Control | ❌ FAIL | Auth bypassed by default; no rate limiting |
| A02 Cryptographic Failures | ✅ PASS | No passwords stored; API key not logged |
| A03 Injection | ⚠️ WARN | Prompt injection mitigations partial |
| A04 Insecure Design | ⚠️ WARN | TOCTOU race; default-open API auth |
| A05 Security Misconfiguration | ❌ FAIL | No security headers; auth off by default |
| A06 Vulnerable Components | ❌ FAIL | FastAPI 26 versions behind; no audit tooling |
| A07 Identification Failures | ❌ FAIL | Observability endpoints unauthenticated |
| A08 Integrity Failures | ✅ PASS | No unsafe deserialization |
| A09 Logging Failures | ⚠️ WARN | Logs exposed unauthenticated; exception strings leaked |
| A10 SSRF | ⚠️ WARN | IP validation strong; DNS rebinding unmitigated |

---

## Complete Finding Index

| ID | Severity | File | Line | Description |
|----|----------|------|------|-------------|
| S-1 | CRITICAL | `.env` | 3 | Live API key on OneDrive-synced drive |
| A-1 | HIGH | `api.py` | 69 | Auth silently bypassed when `API_SECRET_KEY` empty |
| A-2 | HIGH | `api.py` | — | No rate limiting on any endpoint |
| D-1 | HIGH | `requirements.txt` | 8 | `fastapi==0.110.3` — 26 versions behind |
| D-5 | HIGH | — | — | No automated dependency vulnerability scanning |
| A-3 | MEDIUM | `api.py` | 263 | `GET /pipeline/logs` unauthenticated |
| A-4 | MEDIUM | `api.py` | 318 | `GET /llm-calls` unauthenticated |
| D-2 | MEDIUM | `requirements.txt` | 1 | `httpx==0.27.0` outdated |
| D-3 | MEDIUM | `requirements.txt` | 3 | `requests==2.31.0` outdated |
| IE-1 | MEDIUM | `api.py` | 190 | Raw `str(exc)` in unauthenticated status endpoint |
| PI-2 | MEDIUM | `utils/content_utils.py` | 36 | Injection regex only matches line-start |
| PI-3 | MEDIUM | `Stages/ENTITIES_EXTRACTED.py` | 115 | `source_url` in LLM prompt unsanitised |
| PT-2 | MEDIUM | `api.py` | 305 | Path traversal check uses fragile `str.startswith()` |
| RC-2 | MEDIUM | `api.py` | 239 | Pipeline start check TOCTOU-unsafe |
| SH-1–4 | MEDIUM | `api.py` | — | No HTTP security headers |
| SSRF-3 | MEDIUM | `utils/content_utils.py` | 42 | DNS rebinding not mitigated |
| SSRF-4 | MEDIUM | `utils/content_utils.py` | 32 | HTTP (non-TLS) URLs accepted |
| D-4 | MEDIUM | `requirements.txt` | 9 | `pydantic==2.7.1` — 5 minor versions behind |
| IE-2 | LOW | `api.py` | 215 | JSON parse error detail returned to caller |
| IV-5 | LOW | `Stages/SOURCES_LOADED.py` | 29 | `startswith("http")` accepts plain HTTP |
| L-3 | LOW | `api.py` | 263 | `pipeline.log` readable unauthenticated |
| RC-3 | LOW | `utils/llm_client.py` | 177 | `llm_calls.jsonl` append without file lock |

---

## Files Reviewed

| File | Lines | Status |
|------|-------|--------|
| `run_pipeline.py` | 154 | ✅ Reviewed |
| `validate.py` | 209 | ✅ Reviewed |
| `api.py` | 355 | ✅ Reviewed |
| `utils/llm_client.py` | 319 | ✅ Reviewed |
| `utils/content_utils.py` | 175 | ✅ Reviewed |
| `utils/config.py` | 101 | ✅ Reviewed |
| `utils/models.py` | 110 | ✅ Reviewed |
| `Stages/SOURCES_LOADED.py` | 34 | ✅ Reviewed |
| `Stages/CONTENT_FETCHED.py` | 44 | ✅ Reviewed |
| `Stages/CONTENT_EXTRACTED.py` | 29 | ✅ Reviewed |
| `Stages/CONTENT_NORMALISED.py` | 61 | ✅ Reviewed |
| `Stages/ENTITIES_EXTRACTED.py` | 114 | ✅ Reviewed |
| `Stages/ENTITIES_RESOLVED.py` | 177 | ✅ Reviewed |
| `Stages/ENTITY_SENTIMENT_SCORED.py` | 175 | ✅ Reviewed |
| `Stages/QA_AND_CONFLICTS_CHECKED.py` | 172 | ✅ Reviewed |
| `Stages/REPORTS_GENERATED.py` | 153 | ✅ Reviewed |
| `Stages/COST_REPORT_GENERATED.py` | 220 | ✅ Reviewed |
| `Stages/RESULTS_FINALISED.py` | 149 | ✅ Reviewed |
| `config.json` | 42 | ✅ Reviewed |
| `requirements.txt` | 9 | ✅ Reviewed |
| `.env.example` | 19 | ✅ Reviewed |
| `.gitignore` | 44 | ✅ Reviewed |

---

## Conclusion

**Security Assessment: ⚠️ CONDITIONAL PASS**

The pipeline may be deployed after the Priority 1 items are resolved (key rotation, rate limiting, TOCTOU fix). Priority 2 items should be completed before any public or production exposure. The underlying security architecture is sound — the controls that exist are genuine and code-enforced.

**Overall Risk Level: HIGH → MEDIUM** (after Priority 1 remediation)

---

*Audited by: Claude Security Review Skill*
*Standards: OWASP Top 10, Python Security Best Practices*
*Next review: After Priority 1-2 remediations, or after any significant architecture change*
