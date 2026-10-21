import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from printing_mcp.config import LOCAL_MESH_PYTHON_ENV, TRIPOSR_DIR_ENV
from printing_mcp.generation.backend_client import GenerationBackendClient
from printing_mcp.generation.dependencies import GenerationDependencies
from printing_mcp.generation.jobs import GenerationJobStore
from printing_mcp.generation.mesh import GenerationMeshRunner
from printing_mcp.generation.schemas import GenerationCreateMeshRequest

FAKE_TRIPOSR_DIR = Path(__file__).resolve().parent / "fake_triposr"


class LocalMeshProviderTest(unittest.TestCase):
    def setUp(self):
        self._saved_env = {
            name: os.environ.get(name)
            for name in (
                LOCAL_MESH_PYTHON_ENV,
                TRIPOSR_DIR_ENV,
                "PRINTING_GENERATION_DRY_RUN",
                "FAKE_TSR_ERROR",
            )
        }
        os.environ["PRINTING_GENERATION_DRY_RUN"] = ""
        workspace = tempfile.TemporaryDirectory()
        self.addCleanup(workspace.cleanup)
        self.workspace = Path(workspace.name)
        self.generation_dir = self.workspace / "cube" / "v1" / "generation"
        self.generation_dir.mkdir(parents=True)
        Image.new("RGB", (8, 8), (255, 255, 255)).save(self.generation_dir / "cube.png")
        self.runner = GenerationMeshRunner(
            dependencies=GenerationDependencies(
                workspace_root=self.workspace,
                backend_client=GenerationBackendClient(),
                job_store=GenerationJobStore(),
            )
        )

    def tearDown(self):
        for name, value in self._saved_env.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value

    def create_mesh(self):
        return self.runner.create_mesh(
            GenerationCreateMeshRequest(
                model_name="cube",
                version=1,
                source_image_path=str(self.generation_dir / "cube.png"),
                output_raw_stl_path=str(self.generation_dir / "cube_raw.stl"),
                output_raw_glb_path=str(self.generation_dir / "cube_raw.glb"),
                target_size_mm=(20.0, 30.0, 40.0),
                provider="local",
            )
        )

    def test_local_provider_writes_scaled_mesh_and_provenance(self):
        os.environ[LOCAL_MESH_PYTHON_ENV] = sys.executable
        os.environ[TRIPOSR_DIR_ENV] = str(FAKE_TRIPOSR_DIR)

        result = self.create_mesh()

        self.assertTrue(result.ok, result.message)
        data = result.data or {}
        self.assertEqual(data["provider"], "local")
        self.assertEqual(data["estimated_cost_usd"], 0.0)
        self.assertEqual(
            data["mesh"],
            {
                "units": "mm",
                "target_size_mm": [20.0, 30.0, 40.0],
                "actual_size_mm": [20.0, 20.0, 20.0],
                "scale_factor": 20.0,
                "triangle_count": 48,
            },
        )
        self.assertTrue((self.generation_dir / "cube_raw.stl").stat().st_size > 84)
        self.assertTrue((self.generation_dir / "cube_raw.glb").is_file())
        record = json.loads((self.generation_dir / "generation.json").read_text())
        outputs = record["calls"][-1]["outputs"]
        self.assertEqual(outputs["device"], "cpu")
        self.assertEqual(outputs["estimated_cost_usd"], 0.0)

    def test_local_auth_failure_is_not_reported_as_modal_auth(self):
        os.environ[LOCAL_MESH_PYTHON_ENV] = sys.executable
        os.environ[TRIPOSR_DIR_ENV] = str(FAKE_TRIPOSR_DIR)
        os.environ["FAKE_TSR_ERROR"] = "401 Client Error: invalid Hugging Face token"

        result = self.create_mesh()

        self.assertFalse(result.ok)
        data = result.data or {}
        self.assertEqual(data["error_code"], "provider_job_failed")
        self.assertIn("invalid Hugging Face token", result.message or "")

    def test_local_provider_without_config_fails_with_setup_hint(self):
        os.environ[LOCAL_MESH_PYTHON_ENV] = ""
        os.environ[TRIPOSR_DIR_ENV] = ""

        result = self.create_mesh()

        self.assertFalse(result.ok)
        self.assertIn(
            "Set PRINTING_LOCAL_MESH_PYTHON and PRINTING_TRIPOSR_DIR", result.message or ""
        )


if __name__ == "__main__":
    unittest.main()
