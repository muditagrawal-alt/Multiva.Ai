"""
Translation v2 — NLLB-200-Distilled-600M
Drop-in replacement for translation.py with:
  - 600M params (vs 1B for IndicTrans2) — 40% less memory
  - 200+ language pairs bidirectional (not just EN→Indic)
  - Standard HuggingFace API (no indictrans_toolkit dependency)
  - CTranslate2-compatible for further speedup
"""

import os
import torch
from transformers import AutoModelForSeq2SeqLM, AutoTokenizer

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
import engines

MODEL_NAME = engines.get("mt") or "facebook/nllb-200-distilled-600M"


def _get_device():
    forced = os.getenv("FORCE_DEVICE")
    if forced:
        return forced
    if torch.cuda.is_available():
        return "cuda"
    if torch.backends.mps.is_available():
        return "mps"
    return "cpu"


DEVICE = _get_device()

# ---------------------------------------------------------------------------
# Language code mapping
# ---------------------------------------------------------------------------
# Map short codes (used by frontend/Whisper) to NLLB flores-200 codes
LANG_TO_FLORES = {
    "en": "eng_Latn",
    "hi": "hin_Deva",
    "es": "spa_Latn",
    "fr": "fra_Latn",
    "de": "deu_Latn",
    "it": "ita_Latn",
    "pt": "por_Latn",
    "ru": "rus_Cyrl",
    "zh": "zho_Hans",
    "ja": "jpn_Jpan",
    "ko": "kor_Hang",
    "ar": "arb_Arab",
    "tr": "tur_Latn",
    "nl": "nld_Latn",
    "pl": "pol_Latn",
    "bn": "ben_Beng",
    "ta": "tam_Taml",
    "te": "tel_Telu",
    "mr": "mar_Deva",
    "gu": "guj_Gujr",
    "kn": "kan_Knda",
    "ml": "mal_Mlym",
    "pa": "pan_Guru",
    "ur": "urd_Arab",
    "th": "tha_Thai",
    "vi": "vie_Latn",
    "id": "ind_Latn",
    "ms": "zsm_Latn",
    "sw": "swh_Latn",
    "uk": "ukr_Cyrl",
    "cs": "ces_Latn",
    "ro": "ron_Latn",
    "el": "ell_Grek",
    "hu": "hun_Latn",
    "sv": "swe_Latn",
    "da": "dan_Latn",
    "fi": "fin_Latn",
    "no": "nob_Latn",
    "he": "heb_Hebr",
    "fa": "pes_Arab",
}


def _resolve_flores_code(lang_code: str) -> str:
    """
    Resolve a language code to NLLB flores-200 format.
    Accepts short codes ('hi'), flores codes ('hin_Deva'), or BCP-47 ('hi-IN').
    """
    # Already a flores code
    if "_" in lang_code and len(lang_code) == 8:
        return lang_code

    # Strip region (e.g., 'hi-IN' → 'hi')
    short = lang_code.split("-")[0].lower()

    if short in LANG_TO_FLORES:
        return LANG_TO_FLORES[short]

    # If it looks like a flores code already (e.g., 'hin_Deva')
    if "_" in lang_code:
        return lang_code

    raise ValueError(
        f"Unknown language code: '{lang_code}'. "
        f"Supported: {list(LANG_TO_FLORES.keys())}"
    )


# ---------------------------------------------------------------------------
# Lazy model loading
# ---------------------------------------------------------------------------
_tokenizer = None
_model = None


def _load_model():
    global _tokenizer, _model
    if _model is not None:
        return _tokenizer, _model

    print(f"[Translation-v2] Loading NLLB model: {MODEL_NAME} on {DEVICE}")

    try:
        _tokenizer = AutoTokenizer.from_pretrained(MODEL_NAME)
        _model = AutoModelForSeq2SeqLM.from_pretrained(MODEL_NAME).to(DEVICE)

        # FP16 on CUDA for speed
        if DEVICE == "cuda":
            _model = _model.half()

        _model.eval()
        print(f"[Translation-v2] Model loaded successfully")
        return _tokenizer, _model

    except Exception as e:
        print(f"[Translation-v2] Failed to load model: {e}")
        raise RuntimeError(
            f"Could not load NLLB model '{MODEL_NAME}'. "
            f"Run: pip install transformers sentencepiece && "
            f"huggingface-cli download {MODEL_NAME}"
        ) from e


# ---------------------------------------------------------------------------
# Public API — same interface as translation.py
# ---------------------------------------------------------------------------

