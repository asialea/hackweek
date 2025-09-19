from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi import Body, HTTPException, Query, Depends
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
def analyze(messages: List[Dict[str, Any]] = Body(...), user_id: str = Body(None)):
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
    # Resolve user id in this order for POC: body.user_id > DEFAULT_USER_ID
    DEFAULT_USER_ID = os.environ.get("DEFAULT_USER_ID", "default_user")
    used_user_id = user_id or DEFAULT_USER_ID

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
    print(response)

    return response


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

