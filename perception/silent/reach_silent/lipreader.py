"""Auto-AVSR visual speech recognition: mouth crops -> open-vocabulary n-best + closed-set command scores.

EXPERIMENTAL. The checkpoint (models/vsr_trlrs2lrs3vox2avsp_base.pth) is RESEARCH / NON-COMMERCIAL: trained on LRS2,
LRS3, VoxCeleb2 and AVSpeech. See THIRD_PARTY.md.

Adapted from github.com/Input-Labs-HQ/silent-chat `vsr.py` (MIT, Copyright (c) 2026 justbenzo; notice in THIRD_PARTY.md): model construction, BatchBeamSearch setup, the
hybrid device split (encoder on MPS, beam search on CPU), n-best decoding and the batched closed-set `score()`.
Changes for Reach: no personal checkpoint / retraining, no video I/O, n-best returns (text, log_prob) only, and the
model is meant to be closed (`close()`) when the worker stops.
"""

from __future__ import annotations

import os
import re
import time
import warnings

import numpy as np

from . import AVSR_DIR, CKPT
from .mouth import _avsr_path

os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")

UNITS_FILE = AVSR_DIR / "spm" / "unigram" / "unigram5000_units.txt"
SPM_MODEL = AVSR_DIR / "spm" / "unigram" / "unigram5000.model"


