from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi import Body, HTTPException, Query, Depends, Request
from typing import List, Dict, Any
import re
import os
from datetime import datetime, timezone


# Helper: convert markdown bold (**text**) to HTML <strong> tags
def _md_bold_to_html(s: str) -> str:
    if not s:
        return s
    return re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)


# Helper: remove markdown bold markers for plaintext fallback
def _md_bold_to_plain(s: str) -> str:
    if not s:
        return s
    return re.sub(r"\*\*(.+?)\*\*", r"\1", s)


def _nl_to_html_paragraphs(s: str) -> str:
    """Turn double-newlines into <p>...</p> blocks and single newlines into <br> inside paragraphs."""
    if not s:
        return s
    parts = [p.strip() for p in s.split('\n\n') if p.strip()]
    html_parts = []
    for p in parts:
        # replace single newlines with <br>
        p_html = p.replace('\n', '<br>')
        html_parts.append(f"<p>{p_html}</p>")
    return '\n'.join(html_parts)


def _nl_to_plain(s: str) -> str:
    """Normalize whitespace/newlines for plain-text fallback: collapse multiple blank lines to one."""
    if not s:
        return s
    # collapse sequences of 2+ newlines into exactly two
    return re.sub(r"\n{2,}", "\n\n", s).strip()


def _extract_risk_sentences(text: str, risk_tags: List[str]) -> str:
    """Extract only sentences that contain risk keywords based on detected risk tags."""
    if not text or not risk_tags:
        return text[:400]  # fallback to original behavior
    
    # Import RISK_KEYWORDS from analysis module
    from app.analysis import RISK_KEYWORDS
    
    # Collect all patterns for the detected risk tags
    risk_patterns = []
    for tag in risk_tags:
        if tag in RISK_KEYWORDS:
            risk_patterns.extend(RISK_KEYWORDS[tag])
    
    if not risk_patterns:
        return text[:400]  # fallback if no patterns found
    
    # Split text into sentences (simple approach)
    sentences = re.split(r'[.!?]+', text)
    relevant_sentences = []
    
    for sentence in sentences:
        sentence = sentence.strip()
        if not sentence:
            continue
        
        # Check if this sentence contains any risk keywords
        sentence_lower = sentence.lower()
        for pattern in risk_patterns:
            if pattern.lower() in sentence_lower:
                relevant_sentences.append(sentence)
                break
    
    if relevant_sentences:
        # Join sentences and limit to reasonable length
        result = '. '.join(relevant_sentences)
        if len(result) > 800:  # slightly longer than original 400 to preserve sentence context
            result = result[:800] + '...'
        return result
    else:
        # If no sentences contain keywords, fallback to original behavior
        return text[:400]

# Optional OpenAI fallback (if configured)
try:
    import openai
    OPENAI_AVAILABLE = True
except Exception:
    OPENAI_AVAILABLE = False

# Import modularized components
from app.analysis import analyze_risk, summarize_conversation, extract_themes, uplevel_summary_with_llm
from app.analysis import uplevel_mental_health_assessment
from app.storage import save_user_themes, get_user_themes, save_analysis, get_analyses_for_user_date, save_daily_summary, get_daily_summary
from app.storage import get_user_ids_for_date
from app.storage import get_analyses_for_user
from sendgrid import SendGridAPIClient
from app.auth import get_current_user
from sendgrid.helpers.mail import Mail
import requests
import jwt
from jwt import PyJWKClient
from app.auth import create_jwt

app = FastAPI()

# Enable permissive CORS for development
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
def read_root():
    return {"Hello": "World"}


