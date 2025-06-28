import os
import re
import random
from collections import defaultdict
from dotenv import load_dotenv
from supabase import create_client
from openai import OpenAI

# === Load environment ===
load_dotenv()

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
supabase = create_client(
    os.getenv("SUPABASE_URL"),
    os.getenv("SUPABASE_SERVICE_ROLE_KEY")
)

# === Instant whimsy ===
def check_instant_whimsy(question_lower):
    creator_keywords = ["creator", "developer", "who made you", "who built you","how were you made", "who created you", "who designed you","who programmed you"]
    #feedback_keywords = ["feedback", "suggestion", "complaint"]
    #age_keywords = ["how old", "your age", "age", "years old"]
    dragon_keywords = ["dragon", "castle", "wizard", "unicorn", "fairy", "goblin", "elf", "moat", "magic"]

    if any(k in question_lower for k in creator_keywords):
        return random.choice([
            "My creator was a combination of code, governing documents, and the hard work of your community members working for you.",
            "Created by your fellow HOA members to make your life easier.",
            "Built by your community to help you navigate your governing documents.",
            "Developed by your HOA members to make your life simpler.",
            "I was created by your fellow community members to provide you with an easy-to-use tool to search your governing documents."
        ])
    #elif any(k in question_lower for k in feedback_keywords):
       # return random.choice([
       #     "Currently there is not a feedback form in place. #Please check back for future enhancements."
      #  ])
    #elif any(k in question_lower for k in age_keywords):
        #return random.choice([
          #  "I"
    #    ])
    elif any(k in question_lower for k in dragon_keywords):
        return random.choice([
            "Dragons? I guard HOA secrets like a scaly beast, but I can’t help with fire-breathing dragons. Try fences instead!",
            "Ah, dragons and castles! Sadly I handle covenants, not quests. Ask me about sheds!",
            "If you see a wizard in your yard, call the ARC — or maybe just me. 🧙‍♂️"
        ])
    return None

# === Format Clauses ===
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

        if citation and link:
            link_html = f'<a href="{link}" target="_blank" rel="noopener noreferrer">{citation}</a>'
        else:
            link_html = citation

        entry = (
            f"<b>{idx}. <strong>Summary of Clause</strong>: According to {link_html}, {summary}.</b><br>"
            f"<strong>Match Source</strong>: {source} • "
            f"<code>{c.get('document', 'Unknown')}</code> • "
            f"<strong>Reviewer ID</strong>: <code>{clause_id}</code><br>"
        )
        formatted.append(entry)

    return "<br><br>".join(formatted)

# === GPT Prompt ===
def build_gpt_prompt(question, clause_text, no_matches=False):
    fallback_msg = (
        "⚠️ There were no direct matches to this question. Below are general HOA rules that might still help you respond.<br><br>"
        if no_matches else ""
    )
    return f"""You are an HOA policy assistant. Based on the provided Clause data, answer the resident’s question in clear, friendly, and accurate language.

Resident Question:
{question}

{fallback_msg}
Below are relevant Clause matches:
{clause_text}

Write your response in this format:
1. Brief summary of each Clause that might apply
2. State whether the rules clearly answer the question
3. If unclear, suggest checking with the ARC
4. Always close with: “If you have any other questions, feel free to ask!”

Use HTML for citations like this: <a href="link" target="_blank">Art. VI</a>

---

Final Answer:
"""

# === Vector + Fallback Matching ===
def fetch_matching_clauses(question, tags=None, structure_type=None, concern_level=None):
    embedding_response = client.embeddings.create(
        model="text-embedding-ada-002",
        input=question,
    )
    query_embedding = embedding_response.data[0].embedding

    response = supabase.rpc("match_clauses", {
        "query_embedding": query_embedding,
        "match_threshold": 0.6,
        "match_count": 5
    }).execute()

    vector_matches = response.data or []
    for clause in vector_matches:
        clause["match_source"] = "Vector Match"
        clause["clause_id"] = clause.get("clause_id") or clause.get("id")

    if len(vector_matches) < 5:
        query = supabase.from_("clauses").select("*").ilike("plain_summary", f"%{question}%")
        if tags:
            query = query.contains("tags", tags)
        if structure_type:
            query = query.eq("structure_type", structure_type)
        if concern_level:
            query = query.eq("concern_level", concern_level)
        fallback_matches = query.limit(5).execute()
        for clause in fallback_matches.data or []:
            clause["match_source"] = "Keyword Fallback"
            clause["clause_id"] = clause.get("clause_id") or clause.get("id")
        vector_matches += fallback_matches.data or []

    return vector_matches

# === Soft fallback ===
def fetch_soft_fallback_clauses():
    general_tags = ["shed", "structure", "placement", "approval"]
    query = supabase.from_("clauses").select("*").contains("tags", general_tags).limit(5)
    result = query.execute()
    fallback_data = result.data or []

    for clause in fallback_data:
        clause["match_source"] = "General Soft Fallback"
        clause["clause_id"] = clause.get("clause_id") or clause.get("id")

    if not fallback_data:
        fallback_data = [{
            "precedence_level": "9",
            "plain_summary": "Standard best practice: Your question is very specific; please check your governing documents or with the ARC or Board for precise guidance.",
            "citation": "General Guideline",
            "link": "",
            "document": "Default Fallback",
            "match_source": "Injected Fallback",
            "clause_id": "FALLBACK_GENERAL"
        }]

    return fallback_data

# === MAIN ===
def answer_question(question, tags=None, mode="default", structure_type=None, concern_level=None, output_format="markdown"):
    whimsy_reply = check_instant_whimsy(question.lower().strip())
    if whimsy_reply:
        return whimsy_reply

    raw_clauses = fetch_matching_clauses(
        question,
        tags=tags,
        structure_type=structure_type,
        concern_level=concern_level
    )

    unique_clauses = {}
    for clause in raw_clauses:
        cid = clause.get("clause_id")
        if cid not in unique_clauses and clause.get("match_source") == "Vector Match":
            unique_clauses[cid] = clause
    clauses = list(unique_clauses.values())

    no_matches = False
    if not clauses:
        clauses = fetch_soft_fallback_clauses()
        no_matches = True

    clause_text = format_clauses_for_prompt(clauses)
    prompt = build_gpt_prompt(question, clause_text, no_matches)

    whimsy_keywords = ["dragon", "castle", "wizard", "unicorn", "fairy", "goblin", "moat", "magic"]
    if any(word in question.lower() for word in whimsy_keywords):
        prompt += (
            "\n\nNote: This question appears whimsical. Please answer helpfully with a playful touch if relevant."
        )

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
