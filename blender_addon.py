# ruff: noqa: E402
# pyright: reportMissingImports=false
bl_info = {
    "name": "Printing MCP Bridge",
    "author": "Printing workspace",
    "version": (0, 1, 0),
    "blender": (4, 0, 0),
    "location": "View3D > Sidebar > Printing MCP",
    "description": "Lean Blender socket bridge for the printing-mcp server.",
    "category": "Interface",
}

import io
import json
import math
import os
import select
import socket
import threading
import traceback
from contextlib import redirect_stdout, suppress

import bmesh
import bpy
import mathutils
from bpy.props import BoolProperty, IntProperty, StringProperty

MM_PER_BLENDER_UNIT = 1000.0


def _report_bridge_error(context, error):
    details = "".join(traceback.format_exception(type(error), error, error.__traceback__)).strip()
    print(f"Printing MCP bridge {context}: {details}")


def _report_bridge_debug(message):
    print(f"Printing MCP bridge: {message}")


def _try_parse_command(buffer):
    try:
        return json.loads(buffer.decode("utf-8")), None
    except json.JSONDecodeError as error:
        return None, error


class PrintingMCPServer:
    def __init__(self, host="localhost", port=9876):
        self.host = host
        self.port = port
        self.running = False
        self.socket = None
        self.thread = None

    def start(self):
        if self.running:
            return

        self.running = True
        self.thread = threading.Thread(target=self._server_loop, daemon=True)
        self.thread.start()
        print(f"Printing MCP bridge listening on {self.host}:{self.port}")

    def stop(self):
        self.running = False
        if self.socket:
            with suppress(OSError):
                self.socket.close()
            self.socket = None

    def _server_loop(self):
        try:
            self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.socket.bind((self.host, self.port))
            self.socket.listen(1)

            while self.running:
                readable, _, _ = select.select([self.socket], [], [], 1.0)
                if readable:
                    try:
                        client, _address = self.socket.accept()
                    except OSError as error:
                        if self.running:
                            _report_bridge_error("accept failed", error)
                        break

                    thread = threading.Thread(
                        target=self._handle_client, args=(client,), daemon=True
                    )
                    thread.start()
        except OSError as error:
            _report_bridge_error("server loop failed", error)
            self.running = False
        finally:
            self.running = False

    def _handle_client(self, client):
        buffer = b""
        try:
            while self.running:
                data = client.recv(8192)
                if not data:
                    break
                buffer += data
                command, decode_error = _try_parse_command(buffer)
                if decode_error is not None:
                    _report_bridge_debug(f"waiting for complete JSON command: {decode_error}")
                    command = None

                if command is not None:
                    buffer = b""

                    def execute_wrapper(command_to_execute=command):
                        response = self.execute_command(command_to_execute)
                        with suppress(OSError):
                            client.sendall(json.dumps(response).encode("utf-8"))
                        return None

                    bpy.app.timers.register(execute_wrapper, first_interval=0.0)
        except OSError as error:
            _report_bridge_debug(f"client socket closed while handling request: {error}")
        finally:
            with suppress(OSError):
                client.close()

    def execute_command(self, command):
        try:
            command_type = command.get("type")
            params = command.get("params", {})
            handlers = {
                "bootstrap_model": self.bootstrap_model,
                "export_stl": self.export_stl,
                "get_capabilities": self.get_capabilities,
                "get_scene_info": self.get_scene_info,
                "get_object_info": self.get_object_info,
                "get_viewport_screenshot": self.get_viewport_screenshot,
                "review_printability": self.review_printability,
                "review_visuals": self.review_visuals,
                "validate_object": self.validate_object,
                "execute_code": self.execute_code,
            }
            handler = handlers.get(command_type)
            if handler is None:
                return {"status": "error", "message": f"Unknown command type: {command_type}"}

            result = handler(**params)
            if isinstance(result, dict) and "error" in result:
                return {"status": "error", "message": str(result["error"])}
            return {"status": "success", "result": result}
        except Exception as error:
            _report_bridge_error("command execution failed", error)
            return {"status": "error", "message": str(error)}

    def get_scene_info(self):
        scene = bpy.context.scene
        objects = []
        for obj in scene.objects:
            objects.append(
                {
                    "name": obj.name,
                    "type": obj.type,
                    "location": [round(float(value), 6) for value in obj.location],
                    "visible": bool(obj.visible_get()),
                }
            )

        return {
            "name": scene.name,
            "object_count": len(scene.objects),
            "materials_count": len(bpy.data.materials),
            "unit_system": scene.unit_settings.system,
            "length_unit": scene.unit_settings.length_unit,
            "scale_length": scene.unit_settings.scale_length,
            "objects": objects,
        }

    def get_object_info(self, name):
        obj = bpy.data.objects.get(name)
        if obj is None:
            raise ValueError(f"Object not found: {name}")

        info = {
            "name": obj.name,
            "type": obj.type,
            "location": [float(value) for value in obj.location],
            "rotation": [float(value) for value in obj.rotation_euler],
            "scale": [float(value) for value in obj.scale],
            "dimensions": [float(value) for value in obj.dimensions],
            "visible": bool(obj.visible_get()),
            "materials": [slot.material.name for slot in obj.material_slots if slot.material],
        }

        if obj.type == "MESH" and obj.data:
            info["mesh"] = {
                "vertices": len(obj.data.vertices),
                "edges": len(obj.data.edges),
                "polygons": len(obj.data.polygons),
            }
            info["world_bounding_box"] = self._world_bounding_box(obj)

        return info

    def get_capabilities(self):
        return {
            "blender_version": bpy.app.version_string,
            "has_wm_stl_export": hasattr(bpy.ops.wm, "stl_export"),
            "has_legacy_export_mesh_stl": hasattr(
                getattr(bpy.ops, "export_mesh", None),
                "stl",
            ),
            "validation": {
                "mesh_validate": True,
                "bmesh_manifold_checks": True,
                "evaluated_mesh": True,
                "printability_review": True,
                "visual_review": True,
            },
            "repo_defaults": {
                "mm_per_blender_unit": MM_PER_BLENDER_UNIT,
                "stl_global_scale": 1000.0,
                "bottom_on_z": 0.0,
            },
        }

    def bootstrap_model(self, model_name, version=1, output_root=None):
        root = output_root or "/Users/npapa/dev/printing"
        model_dir = os.path.join(root, model_name)
        version_dir = os.path.join(model_dir, f"v{version}")
        script_path = f"{version_dir}/{model_name}.py"
        blend_path = f"{version_dir}/{model_name}.blend"
        stl_path = f"{version_dir}/{model_name}.stl"
        os.makedirs(version_dir, exist_ok=True)

        bpy.ops.object.select_all(action="SELECT")
        bpy.ops.object.delete(use_global=False, confirm=False)

        scene = bpy.context.scene
        scene.unit_settings.system = "METRIC"
        scene.unit_settings.length_unit = "MILLIMETERS"
        scene.unit_settings.scale_length = 0.001

        return {
            "model_name": model_name,
            "version": version,
            "model_dir": model_dir,
            "version_dir": version_dir,
            "script_path": script_path,
            "blend_path": blend_path,
            "stl_path": stl_path,
            "unit_system": scene.unit_settings.system,
            "length_unit": scene.unit_settings.length_unit,
            "scale_length": scene.unit_settings.scale_length,
        }

    def validate_object(self, object_name, evaluated=True):
        obj = bpy.data.objects.get(object_name)
        if obj is None:
            raise ValueError(f"Object not found: {object_name}")
        if obj.type != "MESH":
            raise ValueError(f"Object is not a mesh: {object_name}")

        mesh, cleanup = self._mesh_for_validation(obj, evaluated=evaluated)
        try:
            mesh_validate_changed = bool(mesh.validate(clean_customdata=False))
            mesh.update(calc_edges=True)
            mesh.calc_loop_triangles()
            bm = bmesh.new()
            try:
                bm.from_mesh(mesh)
                bm.normal_update()
                bm.verts.ensure_lookup_table()
                bm.edges.ensure_lookup_table()
                bm.faces.ensure_lookup_table()

                non_manifold_edges = [edge for edge in bm.edges if not edge.is_manifold]
                boundary_edges = [edge for edge in bm.edges if edge.is_boundary]
                wire_edges = [edge for edge in bm.edges if edge.is_wire]
                non_contiguous_edges = [
                    edge for edge in bm.edges if edge.is_manifold and not edge.is_contiguous
                ]
                zero_area_faces = [face for face in bm.faces if face.calc_area() <= 1e-18]
                signed_volume_bu = bm.calc_volume(signed=True)
            finally:
                bm.free()

            bbox = [obj.matrix_world @ mathutils.Vector(corner) for corner in obj.bound_box]
            dimensions_mm = [
                (max(corner[index] for corner in bbox) - min(corner[index] for corner in bbox))
                * MM_PER_BLENDER_UNIT
                for index in range(3)
            ]
            min_z_mm = min(corner.z for corner in bbox) * MM_PER_BLENDER_UNIT
            volume_mm3 = signed_volume_bu * (MM_PER_BLENDER_UNIT**3)
            validation_passed = (
                len(mesh.polygons) > 0
                and not mesh_validate_changed
                and len(non_manifold_edges) == 0
                and len(boundary_edges) == 0
                and len(wire_edges) == 0
                and len(non_contiguous_edges) == 0
                and len(zero_area_faces) == 0
                and abs(volume_mm3) > 0
            )
            return {
                "object_name": obj.name,
                "evaluated": evaluated,
                "ok": validation_passed,
                "mesh": {
                    "vertices": len(mesh.vertices),
                    "edges": len(mesh.edges),
                    "polygons": len(mesh.polygons),
                    "loop_triangles": len(mesh.loop_triangles),
                },
                "issues": {
                    "mesh_validate_changed": mesh_validate_changed,
                    "non_manifold_edges": len(non_manifold_edges),
                    "boundary_edges": len(boundary_edges),
                    "wire_edges": len(wire_edges),
                    "non_contiguous_edges": len(non_contiguous_edges),
                    "zero_area_faces": len(zero_area_faces),
                },
                "metrics": {
                    "dimensions_mm": [round(value, 4) for value in dimensions_mm],
                    "min_z_mm": round(min_z_mm, 4),
                    "signed_volume_mm3": round(volume_mm3, 4),
                    "absolute_volume_mm3": round(abs(volume_mm3), 4),
                },
                "warnings": self._validation_warnings(
                    validation_passed=validation_passed,
                    min_z_mm=min_z_mm,
                    volume_mm3=volume_mm3,
                    mesh_validate_changed=mesh_validate_changed,
                    non_manifold_count=len(non_manifold_edges),
                ),
            }
        finally:
            cleanup()

    def review_printability(
        self,
        object_name,
        evaluated=True,
        bed_size_mm=(256.0, 256.0, 256.0),
        overhang_degrees=45.0,
        min_feature_mm=0.4,
        require_single_component=False,
    ):
        validation = self.validate_object(object_name, evaluated=evaluated)
        obj = bpy.data.objects.get(object_name)
        if obj is None:
            raise ValueError(f"Object not found: {object_name}")
        if obj.type != "MESH":
            raise ValueError(f"Object is not a mesh: {object_name}")

        mesh, cleanup = self._mesh_for_validation(obj, evaluated=evaluated)
        try:
            mesh.validate(clean_customdata=False)
            mesh.update(calc_edges=True)
            mesh.calc_loop_triangles()
            bm = bmesh.new()
            try:
                bm.from_mesh(mesh)
                bm.normal_update()
                bm.verts.ensure_lookup_table()
                bm.edges.ensure_lookup_table()
                bm.faces.ensure_lookup_table()

                components = self._face_components(bm)
                component_face_counts = sorted(
                    (len(component) for component in components),
                    reverse=True,
                )
                loose_vertices = [vertex for vertex in bm.verts if not vertex.link_edges]
                loose_edges = [edge for edge in bm.edges if not edge.link_faces]
                small_edges = [
                    edge
                    for edge in bm.edges
                    if edge.calc_length() * MM_PER_BLENDER_UNIT < min_feature_mm
                ]
                overhang_faces = self._overhang_faces(
                    obj,
                    bm,
                    overhang_degrees=overhang_degrees,
                )
                downward_area_mm2 = sum(face.calc_area() for face in overhang_faces) * (
                    MM_PER_BLENDER_UNIT**2
                )
            finally:
                bm.free()

            dimensions_mm = validation.get("metrics", {}).get("dimensions_mm", [0.0, 0.0, 0.0])
            min_z_mm = float(validation.get("metrics", {}).get("min_z_mm", 0.0))
            fits_bed = all(
                float(dimension) <= float(limit)
                for dimension, limit in zip(dimensions_mm, bed_size_mm, strict=False)
            )
            warnings = []
            recommendations = []

            if not validation.get("ok"):
                warnings.append("Hard mesh validation did not pass; fix this before slicing.")
                recommendations.append(
                    "Run blender_validate_object and resolve reported mesh issues."
                )

            component_count = len(components)
            if component_count > 1:
                warnings.append(f"Mesh has {component_count} disconnected closed shell components.")
                recommendations.append(
                    "Inspect the slicer preview; decorative shells may need boolean union "
                    "or intentional part separation."
                )
            if require_single_component and component_count != 1:
                recommendations.append(
                    "Rebuild or boolean-union geometry into one connected shell."
                )
            if not fits_bed:
                warnings.append(f"Model exceeds configured bed size {bed_size_mm} mm.")
                recommendations.append("Scale, split, or re-orient the model before slicing.")
            if abs(min_z_mm) > 0.01:
                warnings.append("Model bottom is not on Z=0.")
                recommendations.append("Drop the printable bottom to Z=0 before export.")
            if loose_vertices or loose_edges:
                warnings.append("Mesh contains loose vertices or edges.")
                recommendations.append("Remove loose geometry before export.")
            if overhang_faces:
                warnings.append(
                    f"Detected {len(overhang_faces)} downward overhang faces beyond "
                    f"{overhang_degrees} degrees."
                )
                recommendations.append(
                    "Review support needs, add chamfers, or re-orient the model in the slicer."
                )
            if len(small_edges) > max(25, len(mesh.edges) * 0.05):
                warnings.append(
                    f"Detected {len(small_edges)} edges shorter than {min_feature_mm} mm."
                )
                recommendations.append(
                    "Check tiny details in slicer preview; they may not print with a 0.4 mm nozzle."
                )

            review_ok = (
                bool(validation.get("ok"))
                and fits_bed
                and not loose_vertices
                and not loose_edges
                and (not require_single_component or component_count == 1)
            )

            return {
                "object_name": obj.name,
                "ok": review_ok,
                "needs_attention": bool(warnings or recommendations),
                "validation": validation,
                "bed_fit": {
                    "fits": fits_bed,
                    "bed_size_mm": [float(value) for value in bed_size_mm],
                    "dimensions_mm": dimensions_mm,
                },
                "components": {
                    "count": component_count,
                    "face_counts": component_face_counts[:20],
                    "require_single_component": require_single_component,
                },
                "overhangs": {
                    "threshold_degrees": overhang_degrees,
                    "face_count": len(overhang_faces),
                    "downward_area_mm2": round(downward_area_mm2, 4),
                },
                "small_features": {
                    "threshold_mm": min_feature_mm,
                    "short_edge_count": len(small_edges),
                },
                "loose_geometry": {
                    "vertices": len(loose_vertices),
                    "edges": len(loose_edges),
                },
                "warnings": warnings,
                "recommendations": recommendations,
            }
        finally:
            cleanup()

    def export_stl(
        self,
        object_name,
        target_path,
        global_scale=1000.0,
        validate=True,
        allow_non_manifold=False,
    ):
        obj = bpy.data.objects.get(object_name)
        if obj is None:
            raise ValueError(f"Object not found: {object_name}")
        if obj.type != "MESH":
            raise ValueError(f"Object is not a mesh: {object_name}")

        validation = self.validate_object(object_name, evaluated=True) if validate else None
        if validate and validation and not validation["ok"] and not allow_non_manifold:
            return {
                "error": (
                    "Validation failed; refusing STL export. "
                    "Pass allow_non_manifold=True to override."
                ),
                "validation": validation,
            }

        target_dir = os.path.dirname(target_path)
        if target_dir:
            os.makedirs(target_dir, exist_ok=True)
        bpy.ops.object.select_all(action="DESELECT")
        obj.select_set(True)
        bpy.context.view_layer.objects.active = obj

        export_mesh_ops = getattr(bpy.ops, "export_mesh", None)
        if hasattr(bpy.ops.wm, "stl_export"):
            bpy.ops.wm.stl_export(
                filepath=target_path,
                export_selected_objects=True,
                global_scale=global_scale,
            )
            exporter = "bpy.ops.wm.stl_export"
        elif hasattr(export_mesh_ops, "stl"):
            export_mesh_ops.stl(
                filepath=target_path,
                use_selection=True,
                global_scale=global_scale,
            )
            exporter = "bpy.ops.export_mesh.stl"
        else:
            raise RuntimeError("No STL exporter is available in this Blender build.")

        return {
            "object_name": obj.name,
            "target_path": target_path,
            "size_bytes": os.path.getsize(target_path),
            "global_scale": global_scale,
            "exporter": exporter,
            "validation": validation,
        }

    def get_viewport_screenshot(self, filepath=None, max_size=800, format="png"):
        if not filepath:
            return {"error": "No filepath provided"}

        area = next((item for item in bpy.context.screen.areas if item.type == "VIEW_3D"), None)
        if area is None:
            return {"error": "No 3D viewport found"}

        with bpy.context.temp_override(area=area):
            self._set_select_tool()
            bpy.ops.screen.screenshot_area(filepath=filepath)

        image = bpy.data.images.load(filepath)
        width, height = image.size
        if max(width, height) > max_size:
            scale = max_size / max(width, height)
            width = int(width * scale)
            height = int(height * scale)
            image.scale(width, height)
            image.file_format = format.upper()
            image.save()

        bpy.data.images.remove(image)
        return {"success": True, "filepath": filepath, "width": width, "height": height}

    def review_visuals(
        self,
        object_name,
        output_dir,
        max_size=1200,
        include_top=True,
        include_front=True,
        include_right=True,
        include_isometric=True,
    ):
        obj = bpy.data.objects.get(object_name)
        if obj is None:
            raise ValueError(f"Object not found: {object_name}")
        if not 100 <= int(max_size) <= 4096:
            raise ValueError("max_size must be between 100 and 4096 pixels.")

        os.makedirs(output_dir, exist_ok=True)
        area = next((item for item in bpy.context.screen.areas if item.type == "VIEW_3D"), None)
        if area is None:
            return {"error": "No 3D viewport found"}
        region = next((item for item in area.regions if item.type == "WINDOW"), None)
        space = next((item for item in area.spaces if item.type == "VIEW_3D"), None)
        if region is None or space is None:
            return {"error": "No usable 3D viewport region found"}

        views = []
        if include_front:
            views.append(("front", "FRONT"))
        if include_right:
            views.append(("right", "RIGHT"))
        if include_top:
            views.append(("top", "TOP"))
        if include_isometric:
            views.append(("isometric", "ISOMETRIC"))
        if not views:
            raise ValueError("At least one visual review view must be enabled.")

        bpy.ops.object.select_all(action="DESELECT")
        obj.select_set(True)
        bpy.context.view_layer.objects.active = obj

        screenshots = []
        with bpy.context.temp_override(area=area, region=region, space_data=space):
            self._set_select_tool()
            for view_name, view_axis in views:
                if view_axis == "ISOMETRIC":
                    region_3d = space.region_3d
                    region_3d.view_perspective = "ORTHO"
                    region_3d.view_rotation = mathutils.Euler(
                        (math.radians(60.0), 0.0, math.radians(45.0)),
                        "XYZ",
                    ).to_quaternion()
                    bpy.ops.view3d.view_selected(use_all_regions=False)
                else:
                    bpy.ops.view3d.view_axis(type=view_axis, align_active=False)
                    bpy.ops.view3d.view_selected(use_all_regions=False)

                filepath = os.path.join(output_dir, f"{object_name}_{view_name}.png")
                bpy.ops.screen.screenshot_area(filepath=filepath)
                image = bpy.data.images.load(filepath)
                width, height = image.size
                if max(width, height) > max_size:
                    scale = max_size / max(width, height)
                    width = int(width * scale)
                    height = int(height * scale)
                    image.scale(width, height)
                    image.file_format = "PNG"
                    image.save()
                bpy.data.images.remove(image)
                screenshots.append(
                    {
                        "view": view_name,
                        "filepath": filepath,
                        "width": width,
                        "height": height,
                    }
                )

        world_bounds = self._world_bounding_box(obj)
        dimensions_mm = [
            round((world_bounds[1][index] - world_bounds[0][index]) * MM_PER_BLENDER_UNIT, 4)
            for index in range(3)
        ]
        min_z_mm = round(world_bounds[0][2] * MM_PER_BLENDER_UNIT, 4)
        checklist = [
            "Inspect front/isometric views for overall silhouette and theme readability.",
            "Inspect side/top views for accidental blobs, floating features, and bad proportions.",
            "Confirm the entrance is visible and large enough for the intended animal.",
            "Confirm the model sits on Z=0 and fits the printer bed.",
        ]
        warnings = []
        if abs(min_z_mm) > 0.01:
            warnings.append("Object bottom is not on Z=0.")

        return {
            "object_name": obj.name,
            "output_dir": output_dir,
            "screenshots": screenshots,
            "dimensions_mm": dimensions_mm,
            "min_z_mm": min_z_mm,
            "checklist": checklist,
            "warnings": warnings,
        }

    def execute_code(self, code):
        namespace = {"bpy": bpy}
        capture_buffer = io.StringIO()
        with redirect_stdout(capture_buffer):
            exec(code, namespace)  # nosec B102 - this is the explicit Blender code-execution tool.
        return {"executed": True, "result": capture_buffer.getvalue()}

    @staticmethod
    def _set_select_tool():
        with suppress(Exception):
            bpy.ops.wm.tool_set_by_id(name="builtin.select_box")

    @staticmethod
    def _world_bounding_box(obj):
        corners = [obj.matrix_world @ mathutils.Vector(obj.bound_box[index]) for index in range(8)]
        return [
            [min(corner[index] for corner in corners) for index in range(3)],
            [max(corner[index] for corner in corners) for index in range(3)],
        ]

    @staticmethod
    def _mesh_for_validation(obj, evaluated=True):
        if evaluated:
            depsgraph = bpy.context.evaluated_depsgraph_get()
            evaluated_obj = obj.evaluated_get(depsgraph)
            mesh = evaluated_obj.to_mesh(preserve_all_data_layers=True, depsgraph=depsgraph)

            def cleanup():
                evaluated_obj.to_mesh_clear()

            return mesh, cleanup

        mesh = obj.data.copy()

        def cleanup():
            bpy.data.meshes.remove(mesh)

        return mesh, cleanup

    @staticmethod
    def _validation_warnings(
        *,
        validation_passed,
        min_z_mm,
        volume_mm3,
        mesh_validate_changed,
        non_manifold_count,
    ):
        warnings = []
        if mesh_validate_changed:
            warnings.append("mesh.validate reported and fixed invalid mesh data.")
        if non_manifold_count:
            warnings.append("Object has non-manifold edges and may slice incorrectly.")
        if abs(min_z_mm) > 0.01:
            warnings.append("Printable bottom is not on Z=0.")
        if volume_mm3 < 0:
            warnings.append("Signed volume is negative; normals may be inverted.")
        if not validation_passed:
            warnings.append("Validation did not pass; inspect issues before exporting.")
        return warnings

    @staticmethod
    def _face_components(bm):
        components = []
        remaining = set(bm.faces)
        while remaining:
            start = remaining.pop()
            component = {start}
            stack = [start]
            while stack:
                face = stack.pop()
                for edge in face.edges:
                    for linked_face in edge.link_faces:
                        if linked_face in remaining:
                            remaining.remove(linked_face)
                            component.add(linked_face)
                            stack.append(linked_face)
            components.append(component)
        return components

    @staticmethod
    def _overhang_faces(obj, bm, *, overhang_degrees):
        threshold = -math.cos(math.radians(overhang_degrees))
        normal_matrix = obj.matrix_world.to_3x3().inverted().transposed()
        overhang_faces = []
        for face in bm.faces:
            world_normal = normal_matrix @ face.normal
            world_normal.normalize()
            if world_normal.z < threshold:
                overhang_faces.append(face)
        return overhang_faces