@app.post("/analyze")
def analyze(messages: List[Dict[str, Any]] = Body(...), current_user: Dict[str, Any] = Depends(get_current_user)):
    """
    Expected payload: a JSON array of messages like:
    [{"sender": "child", "text": "I want to..."}, ...]

    Response: {classification fields...}
    """
    # Combine all text for sentiment, but keep per-message tags too
    all_text = " \n ".join(m.get("text", "") for m in messages)

    result = analyze_risk(all_text)

    response = {
        "danger_level": result["danger_level"],
        "risk_tags": list(set(result["risk_tags"])),
        "sentiment": result["sentiment"],
        "themes": extract_themes(all_text),
    }
    # Derive user id from the verified local JWT (subject from token)
    used_user_id = None
    if current_user and isinstance(current_user, dict):
        used_user_id = current_user.get('user_id')
    if not used_user_id:
        # Strict enforcement: reject unauthenticated requests
        raise HTTPException(status_code=401, detail='Authentication required')

    # Persist themes if user_id provided (use the resolved used_user_id)
    if used_user_id:
        try:
            save_user_themes(used_user_id, response["themes"])
            response["themes_saved"] = True
        except Exception as e:
            response["themes_saved"] = False
            response["themes_save_error"] = str(e)

    # Persist a per-request analysis row for later aggregation: ts, user_id, compound sentiment, themes
    if used_user_id:
        try:
            ts = datetime.utcnow().replace(tzinfo=timezone.utc).isoformat()
            # save_analysis expects message_text and a full analysis dict; include sentiment/risk/danger
            analysis_payload = {
                "sentiment": result.get("sentiment"),
                "risk_tags": result.get("risk_tags"),
                "danger_level": result.get("danger_level"),
            }
            # Control whether to store full text via env var to respect privacy
            STORE_FULL_TEXT = os.environ.get("STORE_FULL_TEXT", "false").lower() in ("1", "true", "yes")
            message_text_to_store = all_text if STORE_FULL_TEXT else None
            save_analysis(used_user_id, message_text_to_store, analysis_payload, ts=ts, themes=response.get("themes"))
            response["analysis_saved"] = True
            response["analysis_ts"] = ts
            response["stored_text"] = bool(message_text_to_store)
        except Exception as e:
            response["analysis_saved"] = False
            response["analysis_save_error"] = str(e)

    # include which user id was used to persist
    response["used_user_id"] = used_user_id

    # If this analysis detects high danger, attempt to notify the user's email address (POC behavior)
    try:
        print("risk detected:", result.get('danger_level'))
        if str(result.get('danger_level')).lower() == 'high':
            print("sending email to:", used_user_id)
            # basic email address heuristic
            if isinstance(used_user_id, str) and '@' in used_user_id and '.' in used_user_id.split('@')[-1]:
                sendgrid_key = os.environ.get('SENDGRID_API_KEY')
                send_from = os.environ.get('SENDGRID_FROM')
                if sendgrid_key and send_from:
                    try:
                        # Get daily summary data for context
                        today_date = datetime.utcnow().date().isoformat()
                        try:
                            daily_analyses = get_analyses_for_user_date(used_user_id, today_date)
                            
                            # Build summary metrics
                            theme_counts = {}
                            compounds = []
                            risk_counts = {}
                            for r in daily_analyses:
                                for t in r.get("themes", []):
                                    theme_counts[t] = theme_counts.get(t, 0) + 1
                                for rt in r.get("risk_tags", []):
                                    risk_counts[rt] = risk_counts.get(rt, 0) + 1
                                sent = r.get("sentiment") or {}
                                c = sent.get("compound") if isinstance(sent, dict) else None
                                if c is not None:
                                    try:
                                        compounds.append(float(c))
                                    except Exception:
                                        pass
                            
                            daily_summary = {
                                "themes": theme_counts,
                                "risk_counts": risk_counts,
                                "avg_sentiment": {"compound": (sum(compounds) / len(compounds) if compounds else None)},
                                "count": len(daily_analyses),
                            }
                        except Exception as e:
                            print(f"DEBUG: Error getting daily summary: {e}")
                            daily_summary = {"themes": {}, "risk_counts": {}, "avg_sentiment": {"compound": None}, "count": 0}
                        
                        # Calculate daily summary metrics for use in email
                        daily_risk_total = sum(daily_summary.get("risk_counts", {}).values())
                        daily_avg_sentiment = daily_summary.get("avg_sentiment", {}).get("compound")
                        daily_sentiment_label = "neutral"
                        if daily_avg_sentiment is not None:
                            if daily_avg_sentiment >= 0.05:
                                daily_sentiment_label = "positive"
                            elif daily_avg_sentiment <= -0.05:
                                daily_sentiment_label = "negative"
                        
                        # Compose a short alert email (plain + html) with only sentences containing risk keywords
                        excerpt = _extract_risk_sentences(all_text or '', response.get('risk_tags', []))
                        detected_time = response.get('analysis_ts', datetime.utcnow().isoformat())
                        subj = f"🚨 SafeChat AI Alert: High-Risk Content Detected - {used_user_id}"
                        plain = (
                            f"🚨 HIGH-RISK CONTENT DETECTED 🚨\n\n"
                            f"We detected high-risk content in recent analyzed messages.\n\n"
                            f"ALERT DETAILS:\n"
                            f"Risk tags: {', '.join(response.get('risk_tags', []))}\n"
                            f"Detected at: {detected_time}\n\n"
                            f"RELEVANT CONTENT:\n{excerpt}\n\n"
                            f"TODAY'S ACTIVITY SUMMARY:\n"
                            f"- Total analyses: {daily_summary.get('count', 0)}\n"
                            f"- Overall sentiment: {daily_sentiment_label.title()}\n"
                            f"- Total risk events: {daily_risk_total}\n"
                            f"- Top themes: {', '.join([f'{t}({c})' for t, c in sorted(daily_summary.get('themes', {}).items(), key=lambda x: x[1], reverse=True)[:3]])}\n\n"
                            "⚠️ If this is an emergency, contact local emergency services immediately."
                        )
                        
                        # Create styled HTML similar to summary email but with urgent styling
                        risk_badges_html = ''.join([f'<span class="risk-badge">{tag}</span>' for tag in response.get('risk_tags', [])])
                        
                        # Format daily summary data
                        daily_themes_html = ''
                        if daily_summary.get("themes"):
                            top_themes = sorted(daily_summary["themes"].items(), key=lambda x: x[1], reverse=True)[:5]
                            daily_themes_html = ''.join([f'<span class="theme-badge">{theme} ({count})</span>' for theme, count in top_themes])
                        
                        html = f"""
                        <html>
                        <head>
                            <meta charset="utf-8" />
                            <style>
                                body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial; color: #111; background: #fff8f6; padding: 24px; }}
                                .card {{ background: #fff; border: 2px solid #fca5a5; border-radius: 12px; padding: 0; max-width: 680px; margin: auto; box-shadow: 0 10px 25px rgba(220,38,38,0.15); overflow: hidden; }}
                                .alert-header {{ background: linear-gradient(135deg, #dc2626, #b91c1c); color: white; padding: 20px; position: relative; }}
                                .alert-header::after {{ content: ''; position: absolute; bottom: 0; left: 0; right: 0; height: 4px; background: linear-gradient(90deg, rgba(255,255,255,0.3), rgba(255,255,255,0.1)); }}
                                h1 {{ font-size: 20px; margin: 0; font-weight: 700; text-shadow: 0 1px 2px rgba(0,0,0,0.1); }}
                                .muted {{ color: #666; font-size: 13px; margin-top: 4px; }}
                                .content-wrapper {{ padding: 20px; }}
                                .metrics {{ display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin: 0 0 24px 0; }}
                                .metric {{ background: linear-gradient(135deg, #fef2f2, #fff); border: 1px solid #fecaca; padding: 16px; border-radius: 8px; font-size: 13px; box-shadow: 0 2px 4px rgba(0,0,0,0.05); }}
                                .metric strong {{ display: block; color: #374151; font-size: 11px; text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 4px; }}
                                .metric div {{ font-size: 16px; font-weight: 600; }}
                                .risk-badge {{ display: inline-block; background: linear-gradient(135deg, #dc2626, #b91c1c); color: white; padding: 8px 14px; border-radius: 20px; font-weight: 600; font-size: 12px; margin-right: 8px; margin-bottom: 6px; box-shadow: 0 2px 4px rgba(220,38,38,0.3); }}
                                .theme-badge {{ display: inline-block; background: linear-gradient(135deg, #f8fafc, #f1f5f9); color: #475569; border: 1px solid #e2e8f0; padding: 6px 12px; border-radius: 16px; font-weight: 500; font-size: 11px; margin-right: 6px; margin-bottom: 6px; }}
                                .section-header {{ font-size: 16px; font-weight: 600; margin: 24px 0 12px 0; color: #374151; display: flex; align-items: center; }}
                                .section-header.danger {{ color: #dc2626; }}
                                .excerpt {{ background: linear-gradient(135deg, #f9fafb, #ffffff); border: 1px solid #e5e7eb; border-left: 4px solid #dc2626; padding: 18px; margin: 16px 0; border-radius: 8px; font-size: 14px; line-height: 1.6; box-shadow: 0 2px 4px rgba(0,0,0,0.05); }}
                                .summary-section {{ background: linear-gradient(135deg, #f8fafc, #ffffff); border: 1px solid #e2e8f0; border-radius: 10px; padding: 20px; margin: 24px 0; box-shadow: 0 4px 8px rgba(0,0,0,0.05); }}
                                .summary-metrics {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 10px; margin-top: 16px; }}
                                .summary-metric {{ background: linear-gradient(135deg, #ffffff, #f9fafb); border: 1px solid #e5e7eb; padding: 14px 12px; border-radius: 8px; text-align: center; box-shadow: 0 1px 3px rgba(0,0,0,0.05); transition: transform 0.2s; }}
                                .summary-metric:hover {{ transform: translateY(-1px); }}
                                .summary-metric strong {{ display: block; color: #6b7280; font-size: 11px; text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 6px; }}
                                .summary-metric div {{ font-size: 18px; font-weight: 700; }}
                                .themes-section {{ margin-top: 16px; }}
                                .themes-title {{ font-size: 13px; font-weight: 600; color: #6b7280; margin-bottom: 8px; text-transform: uppercase; letter-spacing: 0.5px; }}
                                .emergency {{ background: linear-gradient(135deg, #fee2e2, #fef2f2); border: 2px solid #fecaca; padding: 20px; border-radius: 10px; margin-top: 24px; font-size: 15px; font-weight: 600; text-align: center; box-shadow: 0 4px 8px rgba(220,38,38,0.1); }}
                                .emergency::before {{ content: '⚠️'; font-size: 24px; display: block; margin-bottom: 8px; }}
                                .timestamp {{ color: rgba(255,255,255,0.8); font-size: 12px; font-family: 'SF Mono', Monaco, monospace; margin-top: 8px; font-weight: 400; }}
                                @media (max-width: 600px) {{ 
                                    .metrics {{ grid-template-columns: 1fr; }}
                                    .summary-metrics {{ grid-template-columns: 1fr; }}
                                }}
                            </style>
                        </head>
                        <body>
                            <div class="card">
                                <div class="alert-header">
                                    <h1>🚨 High-Risk Content Detected</h1>
                                    <div class="timestamp">Detected: {detected_time}</div>
                                </div>

                                <div class="content-wrapper">
                                    <div class="metrics">
                                        <div class="metric">
                                            <strong>Risk Level</strong>
                                            <div style="color: #dc2626;">HIGH</div>
                                        </div>
                                        <div class="metric">
                                            <strong>Categories Found</strong>
                                            <div>{len(response.get('risk_tags', []))}</div>
                                        </div>
                                    </div>

                                    <h2 class="section-header danger">🏷️ Risk Categories Detected</h2>
                                    <div style="margin-bottom: 20px;">
                                        {risk_badges_html}
                                    </div>

                                    <h2 class="section-header">📝 Relevant Content</h2>
                                    <div class="excerpt">
                                        {excerpt}
                                    </div>

                                    <div class="summary-section">
                                        <h2 style="font-size: 16px; margin: 0 0 8px 0; color: #374151; font-weight: 600;">📊 Today's Activity Summary</h2>
                                        <div class="summary-metrics">
                                            <div class="summary-metric">
                                                <strong>Total Analyses</strong>
                                                <div>{daily_summary.get('count', 0)}</div>
                                            </div>
                                            <div class="summary-metric">
                                                <strong>Overall Sentiment</strong>
                                                <div style="color: {'#059669' if daily_sentiment_label == 'positive' else '#dc2626' if daily_sentiment_label == 'negative' else '#6b7280'};">{daily_sentiment_label.title()}</div>
                                            </div>
                                            <div class="summary-metric">
                                                <strong>Risk Events</strong>
                                                <div style="color: {'#dc2626' if daily_risk_total > 0 else '#059669'};">{daily_risk_total}</div>
                                            </div>
                                        </div>
                                        {f'<div class="themes-section"><div class="themes-title">Top Themes Today</div><div>{daily_themes_html}</div></div>' if daily_themes_html else ''}
                                    </div>

                                    <div class="emergency">
                                        If this is an emergency, contact local emergency services immediately
                                    </div>
                                </div>
                            </div>
                        </body>
                        </html>
                        """

                        msg = Mail(
                            from_email=send_from,
                            to_emails=used_user_id,
                            subject=subj,
                            plain_text_content=plain,
                            html_content=html,
                        )
                        sg = SendGridAPIClient(sendgrid_key)
                        sg_resp = sg.send(msg)
                        response['alert_email_sent'] = True
                        response['alert_email_status'] = getattr(sg_resp, 'status_code', None)
                    except Exception as e:
                        print(f"DEBUG: Error sending alert email: {e}")
                        response['alert_email_sent'] = False
                        response['alert_email_error'] = str(e)
                else:
                    print(f"DEBUG: Missing SENDGRID_API_KEY or SENDGRID_FROM")
                    response['alert_email_sent'] = False
                    response['alert_email_error'] = 'Missing SENDGRID_API_KEY or SENDGRID_FROM'
            else:
                print(f"DEBUG: used_user_id is not a valid email: {used_user_id}")
                response['alert_email_sent'] = False
                response['alert_email_error'] = 'used_user_id is not an email'
    except Exception as e:
        # Don't let email failures break analyze
        response['alert_email_sent'] = False
        response['alert_email_error'] = str(e)

    return response


