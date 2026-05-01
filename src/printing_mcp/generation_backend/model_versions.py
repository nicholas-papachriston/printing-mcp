from __future__ import annotations

IMAGE_MODEL = "nano_banana_2"
IMAGE_MODEL_ID = "gemini-3.1-flash-image-preview"
IMAGE_MODEL_REVISION = "preview"

TRIPOSR_MODEL_ID = "stabilityai/TripoSR"
# TODO: replace with a pinned Hugging Face commit after the first successful Modal run.
TRIPOSR_REVISION = "main"
TRIPOSR_GITHUB = "https://github.com/VAST-AI-Research/TripoSR"
# TODO: replace with a pinned Git commit after the Modal image build is stable.
TRIPOSR_GITHUB_COMMIT = "main"

QUALITY_MODEL = "hunyuan3d_dit_shape"
HUNYUAN_MODEL_ID = "tencent/Hunyuan3D-2"
HUNYUAN_SUBFOLDER = "hunyuan3d-dit-v2-0"
HUNYUAN_REVISION = "unverified"

QUALITY_FALLBACK_MODEL = "instantmesh"


def fast_model_manifest() -> dict[str, str]:
    return {
        "image_model": IMAGE_MODEL,
        "image_model_id": IMAGE_MODEL_ID,
        "image_model_revision": IMAGE_MODEL_REVISION,
        "mesh_model": "triposr",
        "mesh_model_id": TRIPOSR_MODEL_ID,
        "mesh_model_revision": TRIPOSR_REVISION,
        "mesh_source": TRIPOSR_GITHUB,
        "mesh_source_commit": TRIPOSR_GITHUB_COMMIT,
    }