class PRINTINGMCP_PT_Panel(bpy.types.Panel):
    bl_label = "Printing MCP"
    bl_idname = "PRINTINGMCP_PT_panel"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Printing MCP"

    def draw(self, context):
        layout = self.layout
        scene = context.scene
        layout.prop(scene, "printing_mcp_host")
        layout.prop(scene, "printing_mcp_port")
        if scene.printing_mcp_server_running:
            layout.operator("printing_mcp.stop_server", text="Stop Printing MCP Bridge")
            layout.label(text=f"Running on {scene.printing_mcp_host}:{scene.printing_mcp_port}")
        else:
            layout.operator("printing_mcp.start_server", text="Start Printing MCP Bridge")


class PRINTINGMCP_OT_StartServer(bpy.types.Operator):
    bl_idname = "printing_mcp.start_server"
    bl_label = "Start Printing MCP Bridge"

    def execute(self, context):
        scene = context.scene
        if not hasattr(bpy.types, "printing_mcp_server") or not bpy.types.printing_mcp_server:
            bpy.types.printing_mcp_server = PrintingMCPServer(
                host=scene.printing_mcp_host,
                port=scene.printing_mcp_port,
            )
        bpy.types.printing_mcp_server.start()
        scene.printing_mcp_server_running = True
        return {"FINISHED"}


