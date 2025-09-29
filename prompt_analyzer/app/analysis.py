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
    "self_harm": [
        "kill myself",
        "commit suicide",
        "suicidal",
        "end my life",
        "take my life",
        "hurt myself",
        "cut myself",
        "self harm",
        "cutting",
        "overdose",
        "hang myself",
        "jump off",
        "i want to die",
        "life not worth living"
    ],
    "sexual": [
        "porn",
        "pornography",
        "sex",
        "sexual",
        "nude",
        "nudes",
        "naked",
        "explicit",
        "xxx",
        "onlyfans",
        "erotic",
        "fetish",
        "camgirl",
        "adult content"
    ],
    "violence": [
        "kill",
        "murder",
        "attack",
        "shoot",
        "shooting",
        "stab",
        "stabbing",
        "massacre",
        "bomb",
        "bombing",
        "terrorist",
        "terrorism",
        "rape",
        "assault",
        "torture",
        "arson"
    ],
    "drugs": [
        "drugs",
        "illegal drugs", 
        "cocaine",
        "heroin",
        "weed",
        "marijuana",
        "cannabis",
        "methamphetamine",
        "lsd",
        "ecstasy",
        "molly",
        "opioid",
        "oxycodone",
        "fentanyl",
        "ketamine",
        "psychedelics"
    ],
    "mental_health": [
        "depression",
        "depressed",
        "mental breakdown",
        "nervous breakdown",
        "can't cope",
        "overwhelmed",
        "hopeless",
        "helpless"
    ]
}


def detect_risk_tags(text: str) -> List[str]:
    tags = []
    lowered = text.lower()
    
    print(f"DEBUG: Checking text for keyword risks: '{lowered[:200]}...'")
    
    for tag, patterns in RISK_KEYWORDS.items():
        for p in patterns:
            # Use regex word boundaries to match only complete words/phrases
            pattern = r'\b' + re.escape(p) + r'\b'
            if re.search(pattern, lowered):
                print(f"DEBUG: Keyword match found - Tag: {tag}, Pattern: '{p}', Text contains: '{p}'")
                tags.append(tag)
                break
    return tags

def detect_risk_themes(themes: List[str]) -> List[str]:
    """Detect risk categories based on extracted themes using the same RISK_KEYWORDS"""
    risk_tags = []
    if not themes:
        return risk_tags
    
    print(f"DEBUG: Checking themes for risk: {themes}")
    
    # Convert themes to lowercase for matching
    themes_lower = [theme.lower() for theme in themes]
    
    # Use the same RISK_KEYWORDS for theme detection
    for tag, keyword_list in RISK_KEYWORDS.items():
        for keyword in keyword_list:
            # Use word boundary matching to avoid false positives
            # Only match if the keyword is a complete word or phrase, not a substring
            matches = [theme for theme in themes_lower if keyword == theme or (len(keyword.split()) > 1 and keyword in theme)]
            if matches:
                print(f"DEBUG: Theme risk match found - Tag: {tag}, Keyword: '{keyword}', Matched themes: {matches}")
                risk_tags.append(tag)
                break
    
    return risk_tags

# Basic local analysis using VADER and keyword spotting
def analyze_risk(text: str, themes: List[str] = None) -> Dict[str, Any]:
    sent = analyzer.polarity_scores(text)
    keyword_tags = detect_risk_tags(text)
    theme_tags = detect_risk_themes(themes) if themes else []
    
    # DEBUG: Print what triggered the risk detection
    if keyword_tags:
        print(f"DEBUG: Keyword tags detected: {keyword_tags}")
        print(f"DEBUG: Text analyzed: {text[:200]}...")
    
    if theme_tags:
        print(f"DEBUG: Theme tags detected: {theme_tags}")
        print(f"DEBUG: Themes analyzed: {themes}")
    
    # Combine keyword and theme-based risk tags
    all_tags = list(set(keyword_tags + theme_tags))
    
    danger = "low"
    
    # Override sentiment for high-risk content
    if all_tags:
        danger = "high"
        print(f"DEBUG: HIGH RISK DETECTED! All tags: {all_tags}")
        print(f"DEBUG: Keyword tags: {keyword_tags}, Theme tags: {theme_tags}")
        
        # For self-harm/suicide content, force negative sentiment
        if "self_harm" in all_tags:
            sent["compound"] = min(sent["compound"], -0.8)  # Force very negative
            sent["neg"] = max(sent["neg"], 0.8)
            sent["pos"] = min(sent["pos"], 0.1)
    elif sent["neg"] > 0.5 or sent["compound"] < -0.6:
        danger = "medium"
    elif sent["compound"] < -0.2:
        danger = "low-medium"

    return {
        "sentiment": sent, 
        "risk_tags": all_tags, 
        "keyword_tags": keyword_tags,
        "theme_tags": theme_tags,
        "danger_level": danger
    }

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
        "You are an AI model analyzing text captured from a browser extension that reads different websites. "
        "Extract only meaningful conversation themes that would be relevant to understanding a person's mental state or daily activities. "
        "Filter out technical terms, website navigation elements, login prompts, error messages, and other irrelevant web content. "
        "Focus on themes related to emotions, relationships, activities, interests, and personal topics. "
        "IMPORTANT: Respond with ONLY a comma-separated list of 1-5 theme words. Do not include any explanations, introductions, or additional text. "
        "Example format: happy, school, friends, stress, gaming"
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
    
    if not content:
        return []
    
    # Split themes and clean up
    themes = [theme.strip() for theme in content.split(", ") if theme.strip()]
    return themes[:top_k]


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

