from typing import List, Dict, Any
import re, os
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
from typing import List, Dict, Any
import re
import os
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()
openai_client = OpenAI(
    api_key=os.getenv("GROQ_API_KEY"),
    base_url="https://api.groq.com/openai/v1"
)


analyzer = SentimentIntensityAnalyzer()

RISK_KEYWORDS = {
    "self_harm": ["kill myself", "suicide", "end my life", "hurt myself"],
    "sexual": ["porn", "sex", "nude", "naked", "explicit"],
    "violence": ["kill", "attack", "shoot", "stab"],
    "drugs": ["drug", "cocaine", "heroin", "weed", "marijuana"],
}


def detect_risk_tags(text: str) -> List[str]:
    tags = []
    lowered = text.lower()
    for tag, patterns in RISK_KEYWORDS.items():
        for p in patterns:
            if p in lowered:
                tags.append(tag)
                break
    return tags

# Basic local analysis using VADER and keyword spotting
def analyze_risk(text: str) -> Dict[str, Any]:
    sent = analyzer.polarity_scores(text)
    tags = detect_risk_tags(text)
    danger = "low"
    if tags:
        danger = "high"
    elif sent["neg"] > 0.5 or sent["compound"] < -0.6:
        danger = "medium"
    elif sent["compound"] < -0.2:
        danger = "low-medium"

    return {"sentiment": sent, "risk_tags": tags, "danger_level": danger}

def summarize_conversation(messages: List[Dict[str, Any]]) -> str:
    joined = " ".join(m.get("text", "") for m in messages[-6:])
    joined = re.sub(r"\s+", " ", joined).strip()
    if len(joined) > 300:
        return joined[:300] + "..."
    return joined or "No content"


def extract_themes(text: str, top_k: int = 5) -> List[str]:
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        return {"error": "No Groq API key configured"}

    model = os.environ.get("GROQ_MODEL", "llama-3.1-8b-instant")
    system_prompt = (
        "You are an ai model trying to categorize text. Give me a comma separated list of major themes of the message in a series of words. Limit it to 5 words."
    )


    resp = openai_client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": text},
        ],
        max_tokens=300,
    )
    # Normalize a few possible shapes
    try:
        content = resp.choices[0].message.content
    except Exception:
        try:
            content = resp.choices[0]["message"]["content"]
        except Exception:
            try:
                content = resp["choices"][0]["message"]["content"]
            except Exception:
                content = str(resp)
    return content.split(", ") if content else []


def uplevel_summary_with_llm(aggregated: Dict[str, Any], excerpts: List[str], user_id: str = None) -> str:
    """Call the configured LLM to produce a human-friendly summary from aggregated metrics.

    Returns the generated text or raises an exception if LLM not available/configured.
    """
    api_key = os.getenv("GROQ_API_KEY") or os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("No OpenAI/Groq API key configured")

    model = os.environ.get("GROQ_MODEL", "gpt-3.5-turbo")
    system = "You are a helpful assistant that writes concise, parent-friendly daily summaries of conversation trends."
    prompt = (
        "Write a short human-readable daily summary using the following aggregated metrics and short excerpts. "
        "Include top themes, overall sentiment and risk highlights. Keep it under 200 words.\n\n"
    )
    body = f"Aggregated: {aggregated}\n\nExcerpts:\n" + "\n".join(f"- {e}" for e in excerpts[:6])

    try:
        resp = openai_client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": prompt + body},
            ],
            max_tokens=300,
        )
        # multiple possible shapes
        try:
            content = resp.choices[0].message.content
        except Exception:
            try:
                content = resp.choices[0]["message"]["content"]
            except Exception:
                content = str(resp)
        return content
    except Exception as e:
        raise


def uplevel_mental_health_assessment(aggregated: Dict[str, Any], excerpts: List[str], user_id: str = None) -> str:
    """Produce a brief mental-health focused assessment and recommended next steps using the configured LLM.

    The function expects aggregated metrics similar to those produced by aggregate_analyses or summary endpoints.
    Returns a short text (under ~300 tokens) with: overall assessment, risk flags, recommended next steps (if any).
    """
    api_key = os.getenv("GROQ_API_KEY") or os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("No OpenAI/Groq API key configured")

    model = os.environ.get("GROQ_MODEL", "gpt-4o")
    system = (
        "You are a clinical-adjacent assistant that writes concise, actionable mental-health assessments for a parent or caregiver. "
        "Be empathetic, non-judgmental, and include clear next steps and emergency instructions if severe risk is detected."
    )

    prompt = (
        "Given the following aggregated conversation metrics and sample excerpts, produce:\n"
        "1) A short assessment (2-4 sentences) of the user's mental state. Your tone should be similar to a therapist speaking to a client's caregiver. \n"
        "2) Risk level summary (mention self-harm/suicidal/violence flags if present).\n"
        "3) Concrete recommended next steps for a caregiver, including when to seek emergency help.\n\n"
    )

    body = f"Aggregated: {aggregated}\n\nExcerpts:\n" + "\n".join(f"- {e}" for e in excerpts[:6])

    resp = openai_client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": prompt + body},
        ],
        max_tokens=400,
    )

    try:
        content = resp.choices[0].message.content
    except Exception:
        try:
            content = resp.choices[0]["message"]["content"]
        except Exception:
            content = str(resp)
    return content

