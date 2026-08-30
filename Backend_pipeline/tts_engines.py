"""
TTS engine layer — warm, in-process, local.

The old design spawned a fresh Python subprocess per request, which reloaded
torch, the OpenVoice converter, and (via OpenVoice's se_extractor) an entire
extra Whisper model on every single job. It also called edge-tts, which is a
network round-trip to Azure and therefore neither local nor reliable.

This module replaces that with warm singletons held in the API process:

  IndicF5Engine  ai4bharat/IndicF5 — 11 Indian languages, zero-shot cloning
                 directly from a reference clip + its transcript. Single stage:
                 no stock voice to repaint, so the speaker identity comes from
                 the reference rather than from a tone-color transfer.

  XTTSEngine     Coqui XTTS-v2 — cross-lingual cloning for foreign languages
                 (phase 2). Loaded lazily; never touched for Indian targets.
                 NOTE: Coqui Public Model License is non-commercial.

Both are pure-local: weights come from the HF cache, nothing hits the network
at inference time.
"""

import os
import threading

import numpy as np

import languages as L

_MPS_FALLBACK_SET = False


def _pick_device() -> str:
    forced = os.getenv("FORCE_DEVICE")
    if forced:
        return forced
    import torch
    if torch.cuda.is_available():
        return "cuda"
    if torch.backends.mps.is_available():
        return "mps"
    return "cpu"


def _ensure_mps_fallback():
    """Some F5/vocos ops have no MPS kernel; let torch fall back silently."""
    global _MPS_FALLBACK_SET
    if not _MPS_FALLBACK_SET:
        os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")
        _MPS_FALLBACK_SET = True


class SynthesisError(RuntimeError):
    pass


_TORCHAUDIO_PATCHED = False


def _patch_torchaudio_load():
    """
    Give `torchaudio.load` a soundfile-backed implementation.

    torchaudio 2.11 dropped its own decoding backends and routes `load` through
    TorchCodec, which is not installed here — so any library calling it dies
    with "TorchCodec is required for load_with_torchcodec". IndicF5 could be
    routed around it (we hand `infer_batch_process` a tensor directly), but XTTS
    calls it deep inside its own inference path, so the only options are to
    install torchcodec or to supply the function torchaudio used to have.

    soundfile is already a dependency and handles every format we feed it.
    """
    global _TORCHAUDIO_PATCHED
    if _TORCHAUDIO_PATCHED:
        return

    import torch
    import torchaudio

    try:
        torchaudio.load(__file__)  # cheap probe: fails either way, but how?
    except Exception as e:
        if "TorchCodec" not in str(e):
            _TORCHAUDIO_PATCHED = True   # a working backend exists
            return

    import soundfile as sf

    def _load(uri, frame_offset=0, num_frames=-1, normalize=True,
              channels_first=True, **_):
        data, sr = sf.read(str(uri), dtype="float32", always_2d=True,
                           start=int(frame_offset),
                           frames=(-1 if num_frames in (-1, None) else int(num_frames)))
        t = torch.from_numpy(data)                    # (frames, channels)
        return (t.T.contiguous() if channels_first else t), sr

    torchaudio.load = _load
    _TORCHAUDIO_PATCHED = True
    print("[TTS] Patched torchaudio.load to use soundfile (no torchcodec)")