@app.post('/auth/exchange')
async def auth_exchange(request: Request):
    """Exchange an OAuth2 authorization code (PKCE) with the provider and return a local JWT.

    Expects environment variables:
      OAUTH_CLIENT_ID, OAUTH_CLIENT_SECRET
    Optional:
      OAUTH_PROVIDER_TOKEN_URL (default Google), OAUTH_USERINFO_URL (default Google)
    """
    # Log the raw request for debugging
    try:
        body = await request.body()
        print(f"DEBUG: Raw request body: {body}")
        
        json_body = await request.json()
        print(f"DEBUG: Parsed JSON body: {json_body}")
        print(f"DEBUG: JSON body keys: {list(json_body.keys()) if isinstance(json_body, dict) else 'not a dict'}")
        
        code = json_body.get('code')
        code_verifier = json_body.get('code_verifier') 
        redirect_uri = json_body.get('redirect_uri')
        
        print(f"DEBUG: code={code}, code_verifier={code_verifier}, redirect_uri={redirect_uri}")
        
        if not code:
            print("DEBUG: Missing 'code' field")
        if not code_verifier:
            print("DEBUG: Missing 'code_verifier' field")
        if not redirect_uri:
            print("DEBUG: Missing 'redirect_uri' field")
        
        if not code or not code_verifier or not redirect_uri:
            raise HTTPException(status_code=400, detail='Missing required fields: code, code_verifier, redirect_uri')
            
    except HTTPException:
        raise
    except Exception as e:
        print(f"DEBUG: Error parsing request: {e}")
        raise HTTPException(status_code=400, detail=f'Invalid request body: {e}')
    
    token_url = os.environ.get('OAUTH_PROVIDER_TOKEN_URL', 'https://oauth2.googleapis.com/token')
    userinfo_url = os.environ.get('OAUTH_USERINFO_URL', 'https://openidconnect.googleapis.com/v1/userinfo')
    client_id = os.environ.get('OAUTH_CLIENT_ID')
    client_secret = os.environ.get('OAUTH_CLIENT_SECRET')
    
    print(f"DEBUG: client_id exists: {bool(client_id)}, client_secret exists: {bool(client_secret)}")

    if not client_id or not client_secret:
        print("ERROR: Missing OAUTH_CLIENT_ID or OAUTH_CLIENT_SECRET")
        raise HTTPException(status_code=400, detail='OAUTH_CLIENT_ID and OAUTH_CLIENT_SECRET must be set on the server')

    # Exchange code for provider tokens
    try:
        resp = requests.post(token_url, data={
            'grant_type': 'authorization_code',
            'code': code,
            'redirect_uri': redirect_uri,
            'client_id': client_id,
            'client_secret': client_secret,
            'code_verifier': code_verifier,
        }, headers={'Accept': 'application/json'}, timeout=15)
    except requests.RequestException as e:
        raise HTTPException(status_code=502, detail=f'Provider token request failed: {e}')

    if not resp.ok:
        raise HTTPException(status_code=502, detail=f'Provider token exchange failed: {resp.status_code} {resp.text}')

    token_data = resp.json()
    id_token = token_data.get('id_token')
    access_token = token_data.get('access_token')

    user_email = None
    subject = None

    # Try verify id_token using provider JWKS (Google)
    if id_token:
        try:
            jwks_uri = 'https://www.googleapis.com/oauth2/v3/certs'
            jwk_client = PyJWKClient(jwks_uri)
            signing_key = jwk_client.get_signing_key_from_jwt(id_token)
            payload = jwt.decode(id_token, signing_key.key, algorithms=[signing_key.algorithm], audience=client_id)
            subject = payload.get('sub')
            user_email = payload.get('email')
        except Exception:
            # fallback to userinfo if id_token verification fails
            subject = None

    # If we don't have subject/email yet, call userinfo with access_token
    if (not subject or not user_email) and access_token:
        try:
            ui = requests.get(userinfo_url, headers={'Authorization': f'Bearer {access_token}'}, timeout=8)
            if ui.ok:
                profile = ui.json()
                subject = subject or profile.get('sub')
                user_email = user_email or profile.get('email')
        except Exception:
            pass

    # Final fallback: use whatever we have
    if not subject and not user_email:
        raise HTTPException(status_code=400, detail='Unable to determine user identity from provider tokens')

    subject_for_jwt = user_email or subject
    local_jwt = create_jwt(subject_for_jwt, expires_minutes=60 * 24)

    return {'access_token': local_jwt, 'expires_in': 60 * 60 * 24, 'user_email': user_email}


