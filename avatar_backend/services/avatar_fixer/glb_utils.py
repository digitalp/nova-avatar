"""
Utilities for reading/writing GLB morph targets using pygltflib.
"""

import numpy as np
from pygltflib import GLTF2, Accessor, BufferView


# glTF component type constants
FLOAT = 5126  # GL_FLOAT
UNSIGNED_SHORT = 5123
ARRAY_BUFFER = 34962


def load_glb(path: str) -> GLTF2:
    """Load a GLB file."""
    return GLTF2().load(path)


def save_glb(gltf: GLTF2, path: str):
    """Save a GLTF2 object to a GLB file."""
    gltf.save(path)


def get_accessor_data(gltf: GLTF2, accessor_index: int) -> np.ndarray:
    """Read accessor data as a numpy array of float32 VEC3 values."""
    accessor = gltf.accessors[accessor_index]
    buffer_view = gltf.bufferViews[accessor.bufferView]

    blob = gltf.binary_blob()
    byte_offset = (buffer_view.byteOffset or 0) + (accessor.byteOffset or 0)

    if accessor.type == "VEC3":
        components = 3
    elif accessor.type == "VEC4":
        components = 4
    elif accessor.type == "VEC2":
        components = 2
    elif accessor.type == "SCALAR":
        components = 1
    else:
        raise ValueError(f"Unsupported accessor type: {accessor.type}")

    if accessor.componentType == FLOAT:
        dtype = np.float32
        comp_size = 4
    elif accessor.componentType == UNSIGNED_SHORT:
        dtype = np.uint16
        comp_size = 2
    else:
        raise ValueError(f"Unsupported component type: {accessor.componentType}")

    stride = buffer_view.byteStride or (components * comp_size)
    count = accessor.count

    if stride == components * comp_size:
        length = count * components * comp_size
        raw = blob[byte_offset : byte_offset + length]
        data = np.frombuffer(raw, dtype=dtype).reshape(count, components)
    else:
        data = np.zeros((count, components), dtype=dtype)
        for i in range(count):
            offset = byte_offset + i * stride
            raw = blob[offset : offset + components * comp_size]
            data[i] = np.frombuffer(raw, dtype=dtype)

    return data.astype(np.float32)


def find_face_mesh(gltf: GLTF2, mesh_name: str = None):
    """
    Find the primary face/head mesh primitive in the GLB.
    Returns (mesh_index, primitive_index) or None.

    Checks mesh names, node names, and falls back to spatial analysis
    (mesh with most vertices in the head Y-range).
    """
    if mesh_name:
        target = mesh_name.lower()
        for mi, mesh in enumerate(gltf.meshes):
            if (mesh.name or "").lower() == target:
                return (mi, 0)
        for mi, mesh in enumerate(gltf.meshes):
            if target in (mesh.name or "").lower():
                return (mi, 0)
        return None

    face_keywords = ["head", "face", "wolf3d_head", "avaturn_body"]
    # Meshes to skip when looking for the head
    skip_keywords = ["hair", "outfit", "body", "footwear", "glasses",
                     "shoe", "look", "eye", "teeth"]

    mesh_to_node_name = {}
    for node in gltf.nodes:
        if node.mesh is not None and node.name:
            mesh_to_node_name[node.mesh] = node.name.lower()

    face_candidate = None
    face_vcount = 0

    for mi, mesh in enumerate(gltf.meshes):
        mesh_name_lower = (mesh.name or "").lower()
        node_name_lower = mesh_to_node_name.get(mi, "")

        for pi, prim in enumerate(mesh.primitives):
            if prim.attributes.POSITION is None:
                continue
            vcount = gltf.accessors[prim.attributes.POSITION].count

            is_face = any(
                kw in mesh_name_lower or kw in node_name_lower
                for kw in face_keywords
            )

            if is_face:
                if face_candidate is None or vcount > face_vcount:
                    face_candidate = (mi, pi)
                    face_vcount = vcount

    if face_candidate:
        return face_candidate

    # Fallback: find the mesh with the most vertices that isn't
    # hair/outfit/body/footwear (likely the head mesh)
    best = None
    best_vcount = 0

    for mi, mesh in enumerate(gltf.meshes):
        mesh_name_lower = (mesh.name or "").lower()
        node_name_lower = mesh_to_node_name.get(mi, "")

        # Skip known non-face meshes
        is_skip = any(
            kw in mesh_name_lower or kw in node_name_lower
            for kw in skip_keywords
        )

        for pi, prim in enumerate(mesh.primitives):
            if prim.attributes.POSITION is None:
                continue
            vcount = gltf.accessors[prim.attributes.POSITION].count

            if not is_skip and vcount > best_vcount:
                best = (mi, pi)
                best_vcount = vcount

    # If we still have nothing, just pick the largest mesh overall
    if best is None:
        largest = None
        largest_vcount = 0
        for mi, mesh in enumerate(gltf.meshes):
            for pi, prim in enumerate(mesh.primitives):
                if prim.attributes.POSITION is None:
                    continue
                vcount = gltf.accessors[prim.attributes.POSITION].count
                if vcount > largest_vcount:
                    largest = (mi, pi)
                    largest_vcount = vcount
        return largest

    return best


