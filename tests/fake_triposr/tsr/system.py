"""Stand-in for TripoSR's tsr.system: returns a 1 mm unit cube of 48 triangles."""

import trimesh


class _Renderer:
    def set_chunk_size(self, size):
        self.chunk_size = size


class TSR:
    def __init__(self):
        self.renderer = _Renderer()

    @classmethod
    def from_pretrained(cls, model_id, config_name, weight_name):
        return cls()

    def to(self, device):
        return self

    def __call__(self, images, device):
        return images

    def extract_mesh(self, scene_codes, has_vertex_color):
        return [trimesh.creation.box(extents=(1.0, 1.0, 1.0)).subdivide()]