@app.get("/analyses/{user_id}")
def analyses_for_user(user_id: str, date: str = Query(None, description="YYYY-MM-DD optional date filter")):
    """Return raw analyses for a user. If date provided, filter to that date prefix (YYYY-MM-DD)."""
    try:
        if date:
            rows = get_analyses_for_user_date(user_id, date)
        else:
            rows = get_analyses_for_user(user_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    return {"user_id": user_id, "count": len(rows), "analyses": rows}


@app.get("/mental_health/{user_id}")
def mental_health_assessment(user_id: str, date: str = Query(None, description="YYYY-MM-DD optional date filter")):
    """Return a human-readable mental health assessment and recommended next steps using an LLM.

    It will aggregate themes and sentiment for the date (or all time if omitted), then call the LLM helper.
    """
    try:
        if date:
            rows = get_analyses_for_user_date(user_id, date)
        else:
            rows = get_analyses_for_user(user_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    # Build aggregated metrics for the LLM using themes and risk counts only (no raw text)
    theme_counts = {}
    compounds = []
    risk_counts = {}
    for r in rows:
        for t in r.get("themes", []):
            theme_counts[t] = theme_counts.get(t, 0) + 1
        for rt in r.get("risk_tags", []):
            risk_counts[rt] = risk_counts.get(rt, 0) + 1
        sent = r.get("sentiment") or {}
        c = sent.get("compound") if isinstance(sent, dict) else None
        if c is not None:
            try:
                compounds.append(float(c))
            except Exception:
                pass

    aggregated = {
        "themes": theme_counts,
        "risk_counts": risk_counts,
        "avg_sentiment": {"compound": (sum(compounds) / len(compounds) if compounds else None)},
        "count": len(rows),
    }

    # Provide top themes as non-identifying context to the LLM
    top_themes = [t for t, _ in sorted(theme_counts.items(), key=lambda x: x[1], reverse=True)[:8]]

    try:
        assessment_text = uplevel_mental_health_assessment(aggregated, top_themes, user_id=user_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"LLM error: {e}")

    # Convert any markdown bold in the assessment into HTML so clients receive rendered tags
    assessment_html = _md_bold_to_html(assessment_text)
    assessment_plain = _md_bold_to_plain(assessment_text)

    # Return a non-null date string for clients; if no date filter was provided, use today's date
    resolved_date = date or datetime.utcnow().date().isoformat()
    return {"user_id": user_id, "date": resolved_date, "aggregated": aggregated, "assessment": assessment_html, "assessment_plain": assessment_plain}


@app.post("/email_summary/{user_id}")
def email_summary(user_id: str, recipient: str = Body(None, embed=True), date: str = Body(None, embed=True)):
    """Compose the mental health assessment and email it using SendGrid.

    Requires SENDGRID_API_KEY and SENDGRID_FROM environment variables. If recipient is not provided,
    SENDGRID_TO env var will be used.
    """
    # build the assessment
    try:
        mh = mental_health_assessment(user_id, date=date)
    except HTTPException as e:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    sendgrid_key = os.environ.get("SENDGRID_API_KEY")
    send_from = os.environ.get("SENDGRID_FROM")
    send_to = recipient or os.environ.get("SENDGRID_TO")

    if not sendgrid_key or not send_from or not send_to:
            raise HTTPException(status_code=400, detail="SENDGRID_API_KEY, SENDGRID_FROM and SENDGRID_TO/recipient must be set")

    subject = f"Daily summary for {user_id}"
    agg = mh["aggregated"]
    # Build an HTML email with simple styling
    top_themes = agg.get("themes") if isinstance(agg.get("themes"), dict) else {}
    risk_counts = agg.get("risk_counts", {})
    avg_comp = agg.get("avg_sentiment", {}).get("compound")

    # Precompute badge HTML and risk rows to avoid evaluation errors in the f-string
    try:
            badges_html = ''.join(['<span class="badge">{} ({})</span> '.format(t, c) for t, c in sorted(top_themes.items(), key=lambda x: x[1], reverse=True)[:8]])
    except Exception:
            badges_html = ''

    try:
            risk_rows = ''.join(["<tr><td>{}</td><td>{}</td></tr>".format(k, v) for k, v in risk_counts.items()])
    except Exception:
            risk_rows = ''

    avg_comp_str = f"{avg_comp:.3f}" if isinstance(avg_comp, (int, float)) else 'N/A'

    # derive a human-readable sentiment label
    if isinstance(avg_comp, (int, float)):
        if avg_comp >= 0.05:
            sentiment_label = 'positive'
        elif avg_comp <= -0.05:
            sentiment_label = 'negative'
        else:
            sentiment_label = 'neutral'
    else:
        sentiment_label = 'N/A'

    # risk summary: total hits and top risk
    try:
        risk_total = sum(int(v) for v in risk_counts.values())
    except Exception:
        risk_total = 0
    if risk_counts:
        try:
            top_risk, top_risk_count = max(risk_counts.items(), key=lambda x: int(x[1]))
        except Exception:
            top_risk, top_risk_count = None, 0
    else:
        top_risk, top_risk_count = None, 0

    # Prepare HTML and plaintext assessment using newline-aware helpers
    assessment_html_raw = mh.get('assessment') or ''
    assessment_plain_raw = mh.get('assessment_plain') or ''
    # Ensure bold markers in the html raw are preserved as <strong>, then convert newlines
    assessment_html = _nl_to_html_paragraphs(assessment_html_raw)
    assessment_plain = _nl_to_plain(assessment_plain_raw)
    assessment_paragraphs = assessment_html

    html = f"""
    <html>
    <head>
        <meta charset="utf-8" />
        <style>
            body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial; color: #111; background: #f6f8fb; padding: 24px; }}
            .card {{ background: #fff; border-radius: 8px; padding: 20px; max-width: 680px; margin: auto; box-shadow: 0 6px 18px rgba(12,20,40,0.08); }}
            h1 {{ font-size: 18px; margin: 0 0 8px 0; }}
            .muted {{ color: #556; font-size: 13px; }}
            .metrics {{ display: flex; gap: 16px; margin: 12px 0 18px 0; flex-wrap: wrap; }}
            .metric {{ background: #f4f6fb; padding: 10px 12px; border-radius: 6px; font-size: 13px; }}
            table {{ width: 100%; border-collapse: collapse; margin-top: 8px; }}
            th, td {{ text-align: left; padding: 8px; border-bottom: 1px solid #eee; font-size: 13px; }}
            .badge {{ display:inline-block; background:#eef2ff; color:#2b4bd3; padding:4px 8px; border-radius:999px; font-weight:600; font-size:12px; }}
            .assessment {{ margin-top: 14px; font-size:14px; line-height:1.45; }}
        </style>
    </head>
    <body>
        <div class="card">
            <h1>Daily summary for {user_id}</h1>
            <div class="muted">Date: {date or 'All time'}</div>

                    <div class="metrics">
                        <div class="metric"><strong>Analyses</strong><div>{agg.get('count')}</div></div>
                        <div class="metric"><strong>Avg sentiment</strong><div>{avg_comp_str}</div></div>
                        <div class="metric"><strong>Summary sentiment</strong><div>{sentiment_label}</div></div>
                        <div class="metric"><strong>Risk hits</strong><div>{risk_total}</div></div>
                        <div class="metric"><strong>Top risk</strong><div>{top_risk or 'None'} ({top_risk_count})</div></div>
                    </div>

            <h2 style="font-size:14px;margin-top:8px;margin-bottom:6px">Top themes</h2>
            <div>
                {badges_html}
            </div>

            <h2 style="font-size:14px;margin-top:14px;margin-bottom:6px">Risk highlights</h2>
            <table>
                <tr><th>Risk type</th><th>Count</th></tr>
                {risk_rows}
            </table>

                    <div class="assessment">
                        {assessment_paragraphs}
                    </div>
        </div>
    </body>
    </html>
    """

    # Plain-text fallback
    body_text = (
        f"Daily summary for {user_id}\nDate: {date or 'All time'}\n\n"
        f"Analyses: {agg.get('count')}\n"
        f"Avg sentiment: {avg_comp_str} ({sentiment_label})\n"
        f"Risk hits: {risk_total} (top: {top_risk or 'None'} {top_risk_count})\n"
        f"Risk counts: {', '.join([f'{k}={v}' for k,v in risk_counts.items()])}\n\n"
        f"Aggregated: {agg}\n\nAssessment:\n{assessment_plain}"
    )

    payload = {
            "personalizations": [{"to": [{"email": send_to}], "subject": subject}],
            "from": {"email": send_from},
            "content": [
                    {"type": "text/plain", "value": body_text},
                    {"type": "text/html", "value": html},
            ],
    }

    message = Mail(
        from_email=send_from,
        to_emails=send_to,
        subject="Safe Chat AI Summary",
        plain_text_content=body_text,
        html_content=html,
    )

    try:
        sg = SendGridAPIClient(sendgrid_key)
        resp = sg.send(message)
        status = resp.status_code
        body = getattr(resp, 'body', None)
        headers = getattr(resp, 'headers', None)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"SendGrid SDK error: {e}")

    if status >= 400:
        raise HTTPException(status_code=500, detail=f"SendGrid error: {status} {body}")

    return {"status": "sent", "to": send_to, "sg_status": status}

