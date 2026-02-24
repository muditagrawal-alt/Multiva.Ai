from transformers import AutoModelForSeq2SeqLM, AutoTokenizer
import torch

MODEL_NAME = "facebook/nllb-200-distilled-600M"

DEVICE = "cuda" if torch.cuda.is_available() else "cpu"

tokenizer = AutoTokenizer.from_pretrained(MODEL_NAME)
model = AutoModelForSeq2SeqLM.from_pretrained(MODEL_NAME).to(DEVICE)

LANGUAGE_MAP = {
    "en": "eng_Latn",
    "es": "spa_Latn",
    "fr": "fra_Latn",
    "de": "deu_Latn",
    "it": "ita_Latn",
    "pt": "por_Latn",
    "hi": "hin_Deva",
    "zh": "zho_Hans",
    "ar": "arb_Arab",     # Arabic
    "ru": "rus_Cyrl",     # Russian
    "ko": "kor_Hang",     # Korean
    "tr": "tur_Latn",     # Turkish
    "nl": "nld_Latn",     # Dutch
    "pl": "pol_Latn",     # Polish
}


def translate_text(text: str, source_lang: str, target_lang: str) -> str:

    if source_lang == target_lang:
        return text

    if source_lang not in LANGUAGE_MAP:
        raise ValueError(f"Source language {source_lang} not supported")

    if target_lang not in LANGUAGE_MAP:
        raise ValueError(f"Target language {target_lang} not supported")

    tokenizer.src_lang = LANGUAGE_MAP[source_lang]

    inputs = tokenizer(text, return_tensors="pt", truncation=True).to(DEVICE)

    forced_bos_token_id = tokenizer.convert_tokens_to_ids(
        LANGUAGE_MAP[target_lang]
    )

    outputs = model.generate(
        **inputs,
        forced_bos_token_id=forced_bos_token_id,
        max_length=600
    )

    return tokenizer.batch_decode(outputs, skip_special_tokens=True)[0]