def translate_text(text: str, source_lang: str, target_lang: str) -> str:
    """
    Translate text between any supported language pair.

    Args:
        text: Source text to translate
        source_lang: Source language code (e.g., 'en', 'eng_Latn')
        target_lang: Target language code (e.g., 'hi', 'hin_Deva')

    Returns:
        Translated text string
    """
    if not text or not text.strip():
        return ""

    # Resolve to flores-200 codes
    src_flores = _resolve_flores_code(source_lang)
    tgt_flores = _resolve_flores_code(target_lang)

    # Skip if same language
    if src_flores == tgt_flores:
        return text

    tokenizer, model = _load_model()

    # Set source language for tokenizer
    tokenizer.src_lang = src_flores

    # Tokenize
    inputs = tokenizer(
        text,
        return_tensors="pt",
        truncation=True,
        max_length=1024,
        padding=True,
    ).to(DEVICE)

    # Get target language token ID
    tgt_lang_id = tokenizer.convert_tokens_to_ids(tgt_flores)

    # Generate translation
    with torch.no_grad():
        generated_tokens = model.generate(
            **inputs,
            forced_bos_token_id=tgt_lang_id,
            max_new_tokens=1024,
            num_beams=5,
            num_return_sequences=1,
        )

    translated = tokenizer.batch_decode(
        generated_tokens, skip_special_tokens=True
    )[0]

    print(f"[Translation-v2] {src_flores} → {tgt_flores}: "
          f"'{text[:60]}...' → '{translated[:60]}...'")

    return translated


# ---------------------------------------------------------------------------
# Segment-level translation (added for the timeline-based dubbing pipeline)
# ---------------------------------------------------------------------------
# NLLB-200 is a SENTENCE-level model. The original pipeline fed it the entire
# transcript of a 1-2 minute video as one string, and it did what that model
# always does with a paragraph: collapsed it, commonly emitting only the first
# sentence or two. The dub then came out far shorter than the video — one half
# of the "audio finishes early and the video keeps playing" complaint.
#
# Translating segment by segment fixes both that and the timing: each segment
# keeps its own slot on the timeline (see dubbing.plan_timeline).

import re as _re

_SENT_SPLIT = _re.compile(r"(?<=[.!?।])\s+")

# NLLB starts degrading well before its positional limit; keep inputs short.
MAX_SEGMENT_CHARS = 480


def _split_long(text: str, limit: int = MAX_SEGMENT_CHARS) -> list:
    """Split an over-long segment on sentence boundaries, then on commas."""
    text = (text or "").strip()
    if len(text) <= limit:
        return [text] if text else []

    parts, buf = [], ""
    for sent in _SENT_SPLIT.split(text):
        if not sent:
            continue
        if len(buf) + len(sent) + 1 <= limit:
            buf = f"{buf} {sent}".strip()
        else:
            if buf:
                parts.append(buf)
            buf = sent if len(sent) <= limit else ""
            if not buf:
                chunk = ""
                for piece in sent.split(","):
                    if len(chunk) + len(piece) + 1 <= limit:
                        chunk = f"{chunk},{piece}" if chunk else piece
                    else:
                        if chunk:
                            parts.append(chunk.strip())
                        chunk = piece[:limit]
                if chunk:
                    buf = chunk.strip()
    if buf:
        parts.append(buf)
    return [p for p in parts if p.strip()]


def translate_batch(texts: list, source_lang: str, target_lang: str,
                    batch_size: int = 8, num_beams: int = None) -> list:
    """
    Translate many short texts. Returns a list the same length as `texts`,
    with empty inputs preserved as empty strings.
    """
    import languages as _L

    src = _L.source_flores(source_lang)
    tgt = _L.target_flores(target_lang)

    if src == tgt:
        return [t or "" for t in texts]

    beams = int(num_beams or os.getenv("NLLB_NUM_BEAMS", 2))
    tokenizer, model = _load_model()
    tokenizer.src_lang = src
    tgt_id = tokenizer.convert_tokens_to_ids(tgt)

    order = [i for i, t in enumerate(texts) if (t or "").strip()]
    out = [""] * len(texts)
    if not order:
        return out

    pending = [texts[i].strip() for i in order]

    for start in range(0, len(pending), batch_size):
        chunk = pending[start:start + batch_size]
        inputs = tokenizer(chunk, return_tensors="pt", truncation=True,
                           max_length=512, padding=True).to(DEVICE)
        with torch.no_grad():
            generated = model.generate(
                **inputs,
                forced_bos_token_id=tgt_id,
                max_new_tokens=512,
                num_beams=beams,
                num_return_sequences=1,
                # NLLB falls into repetition loops on poetry and repeated
                # phrasing — observed emitting the same word 20 times in a row,
                # which the TTS then dutifully spoke. These two bound it without
                # measurably hurting normal prose.
                no_repeat_ngram_size=4,
                repetition_penalty=1.1,
            )
        decoded = tokenizer.batch_decode(generated, skip_special_tokens=True)
        for k, text in enumerate(decoded):
            out[order[start + k]] = text.strip()

    return out


