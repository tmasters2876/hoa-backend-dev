import os
import re
import json
import random
from dotenv import load_dotenv
from supabase import create_client
from openai import OpenAI

load_dotenv()

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
supabase = create_client(
    os.getenv("SUPABASE_URL"),
    os.getenv("SUPABASE_SERVICE_ROLE_KEY")
)

DOCUMENT_HIERARCHY = """
Document authority order (highest to lowest):
1. Texas Property Code
2. CCRs & Declarations
3. CCR Amendments
4. Articles of Incorporation
5. Bylaws
6. Board Resolutions & Clarifying Resolutions
7. Specific Regulations (Solar, Flags, Rain Barrels, etc.)
8. 2022 Builders Guidelines
"""

# Cache all clauses in memory after first load
_clause_cache = None

def get_all_clauses():
    global _clause_cache
    if _clause_cache is not None:
        return _clause_cache

    all_clauses = []
    page_size = 1000
    offset = 0
    while True:
        result = (
            supabase
            .from_("clauses")
            .select("clause_id,document,page,citation,clause_text,plain_summary,link,precedence_level,tags")
            .eq("status", "approved")
            .range(offset, offset + page_size - 1)
            .execute()
        )
        batch = result.data or []
        all_clauses.extend(batch)
        if len(batch) < page_size:
            break
        offset += page_size

    _clause_cache = all_clauses
    print(f"[cache] Loaded {len(all_clauses)} approved clauses")
    return all_clauses

def format_clauses_for_gpt(clauses):
    """Format selected clauses compactly for GPT context."""
    lines = []
    sorted_clauses = sorted(clauses, key=lambda c: int(c.get("precedence_level", 99)))
    for c in sorted_clauses:
        cid = c.get("clause_id", "")
        doc = c.get("document", "")
        citation = c.get("citation", "")
        link = c.get("link", "")
        # Keep prompt payload small: prefer summary, and only fall back to trimmed clause text.
        summary = (c.get("plain_summary") or "").strip()
        if not summary:
            summary = (c.get("clause_text", "") or "").strip()[:500]
        summary = summary[:500]
        lines.append(f"[{cid}] {doc} | {citation} | {link}\n{summary}")
    return "\n\n".join(lines)

def retrieve_relevant_clauses(question, limit=6):
    """Lightweight keyword retrieval over cached clauses; returns top relevant clauses only."""
    all_clauses = get_all_clauses()
    q = (question or "").lower()
    q_tokens = set(re.findall(r"[a-z0-9]+", q))

    # Special-case override for fence height intent to prioritize numeric fence rules.
    is_fence_height = "fence" in q and any(k in q for k in ["height", "tall", "high"])
    fence_dim_terms = ["height", "feet", "foot", "ft", "inch", "inches", "maximum", "max", "not exceed"]
    scored = []
    for clause in all_clauses:
        searchable = " ".join([
            str(clause.get("document", "") or ""),
            str(clause.get("citation", "") or ""),
            str(clause.get("tags", "") or ""),
            str(clause.get("plain_summary", "") or ""),
            str(clause.get("clause_text", "") or ""),
        ]).lower()

        score = 0

        # For fence-height intent, hard-filter to fence + dimensional clauses only.
        if is_fence_height:
            has_fence = "fence" in searchable
            has_dimension = any(term in searchable for term in fence_dim_terms)
            if not (has_fence and has_dimension):
                continue

        # Base keyword overlap scoring.
        for token in q_tokens:
            if len(token) > 2 and token in searchable:
                score += 2

        # Strong fence-height scoring for the filtered set only.
        if is_fence_height:
            if "fence" in searchable:
                score += 40
            if "height" in searchable:
                score += 20
            if "not exceed" in searchable or "maximum" in searchable or "max" in searchable:
                score += 25
            # Strongest boost for explicit dimensional values like "6 feet", "8 ft", "72 inches".
            if re.search(r"\b\d+\s*(feet|foot|ft|inches|inch|in)\b", searchable):
                score += 60

        if score > 0:
            scored.append((score, clause))

    if not scored:
        return []

    scored.sort(key=lambda x: (-x[0], int(x[1].get("precedence_level", 99))))
    # Fence-height intent is intentionally narrow and deterministic: return at most 3.
    if is_fence_height:
        return [c for _, c in scored[:3]]
    return [c for _, c in scored[: min(max(limit, 4), 6)]]

