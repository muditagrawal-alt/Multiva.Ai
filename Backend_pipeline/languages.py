"""
Central language registry — single source of truth for the dubbing pipeline.

Replaces the scattered maps that used to live in `translation_v2.LANG_TO_FLORES`
and `openvoice_worker.EDGE_TTS_VOICES`.

Each entry describes, for one UI-facing language code:
  name         human-readable label
  flores       NLLB-200 flores-200 code for this language
  translate_to flores code we actually translate INTO (differs only where the
               synthesis engine cannot read the language's native script)
  engine       which TTS backend synthesizes it
  synth_lang   language code handed to that engine
  cps          rough characters-per-second of natural speech, used only as a
               sanity bound on duration fitting (see dubbing.plan_timeline)

IndicF5 (ai4bharat/IndicF5) natively covers 11 Indian languages. Its vocabulary
contains all 11 Indian scripts plus Latin, but NO Arabic script — so Urdu is
translated to Hindi/Devanagari and synthesized as Hindi. Urdu and Hindi are the
same spoken language (Hindustani); the audio a listener hears is correct Urdu.
The caveat is lexical: heavily Persianized formal Urdu will drift toward Hindi
vocabulary, because NLLB is asked for Hindi.
"""

ENGINE_INDICF5 = "indicf5"
ENGINE_XTTS = "xtts"

# Languages IndicF5 was trained on.
INDICF5_NATIVE = {"as", "bn", "gu", "hi", "kn", "ml", "mr", "or", "pa", "ta", "te"}

# Languages Coqui XTTS-v2 supports (phase 2: foreign languages).
XTTS_NATIVE = {
    "en", "es", "fr", "de", "it", "pt", "pl", "tr", "ru",
    "nl", "cs", "ar", "zh-cn", "ja", "hu", "ko", "hi",
}

LANGUAGES = {
    # ---- Indian languages: IndicF5 ----
    "hi": dict(name="Hindi",     flores="hin_Deva", engine=ENGINE_INDICF5, cps=13.5),
    "mr": dict(name="Marathi",   flores="mar_Deva", engine=ENGINE_INDICF5, cps=13.0),
    "bn": dict(name="Bengali",   flores="ben_Beng", engine=ENGINE_INDICF5, cps=13.0),
    "ta": dict(name="Tamil",     flores="tam_Taml", engine=ENGINE_INDICF5, cps=12.0),
    "te": dict(name="Telugu",    flores="tel_Telu", engine=ENGINE_INDICF5, cps=12.5),
    "kn": dict(name="Kannada",   flores="kan_Knda", engine=ENGINE_INDICF5, cps=12.5),
    "gu": dict(name="Gujarati",  flores="guj_Gujr", engine=ENGINE_INDICF5, cps=13.0),
    "ml": dict(name="Malayalam", flores="mal_Mlym", engine=ENGINE_INDICF5, cps=12.0),
    "pa": dict(name="Punjabi",   flores="pan_Guru", engine=ENGINE_INDICF5, cps=13.0),
    "or": dict(name="Odia",      flores="ory_Orya", engine=ENGINE_INDICF5, cps=12.5),
    "as": dict(name="Assamese",  flores="asm_Beng", engine=ENGINE_INDICF5, cps=13.0),

    # Urdu: no Arabic script in the IndicF5 vocab -> translate to Hindi, speak as Hindi.
    "ur": dict(name="Urdu", flores="urd_Arab", translate_to="hin_Deva",
               engine=ENGINE_INDICF5, synth_lang="hi", cps=13.5),

    # ---- Foreign languages: XTTS-v2 (phase 2) ----
    "en": dict(name="English",    flores="eng_Latn", engine=ENGINE_XTTS, cps=15.5),
    "es": dict(name="Spanish",    flores="spa_Latn", engine=ENGINE_XTTS, cps=15.0),
    "fr": dict(name="French",     flores="fra_Latn", engine=ENGINE_XTTS, cps=15.0),
    "de": dict(name="German",     flores="deu_Latn", engine=ENGINE_XTTS, cps=14.5),
    "it": dict(name="Italian",    flores="ita_Latn", engine=ENGINE_XTTS, cps=15.0),
    "pt": dict(name="Portuguese", flores="por_Latn", engine=ENGINE_XTTS, cps=15.0),
    "ru": dict(name="Russian",    flores="rus_Cyrl", engine=ENGINE_XTTS, cps=14.0),
    "pl": dict(name="Polish",     flores="pol_Latn", engine=ENGINE_XTTS, cps=14.0),
    "nl": dict(name="Dutch",      flores="nld_Latn", engine=ENGINE_XTTS, cps=14.5),
    "tr": dict(name="Turkish",    flores="tur_Latn", engine=ENGINE_XTTS, cps=14.0),
    "cs": dict(name="Czech",      flores="ces_Latn", engine=ENGINE_XTTS, cps=14.0),
    "hu": dict(name="Hungarian",  flores="hun_Latn", engine=ENGINE_XTTS, cps=14.0),
    "ar": dict(name="Arabic",     flores="arb_Arab", engine=ENGINE_XTTS, cps=13.0),
    "ja": dict(name="Japanese",   flores="jpn_Jpan", engine=ENGINE_XTTS, cps=7.0),
    "ko": dict(name="Korean",     flores="kor_Hang", engine=ENGINE_XTTS, cps=8.0),
    "zh": dict(name="Chinese",    flores="zho_Hans", engine=ENGINE_XTTS,
               synth_lang="zh-cn", cps=6.5),
}