# ---------------------------------------------------------------------------
# IndicF5
# ---------------------------------------------------------------------------
class IndicF5Engine:
    """
    Warm wrapper around ai4bharat/IndicF5.

    Bypasses the model's own `forward()` for three reasons:
      1. it hardcodes device="cpu"
      2. it strips silence and re-normalizes loudness, which fights duration control
      3. it routes through `infer_process`, whose `torchaudio.load` now demands
         torchcodec (absent here) — we hand `infer_batch_process` a tensor instead
    """

    SAMPLE_RATE = 24000
    REPO_ID = "ai4bharat/IndicF5"

    # nfe_step is the number of flow-matching steps; cost is linear in it.
    # Measured on this machine (Apple M4, 4s of Hindi audio, warm model):
    #   mps nfe=32  47.6s  (12.0x realtime)      cpu nfe=32  73.3s (18.4x)
    #   mps nfe=16  24.7s  ( 6.2x realtime)      cpu nfe=16  36.9s ( 9.3x)
    # MPS beats CPU by ~1.5x, and batching four utterances into one call did
    # NOT help (6.8x realtime), so the cost is genuinely the DiT sampling
    # rather than per-call overhead — there is nothing to amortize.
    # 16 is the default here because latency matters; raise it for final renders.

    def __init__(self, nfe_step: int = None, seed: int = None):
        self._model = None
        self._lock = threading.Lock()
        self._ref_cache = {}
        self._spb_cache = {}
        self.device = None
        import engines
        self.nfe_step = int(nfe_step or engines.get("tts") or 16)

        # Flow matching starts from random noise, so the SAME inputs give
        # different audio on every run — and the difference is not cosmetic.
        # Measured: two runs of one clip with byte-identical reference, units,
        # text and pacing scored CER 0.088 and 0.258. Without a fixed seed the
        # evaluation harness cannot attribute a change to a code change rather
        # than to sampling luck, which makes A/B comparison meaningless.
        # Set INDICF5_SEED= (empty) to opt back into stochastic sampling.
        env_seed = os.getenv("INDICF5_SEED", "1234")
        self.seed = (int(seed) if seed is not None
                     else (int(env_seed) if env_seed.strip() else None))

    # -- loading ------------------------------------------------------------
    def load(self):
        if self._model is not None:
            return self._model
        with self._lock:
            if self._model is not None:
                return self._model

            _ensure_mps_fallback()
            import torch
            from transformers import AutoModel

            self.device = _pick_device()
            print(f"[IndicF5] Loading {self.REPO_ID} on {self.device}...")

            model = AutoModel.from_pretrained(self.REPO_ID, trust_remote_code=True)
            self._load_real_weights(model)

            model.ema_model.to(self.device)
            model.vocoder.to(self.device)
            model.ema_model.eval()
            model.runtime_device = self.device

            self._model = model
            print(f"[IndicF5] Ready on {self.device}")
            return self._model

    @staticmethod
    def _load_real_weights(model):
        """
        Load the checkpoint by hand, stripping the `_orig_mod.` prefix.

        Every one of the 447 tensors in ai4bharat/IndicF5's model.safetensors is
        named `ema_model._orig_mod.transformer.…` / `vocoder._orig_mod.…` — the
        checkpoint was saved from a torch.compile()-wrapped module, and compile
        inserts `_orig_mod` into the module path. The instantiated model has no
        such level, so `from_pretrained` matches ZERO weights and silently leaves
        both the DiT and the vocoder randomly initialized (in practice NaN).

        The only sign is a "weights were not used … You should probably TRAIN
        this model" warning buried in transformers' startup output, and the
        result is audio of exactly the right length containing pure silence.

        Verified: 0/447 keys match raw, 447/447 match after stripping, with no
        missing and no unexpected keys.
        """
        import torch
        from huggingface_hub import hf_hub_download
        from safetensors.torch import load_file

        path = hf_hub_download(IndicF5Engine.REPO_ID, "model.safetensors")
        raw = load_file(path)
        state = {k.replace("._orig_mod.", "."): v for k, v in raw.items()}

        result = model.load_state_dict(state, strict=False)
        missing = list(getattr(result, "missing_keys", []))
        unexpected = list(getattr(result, "unexpected_keys", []))
        if missing or unexpected:
            raise SynthesisError(
                f"IndicF5 checkpoint did not map cleanly onto the model "
                f"({len(missing)} missing, {len(unexpected)} unexpected). "
                f"First missing: {missing[:3]}")

        # A NaN here means the weights never landed — fail loudly rather than
        # emit silence that looks like a successful run.
        probe = model.ema_model.transformer.proj_out.weight
        if torch.isnan(probe).any() or float(probe.std()) == 0.0:
            raise SynthesisError(
                "IndicF5 weights are NaN/zero after loading — the model would "
                "generate silence.")

        print(f"[IndicF5] Loaded {len(state)} tensors "
              f"(stripped '_orig_mod.' prefix)")

    # -- reference handling -------------------------------------------------
    def _load_ref(self, ref_path: str):
        """
        Load + cache the reference waveform as a (1, N) float32 tensor at 24 kHz.
        Cached so a 30-segment video resamples the reference once, not 30 times.
        """
        key = (ref_path, os.path.getmtime(ref_path))
        if key in self._ref_cache:
            return self._ref_cache[key]

        import torch
        import soundfile as sf

        data, sr = sf.read(ref_path, dtype="float32", always_2d=False)
        if data.ndim > 1:
            data = data.mean(axis=1)

        if sr != self.SAMPLE_RATE:
            import torchaudio
            t = torch.from_numpy(data).unsqueeze(0)
            t = torchaudio.transforms.Resample(sr, self.SAMPLE_RATE)(t)
            sr = self.SAMPLE_RATE
        else:
            t = torch.from_numpy(data).unsqueeze(0)

        self._ref_cache.clear()          # only ever one reference per job
        self._ref_cache[key] = (t, sr)
        return t, sr

    def natural_duration(self, text: str, ref_path: str, ref_text: str,
                         lang: str = "hi") -> float:
        """
        How long F5 will make this utterance if left alone, in seconds.

        Mirrors the model's own internal heuristic exactly (utils_infer:
        `duration = ref_audio_len + ref_audio_len / ref_text_len * gen_text_len`,
        byte-counted). Asking for a duration that matches what the model already
        wants means it never has to internally compress or pad — which is what
        makes the difference between natural delivery and a rushed, garbled one.
        """
        rt = self._normalize_ref_text(ref_text)
        ref_bytes = max(1, len(rt.encode("utf-8")))
        gen_bytes = len((text or "").strip().encode("utf-8"))
        spb = self._seconds_per_byte(ref_path, ref_bytes)
        spb = self._sanity_clamp(spb, rt, lang)
        return spb * gen_bytes

    @staticmethod
    def _sanity_clamp(spb: float, ref_text: str, lang: str) -> float:
        """
        Keep the seconds-per-byte calibration inside a physically plausible band.

        A short or badly-transcribed reference produces a wild rate, and every
        duration downstream inherits it. Observed on a clip whose reference came
        out at 3.8s: 0.0138 s/byte against a normal ~0.028 — so every phrase was
        asked for in half the time it needs, and F5 responds to an impossible
        duration by garbling and repeating words rather than by speaking faster.
        That single bad reference produced the worst output in the whole corpus
        (CER 3.39, the dub looping "पानी कर लिया है" four times).

        The expected rate is derivable: characters-per-second for the language,
        times the bytes-per-character of its script. Anything far from that is a
        broken reference, not an unusual speaker.
        """
        try:
            cps = L.chars_per_second(lang)
        except Exception:
            return spb
        chars = max(1, len(ref_text.strip()))
        bytes_per_char = len(ref_text.encode("utf-8")) / chars
        expected = 1.0 / max(1e-6, cps * bytes_per_char)

        lo, hi = expected * 0.65, expected * 1.60
        if spb < lo or spb > hi:
            clamped = min(hi, max(lo, spb))
            print(f"[IndicF5] Reference implies {spb * 1000:.1f} ms/byte, "
                  f"outside the plausible {lo * 1000:.1f}-{hi * 1000:.1f} for "
                  f"'{lang}' — clamping to {clamped * 1000:.1f}. "
                  f"The reference clip is probably too short or mistranscribed.")
            return clamped
        return spb

    def _seconds_per_byte(self, ref_path: str, ref_bytes: int) -> float:
        """
        Seconds of SPEECH per byte of text, from the reference clip.

        Calibrates on the reference's voiced time, not its wall time. The clip
        contains the speaker's own pauses (~13% of it), and counting those makes
        every byte look slower than it is: measured on real footage the wall
        clock gave 32.0 ms/byte against an actual speaking rate of 26.0 ms/byte,
        a 23% over-estimate. F5 was therefore handed more time than the words
        needed and padded the remainder with silence — audible as gaps, while
        the speech inside each unit still ran at its own pace.
        """
        key = (ref_path, os.path.getmtime(ref_path), int(ref_bytes))
        if key in self._spb_cache:
            return self._spb_cache[key]

        wave, sr = self._load_ref(ref_path)
        y = wave.reshape(-1).cpu().numpy() if hasattr(wave, "cpu") else np.asarray(wave).reshape(-1)
        voiced = wave.shape[-1] / float(sr)
        try:
            import librosa
            rms = librosa.feature.rms(y=y, frame_length=int(sr * 0.02),
                                      hop_length=int(sr * 0.01))[0]
            if rms.max() > 0:
                db = librosa.amplitude_to_db(rms, ref=np.max)
                frac = float((db > -35.0).mean())
                if 0.3 < frac <= 1.0:
                    voiced = (wave.shape[-1] / float(sr)) * frac
        except Exception:
            pass

        self._spb_cache.clear()
        self._spb_cache[key] = voiced / float(max(1, ref_bytes))
        return self._spb_cache[key]

    def _seed_rng(self):
        """Reset torch RNG so a given input always yields the same audio."""
        if self.seed is None:
            return
        import torch
        torch.manual_seed(self.seed)
        if torch.cuda.is_available():
            torch.cuda.manual_seed_all(self.seed)
        try:
            torch.mps.manual_seed(self.seed)
        except Exception:
            pass

    @staticmethod
    def _normalize_ref_text(text: str) -> str:
        text = (text or "").strip()
        if not text:
            return "."
        if text[-1] not in ".?!।,":
            text += "."
        return text + " "

    # -- synthesis ----------------------------------------------------------
    def synthesize(self, text: str, ref_path: str, ref_text: str,
                   lang: str = "hi", speed: float = 1.0):
        """
        Generate one utterance. Returns (np.float32 waveform, sample_rate).

        `speed` is F5's own duration prior. We keep it near 1.0 and do precise
        duration fitting downstream in `dubbing`, because F5's `fix_duration`
        does not track the requested length closely enough to rely on.
        """
        text = (text or "").strip()
        if not text:
            return np.zeros(0, dtype=np.float32), self.SAMPLE_RATE

        model = self.load()
        from f5_tts.infer.utils_infer import infer_batch_process

        self._seed_rng()
        audio_t, sr = self._load_ref(ref_path)
        rtext = self._normalize_ref_text(ref_text)

        try:
            wave, out_sr, _ = infer_batch_process(
                (audio_t, sr),
                rtext,
                [text],
                model.ema_model,
                model.vocoder,
                mel_spec_type="vocos",
                device=self.device,
                nfe_step=self.nfe_step,
                cross_fade_duration=0.0,   # single batch, nothing to cross-fade
                speed=speed,
                progress=None,
            )
        except Exception as e:
            raise SynthesisError(f"IndicF5 synthesis failed: {e}") from e

        wave = np.asarray(wave, dtype=np.float32).reshape(-1)
        return wave, out_sr


