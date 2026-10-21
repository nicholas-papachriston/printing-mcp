from __future__ import annotations

from typing import Literal, assert_never

IMAGE_MODEL = "nano_banana_2"
IMAGE_MODEL_ID = "gemini-3.1-flash-image-preview"
IMAGE_MODEL_REVISION = "preview"

TRIPOSR_MODEL_ID = "stabilityai/TripoSR"
# TODO: replace with a pinned Hugging Face commit after the first successful Modal run.
TRIPOSR_REVISION = "main"
TRIPOSR_GITHUB = "https://github.com/VAST-AI-Research/TripoSR"
# TODO: replace with a pinned Git commit after the Modal image build is stable.
TRIPOSR_GITHUB_COMMIT = "main"

# Image-to-mesh models; the local GPU provider runs either, Modal runs TripoSR only.
MeshModel = Literal["triposr", "hunyuan3d_mini"]

HUNYUAN3D_MINI_MODEL_ID = "tencent/Hunyuan3D-2mini"
HUNYUAN3D_MINI_SUBFOLDER = "hunyuan3d-dit-v2-mini"
# hy3dgen downloads without a revision; tested on snapshot f90a0f7df7d5e6f71109cf333f6a95a0ae3194a6.
HUNYUAN3D_MINI_REVISION = "main"
HUNYUAN3D_GITHUB = "https://github.com/Tencent-Hunyuan/Hunyuan3D-2"
HUNYUAN3D_GITHUB_COMMIT = "f8db63096c8282cb27354314d896feba5ba6ff8a"

QUALITY_MODEL = "hunyuan3d_dit_shape"
HUNYUAN_MODEL_ID = "tencent/Hunyuan3D-2"
HUNYUAN_SUBFOLDER = "hunyuan3d-dit-v2-0"
HUNYUAN_REVISION = "unverified"

QUALITY_FALLBACK_MODEL = "instantmesh"


def fast_model_manifest(mesh_model: MeshModel = "triposr") -> dict[str, str]:
    match mesh_model:
        case "triposr":
            mesh_entries = {
                "mesh_model_id": TRIPOSR_MODEL_ID,
                "mesh_model_revision": TRIPOSR_REVISION,
                "mesh_source": TRIPOSR_GITHUB,
                "mesh_source_commit": TRIPOSR_GITHUB_COMMIT,
            }
        case "hunyuan3d_mini":
            mesh_entries = {
                "mesh_model_id": HUNYUAN3D_MINI_MODEL_ID,
                "mesh_model_revision": HUNYUAN3D_MINI_REVISION,
                "mesh_source": HUNYUAN3D_GITHUB,
                "mesh_source_commit": HUNYUAN3D_GITHUB_COMMIT,
            }
        case _:
            assert_never(mesh_model)
    return {
        "image_model": IMAGE_MODEL,
        "image_model_id": IMAGE_MODEL_ID,
        "image_model_revision": IMAGE_MODEL_REVISION,
        "mesh_model": mesh_model,
        **mesh_entries,
    }
