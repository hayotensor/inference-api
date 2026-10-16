"""Display metadata derived from a model id (HYP-92).

Nothing in the platform stores a model's provider, family, modality or size:
a miner registers a bare ``model_id`` (in practice a Hugging Face repo id such
as ``meta-llama/Llama-3.3-70B-Instruct``) plus a hash, and the allowlist only
adds an operator ``label``. So the public models page derives everything it
groups/filters by from the id itself, using conservative keyword rules. Every
unknown id still gets a sane answer (``category="chat"``, provider from the
repo org or ``None``) -- this is presentation only and never feeds billing or
routing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

ModelCategory = Literal["chat", "reasoning", "code", "vision", "embedding", "audio"]

# Repo org -> display name. Unlisted orgs fall back to the org verbatim.
_PROVIDERS: dict[str, str] = {
    "meta-llama": "Meta",
    "meta": "Meta",
    "facebook": "Meta",
    "qwen": "Qwen",
    "alibaba": "Qwen",
    "mistralai": "Mistral AI",
    "deepseek-ai": "DeepSeek",
    "google": "Google",
    "microsoft": "Microsoft",
    "openai": "OpenAI",
    "nousresearch": "Nous Research",
    "baai": "BAAI",
    "intfloat": "Microsoft",
    "nvidia": "NVIDIA",
    "ibm-granite": "IBM",
    "tiiuae": "TII",
    "01-ai": "01.AI",
    "cohereforai": "Cohere",
    "moonshotai": "Moonshot AI",
    "zai-org": "Z.ai",
    "thudm": "Z.ai",
}

# (pattern on the model name, family display name, provider if the id has no org).
_FAMILIES: tuple[tuple[str, str, str | None], ...] = (
    # Fine-tunes/distills name their base model too ("DeepSeek-R1-Distill-Llama",
    # "Hermes-3-Llama"), so the publishing family is checked before the bases.
    (r"deepseek", "DeepSeek", "DeepSeek"),
    (r"hermes", "Hermes", "Nous Research"),
    (r"llama", "Llama", "Meta"),
    (r"qwq|qwen", "Qwen", "Qwen"),
    (r"mixtral|mistral|ministral|codestral|pixtral|magistral|devstral", "Mistral", "Mistral AI"),
    (r"gemma", "Gemma", "Google"),
    (r"\bphi", "Phi", "Microsoft"),
    (r"gpt-oss", "GPT-OSS", "OpenAI"),
    (r"glm", "GLM", "Z.ai"),
    (r"kimi", "Kimi", "Moonshot AI"),
    (r"granite", "Granite", "IBM"),
    (r"falcon", "Falcon", "TII"),
    (r"whisper", "Whisper", "OpenAI"),
    (r"bge", "BGE", "BAAI"),
)

# First match wins, so the more specific modalities come before code/reasoning.
_CATEGORY_RULES: tuple[tuple[ModelCategory, str], ...] = (
    ("embedding", r"embed|\bbge\b|\bbge-|\be5-|\bgte-|rerank"),
    ("audio", r"whisper|\btts\b|speech|audio"),
    ("vision", r"vision|\bvl\b|-vl-|-vl$|llava|pixtral|multimodal"),
    ("code", r"coder|codestral|devstral|starcoder|codellama|\bcode\b"),
    ("reasoning", r"\br1\b|-r1|qwq|reason|thinking|magistral"),
)

_SIZE = re.compile(r"(?<![a-z0-9.])(\d+x)?(\d+(?:\.\d+)?)([bm])(?![a-z])", re.IGNORECASE)


@dataclass(frozen=True)
class ModelMetadata:
    name: str
    provider: str | None
    family: str | None
    category: ModelCategory
    parameter_size: str | None


def derive_metadata(model_id: str) -> ModelMetadata:
    org, _, name = model_id.rpartition("/")
    lowered = name.lower()
    # Treat separators as word boundaries so `\b` rules see "Qwen2.5-VL" as "qwen2.5 vl".
    words = re.sub(r"[-_/.:]", " ", lowered)

    family: str | None = None
    family_provider: str | None = None
    for pattern, family_name, provider in _FAMILIES:
        if re.search(pattern, lowered):
            family, family_provider = family_name, provider
            break

    provider = _PROVIDERS.get(org.lower(), org or None) if org else family_provider

    category: ModelCategory = "chat"
    for candidate, pattern in _CATEGORY_RULES:
        if re.search(pattern, lowered) or re.search(pattern, words):
            category = candidate
            break

    size_match = _SIZE.search(name)
    parameter_size = (
        f"{size_match.group(1) or ''}{size_match.group(2)}{size_match.group(3).upper()}"
        if size_match
        else None
    )

    return ModelMetadata(
        name=name or model_id,
        provider=provider,
        family=family,
        category=category,
        parameter_size=parameter_size,
    )
