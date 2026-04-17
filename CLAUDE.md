# CLAUDE.md

## Project Summary

`hoa-backend-dev` is the **development and experimental branch** of `hoa-backend`. It shares the same architecture — Flask API, OpenAI, Supabase — but receives changes for testing before they are promoted to production (`../hoa-backend/`).

There is no admin app in this repo. Only the public-facing API (`app.py` + `ask_gpt.py`) is present.

**Promote to production by copying `ask_gpt.py` to `../hoa-backend/ask_gpt.py` and pushing.**

---

## Runtime Architecture

### Web layer
- `app.py` creates the Flask app and enables global CORS.
- `POST /ask` delegates to `answer_question()` in `ask_gpt.py`.
- `POST /log` forwards question/answer/ip to a hardcoded Google Apps Script URL.

### AI / answer layer (`ask_gpt.py`)

**Architecture: Full-corpus in-memory reasoning**

`ask_gpt.py` loads ALL approved clauses from Supabase once at startup into `_clause_cache` and passes the entire corpus to GPT for every question. There is no vector search or keyword retrieval pipeline.

**Flow:**
1. `get_all_clauses()` — paginates through `clauses` table (page size 1000), filters `status = "approved"`, caches result in `_clause_cache` (module-level global). Selects: `clause_id, document, page, citation, clause_text, plain_summary, link, precedence_level`.
2. `format_all_clauses_for_gpt(clauses)` — formats every clause as `[CLAUSE_ID|DOC_SHORT|CITATION]\nSUMMARY | FULL TEXT: ...` (summary + full clause text, capped at 400 chars). Uses `DOC_SHORT` map to abbreviate long PDF filenames. Sorted by `precedence_level` ascending (highest authority first).
3. GPT call — `gpt-4o` receives the full formatted corpus in the user prompt and cites clauses using `[CLAUSE_ID]` bracket notation. Temperature 0.1.
4. Post-processing pipeline (in order):
   a. Normalize malformed brackets: `[WALLS_01|BG2022|Page 13]` → `[WALLS_01]`
   b. Capture `raw_cited_ids` from cleaned response (before link injection)
   c. Replace `[CLAUSE_ID]` with linked HTML citations using `DOC_SHORT_DISPLAY` map for human-readable document names (e.g. "CCRs, Article VI, Section 3")
5. Build `cited_clauses` from `raw_cited_ids`. Cap Texas Property Code to 1 display result. Keyword-score fallback if no cited clauses.
6. Return: `output_format="json"` → dict with `answer`, `clauses`, `question`, `mode`, `format`. Otherwise return `final_answer` (inline citations are self-contained; clause cards appended only via fallback path).

**Key functions:**
- `get_all_clauses()` — loads and caches full approved clause corpus
- `format_all_clauses_for_gpt(clauses)` — formats corpus for GPT prompt
- `format_clauses_for_display(clauses)` — formats clause cards for UI display
- `check_instant_whimsy(question_lower)` — canned responses for creator/whimsy questions; bypasses GPT
- `answer_question(question, ...)` — main entry point

**Document authority order (encoded in system prompt):**
1. Texas Property Code
2. CCRs & Declarations
3. CCR Amendments
4. Articles of Incorporation
5. Bylaws
6. Board Resolutions & Clarifying Resolutions
7. Specific Regulations (Solar, Flags, Rain Barrels, etc.)
8. 2022 Builders Guidelines

**CCR delegation exception:** When a CCR delegates to Builders Guidelines ("per the Builder Guidelines" / "as approved by the ARC"), the Builders Guidelines rule is authoritative on that topic. GPT cites both documents in those cases.

**Citation grouping:** GPT groups related rules in one paragraph with a single citation at the end — not one citation per sentence.

### Special behavior
- `check_instant_whimsy()` returns canned responses for creator/developer and fantasy/dragon questions. Returns early, skips GPT.

---

## API Endpoints

### `POST /ask`
- Request JSON: `question`, `mode` (optional), `tags` (optional), `output_format` (optional, default `"markdown"`)
- Returns JSON if `output_format == "json"`, otherwise HTML/markdown text

### `POST /log`
- Request JSON: `question`, `answer`, `ip`
- Sends to hardcoded Google Apps Script URL

---

## File Structure

```text
hoa-backend-dev/
├── app.py
├── ask_gpt.py
├── ask_gpt_gpt.py         ← backup/reference copy, not active
├── generated-icon.png
├── pyproject.toml
├── render.yaml
├── requirements.txt
├── test_summary_query.py  ← manual print script, not a real test
└── uv.lock
```

---

## Deployment / Environment

### Render
- `render.yaml` deploys as a Python web service.
- Build: `pip install -r requirements.txt`
- Start: `python app.py`

### Local env
- Set or copy `.env`:
  - `OPENAI_API_KEY`
  - `SUPABASE_URL`
  - `SUPABASE_SERVICE_ROLE_KEY`

### Local run
```bash
cd hoa-backend-dev
source venv/bin/activate
python app.py
```

---

## Promoting Changes to Production

When a change tested here is ready for production:
1. `cp ask_gpt.py ../hoa-backend/ask_gpt.py`
2. `cd ../hoa-backend && git add ask_gpt.py && git commit -m "..." && git push`
3. Render auto-deploys from the `hoa-backend` repo.

---

## Important Observations

- `ask_gpt_gpt.py` is a stale backup — do not deploy it.
- `test_summary_query.py` queries `summary` while `ask_gpt.py` uses `plain_summary` — possible schema drift.
- `pyproject.toml` is stale — names the package `python-template` with no real dependencies.
- `_clause_cache` is a module-level global — cleared on process restart. On Render free tier, restarts are frequent.
- The `DOC_SHORT` map (GPT payload) and `DOC_SHORT_DISPLAY` map (citation links) must both be updated when new documents are added to Supabase.

---

## Practical Notes For Future Sessions

- **Answer pipeline**: all logic in `ask_gpt.py` → `answer_question()`. No vector search. Full corpus loaded once via `get_all_clauses()`.
- **Citation post-processing order matters**: (1) normalize malformed brackets, (2) capture `raw_cited_ids`, (3) replace brackets with HTML anchors. Do not reorder.
- **Weak answers**: check `plain_summary` and `clause_text` quality in Supabase. The 400-char cap in `format_all_clauses_for_gpt` truncates long clauses.
- **Adding new documents**: update `DOC_SHORT` in `format_all_clauses_for_gpt` AND `DOC_SHORT_DISPLAY` in `answer_question`.
- **Token concern**: if the clause corpus grows very large, the full-corpus approach will hit GPT token limits. Monitor prompt size.
