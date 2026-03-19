from transformers import AutoModelForSeq2SeqLM, AutoTokenizer
import torch
import os

MODEL_NAME = "facebook/nllb-200-distilled-600M"


def get_translation_device():
    """
    Priority:
    1. CUDA
    2. CPU
    (NLLB is unstable on MPS)
    """

    forced = os.getenv("FORCE_DEVICE")
    if forced:
        return forced

    if torch.cuda.is_available():
        return "cuda"
    else:
        return "cpu"


TRANSLATION_DEVICE = get_translation_device()

tokenizer = AutoTokenizer.from_pretrained(MODEL_NAME)
model = AutoModelForSeq2SeqLM.from_pretrained(MODEL_NAME).to(TRANSLATION_DEVICE)

# Slight speed improvement on GPU
if TRANSLATION_DEVICE == "cuda":
    model = model.half()

LANGUAGE_MAP = {
    "en": "eng_Latn",
    "es": "spa_Latn",
    "fr": "fra_Latn",
    "de": "deu_Latn",
    "it": "ita_Latn",
    "pt": "por_Latn",
    "hi": "hin_Deva",
    "zh": "zho_Hans",
    "ar": "arb_Arab",
    "ru": "rus_Cyrl",
    "ko": "kor_Hang",
    "tr": "tur_Latn",
    "nl": "nld_Latn",
    "pl": "pol_Latn",
}


def translate_text(text: str, source_lang: str, target_lang: str) -> str:

    if source_lang == target_lang:
        return text

    if source_lang not in LANGUAGE_MAP:
        raise ValueError(f"Source language {source_lang} not supported")

    if target_lang not in LANGUAGE_MAP:
        raise ValueError(f"Target language {target_lang} not supported")

    tokenizer.src_lang = LANGUAGE_MAP[source_lang]

    inputs = tokenizer(
        text,
        return_tensors="pt",
        truncation=True
    ).to(TRANSLATION_DEVICE)

    forced_bos_token_id = tokenizer.convert_tokens_to_ids(
        LANGUAGE_MAP[target_lang]
    )

    with torch.no_grad():
        outputs = model.generate(
            **inputs,
            forced_bos_token_id=forced_bos_token_id,
            max_length=600
        )

    return tokenizer.batch_decode(outputs, skip_special_tokens=True)[0]