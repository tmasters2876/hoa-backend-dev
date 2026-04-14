import os
import re
import random
from dotenv import load_dotenv
from supabase import create_client
from openai import OpenAI

# =========================
# Config
# =========================
RETRIEVAL_COUNT = 30          # How many candidates to retrieve before GPT analysis
INCLUDE_FULL_TEXT_TOP_N = 3   # How many clauses to show full text for in output
INCLUDE_FULL_TEXT_MAX_CHARS = 1500

DOCUMENT_HIERARCHY = """
GOVERNING DOCUMENT HIERARCHY (highest to lowest authority):
1. Texas Property Code — State law. Supersedes all HOA documents.
2. Declaration of Covenants, Conditions & Restrictions (CCRs) — Foundational covenant.
3. Amendments to CCRs — Modify the CCRs. Later amendments supersede earlier ones.
4. Articles of Incorporation — Creates the legal entity.
5. Bylaws — Internal governance of the Association.
6. Board Resolutions & Clarifying Resolutions — Procedural rules and interpretations.
7. Specific Regulations (Solar, Flags, Rain Barrels, etc.) — Additive gap-filling rules.
8. 2022 Builders Guidelines — Most specific, least authoritative.

When documents conflict, the higher authority always governs.
When an Amendment contradicts the original CCR, the Amendment governs.
Texas Property Code sets minimum rights that HOA rules cannot reduce.
"""

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
    "hoa": "homeowners association",
    "arc": "architectural review committee",
    "ccr": "covenants conditions restrictions",
    "dcc": "covenants conditions restrictions",
    "ev": "electric vehicle",
    "sq ft": "square footage",
    "shingles": "roof shingles alternative materials wind hail resistant",
    "roofing": "roof shingles alternative materials",
    "replace roof": "roof shingles alternative materials wind hail resistant",
}

def expand_query(question: str) -> str:
    q = question.lower()
    sorted_expansions = sorted(QUERY_EXPANSIONS.items(), key=lambda x: len(x[0]), reverse=True)
    for phrase, expansion in sorted_expansions:
        q = q.replace(phrase, expansion)
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

# =========================
# Stage 1: Broad Retrieval
# =========================
def fetch_candidate_clauses(question: str) -> list:
    """
    Retrieve a broad set of candidate clauses using both vector search
    and keyword search. Does not score or filter — just gets candidates.
    """
    expanded = expand_query(question)
    keywords = extract_keywords(expanded)
    seen = set()
    candidates = []

    def add_clauses(clauses, source):
        for c in clauses:
            cid = c.get("clause_id") or c.get("id")
            if cid and cid not in seen:
                seen.add(cid)
                c["match_source"] = source
                c["clause_id"] = cid
                candidates.append(c)

    # 1) Vector search — semantic similarity
    try:
        embedding_response = client.embeddings.create(
            model=os.getenv("OPENAI_EMBEDDING_MODEL", "text-embedding-ada-002"),
            input=expanded,
        )
        query_embedding = embedding_response.data[0].embedding
        vector_result = supabase.rpc("match_clauses", {
            "query_embedding": query_embedding,
            "match_threshold": 0.5,
            "match_count": RETRIEVAL_COUNT,
        }).execute()
        vector_clauses = [r for r in (vector_result.data or []) if r.get("status") == "approved"]
        add_clauses(vector_clauses, "Vector Match")
        print(f"[retrieval] Vector search returned {len(vector_clauses)} clauses")
    except Exception as e:
        print(f"[retrieval] WARNING: vector search failed: {e}")

    # 2) Keyword search — literal text matching across plain_summary and clause_text
    if keywords:
        try:
            parts = []
            for k in set(keywords):
                parts.append(f"plain_summary.ilike.%{k}%")
                parts.append(f"clause_text.ilike.%{k}%")
            or_filter = ",".join(parts)
            keyword_result = (
                supabase
                .from_("clauses")
                .select("*")
                .eq("status", "approved")
                .or_(or_filter)
                .limit(RETRIEVAL_COUNT)
                .execute()
            )
            keyword_clauses = keyword_result.data or []
            add_clauses(keyword_clauses, "Keyword Match")
            print(f"[retrieval] Keyword search returned {len(keyword_clauses)} new clauses")
        except Exception as e:
            print(f"[retrieval] WARNING: keyword search failed: {e}")

    # 3) Also search original (non-expanded) question keywords
    original_keywords = extract_keywords(question.lower())
    new_keywords = set(original_keywords) - set(keywords)
    if new_keywords:
        try:
            parts = []
            for k in new_keywords:
                parts.append(f"plain_summary.ilike.%{k}%")
                parts.append(f"clause_text.ilike.%{k}%")
            or_filter = ",".join(parts)
            orig_result = (
                supabase
                .from_("clauses")
                .select("*")
                .eq("status", "approved")
                .or_(or_filter)
                .limit(15)
                .execute()
            )
            add_clauses(orig_result.data or [], "Keyword Match")
        except Exception as e:
            print(f"[retrieval] WARNING: original keyword search failed: {e}")

    print(f"[retrieval] Total unique candidates: {len(candidates)}")
    return candidates


