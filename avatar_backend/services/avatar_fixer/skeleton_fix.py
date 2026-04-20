"""
Fix skeleton to be TalkingHead-compatible.

- Renames bones with numeric suffixes (e.g. Hips_01 -> Hips) from
  Sketchfab/FBX exports
- Strips 'mixamorig' prefix
- Adds missing LeftEye, RightEye, HeadTop_End bones
"""

import re
import numpy as np
from pygltflib import GLTF2, Node, BufferView

LEFT_EYE_OFFSET = [0.03, 0.07, 0.08]
RIGHT_EYE_OFFSET = [-0.03, 0.07, 0.08]
HEAD_TOP_OFFSET = [0.0, 0.15, 0.0]

# Pattern: name followed by _0, _01, _012, etc.
_SUFFIX_RE = re.compile(r'^(.+?)_0\d*$')
# Pattern: mixamorig: or mixamorig prefix
_MIXAMO_RE = re.compile(r'^mixamorig:?')


def fix_bone_names(gltf: GLTF2) -> list:
    """
    Rename bones to be TalkingHead/Mixamo-compatible.
    Strips numeric suffixes from Sketchfab/FBX exports and mixamorig prefixes.

    Returns list of (old_name, new_name) for bones that were renamed.
    """
    renamed = []

    for node in gltf.nodes:
        if not node.name:
            continue

        original = node.name
        name = node.name

        # Strip mixamorig prefix
        name = _MIXAMO_RE.sub('', name)

        # Strip numeric suffixes like _01, _032, _0
        m = _SUFFIX_RE.match(name)
        if m:
            name = m.group(1)

        if name != original:
            node.name = name
            renamed.append((original, name))

    # Also rename the skin if it has a suffixed name
    for skin in gltf.skins:
        if skin.name:
            m = _SUFFIX_RE.match(skin.name)
            if m:
                skin.name = m.group(1)

    return renamed


def add_missing_bones(gltf: GLTF2) -> tuple:
    """
    Add LeftEye, RightEye, and HeadTop_End bones if missing.
    Returns (gltf, added_bones).
    """
    node_names = {node.name: i for i, node in enumerate(gltf.nodes) if node.name}
    added = []
    bones_to_add = []

    head_idx = node_names.get("Head")
    if head_idx is None:
        print("  WARNING: No 'Head' bone found — cannot add eye bones")
        return gltf, added

    head_node = gltf.nodes[head_idx]
    if head_node.children is None:
        head_node.children = []

    for name, offset in [
        ("LeftEye", LEFT_EYE_OFFSET),
        ("RightEye", RIGHT_EYE_OFFSET),
        ("HeadTop_End", HEAD_TOP_OFFSET),
    ]:
        if name not in node_names:
            node = Node(name=name, translation=offset, children=[])
            node_idx = len(gltf.nodes)
            gltf.nodes.append(node)
            head_node.children.append(node_idx)
            node_names[name] = node_idx
            added.append(name)
            bones_to_add.append(node_idx)

    if bones_to_add:
        for skin in gltf.skins:
            _add_joints_to_skin(gltf, skin, bones_to_add)

    return gltf, added


def _add_joints_to_skin(gltf: GLTF2, skin, new_joint_indices: list):
    """
    Add new joints to a skin and extend its inverse bind matrices.
    Appends new data at the END of the blob — never inserts in the middle.
    """
    for idx in new_joint_indices:
        if idx not in skin.joints:
            skin.joints.append(idx)

    if skin.inverseBindMatrices is None:
        return

    accessor = gltf.accessors[skin.inverseBindMatrices]
    old_bv = gltf.bufferViews[accessor.bufferView]

    blob = gltf.binary_blob()
    ibm_start = (old_bv.byteOffset or 0) + (accessor.byteOffset or 0)
    ibm_length = accessor.count * 64
    existing_ibm_data = blob[ibm_start : ibm_start + ibm_length]

    identity = np.eye(4, dtype=np.float32).tobytes()
    new_ibm_data = existing_ibm_data + (identity * len(new_joint_indices))

    blob_bytes = bytearray(blob)
    while len(blob_bytes) % 4 != 0:
        blob_bytes.append(0)

    new_bv_offset = len(blob_bytes)
    blob_bytes.extend(new_ibm_data)

    new_bv_index = len(gltf.bufferViews)
    gltf.bufferViews.append(BufferView(
        buffer=0,
        byteOffset=new_bv_offset,
        byteLength=len(new_ibm_data),
    ))

    accessor.bufferView = new_bv_index
    accessor.byteOffset = 0
    accessor.count += len(new_joint_indices)

    gltf.buffers[0].byteLength = len(blob_bytes)
    gltf.set_binary_blob(bytes(blob_bytes))
