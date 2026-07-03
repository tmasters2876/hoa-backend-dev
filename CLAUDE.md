# CLAUDE.md — hoa-backend-dev

Last updated: April 2026

## Project Summary

`hoa-backend-dev` is the **development branch** of `hoa-backend`. Same architecture — Flask API, OpenAI, Supabase — but receives changes for testing before promotion to production.

**No admin app in this repo.** Only `app.py` + `ask_gpt.py`.

For full architecture, Supabase schema, SQL patterns, auth, and deployment notes see `../hoa-backend/CLAUDE.md`.

---

## Local Run

```bash
cd hoa-backend-dev
/Users/thomasmasters/Projects/.venv/bin/python3 app.py
```
Port 5000. Use full venv path — never system `python3`.

---

## Promoting to Production

```bash
cp ask_gpt.py ../hoa-backend/ask_gpt.py
cd ../hoa-backend
git add ask_gpt.py
git commit -m "Promote: description"
git push origin main
```

---

## ask_gpt.py Architecture

Not yet promoted to production — see `../hoa-backend/CLAUDE.md` → **ask_gpt.py — Full-Corpus Approach** for the base description this builds on.

Key points:
- `get_all_clauses()` loads all `status='approved'` clauses (now including `tags`) into `_clause_cache` at startup
- **New: `filter_relevant_clauses(question, all_clauses, tags=None, min_results=15, min_score=2)`** — scores each clause by keyword overlap with the question plus a higher-weighted match against the clause's own `tags` array (and any explicit `tags` the caller passes, currently always inert from Carrd). Returns the relevant subset sorted by score desc / precedence asc. **Safety net**: if fewer than `min_results` clauses clear `min_score`, returns `all_clauses` completely unfiltered — this is what keeps vague/uncovered questions from losing context. Validated against the live corpus: narrow questions (fences, solar panels, paint) cut the prompt 79–98%; vague ("Tell me about the HOA") or likely-uncovered (chickens) questions correctly fall through to the full corpus.
- Only the prompt actually sent to GPT uses the filtered subset (`answer_question()`'s `clauses_text` build). Every other reference — both `by_id` lookups, the top-3-by-precedence fallback, and the "no citations found" keyword-scored display fallback — still operates over the complete, unfiltered `all_clauses`, unchanged. This is deliberate: it's what keeps citation links from silently vanishing if GPT ever cites something outside the filtered subset.
- Kill-switch: `ENABLE_CLAUSE_PREFILTER=false` (env var) disables the pre-filter instantly, falling back to the old always-full-corpus behavior, no redeploy needed.
- Full corpus (or filtered subset) sent to `gpt-4o` per request; no vector search
- GPT cites via `[CLAUSE_ID]` brackets; post-processed to HTML links
- Post-processing order is fixed: normalize brackets → capture cited IDs → inject links (dev is on an older citation-capture regex than production — see `ask_gpt.py` around the `cited_ids`/`by_id` block; not touched by the pre-filter work)
- `DOC_SHORT` and `DOC_SHORT_DISPLAY` maps must be updated when new documents are added

---

## File Structure

```
hoa-backend-dev/
├── app.py
├── ask_gpt.py
├── ask_gpt_gpt.py        ← backup/reference, not active
├── ask_gpt copy.py       ← another stale backup, not active
├── tests/
│   └── test_ask_gpt.py   ← unit tests for filter_relevant_clauses() (pure function, no mocking needed)
├── requirements.txt
├── render.yaml
├── pyproject.toml        ← stale, ignore
└── test_summary_query.py ← manual print script, not a test
```

---

## Environment Variables

```
OPENAI_API_KEY
SUPABASE_URL
SUPABASE_SERVICE_ROLE_KEY
OPENAI_CHAT_MODEL          (default: gpt-4o)
OPENAI_EMBEDDING_MODEL     (default: text-embedding-ada-002)
ENABLE_CLAUSE_PREFILTER    (default: true; set "false" to disable the relevance pre-filter instantly)
```

No `.env` file exists in this directory today — local runs need to source `../hoa-backend/.env` (same Supabase project/credentials) or create one here.

---

## Notes for Claude Sessions

- Changes here go to dev only — always test before promoting
- `ask_gpt_gpt.py` and `ask_gpt copy.py` are stale backups — do not deploy them
- `pyproject.toml` is stale — names package `python-template`, no real deps
- Token concern (partially addressed): `filter_relevant_clauses()` cuts the prompt significantly for narrow/specific questions, but the corpus is still fetched and cached in full — if it keeps growing, revisit `min_results`/`min_score` or reconsider the admin-side embeddings/vector path (`match_clauses` RPC) for the resident-facing pipeline too