# ---------------------------------------------------------------------------
# XTTS-v2 (foreign languages, phase 2)
# ---------------------------------------------------------------------------
class XTTSEngine:
    """Lazily-loaded Coqui XTTS-v2. Non-commercial license — see module docstring."""

    SAMPLE_RATE = 24000
    MODEL_NAME = "tts_models/multilingual/multi-dataset/xtts_v2"

    def __init__(self):
        self._tts = None
        self._lock = threading.Lock()
        self.device = None

    def load(self):
        if self._tts is not None:
            return self._tts
        with self._lock:
            if self._tts is not None:
                return self._tts
            _ensure_mps_fallback()
            self._allow_xtts_globals()
            _patch_torchaudio_load()
            from TTS.api import TTS
            # XTTS has incomplete MPS kernel coverage; CPU is the reliable path.
            self.device = "cuda" if _pick_device() == "cuda" else "cpu"
            print(f"[XTTS] Loading {self.MODEL_NAME} on {self.device}...")
            self._tts = TTS(self.MODEL_NAME).to(self.device)
            print("[XTTS] Ready")
            return self._tts

    @staticmethod
    def _allow_xtts_globals():
        """
        Allowlist Coqui's config classes for torch.load.

        PyTorch 2.6 flipped `torch.load`'s `weights_only` default to True, and
        the XTTS checkpoint pickles `XttsConfig` and friends — so on torch 2.13
        loading dies with `UnsupportedGlobal: XttsConfig`. These are Coqui's own
        dataclasses from a checkpoint we already trust (it is in the local HF
        cache), so allowlisting them is the narrow fix; flipping weights_only
        off globally would disable the protection for every other load too.
        """
        import torch
        if not hasattr(torch.serialization, "add_safe_globals"):
            return  # older torch: weights_only was not the default
        allowed = []
        for module, names in (
            ("TTS.tts.configs.xtts_config", ["XttsConfig"]),
            ("TTS.tts.models.xtts", ["XttsAudioConfig", "XttsArgs"]),
            ("TTS.config.shared_configs", ["BaseDatasetConfig", "BaseAudioConfig"]),
        ):
            try:
                mod = __import__(module, fromlist=names)
                allowed += [getattr(mod, n) for n in names if hasattr(mod, n)]
            except Exception:
                continue
        if allowed:
            torch.serialization.add_safe_globals(allowed)

    def natural_duration(self, text: str, ref_path: str = None,
                         ref_text: str = None, lang: str = "en") -> float:
        """
        Estimated spoken length, so the planner treats XTTS like IndicF5.

        XTTS has no `fix_duration` equivalent — it speaks at its own pace and
        the timeline absorbs the difference. Without an estimate here the
        planner fell back to the generic characters-per-second model, whose
        targets could be far enough off to force overruns and cascading.
        XTTS's rate is stable enough that the language table is a fair proxy.
        """
        try:
            cps = L.chars_per_second(lang)
        except Exception:
            cps = 15.0
        return max(0.2, len((text or "").strip()) / cps)

    def synthesize(self, text: str, ref_path: str, ref_text: str = "",
                   lang: str = "en", speed: float = 1.0):
        text = (text or "").strip()
        if not text:
            return np.zeros(0, dtype=np.float32), self.SAMPLE_RATE
        tts = self.load()
        try:
            wave = tts.tts(text=text, speaker_wav=ref_path, language=lang, speed=speed)
        except TypeError:
            wave = tts.tts(text=text, speaker_wav=ref_path, language=lang)
        except Exception as e:
            raise SynthesisError(f"XTTS synthesis failed: {e}") from e
        return np.asarray(wave, dtype=np.float32).reshape(-1), self.SAMPLE_RATE


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------
_ENGINES = {}
_REG_LOCK = threading.Lock()


def get_engine(target_lang: str):
    """Return the warm engine instance responsible for `target_lang`."""
    name = L.engine_for(target_lang)
    with _REG_LOCK:
        if name not in _ENGINES:
            _ENGINES[name] = IndicF5Engine() if name == L.ENGINE_INDICF5 else XTTSEngine()
        return _ENGINES[name]


def warmup(target_lang: str = "hi"):
    """Preload weights so the first request doesn't pay the load cost."""
    try:
        get_engine(target_lang).load()
        return True
    except Exception as e:
        print(f"[TTS] Warmup failed for {target_lang}: {e}")
        return False


def synthesize(text: str, ref_path: str, ref_text: str, target_lang: str,
               speed: float = 1.0):
    """Synthesize `text` in `target_lang` in the reference speaker's voice."""
    engine = get_engine(target_lang)
    return engine.synthesize(text, ref_path, ref_text,
                             lang=L.synth_lang(target_lang), speed=speed)
