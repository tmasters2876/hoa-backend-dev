import os
import re
import random
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
            "Dragons? I guard HOA secrets like a scaly beast, but I can't help with fire-breathing dragons. Try fences instead!",
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

QUERY_EXPANSIONS = {
    "ac": "air conditioning",
    "a/c": "air conditioning",
    "rv": "recreational vehicle",
    "hoa": "association",
    "arc": "architectural review committee",
    "ccr": "covenants conditions restrictions",
    "dcc": "covenants conditions restrictions",
    "ev": "electric vehicle",
    "sq ft": "square footage",
}

def expand_query(question: str) -> str:
    """Replace known acronyms with their full forms for better matching."""
    q = question.lower()
    for acronym, expansion in QUERY_EXPANSIONS.items():
        q = re.sub(r'\b' + re.escape(acronym) + r'\b', expansion, q)
    return q

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
        1.5 * token_cov +
        0.1 * precedence_bonus +
        0.2 * tag_overlap
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
            f"<code>{document}</code><br>"
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
    if no_matches:
        fallback_msg = (
            "Note: No clauses matched this question directly. "
            "The clauses below are the closest general rules that may apply — "
            "they may not address the resident's situation exactly.<br><br>"
        )
    else:
        fallback_msg = ""

    return f"""Resident Question:
{question}

{fallback_msg}Relevant Clauses:
{clause_text}

Guidelines:
1. When summarizing a clause, reference the document name (e.g., CCRs, 2022 Builders Guidelines, Texas Property Code) as well as the citation.
2. Clearly state whether the activity in question is allowed, prohibited, or unclear under the rules provided.
3. When a clause comes from the Texas Property Code, note that it represents a state law minimum that applies to all HOAs in Texas.
4. Never fabricate rules or cite documents not represented in the clauses above.
5. If the provided clauses do not clearly answer the question, say so plainly and recommend the resident contact the ARC or board for a definitive answer.
6. Never provide legal advice. For specific legal questions, recommend consulting a licensed attorney.
7. Use HTML for citations: <a href="link" target="_blank">Citation Text</a>
8. Close with: "If you have any other questions, feel free to ask!"
---

Final Answer:
"""

# =========================
# Vector + Fallback Matching (with ranking)
# =========================
def fetch_matching_clauses(question, tags=None, structure_type=None, concern_level=None):
    question = expand_query(question)
    tokens = _tokenize(question)
    keywords = extract_keywords(question)
    short_keywords = [t for t in re.findall(r"[a-zA-Z0-9\-]+", question.lower()) if len(t) == 2 and t not in {"in","on","or","to","at","as","is","of","be","it","we","my","by","do","if","so","no","up","an","us"}]
    keywords = keywords + short_keywords

    # 1) Vector search
    embedding_response = client.embeddings.create(
        model="text-embedding-ada-002",
        input=question,
    )
    query_embedding = embedding_response.data[0].embedding

    response = supabase.rpc("match_clauses", {
        "query_embedding": query_embedding,
        "match_threshold": 0.5,
        "match_count": 10
    }).execute()

    vector_matches = [r for r in (response.data or []) if r.get("status") == "approved"]
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
                if len(k) <= 3:
                    # Short acronyms: require spaces/punctuation around them to avoid "each" matching "ac"
                    parts.append(f"plain_summary.ilike.% {k} %")
                    parts.append(f"clause_text.ilike.% {k} %")
                    parts.append(f"plain_summary.ilike.% {k},%")
                    parts.append(f"clause_text.ilike.% {k},%")
                    parts.append(f"plain_summary.ilike.% {k}.%")
                    parts.append(f"clause_text.ilike.% {k}.%")
                else:
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
            .eq("status", "approved")
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
                q1 = supabase.from_("clauses").select("*").eq("status", "approved").ilike("plain_summary", like_k)
                q2 = supabase.from_("clauses").select("*").eq("status", "approved").ilike("clause_text", like_k)
                chunks.append(q1.limit(10).execute().data or [])
                chunks.append(q2.limit(10).execute().data or [])
        else:
            like = f"%{question}%"
            q1 = supabase.from_("clauses").select("*").eq("status", "approved").ilike("plain_summary", like)
            q2 = supabase.from_("clauses").select("*").eq("status", "approved").ilike("clause_text", like)
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
    for score, c in scored:
        if score < 0.5:
            break  # remaining results are too low-relevance to show
        cid = c.get("clause_id") or c.get("id")
        if cid not in seen:
            seen.add(cid)
            top.append(c)
        if len(top) >= 5:
            break

    # Cap Texas Property Code results at 1 — TX Code sets the legal floor
    # but residents need to see the actual HOA rules. Allow the single
    # highest-scoring TX Code clause through, then fill remaining slots
    # with HOA-specific clauses.
    tx_code_doc = "Texas Property Code"

    tx_clauses = [c for c in top if tx_code_doc in (c.get("document") or "")]
    hoa_clauses = [c for c in top if tx_code_doc not in (c.get("document") or "")]

    if len(tx_clauses) > 1:
        # Keep only the first (highest-scoring) TX Code clause
        tx_clauses = tx_clauses[:1]

        # Backfill with highest-scoring HOA clauses not already in top
        top_ids = {c.get("clause_id") or c.get("id") for c in tx_clauses + hoa_clauses}
        for score, c in scored:
            if len(tx_clauses) + len(hoa_clauses) >= 5:
                break
            cid = c.get("clause_id") or c.get("id")
            if cid not in top_ids and tx_code_doc not in (c.get("document") or ""):
                hoa_clauses.append(c)
                top_ids.add(cid)

        top = tx_clauses + hoa_clauses

    # Safety: if top is still all TX Code (no HOA clauses exist for this
    # topic), leave as-is so residents still get an answer.

    # If the threshold cut too aggressively, take top 2 regardless
    if not top and scored:
        top = [scored[0][1], scored[1][1]] if len(scored) > 1 else [scored[0][1]]

    return top

# =========================
# Soft fallback
# =========================
def fetch_soft_fallback_clauses():
    general_tags = ["rental", "lease","shed","driveway", "fence","guest house","park", "parking", "tenant"]
    query = supabase.from_("clauses").select("*").eq("status", "approved").contains("tags", general_tags).limit(5)
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
            {"role": "system", "content": (
                "You are the PLCA Board Assistant for Plantation Lakes Community Association (PLCA), "
                "located in Waller and Grimes Counties, Texas. "
                "You help residents understand their community's governing documents in plain English. "
                "The governing documents rank in the following order of authority, from highest to lowest: "
                "Texas Property Code, then CCRs and Declarations, then Amendments to CCRs, "
                "then Bylaws, then Board Resolutions and Fine Policies, then the 2022 Builders Guidelines. "
                "When state law conflicts with HOA documents, note that Texas state law governs. "
                "Always be helpful and friendly. Never provide legal advice — for legal questions, "
                "recommend consulting a licensed attorney. "
                "For decisions requiring board or ARC approval, always recommend the resident contact "
                "the ARC or board directly for a final determination. "
                "Never fabricate rules or reference documents not provided in the context."
            )},
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
