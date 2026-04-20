"""
Inject morph targets (blendshapes) into a GLB file.

This module handles the low-level glTF binary manipulation needed to
add new morph targets to an existing mesh primitive.
"""

import numpy as np
from pygltflib import (
    GLTF2,
    Accessor,
    Attributes,
    BufferView,
)

FLOAT = 5126


def _pack_vec3_array(data: np.ndarray) -> bytes:
    """Pack an (N, 3) float32 array into raw bytes."""
    return data.astype(np.float32).tobytes()


def _compute_bounds(data: np.ndarray):
    """Compute min/max for an (N, 3) array."""
    mins = data.min(axis=0).tolist()
    maxs = data.max(axis=0).tolist()
    return mins, maxs


def _align_to_4(offset: int) -> int:
    """Round up to next 4-byte boundary (glTF spec requirement)."""
    return (offset + 3) & ~3


def inject_morph_targets(
    gltf: GLTF2,
    mesh_index: int,
    prim_index: int,
    blendshapes: dict,
    blendshape_order: list = None,
) -> GLTF2:
    """
    Inject morph target displacements into a GLB mesh primitive.

    Preserves all existing binary data and appends new morph target
    data with proper 4-byte alignment per the glTF spec.
    """
    if blendshape_order is None:
        blendshape_order = list(blendshapes.keys())

    mesh = gltf.meshes[mesh_index]
    prim = mesh.primitives[prim_index]

    # Get current binary blob
    blob = gltf.binary_blob()
    if blob is None:
        blob = b""

    # Pad existing blob to 4-byte alignment before appending
    aligned_start = _align_to_4(len(blob))
    padding = aligned_start - len(blob)

    new_data = bytearray(b'\x00' * padding)
    new_targets = []
    target_names = []

    for name in blendshape_order:
        if name not in blendshapes:
            continue

        disp = blendshapes[name]
        raw = _pack_vec3_array(disp)
        mins, maxs = _compute_bounds(disp)

        # Current offset into the combined buffer
        current_offset = len(blob) + len(new_data)

        # Ensure 4-byte alignment for this buffer view
        aligned_offset = _align_to_4(current_offset)
        if aligned_offset > current_offset:
            new_data.extend(b'\x00' * (aligned_offset - current_offset))

        bv_offset = len(blob) + len(new_data)

        # Create buffer view
        bv_index = len(gltf.bufferViews)
        bv = BufferView(
            buffer=0,
            byteOffset=bv_offset,
            byteLength=len(raw),
        )
        gltf.bufferViews.append(bv)

        # Create accessor
        acc_index = len(gltf.accessors)
        acc = Accessor(
            bufferView=bv_index,
            byteOffset=0,
            componentType=FLOAT,
            count=disp.shape[0],
            type="VEC3",
            max=maxs,
            min=mins,
        )
        gltf.accessors.append(acc)

        # pygltflib expects Attributes objects for morph targets
        target = Attributes(POSITION=acc_index)
        new_targets.append(target)
        target_names.append(name)

        new_data.extend(raw)

    # Set the targets on the primitive
    prim.targets = new_targets

    # Set default weights (all zero)
    mesh.weights = [0.0] * len(new_targets)

    # Store target names in mesh extras (standard glTF convention)
    if mesh.extras is None:
        mesh.extras = {}
    mesh.extras["targetNames"] = target_names

    # Update the buffer size and binary blob
    combined = bytearray(blob) + new_data
    gltf.buffers[0].byteLength = len(combined)
    gltf.set_binary_blob(bytes(combined))

    return gltf