class LipReader:
    """device: "cpu", "mps" or "hybrid" (encoder on MPS, beam search on CPU; the fastest on Apple Silicon)."""

    def __init__(self, device: str = "hybrid", ckpt=CKPT, beam_size: int = 20, ctc_weight: float = 0.1) -> None:
        import torch
        import torchvision

        _avsr_path()
        from espnet.nets.batch_beam_search import BatchBeamSearch
        from espnet.nets.pytorch_backend.e2e_asr_conformer import E2E
        from espnet.nets.scorers.length_bonus import LengthBonus

        self.torch = torch
        if device in ("mps", "hybrid") and not torch.backends.mps.is_available():
            device = "cpu"
        self.device_name = device
        self.device = torch.device("mps" if device in ("mps", "hybrid") else "cpu")
        self.search_device = torch.device("cpu" if device in ("cpu", "hybrid") else "mps")
        units = UNITS_FILE.read_text(encoding="utf8").splitlines()
        self.token_list = ["<blank>"] + list(dict.fromkeys(u.split()[0] for u in units)) + ["<eos>"]

        self.model = E2E(len(self.token_list), "video")
        state = torch.load(ckpt, map_location="cpu", weights_only=True)
        self.model.load_state_dict(state)
        del state
        self.model.eval()
        for part in (self.model.frontend, self.model.proj_encoder, self.model.encoder):
            part.to(self.device)
        for part in (self.model.decoder, self.model.ctc):
            part.to(self.search_device)

        scorers = self.model.scorers()
        scorers["lm"] = None
        scorers["length_bonus"] = LengthBonus(len(self.token_list))
        self.beam_search = BatchBeamSearch(
            beam_size=beam_size,
            vocab_size=len(self.token_list),
            weights={"decoder": 1.0 - ctc_weight, "ctc": ctc_weight, "lm": 0.0, "length_bonus": 0.0},
            scorers=scorers,
            sos=self.model.odim - 1,
            eos=self.model.odim - 1,
            token_list=self.token_list,
            pre_beam_score_key=None if ctc_weight == 1.0 else "decoder",
        )
        self.transform = torch.nn.Sequential(  # Auto-AVSR test-time transform
            torchvision.transforms.CenterCrop(88),
            torchvision.transforms.Grayscale(),
            torchvision.transforms.Normalize(0.421, 0.165),
        )

    # ---- encoder / decoder

    def encode(self, crops: np.ndarray):
        """Mouth crops (T,96,96,3) uint8 -> encoder output (1,T,D) on the beam-search device."""
        torch = self.torch
        x = torch.from_numpy(np.ascontiguousarray(crops)).permute(0, 3, 1, 2).float() / 255.0
        x = self.transform(x).unsqueeze(0).to(self.device)
        with torch.no_grad(), warnings.catch_warnings():
            warnings.simplefilter("ignore")
            enc, _ = self.model.encoder(self.model.proj_encoder(self.model.frontend(x)), None)
        return enc.to(self.search_device)

    def _hyp_text(self, hyp) -> str:
        ids = [int(t) for t in hyp.yseq[1:]]
        return "".join(self.token_list[i] for i in ids).replace("<eos>", "").replace("▁", " ").strip()

    def nbest(self, enc, n: int = 5) -> list[tuple[str, float]]:
        """Open-vocabulary n-best [(text, log_prob)], best first, duplicate texts removed."""
        with self.torch.no_grad(), warnings.catch_warnings():
            warnings.simplefilter("ignore")
            hyps = self.beam_search(enc.squeeze(0))
        ranked: dict[str, float] = {}
        for h in hyps:
            ranked.setdefault(self._hyp_text(h).lower(), float(h.score))
            if len(ranked) == n:
                break
        return list(ranked.items())

    def tokens(self, text: str) -> list[int]:
        if not hasattr(self, "_sp"):
            import sentencepiece

            self._sp = sentencepiece.SentencePieceProcessor(model_file=str(SPM_MODEL))
            self._index = {t: i for i, t in enumerate(self.token_list)}
        clean = re.sub(r"[^A-Z0-9' ]+", " ", text.upper()).split()
        return [self._index.get(p, self._index.get("<unk>", 1)) for p in self._sp.encode(" ".join(clean), out_type=str)]

    def score(self, enc, candidates: list[str]) -> list[tuple[str, float]]:
        """Closed-set: log-prob of each candidate phrase given the clip (decoder + CTC mix), best first."""
        torch = self.torch
        import torch.nn.functional as F
        from espnet.nets.pytorch_backend.transformer.mask import target_mask

        w = self.beam_search.weights["ctc"]
        ids = [self.tokens(c) or [0] for c in candidates]
        k, longest, eos = len(ids), max(len(i) for i in ids), self.model.eos
        dev = enc.device
        ys_in = torch.full((k, longest + 1), eos, dtype=torch.long, device=dev)
        ys_out = torch.full((k, longest + 1), -1, dtype=torch.long, device=dev)
        for j, seq in enumerate(ids):
            ys_in[j, 0] = self.model.sos
            ys_in[j, 1:len(seq) + 1] = torch.tensor(seq)
            ys_out[j, :len(seq)] = torch.tensor(seq)
            ys_out[j, len(seq)] = eos
        with torch.no_grad():
            mem = enc.expand(k, -1, -1)
            pred, _ = self.model.decoder(ys_in, target_mask(ys_in, -1), mem, None)
            logp = F.log_softmax(pred, dim=-1)
            valid = ys_out >= 0
            att = (logp.gather(-1, ys_out.clamp(min=0).unsqueeze(-1)).squeeze(-1) * valid).sum(-1)
            lp = self.model.ctc.log_softmax(enc).transpose(0, 1).expand(-1, k, -1)
            targets = torch.tensor([t for seq in ids for t in seq], dtype=torch.long)
            lengths = torch.tensor([len(seq) for seq in ids])
            ctc = -F.ctc_loss(lp.cpu(), targets, torch.full((k,), lp.shape[0], dtype=torch.long), lengths,
                              blank=0, reduction="none", zero_infinity=True)
            total = (1 - w) * att.cpu() + w * ctc
        return sorted(zip(candidates, total.tolist()), key=lambda c: -c[1])

    def read(self, crops: np.ndarray, phrases: list[str], n: int = 5) -> dict:
        """One clip -> {"nbest": [(text, logp)], "closed": [(phrase, logp)], "timings_ms": {...}}."""
        t0 = time.perf_counter()
        enc = self.encode(crops)
        if self.device.type == "mps":
            self.torch.mps.synchronize()
        t1 = time.perf_counter()
        nb = self.nbest(enc, n)
        t2 = time.perf_counter()
        closed = self.score(enc, phrases)
        t3 = time.perf_counter()
        return {
            "nbest": nb,
            "closed": closed,
            "timings_ms": {"encode": round((t1 - t0) * 1000), "beam": round((t2 - t1) * 1000),
                           "closed_set": round((t3 - t2) * 1000)},
        }

    def close(self) -> None:
        self.model = self.beam_search = None
        import gc

        gc.collect()
        if self.torch.backends.mps.is_available():
            self.torch.mps.empty_cache()
