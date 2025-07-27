import os
import re
import random
from collections import defaultdict
from dotenv import load_dotenv
from supabase import create_client
from openai import OpenAI

# =========================
# Config toggles
# =========================
INCLUDE_FULL_TEXT_TOP_N = 2
INCLUDE_FULL_TEXT_MAX_CHARS = 1200

# =========================
# Load environment
# =========================
load_dotenv()

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
supabase = create_client(
    os.getenv("SUPABASE_URL"),
    os.getenv("SUPABASE_SERVICE_ROLE_KEY")
)

# =========================
# Instant whimsy
# =========================
def check_instant_whimsy(question_lower):
    creator_keywords = [
        "creator", "developer", "who made you", "who built you",
        "how were you made", "who created you", "who designed you", "who programmed you"
    ]
    dragon_keywords = ["dragon", "castle", "wizard", "unicorn", "fairy", "goblin", "elf", "moat", "magic"]

    if any(k in question_lower for k in creator_keywords):
        return random.choice([
            "My creator was a combination of code, governing documents, and the hard work of your community members working for you.",
            "Created by your fellow HOA members to make your life easier.",
            "Built by your community to help you navigate your governing documents.",
            "Developed by your HOA members to make your life simpler.",
            "I was created by your fellow community members to provide you with an easy-to-use tool to search your governing documents."
        ])
    elif any(k in question_lower for k in dragon_keywords):
        return random.choice([
            "Dragons? I guard HOA secrets like a scaly beast, but I can’t help with fire-breathing dragons. Try fences instead!",
            "Ah, dragons and castles! Sadly I handle covenants, not quests. Ask me about sheds!",
            "If you see a wizard in your yard, call the ARC — or maybe just me. 🧙‍♂️"
        ])
    return None

# =========================
# Helpers
# =========================
STOPWORDS = {
    "the","a","an","and","or","of","to","in","on","for","with","by","is","are","was","were",
    "be","can","i","my","our","your","their","as","it","that","this","do","does","did","from",
    "at","we","you","they","have","has","had","me","us","them","am","will","shall","may",
    "should","would","could","not","no","yes","if","but","so","than","then","who","what",
    "when","where","why","how"
}

def _trim(text, max_chars):
    if not text:
        return ""
    text = text.strip()
    return text if len(text) <= max_chars else (text[:max_chars] + "…")

def _tokenize(query: str):
    return [t for t in re.findall(r"[a-zA-Z0-9\-]+", (query or "").lower()) if len(t) > 2]

def extract_keywords(question: str):
    words = _tokenize(question)
    return [w for w in words if w not in STOPWORDS]

def _hit_ratio(text: str, tokens):
    if not text:
        return 0.0
    text_l = text.lower()
    hits = sum(1 for t in tokens if t in text_l)
    return hits / max(1, len(tokens))

def _score_clause(clause, tokens, source_weight):
    ps = clause.get("plain_summary", "") or ""
    ct = clause.get("clause_text", "") or ""
    both = (ps + " " + ct).lower()

    token_cov = _hit_ratio(both, tokens)
    precedence = clause.get("precedence_level")
    try:
        precedence_bonus = max(0, 10 - int(precedence)) / 100.0
    except Exception:
        precedence_bonus = 0.0

    tags = clause.get("tags") or []
    tag_overlap = 0.0
    if isinstance(tags, list) and tokens:
        tag_overlap = len(set(tokens) & {str(t).lower() for t in tags}) / 50.0

    return (
        source_weight +
        0.6 * token_cov +
        0.1 * precedence_bonus +
        0.05 * tag_overlap
    )

# =========================
# Format Clauses
# =========================
def format_clauses_for_prompt(clauses):
    sorted_clauses = sorted(
        clauses,
        key=lambda c: int(c.get("precedence_level", 99))
    )

    formatted = []
    for idx, c in enumerate(sorted_clauses, 1):
        citation = c.get("citation", f"Clause {idx}")
        link = c.get("link", "")
        summary = c.get("plain_summary", "No summary provided.")
        source = c.get("match_source", "Unknown")
        clause_id = c.get("clause_id", "")
        document = c.get("document", "Unknown")

        clause_text_full = c.get("clause_text") or ""
        clause_text_to_show = ""
        if clause_text_full and idx <= INCLUDE_FULL_TEXT_TOP_N:
            clause_text_to_show = _trim(clause_text_full, INCLUDE_FULL_TEXT_MAX_CHARS)

        if citation and link:
            link_html = f'<a href="{link}" target="_blank" rel="noopener noreferrer">{citation}</a>'
        else:
            link_html = citation

        entry = (
            f"<b>{idx}. <strong>Summary</strong>: According to {link_html}, {summary}.</b><br>"
            f"<strong>Match Source</strong>: {source} • "
            f"<code>{document}</code> • "
            f"<strong>Reviewer ID</strong>: <code>{clause_id}</code><br>"
        )

        if clause_text_to_show:
            entry += (
                f"<details><summary>View Full Clause Text</summary>"
                f"<pre>{clause_text_to_show}</pre></details>"
            )

        formatted.append(entry)

    return "<br><br>".join(formatted)

# =========================
# GPT Prompt
# =========================
def build_gpt_prompt(question, clause_text, no_matches=False):
    fallback_msg = (
        "⚠️ There were no direct matches to this question. Below are general HOA rules that might still help you respond.<br><br>"
        if no_matches else ""
    )
    return f"""You are an HOA policy assistant. Use both the clause summaries and original clause texts to answer clearly.

Resident Question:
{question}

{fallback_msg}
Relevant Clauses:
{clause_text}

Guidelines:
1. Summarize each relevant clause (use both summary and key clause text).
2. Clearly state if the rules allow or prohibit the activity (e.g., Airbnb).
3. If unclear, recommend checking with the ARC.
4. Close with: “If you have any other questions, feel free to ask!”

Use HTML for citations like this: <a href="link" target="_blank">Art. VI</a>
---

Final Answer:
"""