# =========================
# Format Clauses for Display
# =========================
def format_clauses_for_prompt(clauses):
    # Sort by precedence (lower = higher authority)
    sorted_clauses = sorted(clauses, key=lambda c: int(c.get("precedence_level", 99)))

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
# Stage 3: Build Final Answer
# =========================
def build_gpt_prompt(question, clause_text, no_matches=False):
    if no_matches:
        fallback_msg = (
            "Note: No clauses directly matched this question. "
            "The clauses below are the closest general rules that may apply.<br><br>"
        )
    else:
        fallback_msg = ""

    return f"""Resident Question:
{question}

{fallback_msg}
{DOCUMENT_HIERARCHY}

Relevant Clauses (already filtered and ordered by relevance and authority):
{clause_text}

Guidelines:
1. Answer the question directly and specifically. Do not be vague.
2. Reference the specific document name and citation for each rule you cite.
3. When documents conflict, apply the hierarchy above — state which document governs.
4. When a Texas Property Code clause applies, note it sets the legal minimum floor.
5. Clearly state whether the activity is allowed, prohibited, requires approval, or is unclear.
6. If an amendment modifies an earlier rule, explain what changed.
7. Never fabricate rules or cite documents not in the clauses above.
8. Never provide legal advice — recommend a licensed attorney for legal questions.
9. For ARC or board approval decisions, recommend the resident contact them directly.
10. Use HTML for citations: <a href="link" target="_blank">Citation Text</a>
11. Close with: "If you have any other questions, feel free to ask!"
---

Final Answer:
"""


# =========================
# Soft fallback
# =========================
def fetch_soft_fallback_clauses():
    general_tags = ["rental", "lease", "shed", "driveway", "fence", "guest house", "park", "parking", "tenant"]
    query = supabase.from_("clauses").select("*").eq("status", "approved").contains("tags", general_tags).limit(5)
    result = query.execute()
    fallback_data = result.data or []
    for clause in fallback_data:
        clause["match_source"] = "General Soft Fallback"
        clause["clause_id"] = clause.get("clause_id") or clause.get("id")

    if not fallback_data:
        fallback_data = [{
            "precedence_level": "9",
            "plain_summary": "Please check your HOA documents or ARC for specific rules on this topic.",
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

    # Stage 1: Broad retrieval
    candidates = fetch_candidate_clauses(question)

    # Fall back to soft fallback if nothing found
    no_matches = False
    if not candidates:
        candidates = fetch_soft_fallback_clauses()
        no_matches = True

    # Stage 2: GPT answers directly from all candidates
    # Sort by precedence so highest authority appears first
    sorted_candidates = sorted(
        candidates,
        key=lambda c: int(c.get("precedence_level", 99))
    )

    clause_text = format_clauses_for_prompt(sorted_candidates)
    prompt = build_gpt_prompt(question, clause_text, no_matches)

    gpt_response = client.chat.completions.create(
        model=os.getenv("OPENAI_CHAT_MODEL", "gpt-4o"),
        messages=[
            {"role": "system", "content": (
                "You are the PLCA Board Assistant for Plantation Lakes Community Association (PLCA), "
                "located in Waller and Grimes Counties, Texas. "
                "You help residents understand their HOA governing documents in plain English. "
                "You will be given a set of candidate clauses retrieved from the governing documents. "
                "Many of these clauses may not be relevant to the question — ignore them. "
                "Focus only on clauses that directly answer the resident's question. "
                "Apply the document hierarchy: Texas Property Code > CCRs > Amendments > Bylaws > Resolutions > Builders Guidelines. "
                "When documents conflict, the higher authority governs. "
                "Give specific, direct answers. Never fabricate rules. "
                "Never provide legal advice. "
                "Recommend contacting the ARC or board for approval decisions."
            )},
            {"role": "user", "content": prompt}
        ],
        temperature=0.2,
    )

    final_answer = gpt_response.choices[0].message.content
    final_answer = re.sub(r"\[(.*?)\] \((.*?)\)", r"\1 \2", final_answer)

    if output_format == "json":
        return {
            "question": question,
            "answer": final_answer,
            "clauses": sorted_candidates,
            "mode": mode,
            "format": "json"
        }

    return f"{final_answer}<br><br>{clause_text}"
