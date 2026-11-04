"""Compact CadQuery's per-face draw calls without altering CAD mesh geometry."""
import json
import struct
import numpy as np


def node_transform(node):
    """Return a glTF node's local transform as a conventional row-indexed matrix."""
    if "matrix" in node:
        if any(key in node for key in ("rotation", "translation", "scale")):
            raise ValueError("GLB node mixes matrix and TRS transforms")
        result = np.asarray(node["matrix"], dtype=float).reshape(4, 4).T
    else:
        quaternion = np.asarray(node.get("rotation", [0, 0, 0, 1]), dtype=float)
        length = np.linalg.norm(quaternion)
        if length < 1e-12:
            raise ValueError("GLB node has zero rotation quaternion")
        x, y, z, w = quaternion / length
        rotation = np.array([
            [1 - 2 * (y*y + z*z), 2 * (x*y - z*w), 2 * (x*z + y*w)],
            [2 * (x*y + z*w), 1 - 2 * (x*x + z*z), 2 * (y*z - x*w)],
            [2 * (x*z - y*w), 2 * (y*z + x*w), 1 - 2 * (x*x + y*y)],
        ])
        result = np.eye(4)
        result[:3, :3] = rotation @ np.diag(node.get("scale", [1, 1, 1]))
        result[:3, 3] = node.get("translation", [0, 0, 0])
    if not np.isfinite(result).all():
        raise ValueError("GLB transform must be finite")
    return result


def scene_coordinate_frame(document):
    """Expose the actual single assembly root frame for external animation.

    CadQuery exports CAD XYZ through a scene root, rather than baking a fixed
    axis conversion into vertices. Consumers must read this root or metadata;
    they must not infer a reflection from Z-up versus Y-up.
    """
    roots = document["scenes"][document.get("scene", 0)]["nodes"]
    if len(roots) != 1:
        raise ValueError("Assembly GLB must have one root coordinate frame")
    transform = node_transform(document["nodes"][roots[0]])
    return {"cad_up_axis": "+Z", "gltf_up_axis": "+Y",
            "cad_to_gltf_matrix_column_major": transform.T.reshape(-1).tolist(),
            "linear_determinant": float(np.linalg.det(transform[:3, :3])),
            "transform_source": "active scene root node"}


def read_glb(path):
    raw = path.read_bytes()
    if raw[:4] != b"glTF" or struct.unpack_from("<II", raw, 4) != (2, len(raw)):
        raise ValueError("Invalid GLB header")
    size, kind = struct.unpack_from("<II", raw, 12)
    if kind != 0x4E4F534A:
        raise ValueError("GLB JSON chunk missing")
    document = json.loads(raw[20:20+size])
    length, kind = struct.unpack_from("<II", raw, 20+size)
    if kind != 0x004E4942:
        raise ValueError("GLB binary chunk missing")
    return document, memoryview(raw)[28+size:28+size+length]


def accessor(document, binary, index):
    item = document["accessors"][index]
    view = document["bufferViews"][item["bufferView"]]
    dtypes = {5121: np.uint8, 5123: np.uint16, 5125: np.uint32, 5126: np.float32}
    sizes = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4}
    dtype, width = np.dtype(dtypes[item["componentType"]]), sizes[item["type"]]
    return np.ndarray((item["count"], width), dtype=dtype, buffer=binary,
                      offset=view.get("byteOffset", 0)+item.get("byteOffset", 0),
                      strides=(view.get("byteStride", width*dtype.itemsize), dtype.itemsize))


def compact_glb(path):
    document, binary = read_glb(path)
    frame = scene_coordinate_frame(document)
    document.setdefault("asset", {}).setdefault("extras", {})["product3d_coordinate_frame"] = frame
    new_accessors, views, chunks = [], [], []
    byte_count = 0
    before = sum(len(mesh["primitives"]) for mesh in document["meshes"])
    triangles = 0

    def pack(array, component, width, target):
        nonlocal byte_count
        array = np.ascontiguousarray(array)
        data = array.tobytes()
        views.append({"buffer": 0, "byteOffset": byte_count, "byteLength": len(data), "target": target})
        item = {"bufferView": len(views)-1, "componentType": component,
                "count": len(array), "type": "SCALAR" if width == 1 else f"VEC{width}"}
        if target == 34962:
            item["min"], item["max"] = array.min(axis=0).tolist(), array.max(axis=0).tolist()
        new_accessors.append(item)
        chunks.append(data)
        byte_count += len(data)
        return len(new_accessors)-1

    for mesh in document["meshes"]:
        groups = {}
        for primitive in mesh["primitives"]:
            attrs = primitive["attributes"]
            if primitive.get("mode", 4) != 4 or set(attrs)-{"POSITION", "NORMAL"}:
                raise ValueError("Preview compaction supports CadQuery triangle position/normal meshes")
            if document["accessors"][attrs["POSITION"]]["componentType"] != 5126:
                raise ValueError("Preview positions must be float32")
            key = primitive.get("material"), "NORMAL" in attrs
            group = groups.setdefault(key, {"position": [], "normal": [], "indices": [], "vertices": 0})
            points = accessor(document, binary, attrs["POSITION"])
            indices = accessor(document, binary, primitive["indices"]).reshape(-1).astype(np.uint32)
            if len(indices)%3 or (len(indices) and int(indices.max()) >= len(points)):
                raise ValueError("Invalid triangle indices")
            if group["vertices"]+len(points) > np.iinfo(np.uint32).max:
                raise ValueError("Preview exceeds uint32 vertex capacity")
            group["position"].append(points)
            if key[1]:
                group["normal"].append(accessor(document, binary, attrs["NORMAL"]))
            group["indices"].append(indices+group["vertices"])
            group["vertices"] += len(points)
            triangles += len(indices)//3
        primitives = []
        for (material, has_normals), group in groups.items():
            attributes = {"POSITION": pack(np.concatenate(group["position"]), 5126, 3, 34962)}
            if has_normals:
                attributes["NORMAL"] = pack(np.concatenate(group["normal"]), 5126, 3, 34962)
            indices = pack(np.concatenate(group["indices"]).reshape(-1, 1), 5125, 1, 34963)
            primitive = {"attributes": attributes, "indices": indices, "mode": 4}
            if material is not None:
                primitive["material"] = material
            primitives.append(primitive)
        mesh["primitives"] = primitives
    document["accessors"], document["bufferViews"] = new_accessors, views
    document["buffers"] = [{"byteLength": byte_count}]
    text = json.dumps(document, separators=(",", ":")).encode()
    text += b" "*((-len(text))%4)
    payload = b"".join(chunks)
    payload += b"\0"*((-len(payload))%4)
    data = struct.pack("<III", 0x46546C67, 2, 28+len(text)+len(payload))
    data += struct.pack("<II", len(text), 0x4E4F534A)+text
    data += struct.pack("<II", len(payload), 0x004E4942)+payload
    path.write_bytes(data)
    return {"primitives_before": before, "primitives_after": sum(len(mesh["primitives"]) for mesh in document["meshes"]),
            "triangles_preserved": triangles, "bytes_after": len(data)}
