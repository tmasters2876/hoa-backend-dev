import os
import re
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
            .select("clause_id,document,page,citation,clause_text,plain_summary,link,precedence_level")
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

def format_all_clauses_for_gpt(clauses):
    lines = []
    sorted_clauses = sorted(clauses, key=lambda c: int(c.get("precedence_level", 99)))
    for c in sorted_clauses:
        cid = c.get("clause_id", "")
        doc = c.get("document", "")
        citation = c.get("citation", "")
        link = c.get("link", "")
        summary = (c.get("plain_summary") or "").strip()
        if not summary:
            summary = (c.get("clause_text") or "").strip()[:150]
        else:
            summary = summary[:150]
        lines.append(f"[{cid}] {doc} | {citation} | {link}\n{summary}")
    return "\n\n".join(lines)

def format_clauses_for_display(clauses):
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
            f"<strong>Document</strong>: <code>{document}</code><br>"
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

    all_clauses = get_all_clauses()
    clauses_text = format_all_clauses_for_gpt(all_clauses)

    system_prompt = """You are the PLCA Board Assistant for Plantation Lakes Community Association (PLCA), located in Waller and Grimes Counties, Texas.

You have been given ALL governing document clauses for PLCA. Read through them carefully and answer the resident's question accurately.

DOCUMENT AUTHORITY ORDER (highest to lowest):
1. Texas Property Code — State law, supersedes all HOA rules
2. Declaration of Covenants, Conditions & Restrictions (CCRs)
3. Amendments to CCRs — later amendments supersede earlier ones and the original CCR on that topic
4. Articles of Incorporation
5. Bylaws
6. Board Resolutions and Clarifying Resolutions
7. Specific Regulations (Solar, Flags, Rain Barrels, etc.)
8. 2022 Builders Guidelines — most specific design standards

INSTRUCTIONS:
- Find every clause that is relevant to the question, regardless of which document it comes from
- When documents conflict, apply the authority order above — state which document governs and why
- Give a specific, direct answer. Do not be vague.
- Clearly state whether something is: allowed, prohibited, requires ARC approval, or not addressed
- Cite the specific document and citation for every rule you reference
- If an amendment changes an earlier rule, explain what changed
- If the documents do not address something, say so plainly
- Never fabricate rules not present in the provided clauses
- Never provide legal advice
- For ARC approval decisions, recommend the resident contact the ARC directly
- Use HTML links for citations: <a href="LINK" target="_blank">CITATION</a>
- Close with: "If you have any other questions, feel free to ask!" """

    user_prompt = f"""Resident Question: {question}

ALL GOVERNING DOCUMENT CLAUSES (sorted by document authority, highest first):
{clauses_text}

Answer the resident's question using only the clauses above."""

    response = client.chat.completions.create(
        model=os.getenv("OPENAI_CHAT_MODEL", "gpt-4o"),
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ],
        temperature=0.1,
    )

    final_answer = response.choices[0].message.content
    final_answer = re.sub(r"\[(.*?)\] \((.*?)\)", r"\1 \2", final_answer)

    cited_ids = set(re.findall(r'\b([A-Z][A-Z0-9_\-]{3,})\b', final_answer))
    by_id = {c.get("clause_id"): c for c in all_clauses}
    cited_clauses = [by_id[cid] for cid in cited_ids if cid in by_id]
    cited_clauses.sort(key=lambda c: int(c.get("precedence_level", 99)))

    if not cited_clauses:
        cited_clauses = sorted(all_clauses, key=lambda c: int(c.get("precedence_level", 99)))[:3]

    display_text = format_clauses_for_display(cited_clauses[:5])

    if output_format == "json":
        return {
            "question": question,
            "answer": final_answer,
            "clauses": cited_clauses[:5],
            "mode": mode,
            "format": "json"
        }

    return f"{final_answer}<br><br>{display_text}"
