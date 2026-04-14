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

TOPIC_PROFILES = {
    "fence_height": {
        "detect": lambda q: "fence" in q and any(k in q for k in ["tall", "height", "high", "maximum", "minimum", "how tall", "how high"]),
        "required": ["fence"],
        "required_any": ["height", "feet", "foot", "ft", "inch", "maximum", "minimum", "not exceed", "tall"],
        "boost": ["height", "feet", "maximum", "minimum", "not exceed", "tall"],
        "limit": 4,
    },
    "fence_general": {
        "detect": lambda q: "fence" in q,
        "required": ["fence"],
        "required_any": None,
        "boost": ["fence", "material", "placement", "barbed", "chain link", "wrought iron", "wood"],
        "limit": 5,
    },
    "rental": {
        "detect": lambda q: any(k in q for k in ["rent", "rental", "lease", "airbnb", "vrbo", "tenant", "short-term"]),
        "required_any": ["rent", "lease", "rental", "tenant", "airbnb", "vrbo"],
        "boost": ["rent", "lease", "single-family", "homesite", "prohibited", "allowed"],
        "limit": 5,
    },
    "animals": {
        "detect": lambda q: any(k in q for k in ["chicken", "dog", "cat", "pet", "animal", "livestock", "horse", "goat", "pig", "poultry", "fowl"]),
        "required_any": ["animal", "pet", "chicken", "poultry", "livestock", "horse", "dog", "cat", "fowl"],
        "boost": ["animal", "pet", "livestock", "chicken", "prohibited", "allowed", "limit"],
        "limit": 5,
    },
    "solar": {
        "detect": lambda q: any(k in q for k in ["solar", "panel", "photovoltaic"]),
        "required_any": ["solar"],
        "boost": ["solar", "panel", "roof", "arc", "approval", "installation"],
        "limit": 5,
    },
    "flag_pole": {
        "detect": lambda q: any(k in q for k in ["flag", "flagpole", "flag pole"]),
        "required_any": ["flag"],
        "boost": ["flag", "pole", "height", "feet", "arc", "approval"],
        "limit": 5,
    },
    "parking": {
        "detect": lambda q: any(k in q for k in ["park", "parking", "vehicle", "car", "truck", "rv", "boat", "trailer", "driveway"]),
        "required_any": ["park", "vehicle", "driveway", "car", "truck", "rv", "boat", "trailer"],
        "boost": ["park", "driveway", "street", "prohibited", "allowed", "overnight"],
        "limit": 5,
    },
    "shed": {
        "detect": lambda q: any(k in q for k in ["shed", "outbuilding", "workshop", "barn", "storage"]),
        "required_any": ["shed", "outbuilding", "workshop", "barn", "storage", "out building"],
        "boost": ["shed", "outbuilding", "approval", "arc", "size", "placement"],
        "limit": 5,
    },
    "setbacks": {
        "detect": lambda q: any(k in q for k in ["setback", "set back", "property line", "how close", "distance from"]),
        "required_any": ["setback", "property line", "feet from", "distance", "front yard", "rear yard", "side yard"],
        "boost": ["setback", "property line", "feet", "front", "rear", "side"],
        "limit": 5,
    },
    "enforcement": {
        "detect": lambda q: any(k in q for k in ["violat", "fine", "penalty", "enforcement", "letter", "209", "compliance", "notice"]),
        "required_any": ["violat", "fine", "penalty", "enforcement", "compliance", "notice", "letter"],
        "boost": ["fine", "violation", "enforcement", "notice", "letter", "cure", "hearing"],
        "limit": 6,
    },
    "assessment": {
        "detect": lambda q: any(k in q for k in ["assessment", "dues", "fee", "payment", "lien", "delinquent"]),
        "required_any": ["assessment", "dues", "fee", "payment", "lien"],
        "boost": ["assessment", "annual", "special", "lien", "delinquent", "pay"],
        "limit": 5,
    },
    "arc_approval": {
        "detect": lambda q: any(k in q for k in ["arc", "approval", "architectural review", "application", "submit", "approve"]),
        "required_any": ["arc", "approval", "architectural review", "application", "submit", "approve"],
        "boost": ["arc", "approval", "application", "submit", "written", "review"],
        "limit": 6,
    },
    "paint": {
        "detect": lambda q: any(k in q for k in ["paint", "color", "colour", "repaint", "exterior"]),
        "required_any": ["paint", "color", "colour", "repaint", "exterior"],
        "boost": ["paint", "color", "approval", "arc", "exterior"],
        "limit": 5,
    },
    "guest_house": {
        "detect": lambda q: any(k in q for k in ["guest house", "guest home", "casita", "second dwelling", "accessory"]),
        "required_any": ["guest", "second dwelling", "accessory", "casita"],
        "boost": ["guest house", "guest home", "second", "dwelling", "approval"],
        "limit": 5,
    },
    "pools": {
        "detect": lambda q: any(k in q for k in ["pool", "spa", "hot tub", "swimming"]),
        "required_any": ["pool", "spa", "hot tub", "swimming"],
        "boost": ["pool", "spa", "approval", "arc", "fence", "enclosure"],
        "limit": 5,
    },
    "trees_landscaping": {
        "detect": lambda q: any(k in q for k in ["tree", "landscape", "landscaping", "plant", "grass", "lawn", "shrub"]),
        "required_any": ["tree", "landscape", "plant", "grass", "lawn", "shrub"],
        "boost": ["tree", "removal", "approval", "arc", "landscape"],
        "limit": 5,
    },
    "roofing": {
        "detect": lambda q: any(k in q for k in ["roof", "shingle", "roofing", "replace roof"]),
        "required_any": ["roof", "shingle"],
        "boost": ["roof", "shingle", "approval", "arc", "material", "wind", "hail"],
        "limit": 5,
    },
    "fishing_lake": {
        "detect": lambda q: any(k in q for k in ["fish", "fishing", "lake", "pond", "dock", "boat", "swim"]),
        "required_any": ["fish", "lake", "pond", "dock", "boat", "swim", "water"],
        "boost": ["fish", "lake", "pond", "access", "recreational", "dock"],
        "limit": 5,
    },
    "business": {
        "detect": lambda q: any(k in q for k in ["business", "commercial", "work from home", "home office", "run a business"]),
        "required_any": ["business", "commercial", "trade", "income", "work"],
        "boost": ["business", "commercial", "prohibited", "zoning", "undetectable"],
        "limit": 5,
    },
}

