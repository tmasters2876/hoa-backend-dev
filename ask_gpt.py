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

    # Short document name map to save tokens
    DOC_SHORT = {
        "Declaration_of_Covenants,_Conditions,_&_Restrictions_-_09-17-2004.pdf": "CCR",
        "First_Amendment_to_the_Declaration_of_Covenants,_Conditions_&_Restrictions_-_10-05-2004.pdf": "CCR-Amend1",
        "1Second_Amendment_to_the_Declaration_of_Covenants,_Conditions_and_Restrictions_11-08-2005.pdf": "CCR-Amend2",
        "Supplemental_Amendment_to_the_Declaration_of_Covenants,_Conditions_&_Restrictions_-_Sec_2_-_(Waller_Cty_10-4-2004).pdf": "CCR-Supp2",
        "Supplemental_Amendment_to_the_Declaration_of_Covenants,_Conditions_&_Restrictions_-_Sec_3_(Grimes_Cty_-_11-16-2004).pdf": "CCR-Supp3",
        "Supplemental_Amendment_to_the_Declaration_of_Covenants,_Conditions_&_Restrictions_-_Sec_4_-_11-02-2005.pdf": "CCR-Supp4",
        "Amendment_to_DCC&R_-_11.04.19.pdf": "CCR-Amend2019",
        "ByLaws_-_PLCA_-_10-19-2004.pdf": "Bylaws",
        "Articles_of_Incorporation_-_Waller_-_3-26-18.pdf": "Articles-Waller",
        "PLCA_-_Articles_of_Incorporation_-_Grimes_-_3-26-18.pdf": "Articles-Grimes",
        "Resolution_Adopting_Covenants,_Conditions_&_Restrictions_Enforcement_Process_(Recorded_Waller_Co_-_02-09-17.pdf": "Enforce-Waller",
        "Resolution_Adopting_Conditions,_Conditions_&_Restrictions_Enforcement_Process_-_(Grimes_Cty_02-27-2017).pdf": "Enforce-Grimes",
        "Recorded_Adopting_Governing_Documents_Enforcement_Process_and_Fine_Policies_(Grimes)_Fixed.pdf": "Enforce2-Grimes",
        "2022_Recorded_Adopting_Governing_Documents_Enforcement_Process_and_Fine_Policies_(Waller).pdf": "Enforce2-Waller",
        "Resolution_Clarifying_Articles_7_and_8_of_the_Declaration_of_Covenants,_Conditions_&_Restrictions_-_(Grimes_Cty_05-01-2017).pdf": "Clarify-Grimes",
        "Resolution_Clarfying_Articles_7_and_8_of_the_Declaration_of_Covenants,_Conditions_&_Restrictions_-_(Waller_Cty_04-26-2017).pdf": "Clarify-Waller",
        "Resolution_Regarding_Assessment_of_Fines_for_Violations_of_Restrictive_Covenants_and-or_Rules_&_Regulations__-_(Waller_01-23-2012_&_Grimes_02-22-2012).pdf": "Fines",
        "Regulation_of_Solar_Panels,_Roof_Shingles,_Flag,_Flag_Poles,_Religious_Items_and_Rain_Barrels_-_(Waller_01-23-2012_&_Grimes_02-22-2012).pdf": "Regs",
        "Recorded_Resolution_Window_Coverings_Waller-Fixed.pdf": "WindowCoverings",
        "2022 Builders Guidelines & Application": "BG2022",
        "Texas Property Code Chapter 202": "TXC202",
        "Texas Property Code Chapter 207": "TXC207",
        "Texas Property Code Chapter 209": "TXC209",
        "Texas Property Code Chapter 5": "TXC5",
    }

    for c in sorted_clauses:
        cid = c.get("clause_id", "")
        doc = c.get("document", "")
        doc_short = DOC_SHORT.get(doc, doc[:20])
        citation = c.get("citation", "")
        link = c.get("link", "")
        summary = (c.get("plain_summary") or "").strip()
        clause_text = (c.get("clause_text") or "").strip()
        if clause_text:
            summary = f"{summary} | FULL TEXT: {clause_text}"
        summary = summary[:400]
        lines.append(f"[{cid}|{doc_short}|{citation}]\n{summary}")
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
            f"<strong>Source</strong>: <code>{document}</code><br>"
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
- When documents conflict, apply the authority order above
- IMPORTANT EXCEPTION: The CCRs explicitly delegate architectural and design standards to the Builders Guidelines and ARC. When a CCR clause says "per the Builder Guidelines" or "as approved by the ARC", the Builders Guidelines rule on that topic is authoritative — it is not overridden by the CCR. In these cases, cite both: the CCR as the granting authority and the Builders Guidelines as the specific rule.
- Only flag a conflict when two documents genuinely contradict each other on the same point without one delegating to the other
- Give a specific, direct answer. Do not be vague.
- Clearly state whether something is: allowed, prohibited, requires ARC approval, or not addressed
- Cite the specific document and citation for every rule you reference
- If an amendment changes an earlier rule, explain what changed
- If the documents do not address something, say so plainly
- Never fabricate rules not present in the provided clauses
- Never provide legal advice
- For ARC approval decisions, recommend the resident contact the ARC directly
- When citing a clause, reference it by its clause ID in square brackets like [DECL_27_08]. Do not generate HTML links. Do not use the word CITATION. Just use the clause ID.
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
    # Build lookup by clause ID
    by_id = {c.get("clause_id"): c for c in all_clauses}

    DOC_SHORT_DISPLAY = {
        "Declaration_of_Covenants,_Conditions,_&_Restrictions_-_09-17-2004.pdf": "CCRs",
        "First_Amendment_to_the_Declaration_of_Covenants,_Conditions_&_Restrictions_-_10-05-2004.pdf": "CCR First Amendment",
        "1Second_Amendment_to_the_Declaration_of_Covenants,_Conditions_and_Restrictions_11-08-2005.pdf": "CCR Second Amendment",
        "Supplemental_Amendment_to_the_Declaration_of_Covenants,_Conditions_&_Restrictions_-_Sec_2_-_(Waller_Cty_10-4-2004).pdf": "CCR Supplemental Sec 2",
        "Supplemental_Amendment_to_the_Declaration_of_Covenants,_Conditions_&_Restrictions_-_Sec_3_(Grimes_Cty_-_11-16-2004).pdf": "CCR Supplemental Sec 3",
        "Supplemental_Amendment_to_the_Declaration_of_Covenants,_Conditions_&_Restrictions_-_Sec_4_-_11-02-2005.pdf": "CCR Supplemental Sec 4",
        "Amendment_to_DCC&R_-_11.04.19.pdf": "CCR 2019 Amendment",
        "ByLaws_-_PLCA_-_10-19-2004.pdf": "PLCA Bylaws",
        "Articles_of_Incorporation_-_Waller_-_3-26-18.pdf": "Articles of Incorporation",
        "PLCA_-_Articles_of_Incorporation_-_Grimes_-_3-26-18.pdf": "Articles of Incorporation",
        "Resolution_Adopting_Covenants,_Conditions_&_Restrictions_Enforcement_Process_(Recorded_Waller_Co_-_02-09-17.pdf": "Enforcement Resolution",
        "Resolution_Adopting_Conditions,_Conditions_&_Restrictions_Enforcement_Process_-_(Grimes_Cty_02-27-2017).pdf": "Enforcement Resolution",
        "Recorded_Adopting_Governing_Documents_Enforcement_Process_and_Fine_Policies_(Grimes)_Fixed.pdf": "Enforcement & Fine Policy",
        "2022_Recorded_Adopting_Governing_Documents_Enforcement_Process_and_Fine_Policies_(Waller).pdf": "Enforcement & Fine Policy",
        "Resolution_Clarifying_Articles_7_and_8_of_the_Declaration_of_Covenants,_Conditions_&_Restrictions_-_(Grimes_Cty_05-01-2017).pdf": "Clarifying Resolution",
        "Resolution_Clarfying_Articles_7_and_8_of_the_Declaration_of_Covenants,_Conditions_&_Restrictions_-_(Waller_Cty_04-26-2017).pdf": "Clarifying Resolution",
        "Resolution_Regarding_Assessment_of_Fines_for_Violations_of_Restrictive_Covenants_and-or_Rules_&_Regulations__-_(Waller_01-23-2012_&_Grimes_02-22-2012).pdf": "Fine Schedule",
        "Regulation_of_Solar_Panels,_Roof_Shingles,_Flag,_Flag_Poles,_Religious_Items_and_Rain_Barrels_-_(Waller_01-23-2012_&_Grimes_02-22-2012).pdf": "Solar, Flags & Rain Barrel Regulations",
        "Recorded_Resolution_Window_Coverings_Waller-Fixed.pdf": "Window Coverings Resolution",
        "2022 Builders Guidelines & Application": "2022 Builders Guidelines",
        "Texas Property Code Chapter 202": "Texas Property Code Ch. 202",
        "Texas Property Code Chapter 207": "Texas Property Code Ch. 207",
        "Texas Property Code Chapter 209": "Texas Property Code Ch. 209",
        "Texas Property Code Chapter 5": "Texas Property Code Ch. 5",
    }

    def replace_bracketed_id(match):
        cid = match.group(1)
        if cid in by_id:
            clause = by_id[cid]
            link = clause.get("link", "")
            citation = clause.get("citation", "")
            doc = clause.get("document", "")
            doc_display = DOC_SHORT_DISPLAY.get(doc, doc)
            # Build display text as "Document Name, Citation"
            # citation already contains page/section info like "Page 13" or "Article VI, Section B"
            if citation:
                display_text = f"{doc_display}, {citation}"
            else:
                display_text = doc_display
            if link:
                return f'<a href="{link}" target="_blank" rel="noopener noreferrer">{display_text}</a>'
            return display_text
        return ""

    # Clean up malformed [WALLS_01|BG2022|Page 13] to [WALLS_01]
    final_answer = re.sub(
        r'\[([A-Z][A-Z0-9_\-]{3,})[^\]]*\]',
        lambda m: f'[{m.group(1)}]',
        final_answer
    )

    # Replace [CLAUSE_ID] with proper linked citation
    final_answer = re.sub(
        r'\[([A-Z][A-Z0-9_\-]{3,})\]',
        replace_bracketed_id,
        final_answer
    )

    cited_ids = set(re.findall(r'\b([A-Z][A-Z0-9_\-]{3,})\b', final_answer))
    by_id = {c.get("clause_id"): c for c in all_clauses}
    cited_clauses = [by_id[cid] for cid in cited_ids if cid in by_id]
    cited_clauses.sort(key=lambda c: int(c.get("precedence_level", 99)))

    # Cap Texas Property Code to 1 result in display
    tx_clauses = [c for c in cited_clauses if "Texas Property Code" in (c.get("document") or "")]
    hoa_clauses = [c for c in cited_clauses if "Texas Property Code" not in (c.get("document") or "")]
    cited_clauses = hoa_clauses + tx_clauses[:1]

    if not cited_clauses:
        # Fallback: show top HOA clauses by precedence, no TX Code
        cited_clauses = [c for c in sorted(all_clauses,
            key=lambda c: int(c.get("precedence_level", 99)))
            if "Texas Property Code" not in (c.get("document") or "")][:3]

    if output_format == "json":
        return {
            "question": question,
            "answer": final_answer,
            "clauses": cited_clauses[:5],
            "mode": mode,
            "format": "json"
        }

    # If GPT produced inline citations, answer is self-contained
    if cited_clauses:
        return final_answer

    # No inline citations found — show the top relevant clauses
    # as source references so residents can verify
    # Use clauses that were retrieved, filtered to non-TX-Code first
    hoa_clauses = [c for c in all_clauses
                   if "Texas Property Code" not in (c.get("document") or "")]

    # Try to find clauses relevant to the question by simple keyword match
    q_lower = question.lower()
    q_words = set(w for w in re.findall(r'[a-z]+', q_lower) if len(w) > 3)

    scored = []
    for c in hoa_clauses:
        text = ((c.get("plain_summary") or "") + " " + (c.get("clause_text") or "")).lower()
        score = sum(1 for w in q_words if w in text)
        if score > 0:
            scored.append((score, c))

    scored.sort(key=lambda x: (-x[0], int(x[1].get("precedence_level", 99))))
    fallback_clauses = [c for _, c in scored[:3]]

    if fallback_clauses:
        display_text = format_clauses_for_display(fallback_clauses)
        return f"{final_answer}<br><br>{display_text}"

    return final_answer
