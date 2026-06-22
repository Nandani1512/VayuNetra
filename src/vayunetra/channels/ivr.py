"""IVR channel stub — Twilio/Exotel compatible TwiML webhook."""

from __future__ import annotations

import httpx
from fastapi import APIRouter, Form, Request
from fastapi.responses import Response

router = APIRouter(prefix="/ivr", tags=["ivr"])

LANG_MAP = {"1": "hi", "2": "en", "3": "kn"}
TTS_LANG = {"hi": "hi-IN", "en": "en-IN", "kn": "kn-IN"}


def _twiml(body: str) -> Response:
    xml = f'<?xml version="1.0" encoding="UTF-8"?>\n<Response>\n{body}\n</Response>'
    return Response(content=xml, media_type="application/xml")


@router.post("/voice")
def voice():
    """Initial webhook — greet and ask for language via DTMF."""
    return _twiml(
        '  <Gather action="/ivr/gather" numDigits="1" timeout="5">\n'
        '    <Say language="hi-IN">वायुनेत्र में आपका स्वागत है।</Say>\n'
        '    <Say language="en-IN">Welcome to VayuNetra.</Say>\n'
        '    <Say language="hi-IN">हिंदी के लिए 1 दबाएं।</Say>\n'
        '    <Say language="en-IN">Press 2 for English.</Say>\n'
        '    <Say language="kn-IN">ಕನ್ನಡಕ್ಕಾಗಿ 3 ಒತ್ತಿ.</Say>\n'
        "  </Gather>\n"
        '  <Say language="en-IN">No input received. Goodbye.</Say>'
    )


@router.post("/gather")
async def gather(request: Request, Digits: str = Form("2")):
    """Handle DTMF input, fetch advisory, read it via TTS."""
    lang = LANG_MAP.get(Digits, "en")
    tts_lang = TTS_LANG.get(lang, "en-IN")

    # Fetch advisory from internal endpoint
    base = str(request.base_url).rstrip("/")
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{base}/advisory", params={"city": "delhi", "lang": lang})
            data = resp.json()
        headline = data.get("headline", "Air quality advisory unavailable.")
        advice = data.get("advice", "")
    except Exception:
        headline = "Advisory service unavailable."
        advice = "Please try again later."

    return _twiml(
        f'  <Say language="{tts_lang}">{headline}</Say>\n'
        f'  <Say language="{tts_lang}">{advice}</Say>\n'
        f'  <Say language="{tts_lang}">Goodbye.</Say>'
    )


@router.get("/status")
def status():
    return {"status": "ok", "channel": "ivr"}