def translate_segments(segments: list, source_lang: str, target_lang: str,
                       num_beams: int = None) -> list:
    """
    Translate Whisper segments one by one, preserving alignment with the input
    list. Over-long segments are split, translated, and rejoined so a segment
    never silently loses its tail.

    Returns a list of translated strings, one per input segment.
    """
    import languages as _L

    flat, owner = [], []
    for i, seg in enumerate(segments):
        pieces = _split_long((seg.get("text") or "").strip())
        for p in pieces:
            flat.append(p)
            owner.append(i)

    if not flat:
        return [""] * len(segments)

    translated = translate_batch(flat, source_lang, target_lang, num_beams=num_beams)

    joined = [[] for _ in segments]
    for idx, text in zip(owner, translated):
        if text:
            joined[idx].append(text)

    result = [" ".join(parts).strip() for parts in joined]

    src = _L.source_flores(source_lang)
    tgt = _L.target_flores(target_lang)
    kept = sum(1 for r in result if r)
    print(f"[Translation-v2] {src} -> {tgt}: {kept}/{len(segments)} segments "
          f"({len(flat)} sub-parts, beams={num_beams or os.getenv('NLLB_NUM_BEAMS', 2)})")
    return result


# ---------------------------------------------------------------------------
# Residual code-switch cleanup
# ---------------------------------------------------------------------------
# 10% of segments targeting Hindi still contained Latin-script words after
# translation: untranslated code-switching ("Have I done something wrong?"),
# acronyms (DBMS, LLM) and proper nouns. IndicF5 reads Latin with Latin
# phonetics, so those words come out sounding like a different language
# dropped into the middle of the sentence.
#
# Same-language re-voicing never runs the translator at all (src == tgt), so
# that path had no chance to clean them up. This pass catches both.

_LATIN_RUN = _re.compile(r"[A-Za-z][A-Za-z'’]*(?:\s+[A-Za-z][A-Za-z'’]*)*")

# Left alone deliberately: an all-caps acronym is usually said letter by letter
# in Indian speech too ("DBMS", "LLM", "AI"), and translating it produces
# nonsense. A single capitalised word is probably a name.
_KEEP_ACRONYM = _re.compile(r"^[A-Z]{2,6}$")


def _should_translate_run(run: str) -> bool:
    words = run.split()
    if not words:
        return False
    if len(words) == 1:
        w = words[0]
        if _KEEP_ACRONYM.match(w):      # DBMS, LLM, AI
            return False
        if w[:1].isupper() and len(w) > 1:   # probably a name
            return False
        return len(w) >= 4              # a real English word
    return True                          # a phrase — translate it


def fix_code_switching(texts: list, target_lang: str,
                       source_hint: str = "en") -> list:
    """
    Translate leftover Latin-script runs into the target script.

    Returns `texts` with each qualifying run replaced by its translation.
    Anything that fails to translate is left exactly as it was.
    """
    import languages as _L

    try:
        tgt = _L.target_flores(target_lang)
    except Exception:
        return texts
    if tgt.endswith("_Latn"):
        return texts                     # target is Latin-script; nothing to do

    jobs, sites = [], []
    for i, text in enumerate(texts):
        for m in _LATIN_RUN.finditer(text or ""):
            run = m.group(0).strip()
            if _should_translate_run(run):
                jobs.append(run)
                sites.append((i, m.start(), m.end()))

    if not jobs:
        return texts

    try:
        fixed = translate_batch(jobs, source_hint, target_lang)
    except Exception as e:
        print(f"[Translation-v2] Code-switch cleanup skipped: {e}")
        return texts

    out = list(texts)
    # Replace right-to-left so earlier offsets stay valid.
    for (i, a, b), new in sorted(zip(sites, fixed), key=lambda x: -x[0][1]):
        if new and new.strip():
            out[i] = out[i][:a] + new.strip() + out[i][b:]

    print(f"[Translation-v2] Rewrote {len(jobs)} Latin-script run(s) into "
          f"{tgt} to stop them being spoken as English")
    return out
