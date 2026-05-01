# Printing MCP

Printing MCP is an all-in-one Model Context Protocol server for desktop
3D-printing workflows. It connects MCP clients to Blender, OrcaSlicer-compatible
slicers, generated mesh validation, and Elegoo LAN printer control.

The server registers as `printing` and provides:

- Blender modeling tools through a lightweight local socket bridge.
- OrcaSlicer CLI tools for inspecting, exporting, and slicing models.
- Geometry authoring guidance plus standalone mesh and G-code validation.
- Elegoo LAN-only tools for discovery, upload, print start, pause, stop, resume,
  cancel, and filament load/unload.
- Shared status, config, last-run, and workflow resources.

This project is designed to run locally. It does not proxy printer traffic
through a hosted service.

## Repository Setup

Clone this repository, then install the Python environment with `uv`:

```sh
git clone https://github.com/nicholas-papachriston/printing-mcp.git
cd printing-mcp
uv sync
```

For local shell usage, copy `.env.example` to `.env` and fill in only the values
you need. MCP clients can also pass the same values through their own
configuration.

## Requirements

- Python `>=3.11`
- [`uv`](https://docs.astral.sh/uv/) / `uvx`
- Blender, for modeling tools
- OrcaSlicer, for slicing/export tools
- An Elegoo printer on the same LAN, for machine interaction tools

OrcaSlicer can be found on `PATH`, in common install locations, or through
`ORCASLICER_BIN`. `ORCASLICER_BIN` may point to the executable, the install
folder, or a macOS `.app` bundle.

Elegoo machine interaction is LAN-only. This package does not use Elegoo cloud,
WAN device lists, Agora RTM, cloud MQTT, or object-storage upload flows.

## Install The Blender Addon

The MCP server can start without Blender, but Blender tools only work after the
addon bridge is running inside Blender.

1. Open Blender.
2. Go to `Edit > Preferences > Add-ons > Install...`.
3. Select `blender_addon.py` from this directory.
4. Enable **Interface: Printing MCP Bridge**.
5. Save preferences.

Start it each Blender session:

1. Open the 3D Viewport sidebar with `N`.
2. Open the **Printing MCP** tab.
3. Click **Start Printing MCP Bridge**.
4. Confirm it shows `Running on localhost:9876`.

## Register The MCP Server

Use `uvx --from` to run Printing MCP from a local clone or from a Git URL.
Replace `/path/to/printing-mcp` with your clone path.

### Cursor

Add this to `~/.cursor/mcp.json`:

```json
{
  "mcpServers": {
    "printing": {
      "command": "uvx",
      "args": [
        "--from",
        "git+https://github.com/nicholas-papachriston/printing-mcp.git",
        "printing-mcp"
      ],
      "env": {
        "ORCASLICER_BIN": "/Applications/OrcaSlicer.app/Contents/MacOS/OrcaSlicer",
        "ELEGOO_PRINTER_HOST": "192.168.1.50",
        "ELEGOO_PRINTER_TYPE": "cc2"
      }
    }
  }
}
```

If OrcaSlicer is already on `PATH`, remove `ORCASLICER_BIN`. If you prefer
passing the printer host per tool call, remove the `ELEGOO_*` entries.

### VS Code

For VS Code MCP configuration, use the `servers` key:

```json
{
  "servers": {
    "printing": {
      "type": "stdio",
      "command": "uvx",
      "args": [
        "--from",
        "git+https://github.com/nicholas-papachriston/printing-mcp.git",
        "printing-mcp"
      ],
      "env": {
        "ORCASLICER_BIN": "/Applications/OrcaSlicer.app/Contents/MacOS/OrcaSlicer",
        "ELEGOO_PRINTER_HOST": "192.168.1.50",
        "ELEGOO_PRINTER_TYPE": "cc2"
      }
    }
  }
}
```

## Environment Variables

- `BLENDER_HOST` - Blender bridge host, default `localhost`.
- `BLENDER_PORT` - Blender bridge port, default `9876`.
- `BLENDER_TIMEOUT_SECONDS` - Blender command timeout, default `180`.
- `ELEGOO_PRINTER_HOST` - optional LAN printer IP/host for machine tools.
- `ELEGOO_PRINTER_TYPE` - `cc2` for Centauri Carbon 2 or `cc` for Centauri /
  Centauri Carbon.
- `ELEGOO_ACCESS_CODE` - optional LAN access code for printers requiring auth.
- `ELEGOO_SERIAL_NUMBER` - optional CC2 serial number; fetched over LAN if omitted.
- `ELEGOO_MAINBOARD_ID` - optional Centauri mainboard ID; required for `cc`
  WebSocket commands unless passed per request.
- `ELEGOO_TIMEOUT_SECONDS` - LAN command/upload timeout, default `30`.
- `ELEGOO_DISCOVERY_TIMEOUT_SECONDS` - UDP discovery timeout, default `8`.
- `ELEGOO_UPLOAD_CHUNK_BYTES` - upload chunk size, max/default `1048576`.
- `ORCASLICER_BIN` - path to the OrcaSlicer executable, install folder, or
  macOS `.app` bundle. On Windows, prefer `orca-slicer-console.exe` if your
  install includes it; otherwise `orca-slicer.exe` is the normal CLI-capable
  binary in current releases.
- `ORCASLICER_DATADIR` - optional OrcaSlicer profile/data directory.
- `ORCASLICER_TIMEOUT_SECONDS` - OrcaSlicer command timeout, default `900`.
- `PRINTING_GENERATION_MODAL_APP` - Modal app name for remote generation, default
  `printing-generation`.
- `PRINTING_GENERATION_MAX_IMAGE_COST_USD` - default text-to-image cost ceiling.
- `PRINTING_GENERATION_MAX_FAST_MESH_COST_USD` - default fast mesh cost ceiling.
- `PRINTING_GENERATION_MAX_QUALITY_MESH_COST_USD` - default quality mesh cost
  ceiling.
- `PRINTING_GENERATION_MAX_ARTIFACT_MB` - maximum returned generation artifact
  payload size.
- `MODAL_TOKEN_ID` / `MODAL_TOKEN_SECRET` - optional Modal headless auth. Leave
  unset when using local Modal CLI auth.
- `GEMINI_API_KEY` or `GOOGLE_API_KEY` - Google Gemini API key used by
  `generation_create_image` with the configured image model.
- `PRINTING_GENERATION_DRY_RUN` - set to `1` only when you intentionally want
  placeholder mesh artifacts instead of calling Modal.

See `.env.example` for a copyable non-secret local template.

## Tools

Blender tools:

- `blender_bootstrap_model` - reset the scene and create a standard
  `<model>/v<N>/` folder.
- `blender_get_capabilities` - report bridge features, Blender version, and STL
  exporter availability.
- `blender_get_scene_info` - inspect current scene units, objects, and counts.
- `blender_get_object_info` - inspect one object's transform, dimensions, and
  mesh/material summary.
- `blender_validate_object` - run hard manifold, boundary, volume, and Z
  placement validation.
- `blender_review_printability` - check softer print risks like disconnected
  shells, overhangs, bed fit, and tiny features.
- `blender_review_visuals` - save front, right, top, and isometric screenshots
  for human visual review.
- `blender_export_stl` - validate and export one Blender object through
  Blender's STL exporter.
- `blender_execute_code` - run saved, file-backed Python in Blender for geometry
  creation or scene edits.
- `blender_get_viewport_screenshot` - return the current viewport as a PNG image.

Printing/slicer tools:

- `printing_status` - check Blender, Elegoo LAN, and OrcaSlicer availability.
- `geometry_authoring_capabilities` - return recommended geometry authoring
  routes, installed library availability, and reusable Blender/build123d starter
  snippets for agents.
- `mesh_validate_artifact` - validate an on-disk STL/OBJ/GLB/3MF artifact with
  `trimesh` before slicing or after export.
- `gcode_validate_file` - validate a sliced `.gcode` / `.bgcode` file with
  `gcode-lib` before upload or print start.
- `generation_warm_backend` - explicitly prepare the generation backend.
- `generation_create_image` - create a source image from a prompt with Nano
  Banana 2 when `GEMINI_API_KEY` / `GOOGLE_API_KEY` is configured, then return a
  generation job ID.
- `generation_remove_background` - create a local foreground cutout image and
  append a `generation/generation.json` call entry.
- `generation_create_mesh` - call Modal TripoSR to create scaled raw fast-tier
  STL/GLB artifacts under `<model>/v<N>/generation/` and return a generation job
  ID. Set `PRINTING_GENERATION_DRY_RUN=1` for placeholder artifacts.
- `generation_get_job` - poll a generation job and return completed artifact
  paths.
- `generation_cancel_job` - request cancellation for a non-terminal generation
  job.

Generated raw meshes must still be imported into Blender, validated, reviewed,
and exported with `blender_export_stl` before slicing. The helper module
`printing_mcp.generation.blender_handoff` can write the file-backed Blender
import script used for that handoff.

## Generation Backend

Deploy the Modal backend from this directory:

```sh
uv run modal deploy src/printing_mcp/generation_backend/modal_app.py
```

Run a real TripoSR smoke test:

```sh
uv run modal run src/printing_mcp/generation_backend/modal_app.py::modal_smoke \
  --prompt "single printable figurine, white background" \
  --target-size-mm 60,50,60 \
  --output-dir /tmp/printing-generation-real \
  --max-cost-usd 0.25 \
  --real
```

The first real run builds a CUDA Modal image and downloads the TripoSR checkpoint
into the Modal Hugging Face cache, so it is much slower than later runs. A known
good smoke produced `dry_run: false`, `mesh_model: triposr`, and about 184k
triangles. Raw output is intentionally named `*_raw.stl` / `*_raw.glb`; do not
slice it directly. Import it into Blender, clean/review as needed, then export the
canonical `<model>.stl` with `blender_export_stl`.

- `orcaslicer_help` - return OrcaSlicer CLI help text.
- `orcaslicer_slice_model` - slice STL/3MF input files and return generated
  G-code/project files. Set `validate_gcode: true` to run the generated G-code
  through the same post-slice validator before accepting the result.
- `orcaslicer_export_3mf` - export model inputs into an OrcaSlicer 3MF project.
- `orcaslicer_export_stl` - convert supported model/project inputs to STL via
  OrcaSlicer.
- `orcaslicer_inspect_model` - return OrcaSlicer model information output.
- `orcaslicer_export_settings` - export resolved OrcaSlicer settings to JSON.

Elegoo LAN machine tools:

- `elegoo_discover_lan_printers` - find Centauri printers on the LAN over UDP.
- `elegoo_get_status` - get current LAN printer status.
- `elegoo_upload_file` - upload a sliced file over LAN without cloud staging.
- `elegoo_upload_and_start_print` - upload a local file and start it.
- `elegoo_start_print` - start a file already present on the printer.
- `elegoo_pause_print` - pause the active LAN print.
- `elegoo_resume_print` - resume the active paused LAN print.
- `elegoo_cancel_print` - cancel the active LAN print and return toward idle.
- `elegoo_stop_print` - stop the active LAN print.
- `elegoo_load_filament` - start the printer's built-in filament load routine.
- `elegoo_unload_filament` - start the printer's built-in filament unload routine.

Resources:

- `printing://config`
- `printing://last-run`
- `printing://workflow`

## Verify Locally

From this directory:

```sh
uv sync
uv run ruff format --check src blender_addon.py
uv run ruff check src blender_addon.py
uv run ty check src
uv run python -m compileall src
uv run python -m py_compile blender_addon.py
```

Smoke-test MCP registration:

```sh
uv run python - <<'PY'
import asyncio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

async def main():
    params = StdioServerParameters(command="uv", args=["run", "printing-mcp"])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            print([tool.name for tool in tools.tools])

asyncio.run(main())
PY
```

Then call `printing_status` from your MCP client. Blender should report
available once the addon bridge is started. OrcaSlicer should report available
once `ORCASLICER_BIN` is set or the binary is discoverable. Elegoo should report
available once `ELEGOO_PRINTER_HOST` and `ELEGOO_PRINTER_TYPE` are configured
or when you pass those fields directly to an Elegoo tool.

## Blender Reliability Workflow

Use the structured Blender tools for repeatable modeling/export gates, and keep
`blender_execute_code` for custom geometry construction.

1. Start a `<model>/v<N>/` folder and reset scene units with
   `blender_bootstrap_model`.
2. Write the Blender Python to `<model>/v<N>/<model>.py` before executing it.
   Build geometry with mesh/object data APIs, `bmesh`, and Boolean modifiers
   with `solver="EXACT"` for overlapping solids.
3. For organic or decorative models, write down the design intent before
   modeling: subject, style, target size, base/no-base choice, and the key
   silhouette features that must read clearly from multiple angles.
4. Execute the saved file contents with `blender_execute_code`. When iterating,
   edit that file first and then execute the updated file contents again. If the
   script uses `if __name__ == "__main__":`, execute it with `__name__` set to
   `"__main__"` and `__file__` set to the saved script path.
5. Ensure decorative details such as spines, horns, eyes, labels, scales, and
   feet intersect the body by at least `1 mm`, are joined/remeshed into a single
   shell, or are exported as intentionally separate parts. If printability
   review still reports disconnected components, inspect component bounding
   boxes and use Exact Boolean `UNION`; object join alone is not a topology
   guarantee for overlapping closed meshes.
6. Run `blender_validate_object` on the final mesh. A passing object is manifold,
   has no boundary/wire/non-contiguous edges, has non-zero volume, and reports
   dimensions in millimeters.
7. Run `blender_review_printability` before slicing. This catches softer issues
   that can still need manual tweaks, including disconnected shell components,
   overhang-heavy geometry, bed-fit problems, loose geometry, tiny-feature
   candidates, and bottoms not on `Z=0`.
8. Run `blender_review_visuals` and inspect the saved front, right, top, and
   isometric screenshots. This is the check that catches ugly proportions,
   accidental blobs, missing features, and silhouette problems that mesh
   validation cannot detect.
9. Export with `blender_export_stl`. It validates by default, refuses invalid
   meshes unless `allow_non_manifold` is explicitly set, and uses
   `bpy.ops.wm.stl_export` when available.

Example saved-script execution wrapper:

```python
script_path = "/path/to/workspace/example/v1/example.py"
namespace = {"__name__": "__main__", "__file__": script_path}
exec(compile(open(script_path, "r", encoding="utf-8").read(), script_path, "exec"), namespace)
```

Example `blender_validate_object` request:

```json
{
  "request": {
    "object_name": "final_part",
    "evaluated": true
  }
}
```

Example `blender_review_printability` request:

```json
{
  "request": {
    "object_name": "final_part",
    "evaluated": true,
    "bed_size_mm": [256.0, 256.0, 256.0],
    "overhang_degrees": 45.0,
    "min_feature_mm": 0.4,
    "require_single_component": false
  }
}
```

Example `blender_review_visuals` request:

```json
{
  "request": {
    "object_name": "final_part",
    "output_dir": "/path/to/workspace/example/v1/visual_review",
    "max_size": 1200
  }
}
```

Example `blender_export_stl` request:

```json
{
  "request": {
    "object_name": "final_part",
    "target_path": "/path/to/workspace/example/v1/example.stl",
    "global_scale": 1000.0,
    "validate_mesh": true
  }
}
```

When changing `blender_addon.py`, restart the Blender-side Printing MCP bridge
as well as the MCP server. The server can reconnect to Blender, but it cannot
reload addon code that is already running inside Blender.

## Artifact And G-code Validation

Use `geometry_authoring_capabilities` before complex shape work when the right
authoring route is unclear. It reports whether `build123d`, `trimesh`, and
`gcode-lib` are available and returns copyable starter snippets for file-backed
Blender scripts or build123d parametric CAD.

Use `mesh_validate_artifact` as a fast artifact gate for generated or exported
mesh files. It checks watertightness, volume, connected components, dimensions,
bed fit, face count limits, winding consistency, and optional `Z=0` placement.
This does not replace Blender review; raw generated meshes still need Blender
cleanup, visual review, and canonical STL export.

Use `gcode_validate_file` after slicing and before upload. It parses moves and
layers, checks extrusion presence, XY/Z bounds, nozzle/bed temperature ceilings,
optional first-wait temperature ranges, and optional time/material budgets. For
OrcaSlicer runs, pass `validate_gcode: true` to `orcaslicer_slice_model` and set
`gcode_validation` thresholds inline:

```json
{
  "request": {
    "model_paths": ["/path/to/workspace/example/v1/example.stl"],
    "output_dir": "/path/to/workspace/example/v1/sliced",
    "validate_gcode": true,
    "gcode_validation": {
      "min_wait_nozzle_temp_c": 200,
      "max_wait_nozzle_temp_c": 215,
      "min_wait_bed_temp_c": 55,
      "max_wait_bed_temp_c": 70,
      "max_filament_weight_g": 80
    }
  }
}
```

## Elegoo LAN Workflow

Discover printers first with `elegoo_discover_lan_printers`:

```json
{
  "request": {
    "timeout_seconds": 8,
    "include_cc2": true,
    "include_cc": true
  }
}
```

Use the discovered `host`, `printer_type`, `serial_number`, and `mainboard_id`
in later calls.

Check status with `elegoo_get_status`:

```json
{
  "request": {
    "host": "192.168.1.50",
    "printer_type": "cc2",
    "access_code": "123456"
  }
}
```

Upload a local sliced file over LAN with `elegoo_upload_file`:

```json
{
  "request": {
    "host": "192.168.1.50",
    "printer_type": "cc2",
    "file_path": "/path/to/workspace/model/v1/model.gcode"
  }
}
```

Before uploading custom-sliced PLA, inspect the generated G-code temperature
commands. The CC2 profile can use `cool_plate_temp` for the selected bed type,
so process JSON must set `cool_plate_temp`, `hot_plate_temp`, and
`textured_plate_temp` plus their `_initial_layer` variants. A good PLA slice
should show first-layer nozzle/bed waits around `M109 S205-210` and
`M190 S60-65`; `M190 S35` means the bed temperature inherited from the wrong
plate key.

Start the uploaded file with `elegoo_start_print`:

```json
{
  "request": {
    "host": "192.168.1.50",
    "printer_type": "cc2",
    "file_name": "model.gcode",
    "storage": "local",
    "heated_bed_leveling": true,
    "time_lapse": false
  }
}
```

For Centauri / Centauri Carbon (`printer_type: "cc"`), include `mainboard_id`
from discovery for status and print commands.
