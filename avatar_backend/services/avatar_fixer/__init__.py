"""
Avatar Fixer — auto-fix GLB avatars to be TalkingHead-compatible.

On upload, detects and fixes:
1. Missing ARKit blendshapes (52) + Oculus visemes (15) — transferred from reference GLB
2. Wrong bone names (mixamorig prefix, numeric suffixes, AvatarRoot)
3. Missing eye/head bones (LeftEye, RightEye, HeadTop_End)
4. Missing Armature root node
5. Strip unused morph targets from non-face meshes (performance)
6. Strip non-TalkingHead morph targets from face mesh (performance)
7. Compress textures to WebP (file size)
"""
from __future__ import annotations

import io
import shutil
from pathlib import Path

import numpy as np
import structlog

_LOGGER = structlog.get_logger()
_REFERENCE_GLB = Path(__file__).parent.parent.parent.parent / "models" / "reference" / "brunette.glb"


def _compress_textures_webp(gltf, quality: int = 80) -> int:
    """Convert JPEG/PNG textures to WebP in-place. Returns count of compressed textures."""
    try:
        from PIL import Image as PILImage
    except ImportError:
        return 0

    blob = bytearray(gltf.binary_blob() or b"")
    compressed = 0

    for img in (gltf.images or []):
        bv_idx = img.bufferView
        if bv_idx is None:
            continue
        bv = gltf.bufferViews[bv_idx]
        offset = bv.byteOffset or 0
        raw = bytes(blob[offset:offset + bv.byteLength])

        mime = (img.mimeType or "").lower()
        if "webp" in mime:
            continue  # already WebP

        try:
            pil_img = PILImage.open(io.BytesIO(raw))
            buf = io.BytesIO()
            pil_img.save(buf, format="WEBP", quality=quality)
            webp_bytes = buf.getvalue()

            if len(webp_bytes) >= len(raw):
                continue  # WebP is larger, skip

            # Append new data at end of blob
            new_offset = len(blob)
            while len(blob) % 4:
                blob.append(0)
            new_offset = len(blob)
            blob.extend(webp_bytes)

            # Update buffer view
            from pygltflib import BufferView
            new_bv_idx = len(gltf.bufferViews)
            gltf.bufferViews.append(BufferView(
                buffer=0, byteOffset=new_offset, byteLength=len(webp_bytes),
            ))
            img.bufferView = new_bv_idx
            img.mimeType = "image/webp"
            compressed += 1
        except Exception:
            continue

    if compressed:
        gltf.buffers[0].byteLength = len(blob)
        gltf.set_binary_blob(bytes(blob))

    return compressed


def _strip_morph_targets(gltf, keep_mesh_idx: int, keep_prim_idx: int) -> int:
    """Remove all morph targets from meshes except the specified face mesh primitive.
    Returns count of stripped meshes."""
    stripped = 0
    for mi, mesh in enumerate(gltf.meshes):
        for pi, prim in enumerate(mesh.primitives):
            if mi == keep_mesh_idx and pi == keep_prim_idx:
                continue
            if prim.targets:
                prim.targets = None
                stripped += 1
        # Clear weights if no primitives have targets
        has_targets = any(p.targets for p in mesh.primitives)
        if not has_targets:
            mesh.weights = None
            if mesh.extras and "targetNames" in (mesh.extras or {}):
                if mi != keep_mesh_idx:
                    mesh.extras.pop("targetNames", None)
    return stripped


def _filter_face_morph_targets(gltf, mesh_idx: int, prim_idx: int, keep_names: set) -> int:
    """Keep only TalkingHead-needed morph targets on the face mesh. Returns count removed."""
    from .glb_utils import get_existing_morph_target_names, get_morph_target_data
    from .inject import inject_morph_targets

    existing_names = get_existing_morph_target_names(gltf, mesh_idx)
    if not existing_names:
        return 0

    existing_data = get_morph_target_data(gltf, mesh_idx, prim_idx)
    to_keep = {n: d for n, d in existing_data.items() if n in keep_names}
    removed = len(existing_data) - len(to_keep)

    if removed > 0 and to_keep:
        order = [n for n in existing_names if n in keep_names]
        gltf = inject_morph_targets(gltf, mesh_idx, prim_idx, to_keep, order)

    return removed


