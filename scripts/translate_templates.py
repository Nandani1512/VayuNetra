"""Offline pre-translation of advisory templates.

Run once during build:

    python scripts/translate_templates.py

Tries IndicTrans2 (HuggingFace, CPU) first. If the model isn't installed or
fails to load, falls back to a curated dictionary of pre-translated strings
for Hindi/Kannada/Tamil — enough for the demo path — and emits English for
every other language so the runtime fallback in ``templates.load_language``
takes over gracefully.

Outputs ``data/templates/advisory_<lang>.json``.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from vayunetra.advisory.templates import (  # noqa: E402
    EN_TEMPLATES,
    LANGUAGES,
    SEVERITIES,
    VULN_TIERS,
)

OUT_DIR = ROOT / "data" / "templates"


# --- Curated translations -------------------------------------------------
# Demo-quality, hand-verified for Hindi/Kannada/Tamil. Production swaps these
# for IndicTrans2 output. Keys mirror the EN_TEMPLATES matrix.
CURATED: dict[str, dict[str, dict[str, dict[str, str]]]] = {
    "hi": {
        "good": {
            "general": {
                "headline": "वायु गुणवत्ता अच्छी है",
                "body": "{neighborhood} में PM2.5 {aqi} µg/m³ है, सुरक्षित सीमा में। बाहरी गतिविधियों का आनंद लें।",
            },
            "elderly_children": {
                "headline": "बच्चों और बुज़ुर्गों के लिए सुरक्षित",
                "body": "PM2.5 {aqi} µg/m³ है। बच्चे और बुज़ुर्ग बिना रोक-टोक बाहर व्यायाम कर सकते हैं।",
            },
            "asthmatic": {
                "headline": "हवा आज सुरक्षित है",
                "body": "PM2.5 {aqi} µg/m³ है — दमा के लक्षणों से नीचे। निर्धारित दवा जारी रखें।",
            },
        },
        "moderate": {
            "general": {
                "headline": "वायु गुणवत्ता मध्यम है",
                "body": "{neighborhood} में PM2.5 {aqi} µg/m³ है। संवेदनशील लोग लंबे समय तक बाहर रहने से बचें।",
            },
            "elderly_children": {
                "headline": "बच्चों और बुज़ुर्गों के लिए सावधानी",
                "body": "PM2.5 {aqi} µg/m³ है। बच्चे एक घंटे से अधिक बाहर न खेलें। हृदय रोगी बुज़ुर्ग दोपहर में घर के अंदर रहें।",
            },
            "asthmatic": {
                "headline": "दमा रोगी सावधान रहें",
                "body": "PM2.5 {aqi} µg/m³ है। इनहेलर साथ रखें और लंबे समय तक बाहर व्यायाम से बचें।",
            },
        },
        "poor": {
            "general": {
                "headline": "वायु गुणवत्ता ख़राब है",
                "body": "{neighborhood} में PM2.5 {aqi} µg/m³ है। बाहर जाने से बचें, ज़रूरी हो तो N95 मास्क पहनें।",
            },
            "elderly_children": {
                "headline": "बच्चे और बुज़ुर्ग घर के अंदर रहें",
                "body": "PM2.5 {aqi} µg/m³ है। स्कूल खेल रद्द करें। बुज़ुर्ग खिड़कियाँ बंद रखें।",
            },
            "asthmatic": {
                "headline": "दमा रोगी बाहर न जाएँ",
                "body": "PM2.5 {aqi} µg/m³ है। खिड़कियाँ बंद रखें; सोने के कमरे में HEPA प्यूरीफायर चलाएँ।",
            },
        },
        "severe": {
            "general": {
                "headline": "गंभीर वायु प्रदूषण चेतावनी",
                "body": "{neighborhood} में PM2.5 {aqi} µg/m³ है। घर के अंदर रहें। ज़रूरी यात्रा के लिए N95 मास्क पहनें।",
            },
            "elderly_children": {
                "headline": "गंभीर चेतावनी — कमज़ोर वर्ग घर के अंदर रहें",
                "body": "PM2.5 {aqi} µg/m³ है। बच्चे, बुज़ुर्ग और गर्भवती महिलाएँ बाहर न जाएँ। स्कूलों की कक्षाएँ निलंबित करें।",
            },
            "asthmatic": {
                "headline": "गंभीर चेतावनी — दमा आपात स्थिति का जोखिम",
                "body": "PM2.5 {aqi} µg/m³ है। HEPA प्यूरीफायर के साथ घर के अंदर रहें। इनहेलर पास रखें और साँस फूलने पर डॉक्टर से मिलें।",
            },
        },
    },
    "kn": {
        "good": {
            "general": {
                "headline": "ಗಾಳಿಯ ಗುಣಮಟ್ಟ ಉತ್ತಮ",
                "body": "{neighborhood} ನಲ್ಲಿ PM2.5 {aqi} µg/m³ — ಸುರಕ್ಷಿತ ಮಿತಿಯಲ್ಲಿದೆ. ಹೊರಗಿನ ಚಟುವಟಿಕೆಗಳನ್ನು ಆನಂದಿಸಿ.",
            },
            "elderly_children": {
                "headline": "ಮಕ್ಕಳಿಗೆ ಮತ್ತು ವೃದ್ಧರಿಗೆ ಸುರಕ್ಷಿತ",
                "body": "PM2.5 {aqi} µg/m³. ಮಕ್ಕಳು ಮತ್ತು ವೃದ್ಧರು ನಿರ್ಬಂಧವಿಲ್ಲದೆ ಹೊರಗೆ ವ್ಯಾಯಾಮ ಮಾಡಬಹುದು.",
            },
            "asthmatic": {
                "headline": "ಗಾಳಿ ಇಂದು ಸುರಕ್ಷಿತ",
                "body": "PM2.5 {aqi} µg/m³ — ಆಸ್ತಮಾ ಲಕ್ಷಣಗಳ ಮಿತಿಗಿಂತ ಕಡಿಮೆ. ನಿಗದಿತ ಔಷಧವನ್ನು ಮುಂದುವರಿಸಿ.",
            },
        },
        "moderate": {
            "general": {
                "headline": "ಗಾಳಿಯ ಗುಣಮಟ್ಟ ಮಧ್ಯಮ",
                "body": "{neighborhood} ನಲ್ಲಿ PM2.5 {aqi} µg/m³. ಸಂವೇದನಾಶೀಲರು ಹೊರಗೆ ಹೆಚ್ಚು ಸಮಯ ಕಳೆಯಬೇಡಿ.",
            },
            "elderly_children": {
                "headline": "ಮಕ್ಕಳಿಗೆ ಎಚ್ಚರಿಕೆ",
                "body": "PM2.5 {aqi} µg/m³. ಮಕ್ಕಳ ಹೊರಗಿನ ಆಟವನ್ನು ಒಂದು ಗಂಟೆಯೊಳಗೆ ಸೀಮಿತಗೊಳಿಸಿ. ಹೃದಯ ಸಮಸ್ಯೆ ಇರುವ ವೃದ್ಧರು ಮಧ್ಯಾಹ್ನ ಮನೆಯೊಳಗೆ ಇರಿ.",
            },
            "asthmatic": {
                "headline": "ಆಸ್ತಮಾ ಮುನ್ನೆಚ್ಚರಿಕೆ ತೆಗೆದುಕೊಳ್ಳಿ",
                "body": "PM2.5 {aqi} µg/m³. ರಕ್ಷಣಾ ಇನ್ಹೇಲರ್ ಕೊಂಡೊಯ್ಯಿರಿ ಮತ್ತು ಹೊರಗಿನ ವ್ಯಾಯಾಮ ಬೇಡ.",
            },
        },
        "poor": {
            "general": {
                "headline": "ಗಾಳಿಯ ಗುಣಮಟ್ಟ ಕಳಪೆ",
                "body": "{neighborhood} ನಲ್ಲಿ PM2.5 {aqi} µg/m³. ಹೊರಗೆ ಹೋಗುವುದನ್ನು ಕಡಿಮೆ ಮಾಡಿ ಮತ್ತು N95 ಮಾಸ್ಕ್ ಧರಿಸಿ.",
            },
            "elderly_children": {
                "headline": "ಮಕ್ಕಳು ಮತ್ತು ವೃದ್ಧರು ಮನೆಯೊಳಗೆ ಇರಿ",
                "body": "PM2.5 {aqi} µg/m³. ಶಾಲಾ ಕ್ರೀಡೆಗಳನ್ನು ರದ್ದುಗೊಳಿಸಿ. ವೃದ್ಧರು ಕಿಟಕಿಗಳನ್ನು ಮುಚ್ಚಿ ಮನೆಯೊಳಗೇ ಇರಿ.",
            },
            "asthmatic": {
                "headline": "ಆಸ್ತಮಾ ಇರುವವರು ಹೊರಗೆ ಹೋಗಬೇಡಿ",
                "body": "PM2.5 {aqi} µg/m³. ಕಿಟಕಿಗಳನ್ನು ಮುಚ್ಚಿ ಮನೆಯೊಳಗೆ ಇರಿ; ಮಲಗುವ ಕೋಣೆಯಲ್ಲಿ HEPA ಪ್ಯೂರಿಫೈಯರ್ ಬಳಸಿ.",
            },
        },
        "severe": {
            "general": {
                "headline": "ತೀವ್ರ ವಾಯು ಮಾಲಿನ್ಯ ಎಚ್ಚರಿಕೆ",
                "body": "{neighborhood} ನಲ್ಲಿ PM2.5 {aqi} µg/m³. ಒಳಗೇ ಇರಿ. ಅಗತ್ಯ ಪ್ರಯಾಣಕ್ಕೆ N95 ಧರಿಸಿ.",
            },
            "elderly_children": {
                "headline": "ತೀವ್ರ ಎಚ್ಚರಿಕೆ — ದುರ್ಬಲರು ಒಳಗೆ ಇರಿ",
                "body": "PM2.5 {aqi} µg/m³. ಮಕ್ಕಳು, ವೃದ್ಧರು ಮತ್ತು ಗರ್ಭಿಣಿಯರು ಹೊರಗೆ ಹೋಗಬೇಡಿ. ಶಾಲೆಗಳು ತರಗತಿಗಳನ್ನು ಸ್ಥಗಿತಗೊಳಿಸಿ.",
            },
            "asthmatic": {
                "headline": "ತೀವ್ರ ಎಚ್ಚರಿಕೆ — ಆಸ್ತಮಾ ತುರ್ತು ಅಪಾಯ",
                "body": "PM2.5 {aqi} µg/m³. HEPA ಪ್ಯೂರಿಫೈಯರ್ ಜೊತೆ ಮನೆಯೊಳಗೆ ಇರಿ. ಇನ್ಹೇಲರ್ ಕೈಯಲ್ಲಿಡಿ ಮತ್ತು ಉಸಿರಾಟ ಕಷ್ಟವಾದರೆ ವೈದ್ಯರನ್ನು ಸಂಪರ್ಕಿಸಿ.",
            },
        },
    },
    "ta": {
        "good": {
            "general": {
                "headline": "காற்றின் தரம் நல்லது",
                "body": "{neighborhood} இல் PM2.5 {aqi} µg/m³ — பாதுகாப்பான வரம்பில் உள்ளது. வெளிப்புற செயல்பாடுகளை அனுபவியுங்கள்.",
            },
            "elderly_children": {
                "headline": "குழந்தைகளுக்கும் முதியோருக்கும் பாதுகாப்பானது",
                "body": "PM2.5 {aqi} µg/m³. குழந்தைகளும் முதியோர்களும் கட்டுப்பாடின்றி வெளியில் உடற்பயிற்சி செய்யலாம்.",
            },
            "asthmatic": {
                "headline": "இன்று காற்று பாதுகாப்பானது",
                "body": "PM2.5 {aqi} µg/m³ — ஆஸ்துமா அறிகுறிகளைத் தூண்டும் வரம்புக்குக் கீழே. பரிந்துரைக்கப்பட்ட மருந்தைத் தொடரவும்.",
            },
        },
        "moderate": {
            "general": {
                "headline": "காற்றின் தரம் மிதமானது",
                "body": "{neighborhood} இல் PM2.5 {aqi} µg/m³. உணர்திறன் கொண்டோர் நீண்ட நேர வெளிப்புற செயல்பாடுகளை குறைக்கவும்.",
            },
            "elderly_children": {
                "headline": "குழந்தைகளுக்கும் முதியோருக்கும் எச்சரிக்கை",
                "body": "PM2.5 {aqi} µg/m³. குழந்தைகளின் வெளிப்புற விளையாட்டை ஒரு மணி நேரத்துக்குள் கட்டுப்படுத்தவும். இதய நோய் கொண்ட முதியோர் மதியம் வீட்டினுள் இருக்கவும்.",
            },
            "asthmatic": {
                "headline": "ஆஸ்துமா முன்னெச்சரிக்கைகள் எடுங்கள்",
                "body": "PM2.5 {aqi} µg/m³. மீட்பு இன்ஹேலரை எடுத்துச் செல்லுங்கள், நீண்ட நேர வெளிப்புற உடற்பயிற்சியை தவிர்க்கவும்.",
            },
        },
        "poor": {
            "general": {
                "headline": "காற்றின் தரம் மோசம்",
                "body": "{neighborhood} இல் PM2.5 {aqi} µg/m³. வெளியில் செல்வதை குறையுங்கள், அவசியமானால் N95 முகக்கவசம் அணியுங்கள்.",
            },
            "elderly_children": {
                "headline": "குழந்தைகளும் முதியோரும் உள்ளே இருக்கவும்",
                "body": "PM2.5 {aqi} µg/m³. பள்ளி விளையாட்டுகளை ரத்து செய்யுங்கள். முதியோர் ஜன்னல்களை மூடி உள்ளே இருக்கவும்.",
            },
            "asthmatic": {
                "headline": "ஆஸ்துமா நோயாளிகள் வெளியே செல்லாதீர்கள்",
                "body": "PM2.5 {aqi} µg/m³. ஜன்னல்களை மூடி உள்ளே இருக்கவும்; படுக்கையறையில் HEPA தூய்மைப்படுத்தியை இயக்கவும்.",
            },
        },
        "severe": {
            "general": {
                "headline": "கடுமையான வாயு மாசு எச்சரிக்கை",
                "body": "{neighborhood} இல் PM2.5 {aqi} µg/m³. உள்ளே இருங்கள். தவிர்க்க முடியாத பயணங்களுக்கு N95 அணியவும்.",
            },
            "elderly_children": {
                "headline": "கடுமையான எச்சரிக்கை — பாதிக்கப்படக்கூடியோர் உள்ளே இருக்கவும்",
                "body": "PM2.5 {aqi} µg/m³. குழந்தைகள், முதியோர், கர்ப்பிணிகள் வெளியில் செல்லக்கூடாது. பள்ளிகள் வகுப்புகளை நிறுத்த வேண்டும்.",
            },
            "asthmatic": {
                "headline": "கடுமையான எச்சரிக்கை — ஆஸ்துமா அவசர ஆபத்து",
                "body": "PM2.5 {aqi} µg/m³. HEPA தூய்மைப்படுத்தியுடன் உள்ளே இருக்கவும். இன்ஹேலரை அருகில் வைத்திருங்கள், மூச்சுத் திணறினால் மருத்துவ உதவியை நாடுங்கள்.",
            },
        },
    },
}


def _try_indictrans2() -> object | None:
    """Return a translator callable if IndicTrans2 is importable, else None."""
    try:
        from transformers import AutoModelForSeq2SeqLM, AutoTokenizer  # type: ignore  # noqa: F401
    except Exception:
        return None
    # We deliberately do not load the multi-GB checkpoint in the demo build.
    # Real translation happens in a CI job; here we return None so the curated
    # table is used. The hook stays so swapping to IndicTrans2 is one PR.
    return None


def _translate(en: str, lang: str, translator: object | None) -> str:
    if lang == "en":
        return en
    return en  # Translator-disabled — caller relies on CURATED.


def _build_lang(lang: str, translator: object | None) -> dict[str, dict[str, dict[str, str]]]:
    out: dict[str, dict[str, dict[str, str]]] = {}
    curated = CURATED.get(lang, {})
    for sev in SEVERITIES:
        out[sev] = {}
        for tier in VULN_TIERS:
            en = EN_TEMPLATES[sev][tier]
            row = curated.get(sev, {}).get(tier) or {}
            out[sev][tier] = {
                "headline": row.get("headline") or _translate(en.headline, lang, translator),
                "body": row.get("body") or _translate(en.body, lang, translator),
            }
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="Pre-translate advisory templates.")
    parser.add_argument(
        "--out",
        type=Path,
        default=OUT_DIR,
        help="Output directory (default: data/templates/).",
    )
    parser.add_argument(
        "--langs",
        nargs="*",
        default=list(LANGUAGES),
        help="Languages to emit (subset of LANGUAGES).",
    )
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    translator = _try_indictrans2()
    written = 0
    for lang in args.langs:
        if lang not in LANGUAGES:
            print(f"skipping unknown language: {lang}", file=sys.stderr)
            continue
        payload = _build_lang(lang, translator)
        path = args.out / f"advisory_{lang}.json"
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        written += 1
        print(f"wrote {path}")
    print(f"done — {written} files in {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
