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
    """Format all clauses compactly for GPT context."""
    lines = []
    sorted_clauses = sorted(clauses, key=lambda c: int(c.get("precedence_level", 99)))
    for c in sorted_clauses:
        cid = c.get("clause_id", "")
        doc = c.get("document", "")
        citation = c.get("citation", "")
        link = c.get("link", "")
        summary = c.get("plain_summary") or c.get("clause_text", "")[:300]
        lines.append(f"[{cid}] {doc} | {citation} | {link}\n{summary}")
    return "\n\n".join(lines)

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

    # Get all clauses
    all_clauses = get_all_clauses()
    clauses_text = format_clauses_for_gpt(all_clauses)

    system_prompt = f"""You are the PLCA Board Assistant for Plantation Lakes Community Association (PLCA), located in Waller and Grimes Counties, Texas.

You have access to ALL governing documents for PLCA. Your job is to find the answer to the resident's question by reading through the provided clauses.

{DOCUMENT_HIERARCHY}

INSTRUCTIONS:
1. Read ALL provided clauses carefully to find ones that answer the question
2. Apply the document hierarchy — higher authority governs when documents conflict
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

ALL GOVERNING DOCUMENT CLAUSES:
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

    # For display, find clauses GPT mentioned by looking for clause IDs in the answer
    cited_ids = re.findall(r'\b([A-Z][A-Z0-9_\-]{3,})\b', final_answer)
    cited_clauses = [c for c in all_clauses if c.get("clause_id") in cited_ids]
    if not cited_clauses:
        cited_clauses = sorted(all_clauses, key=lambda c: int(c.get("precedence_level", 99)))[:3]

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
