"""Stand-in for hy3dgen.shapegen: returns a 1 x 4 x 1 box of 48 triangles, tall in Y like
real Hunyuan3D output.

Generating without FlashVDM raises: the dense volume decoder takes minutes on a GTX 1080 Ti.
"""

import trimesh


class Hunyuan3DDiTFlowMatchingPipeline:
    def __init__(self):
        self.flashvdm = False

    @classmethod
    def from_pretrained(cls, model_path, subfolder, use_safetensors, device, dtype):
        return cls()

    def enable_flashvdm(self):
        self.flashvdm = True

    def to(self, device, dtype):
        return self

    def __call__(self, image, output_type, **kwargs):
        if not self.flashvdm:
            raise RuntimeError("dense volume decoding: call enable_flashvdm() first")
        return [trimesh.creation.box(extents=(1.0, 4.0, 1.0)).subdivide()]