def find_all_face_meshes(gltf: GLTF2) -> list:
    """
    Find ALL face-related mesh primitives in the GLB.
    Checks both mesh names and node names that reference meshes.
    Returns list of (mesh_index, primitive_index) tuples.
    """
    from .blendshape_names import FACE_MESH_KEYWORDS

    # Build mesh_index -> node name map
    mesh_to_node_name = {}
    for node in gltf.nodes:
        if node.mesh is not None and node.name:
            mesh_to_node_name[node.mesh] = node.name.lower()

    results = []
    for mi, mesh in enumerate(gltf.meshes):
        mesh_name = (mesh.name or "").lower()
        node_name = mesh_to_node_name.get(mi, "")

        if any(kw in mesh_name or kw in node_name for kw in FACE_MESH_KEYWORDS):
            for pi in range(len(mesh.primitives)):
                results.append((mi, pi))

    return results


def get_bone_names(gltf: GLTF2) -> list:
    """Get all bone/node names from the GLB."""
    return [node.name for node in gltf.nodes if node.name]


def validate_skeleton(gltf: GLTF2) -> dict:
    """
    Check if the GLB has the bones TalkingHead requires.
    Returns dict with 'valid', 'present', 'missing' keys.
    """
    from .blendshape_names import REQUIRED_BONES

    bone_names = set(get_bone_names(gltf))
    # Also check without 'mixamorig' prefix (TalkingHead strips it)
    stripped = set()
    for name in bone_names:
        if name.startswith("mixamorig"):
            stripped.add(name.replace("mixamorig", ""))
        stripped.add(name)

    present = [b for b in REQUIRED_BONES if b in stripped]
    missing = [b for b in REQUIRED_BONES if b not in stripped]

    return {
        "valid": len(missing) == 0,
        "present": present,
        "missing": missing,
    }


def get_existing_morph_target_names(gltf: GLTF2, mesh_index: int) -> list:
    """Get the list of morph target names from mesh extras or targetNames."""
    mesh = gltf.meshes[mesh_index]

    if mesh.extras and isinstance(mesh.extras, dict):
        names = mesh.extras.get("targetNames", [])
        if names:
            return names

    return []


def get_morph_target_data(gltf: GLTF2, mesh_index: int, prim_index: int):
    """
    Extract existing morph target displacement data.
    Returns dict of {name: np.ndarray of shape (V, 3)}.
    """
    mesh = gltf.meshes[mesh_index]
    prim = mesh.primitives[prim_index]
    names = get_existing_morph_target_names(gltf, mesh_index)

    targets = {}
    if prim.targets:
        for i, target in enumerate(prim.targets):
            name = names[i] if i < len(names) else f"target_{i}"
            if isinstance(target, dict):
                pos_idx = target.get("POSITION")
            else:
                pos_idx = getattr(target, "POSITION", None)
            if pos_idx is not None:
                targets[name] = get_accessor_data(gltf, pos_idx)

    return targets