def format_clauses_for_display(clauses):
    """Format relevant clauses for display in the UI."""
    formatted = []
    for idx, c in enumerate(clauses, 1):
        citation = c.get("citation", f"Clause {idx}")
        link = c.get("link", "")
        summary = c.get("plain_summary", "No summary provided.")
        document = c.get("document", "Unknown")
        clause_text = c.get("clause_text", "")

        if citation and link:
            link_html = f'<a href="{link}" target="_blank" rel="noopener noreferrer">{citation}</a>'
        else:
            link_html = citation

        entry = (
            f"<b>{idx}. <strong>Summary</strong>: According to {link_html}, {summary}.</b><br>"
            f"<strong>Match Source</strong>: Governing Documents • "
            f"<code>{document}</code><br>"
        )
        if clause_text and idx <= 2:
            trimmed = clause_text[:1500] + "…" if len(clause_text) > 1500 else clause_text
            entry += (
                f"<details><summary>View Full Clause Text</summary>"
                f"<pre>{trimmed}</pre></details>"
            )
        formatted.append(entry)
    return "<br><br>".join(formatted)

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

def answer_question(question, tags=None, mode="default", structure_type=None, concern_level=None, output_format="markdown"):
    whimsy_reply = check_instant_whimsy(question.lower().strip())
    if whimsy_reply:
        return whimsy_reply

    # Retrieve only the most relevant clauses (prevents token overflow and noisy context).
    selected_clauses = retrieve_relevant_clauses(question, limit=6)
    if not selected_clauses:
        no_match_answer = (
            "I could not find a specific governing clause that directly answers this question. "
            "Please contact the ARC or the HOA board for clarification. "
            "If you have any other questions, feel free to ask!"
        )
        if output_format == "json":
            return {
                "question": question,
                "answer": no_match_answer,
                "clauses": [],
                "mode": mode,
                "format": "json"
            }
        return no_match_answer

    clauses_text = format_clauses_for_gpt(selected_clauses)

    system_prompt = f"""You are the PLCA Board Assistant for Plantation Lakes Community Association (PLCA), located in Waller and Grimes Counties, Texas.

You are given a preselected set of relevant clauses from PLCA governing documents. Your job is to find the answer to the resident's question by reading through the provided clauses.

{DOCUMENT_HIERARCHY}

INSTRUCTIONS:
1. Read ALL provided clauses carefully to find ones that answer the question
2. Apply the document hierarchy — higher authority governs when documents conflict
2a. If clauses conflict, do NOT merge or average rules; state the single governing rule from the higher-authority document and explain why it controls
3. Give a specific, direct answer grounded only in the clauses provided
4. Reference the specific document and citation for each rule you cite
5. If an amendment modifies an earlier rule, say what changed
6. State clearly whether something is allowed, prohibited, or requires ARC approval
7. If no clause addresses the question, say so plainly and recommend contacting the ARC
8. Never fabricate rules not present in the clauses
9. Never provide legal advice
10. Close with: "If you have any other questions, feel free to ask!"

Use HTML links for citations: <a href="LINK" target="_blank">CITATION</a>"""

    user_prompt = f"""Resident Question: {question}

PRESELECTED RELEVANT CLAUSES:
{clauses_text}

Please answer the resident's question based on the clauses above."""

    response = client.chat.completions.create(
        model=os.getenv("OPENAI_CHAT_MODEL", "gpt-4o"),
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ],
        temperature=0.2,
    )

    final_answer = response.choices[0].message.content
    final_answer = re.sub(r"\[(.*?)\] \((.*?)\)", r"\1 \2", final_answer)

    # Display the same retrieved evidence that was sent to GPT (no regex-based post-parsing).
    cited_clauses = selected_clauses[:3]

    display_text = format_clauses_for_display(cited_clauses)

    if output_format == "json":
        return {
            "question": question,
            "answer": final_answer,
            "clauses": cited_clauses,
            "mode": mode,
            "format": "json"
        }

    return f"{final_answer}<br><br>{display_text}"