def fix_avatar(input_path: str, output_path: str | None = None) -> dict:
    """
    Auto-fix a GLB avatar for TalkingHead compatibility.
    Returns dict with keys: fixed (bool), actions (list[str]), error (str|None).
    """
    if output_path is None:
        output_path = input_path

    actions = []

    try:
        from .glb_utils import (load_glb, save_glb, find_face_mesh,
                                get_existing_morph_target_names, get_morph_target_data,
                                get_accessor_data)
        from .blendshape_names import ARKIT_BLENDSHAPES, OCULUS_VISEMES, ALL_BLENDSHAPES
        from .skeleton_fix import fix_bone_names, add_missing_bones

        KEEP_NAMES = set(ALL_BLENDSHAPES)

        gltf = load_glb(input_path)

        # 1. Fix bone names
        renamed = fix_bone_names(gltf)
        if renamed:
            actions.append(f"Renamed {len(renamed)} bones")

        # 2. Rename AvatarRoot → Armature
        for node in gltf.nodes:
            if node.name == "AvatarRoot":
                node.name = "Armature"
                actions.append("Renamed AvatarRoot → Armature")
                break

        # 3. Add Armature wrapper if missing
        node_names = [n.name for n in gltf.nodes if n.name]
        if "Armature" not in node_names and "Hips" in node_names:
            from pygltflib import Node
            hips_idx = next(i for i, n in enumerate(gltf.nodes) if n.name == "Hips")
            armature_idx = len(gltf.nodes)
            gltf.nodes.append(Node(name="Armature", children=[hips_idx]))
            for scene in gltf.scenes:
                if hips_idx in (scene.nodes or []):
                    scene.nodes[scene.nodes.index(hips_idx)] = armature_idx
            actions.append("Added Armature root node")

        # 4. Add missing eye/head bones
        gltf, added_bones = add_missing_bones(gltf)
        if added_bones:
            actions.append(f"Added missing bones: {', '.join(added_bones)}")

        # 4b. Zero out extra neck bone rotations (Neck1, Neck2)
        # TalkingHead only animates Neck and Head — extra neck bones with
        # rest-pose rotations cause the avatar to look off-center.
        zeroed_necks = []
        for node in gltf.nodes:
            if node.name in ("Neck1", "Neck2") and node.rotation:
                node.rotation = [0, 0, 0, 1]
                zeroed_necks.append(node.name)
        if zeroed_necks:
            actions.append(f"Fixed head orientation: zeroed {', '.join(zeroed_necks)} rotation")

        # 4c. Fix skin.skeleton reference — TalkingHead requires it to point to Armature
        armature_idx = next((i for i, n in enumerate(gltf.nodes) if n.name == "Armature"), None)
        if armature_idx is not None:
            for skin in gltf.skins:
                if skin.skeleton is None:
                    skin.skeleton = armature_idx
                    actions.append("Fixed skin.skeleton → Armature")

        # 5. Find face mesh
        face = find_face_mesh(gltf)
        if face is None:
            _LOGGER.warning("avatar_fixer.no_face_mesh")
            save_glb(gltf, output_path)
            actions.append("No face mesh found — skeleton fixes only")
            return {"fixed": bool(actions), "actions": actions, "error": None}

        mi, pi = face

        # 6. Rename visemes that exist without the viseme_ prefix
        VISEME_RENAME = {
            'sil': 'viseme_sil', 'PP': 'viseme_PP', 'FF': 'viseme_FF',
            'TH': 'viseme_TH', 'DD': 'viseme_DD', 'kk': 'viseme_kk',
            'CH': 'viseme_CH', 'SS': 'viseme_SS', 'nn': 'viseme_nn',
            'RR': 'viseme_RR', 'aa': 'viseme_aa', 'E': 'viseme_E',
            'I': 'viseme_I', 'O': 'viseme_O', 'U': 'viseme_U',
            'ih': 'viseme_I', 'oh': 'viseme_O', 'ou': 'viseme_U',
        }
        for mesh in gltf.meshes:
            extras = mesh.extras or {}
            names = extras.get('targetNames', [])
            renamed_vis = 0
            for i, name in enumerate(names):
                if name in VISEME_RENAME:
                    names[i] = VISEME_RENAME[name]
                    renamed_vis += 1
            if renamed_vis:
                extras['targetNames'] = names
                mesh.extras = extras
        if renamed_vis:
            actions.append(f"Renamed {renamed_vis} visemes to TalkingHead format")

        # 7. Strip morph targets from non-face meshes
        stripped = _strip_morph_targets(gltf, mi, pi)
        if stripped:
            actions.append(f"Stripped morph targets from {stripped} non-face meshes")

        # 7. Filter face morph targets to only TalkingHead-needed ones
        existing = set(get_existing_morph_target_names(gltf, mi))
        unused = existing - KEEP_NAMES
        if unused:
            removed = _filter_face_morph_targets(gltf, mi, pi, KEEP_NAMES)
            if removed:
                actions.append(f"Removed {removed} unused face morph targets")
            existing = existing & KEEP_NAMES

        # 8. Re-check what's present after rename, then transfer only truly missing shapes
        existing = set(get_existing_morph_target_names(gltf, mi))
        needed_arkit = [n for n in ARKIT_BLENDSHAPES if n not in existing]
        needed_visemes = [n for n in OCULUS_VISEMES if n not in existing]

        if needed_arkit or needed_visemes:
            if not _REFERENCE_GLB.exists():
                save_glb(gltf, output_path)
                return {"fixed": bool(actions), "actions": actions,
                        "error": f"Reference GLB not found at {_REFERENCE_GLB}"}

            ref_gltf = load_glb(str(_REFERENCE_GLB))
            ref_face = find_face_mesh(ref_gltf)
            if ref_face:
                ref_mi, ref_pi = ref_face
                ref_positions = get_accessor_data(ref_gltf, ref_gltf.meshes[ref_mi].primitives[ref_pi].attributes.POSITION)
                ref_blendshapes = get_morph_target_data(ref_gltf, ref_mi, ref_pi)
                target_positions = get_accessor_data(gltf, gltf.meshes[mi].primitives[pi].attributes.POSITION)

                to_transfer = {k: v for k, v in ref_blendshapes.items()
                               if k in needed_arkit or k in needed_visemes}

                if to_transfer:
                    from .transfer import transfer_all_blendshapes
                    from .inject import inject_morph_targets

                    transferred = transfer_all_blendshapes(
                        ref_positions, target_positions, to_transfer, max_distance=0.15)

                    existing_data = get_morph_target_data(gltf, mi, pi)
                    all_bs = {**existing_data, **transferred}
                    order = [n for n in ALL_BLENDSHAPES if n in all_bs]
                    gltf = inject_morph_targets(gltf, mi, pi, all_bs, order)
                    actions.append(f"Transferred {len(transferred)} blendshapes")

        # 9. Compress textures to WebP
        compressed = _compress_textures_webp(gltf)
        if compressed:
            actions.append(f"Compressed {compressed} textures to WebP")

        save_glb(gltf, output_path)

        import os
        size_mb = os.path.getsize(output_path) / 1024 / 1024
        actions.append(f"Output: {size_mb:.1f} MB")
        _LOGGER.info("avatar_fixer.complete", actions=len(actions), size_mb=round(size_mb, 1))
        return {"fixed": True, "actions": actions, "error": None}

    except Exception as exc:
        _LOGGER.error("avatar_fixer.failed", exc=str(exc)[:200])
        if output_path != input_path:
            shutil.copy2(input_path, output_path)
        return {"fixed": False, "actions": actions, "error": str(exc)}
