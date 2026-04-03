# CLAUDE.md

## Project Summary

`hoa-backend-dev` is the **development and experimental branch** of `hoa-backend`. It shares the same architecture — Flask API, OpenAI, Supabase — but receives changes for testing before they are promoted to production (`../hoa-backend/`).

There is no admin app in this repo. Only the public-facing API (`app.py` + `ask_gpt.py`) is present.

## Runtime Architecture

### Web layer
- `app.py` creates the Flask app and enables global CORS.
- `POST /ask` delegates to `answer_question()` in `ask_gpt.py`.
- `POST /log` forwards question/answer/ip to a hardcoded Google Apps Script URL.

### AI / retrieval layer
- `ask_gpt.py` loads env vars with `load_dotenv()`.
- It initializes `OpenAI` and a Supabase client directly at import time.
- Retrieval flow (`fetch_matching_clauses`):
  1. Generate an embedding for the user question using `text-embedding-ada-002`.
  2. Call Supabase RPC `match_clauses` with that embedding.
  3. If fewer than 5 vector matches, run keyword fallback queries.
  4. Score and dedupe candidates; keep only those above a 0.5 threshold, up to 5.
  5. **Document diversity enforcement**: if all top results are from `"Texas Property Code"`, replace the lowest-scoring entry with the highest-scoring non-TX-Code clause from the scored list. This ensures residents always see the actual HOA rule alongside state law minimums.
  6. If nothing remains after threshold, take top 2 regardless.
  7. Format clauses into HTML snippets and send to `gpt-4o` for final answer.

### Special behavior
- `check_instant_whimsy()` returns canned responses for creator/developer questions, feedback/complaint questions, and fantasy-themed questions. Returns early, skipping retrieval.

## API Endpoints

### `POST /ask`
- Request JSON: `question`, `mode` (optional), `tags` (optional), `output_format` (optional, default `"markdown"`)
- Returns JSON if `output_format == "json"`, otherwise `text/markdown`

### `POST /log`
- Request JSON: `question`, `answer`, `ip`
- Sends to hardcoded Google Apps Script URL

## File Structure

```text
hoa-backend-dev/
├── app.py
├── ask_gpt.py
├── ask_gpt.py.old_but_newer   ← backup of a previous iteration, not active
├── generated-icon.png
├── pyproject.toml
├── render.yaml
├── requirements.txt
├── test_summary_query.py       ← manual print script, not a real test
└── uv.lock
```

## Deployment / Environment

### Render
- `render.yaml` deploys as a Python web service.
- Build: `pip install -r requirements.txt`
- Start: `python app.py`

### Local env
- Copy `.env.example` → `.env` (if present) or set manually:
  - `OPENAI_API_KEY`
  - `SUPABASE_URL`
  - `SUPABASE_SERVICE_ROLE_KEY`

### Local run
```bash
cd hoa-backend-dev
source venv/bin/activate
python app.py
```

## Change History (relative to hoa-backend production)

| Commit | Description |
|---|---|
| `7edb0f2` | Add document diversity enforcement — inject best non-TX-Code clause when all top results are Texas Property Code |

## Promoting Changes to Production

When a change tested here is ready for production:
1. Apply the same edit to `../hoa-backend/ask_gpt.py` (and any other affected files)
2. Commit and push in `hoa-backend`
3. Render will auto-deploy from the `hoa-backend` repo

## Important Observations

- `ask_gpt.py.old_but_newer` is a stale backup — do not use or deploy it.
- `test_summary_query.py` queries `summary` while the main fallback in `ask_gpt.py` uses `plain_summary` — possible schema drift, check before relying on it.
- `pyproject.toml` is stale — names the package `python-template` with no real dependencies.
- `.replit` (if present) references `main.py` but entry file is `app.py`.
- The document diversity check (`tx_code_doc = "Texas Property Code"`) is a string match against the `document` field — if the document name in Supabase ever changes, update the constant here and in production.

## Practical Notes For Future Sessions

- All retrieval logic lives in `ask_gpt.py` → `fetch_matching_clauses()`.
- Scoring logic: `_score_clause()` weights source (vector vs keyword), token coverage, precedence bonus, tag overlap.
- Document diversity block sits between the top-5 selection loop and the "threshold cut too aggressively" fallback.
- If answers are weak, inspect: RPC `match_clauses` threshold (0.6), scoring weights in `_score_clause`, and the diversity enforcement block.