# =========================
# Vector + Fallback Matching (with ranking)
# =========================
def fetch_matching_clauses(question, tags=None, structure_type=None, concern_level=None):
    tokens = _tokenize(question)
    keywords = extract_keywords(question)

    # 1) Vector search
    embedding_response = client.embeddings.create(
        model="text-embedding-ada-002",
        input=question,
    )
    query_embedding = embedding_response.data[0].embedding

    response = supabase.rpc("match_clauses", {
        "query_embedding": query_embedding,
        "match_threshold": 0.6,
        "match_count": 10
    }).execute()

    vector_matches = response.data or []
    for clause in vector_matches:
        clause["match_source"] = "Vector Match"
        clause["clause_id"] = clause.get("clause_id") or clause.get("id")

    # 2) Keyword fallback (always run)
    # Build an or_ filter dynamically from extracted keywords.
    # If we have no decent keywords, fall back to the whole question.
    fallback_matches = []
    try:
        if keywords:
            # Build a long OR filter like: plain_summary.ilike.%shed%,clause_text.ilike.%shed%,plain_summary.ilike.%paint%,...
            parts = []
            for k in set(keywords):
                parts.append(f"plain_summary.ilike.%{k}%")
                parts.append(f"clause_text.ilike.%{k}%")
            or_filter = ",".join(parts)
        else:
            like = f"%{question}%"
            or_filter = f"plain_summary.ilike.{like},clause_text.ilike.{like}"

        query = (
            supabase
            .from_("clauses")
            .select("*")
            .or_(or_filter)
        )
        if tags:
            query = query.contains("tags", tags)
        if structure_type:
            query = query.eq("structure_type", structure_type)
        if concern_level:
            query = query.eq("concern_level", concern_level)

        fallback_matches = query.limit(10).execute().data or []
    except Exception:
        # older client fallback: two queries
        seen = set()
        chunks = []

        if keywords:
            # run multiple ilike queries per keyword and merge
            merged = []
            for k in set(keywords):
                like_k = f"%{k}%"
                q1 = supabase.from_("clauses").select("*").ilike("plain_summary", like_k)
                q2 = supabase.from_("clauses").select("*").ilike("clause_text", like_k)
                chunks.append(q1.limit(10).execute().data or [])
                chunks.append(q2.limit(10).execute().data or [])
        else:
            like = f"%{question}%"
            q1 = supabase.from_("clauses").select("*").ilike("plain_summary", like)
            q2 = supabase.from_("clauses").select("*").ilike("clause_text", like)
            chunks.append(q1.limit(10).execute().data or [])
            chunks.append(q2.limit(10).execute().data or [])

        merged = []
        for chunk in chunks:
            for row in chunk:
                cid = row.get("clause_id") or row.get("id")
                if cid not in seen:
                    seen.add(cid)
                    merged.append(row)
        fallback_matches = merged

    for clause in fallback_matches:
        clause["match_source"] = "Keyword Fallback"
        clause["clause_id"] = clause.get("clause_id") or clause.get("id")

    # 3) Merge + score + dedupe
    scored = []
    for c in vector_matches:
        scored.append((_score_clause(c, tokens, 1.0), c))
    for c in fallback_matches:
        scored.append((_score_clause(c, tokens, 0.7), c))

    scored.sort(key=lambda x: x[0], reverse=True)

    seen = set()
    top = []
    for _, c in scored:
        cid = c.get("clause_id") or c.get("id")
        if cid not in seen:
            seen.add(cid)
            top.append(c)
        if len(top) >= 5:
            break

    return top

# =========================
# Soft fallback
# =========================
def fetch_soft_fallback_clauses():
    general_tags = ["rental", "lease", "guest house", "tenant"]
    query = supabase.from_("clauses").select("*").contains("tags", general_tags).limit(5)
    result = query.execute()
    fallback_data = result.data or []
    for clause in fallback_data:
        clause["match_source"] = "General Soft Fallback"
        clause["clause_id"] = clause.get("clause_id") or clause.get("id")

    if not fallback_data:
        fallback_data = [{
            "precedence_level": "9",
            "plain_summary": "Please check your HOA documents or ARC for specific rental rules.",
            "citation": "General Guideline",
            "link": "",
            "document": "Default Fallback",
            "match_source": "Injected Fallback",
            "clause_id": "FALLBACK_GENERAL",
            "clause_text": ""
        }]
    return fallback_data

# =========================
# MAIN
# =========================
def answer_question(question, tags=None, mode="default", structure_type=None, concern_level=None, output_format="markdown"):
    whimsy_reply = check_instant_whimsy(question.lower().strip())
    if whimsy_reply:
        return whimsy_reply

    clauses = fetch_matching_clauses(question, tags=tags, structure_type=structure_type, concern_level=concern_level)

    no_matches = False
    if not clauses:
        clauses = fetch_soft_fallback_clauses()
        no_matches = True

    clause_text = format_clauses_for_prompt(clauses)
    prompt = build_gpt_prompt(question, clause_text, no_matches)

    gpt_response = client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": "You are an expert HOA assistant."},
            {"role": "user", "content": prompt}
        ],
        temperature=0.4
    )

    final_answer = gpt_response.choices[0].message.content
    final_answer = re.sub(r"\[(.*?)\] \((.*?)\)", r"\1 \2", final_answer)

    if output_format == "json":
        return {
            "question": question,
            "answer": final_answer,
            "clauses": clauses,
            "mode": mode,
            "format": "json"
        }

    return f"{final_answer}<br><br>{clause_text}"