# Source-side only: Whisper may detect these even though we cannot synthesize them.
# Used to resolve a translation source code, never a synthesis target.
EXTRA_SOURCE_FLORES = {
    "th": "tha_Thai", "vi": "vie_Latn", "id": "ind_Latn", "ms": "zsm_Latn",
    "sw": "swh_Latn", "uk": "ukr_Cyrl", "ro": "ron_Latn", "el": "ell_Grek",
    "sv": "swe_Latn", "da": "dan_Latn", "fi": "fin_Latn", "no": "nob_Latn",
    "he": "heb_Hebr", "fa": "pes_Arab", "ne": "npi_Deva", "si": "sin_Sinh",
}


class UnsupportedLanguage(ValueError):
    pass


def normalize(code: str) -> str:
    """'hi-IN' -> 'hi'; 'zh-CN' -> 'zh'. Case-insensitive."""
    if not code:
        raise UnsupportedLanguage("empty language code")
    c = code.strip().lower()
    if c in LANGUAGES:
        return c
    base = c.split("-")[0].split("_")[0]
    if base in LANGUAGES or base in EXTRA_SOURCE_FLORES:
        return base
    raise UnsupportedLanguage(f"unknown language code: {code!r}")


def source_flores(code: str) -> str:
    """flores-200 code to use as the TRANSLATION SOURCE."""
    c = normalize(code)
    if c in LANGUAGES:
        return LANGUAGES[c]["flores"]
    return EXTRA_SOURCE_FLORES[c]


def target_flores(code: str) -> str:
    """flores-200 code to translate INTO for this synthesis target."""
    c = normalize(code)
    if c not in LANGUAGES:
        raise UnsupportedLanguage(f"{code!r} is not a supported dubbing target")
    e = LANGUAGES[c]
    return e.get("translate_to", e["flores"])


def engine_for(code: str) -> str:
    c = normalize(code)
    if c not in LANGUAGES:
        raise UnsupportedLanguage(f"{code!r} is not a supported dubbing target")
    return LANGUAGES[c]["engine"]


def synth_lang(code: str) -> str:
    """Language code handed to the TTS engine (Urdu -> 'hi', Chinese -> 'zh-cn')."""
    c = normalize(code)
    return LANGUAGES[c].get("synth_lang", c)


def chars_per_second(code: str) -> float:
    c = normalize(code)
    return LANGUAGES.get(c, {}).get("cps", 14.0)


def display_name(code: str) -> str:
    return LANGUAGES[normalize(code)]["name"]


def supported_targets() -> list:
    """UI-facing list, Indian languages first."""
    indic = [c for c, e in LANGUAGES.items() if e["engine"] == ENGINE_INDICF5]
    other = [c for c, e in LANGUAGES.items() if e["engine"] != ENGINE_INDICF5]
    return [{"code": c, "name": LANGUAGES[c]["name"],
             "engine": LANGUAGES[c]["engine"]} for c in indic + other]