class PRINTINGMCP_OT_StopServer(bpy.types.Operator):
    bl_idname = "printing_mcp.stop_server"
    bl_label = "Stop Printing MCP Bridge"

    def execute(self, context):
        scene = context.scene
        if hasattr(bpy.types, "printing_mcp_server") and bpy.types.printing_mcp_server:
            bpy.types.printing_mcp_server.stop()
            del bpy.types.printing_mcp_server
        scene.printing_mcp_server_running = False
        return {"FINISHED"}


CLASSES = (
    PRINTINGMCP_PT_Panel,
    PRINTINGMCP_OT_StartServer,
    PRINTINGMCP_OT_StopServer,
)


def register():
    bpy.types.Scene.printing_mcp_host = StringProperty(
        name="Host",
        default="localhost",
        description="Host for the local Printing MCP bridge",
    )
    bpy.types.Scene.printing_mcp_port = IntProperty(
        name="Port",
        default=9876,
        min=1024,
        max=65535,
        description="Port for the local Printing MCP bridge",
    )
    bpy.types.Scene.printing_mcp_server_running = BoolProperty(
        name="Server Running",
        default=False,
    )
    for cls in CLASSES:
        bpy.utils.register_class(cls)


def unregister():
    if hasattr(bpy.types, "printing_mcp_server") and bpy.types.printing_mcp_server:
        bpy.types.printing_mcp_server.stop()
        del bpy.types.printing_mcp_server
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
    del bpy.types.Scene.printing_mcp_host
    del bpy.types.Scene.printing_mcp_port
    del bpy.types.Scene.printing_mcp_server_running


if __name__ == "__main__":
    register()