def build_query_profile(question):
    q = question.lower()
    for topic_key, profile in TOPIC_PROFILES.items():
        if profile["detect"](q):
            return topic_key, profile
    return "general", None

def retrieve_relevant_clauses(question, limit=6):
    all_clauses = get_all_clauses()
    q = question.lower()

    topic_key, profile = build_query_profile(question)
    print(f"[retrieval] Topic detected: {topic_key}")

    scored = []

    for clause in all_clauses:
        searchable = " ".join([
            str(clause.get("document", "") or ""),
            str(clause.get("plain_summary", "") or ""),
            str(clause.get("clause_text", "") or ""),
            str(clause.get("tags", "") or ""),
        ]).lower()

        score = 0

        if profile is not None:
            # Check required terms — clause must contain at least one
            required_any = profile.get("required_any")
            required = profile.get("required", [])

            # Hard required: ALL of these must be present
            if required:
                if not all(r in searchable for r in required):
                    continue

            # Soft required: at least ONE of these must be present
            if required_any:
                if not any(r in searchable for r in required_any):
                    continue

            # Boost scoring for matching boost terms
            boost_terms = profile.get("boost", [])
            for term in boost_terms:
                if term in searchable:
                    score += 10

            # Extra boost for dimensional values on height questions
            if "height" in topic_key:
                if re.search(r"\b\d+\s*(feet|foot|ft|inches|inch|in)\b", searchable):
                    score += 40

            # Minimum score threshold
            if score < 5:
                continue

        else:
            # General fallback: simple keyword overlap
            q_tokens = set(re.findall(r"[a-z0-9]+", q))
            for token in q_tokens:
                if len(token) > 2 and token in searchable:
                    score += 2
            if score < 6:
                continue

        scored.append((score, clause))

    # Sort by score descending, then by precedence
    scored.sort(key=lambda x: (-x[0], int(x[1].get("precedence_level", 99))))

    result_limit = profile.get("limit", limit) if profile else limit
    result = [c for _, c in scored[:result_limit]]
    print(f"[retrieval] Returning {len(result)} clauses for topic '{topic_key}'")
    return result

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

    # Retrieval scans the full corpus; only the selected evidence set is sent to GPT context.
    clauses_text = format_clauses_for_gpt(selected_clauses)

    system_prompt = f"""You are the PLCA Board Assistant for Plantation Lakes Community Association (PLCA), located in Waller and Grimes Counties, Texas.

You are given a preselected set of relevant clauses from PLCA governing documents. Your job is to find the answer to the resident's question by reading through the provided clauses.

{DOCUMENT_HIERARCHY}

INSTRUCTIONS:
1. Read ALL provided clauses carefully to find ones that answer the question
2. Apply the document hierarchy — higher authority governs when documents conflict
2a. If clauses conflict, do NOT merge or average rules; state the single governing rule from the higher-authority document and explain why it controls
2b. For architectural/design standards (for example fences, exterior colors, sheds, placement/details), Builder Guidelines may control when they provide the specific requirement; do not automatically override them due to general hierarchy rank, and explain why the controlling document governs this architectural issue
3. Give a specific, direct answer grounded only in the clauses provided
4. Reference the specific document and citation for each rule you cite
5. If an amendment modifies an earlier rule, say what changed
6. State clearly whether something is allowed, prohibited, or requires ARC approval
7. If no clause addresses the question, say so plainly and recommend contacting the ARC
8. Never fabricate rules not present in the clauses
9. Never provide legal advice
10. Close with: "If you have any other questions, feel free to ask!"
11. Only make definitive statements if explicitly supported by the provided clauses.
12. If the clauses do not clearly state a rule, say: "The governing documents do not explicitly specify..."
13. Do NOT infer or assume rules based on general construction or remodeling language.
14. Prefer "unclear / not specified" over guessing.
15. Always distinguish between:
    1. Explicit rule
    2. Likely interpretation
    3. Not addressed
16. Do NOT use phrases like "this implies" or "this means" unless the clause explicitly states it.
17. When interpreting, always say: "This may suggest..." or "This could be interpreted as..."
18. Never embed interpretation inside factual statements.
19. Always separate interpretation using a labeled section: "Interpretation:"
20.Do not use phrases like "which could include" or "this suggests" inside factual clauses.

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
