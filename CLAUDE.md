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

Identical to production. See `../hoa-backend/CLAUDE.md` → **ask_gpt.py — Full-Corpus Approach** for the full description.

Key points:
- `get_all_clauses()` loads all `status='approved'` clauses into `_clause_cache` at startup
- Full corpus sent to `gpt-4o` per request; no vector search
- GPT cites via `[CLAUSE_ID]` brackets; post-processed to HTML links
- Post-processing order is fixed: normalize brackets → capture `raw_cited_ids` → inject links
- `DOC_SHORT` and `DOC_SHORT_DISPLAY` maps must be updated when new documents are added

---

## File Structure

```
hoa-backend-dev/
├── app.py
├── ask_gpt.py
├── ask_gpt_gpt.py        ← backup/reference, not active
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
```

---

## Notes for Claude Sessions

- Changes here go to dev only — always test before promoting
- `ask_gpt_gpt.py` is a stale backup — do not deploy it
- `pyproject.toml` is stale — names package `python-template`, no real deps
- Token concern: if clause corpus grows very large, full-corpus approach will hit GPT token limits
