"""
Blendshape transfer using nearest-vertex correspondence.

Given a reference mesh (with blendshapes) and a target mesh (without),
this module transfers the morph target displacements by:
1. Auto-aligning the reference head to the target head region
2. Building a KD-tree of the aligned reference mesh vertices
3. For each target vertex, finding the nearest reference vertex
4. Transferring displacement directly (no destructive falloff)
"""

import numpy as np
from scipy.spatial import cKDTree


def _estimate_head_center(positions: np.ndarray) -> np.ndarray:
    """Estimate the center of the head region in a mesh."""
    if positions.shape[0] < 5000:
        return positions.mean(axis=0)
    y_vals = positions[:, 1]
    y_threshold = np.percentile(y_vals, 75)
    head_mask = y_vals > y_threshold
    return positions[head_mask].mean(axis=0)


def _estimate_head_radius(positions: np.ndarray, center: np.ndarray) -> float:
    """Estimate the radius of the head region."""
    if positions.shape[0] < 5000:
        dists = np.linalg.norm(positions - center, axis=1)
    else:
        y_vals = positions[:, 1]
        head_mask = y_vals > np.percentile(y_vals, 75)
        dists = np.linalg.norm(positions[head_mask] - center, axis=1)
    return float(np.percentile(dists, 90))


def _align_ref_to_target(
    ref_positions: np.ndarray,
    target_positions: np.ndarray,
) -> np.ndarray:
    """
    Translate AND scale the reference positions so its head aligns with
    the target's head region. Returns the aligned ref positions.
    """
    ref_center = _estimate_head_center(ref_positions)
    tgt_center = _estimate_head_center(target_positions)

    # Scale to match head size
    ref_radius = _estimate_head_radius(ref_positions, ref_center)
    tgt_radius = _estimate_head_radius(target_positions, tgt_center)
    scale = tgt_radius / ref_radius if ref_radius > 1e-6 else 1.0
    scale = np.clip(scale, 0.5, 2.0)

    aligned = (ref_positions - ref_center) * scale + tgt_center
    return aligned


def build_correspondence(
    ref_positions: np.ndarray,
    target_positions: np.ndarray,
    max_distance: float = 0.1,
) -> tuple:
    """
    Build vertex correspondence using nearest-neighbor lookup.
    Returns (indices, distances, mask).
    """
    tree = cKDTree(ref_positions)
    distances, indices = tree.query(target_positions, k=1)
    mask = distances < max_distance
    return indices, distances, mask


def compute_local_scale(
    ref_positions: np.ndarray,
    target_positions: np.ndarray,
    indices: np.ndarray,
    neighborhood_k: int = 6,
) -> np.ndarray:
    """
    Compute per-vertex scale factor based on local neighborhood size
    differences between reference and target meshes.
    """
    ref_tree = cKDTree(ref_positions)
    target_tree = cKDTree(target_positions)

    target_dists, _ = target_tree.query(target_positions, k=neighborhood_k + 1)
    target_radius = np.mean(target_dists[:, 1:], axis=1)

    ref_subset = ref_positions[indices]
    ref_dists, _ = ref_tree.query(ref_subset, k=neighborhood_k + 1)
    ref_radius = np.mean(ref_dists[:, 1:], axis=1)

    scale = np.where(ref_radius > 1e-8, target_radius / ref_radius, 1.0)
    scale = np.clip(scale, 0.5, 2.0)
    return scale


def transfer_blendshape(
    ref_displacement: np.ndarray,
    indices: np.ndarray,
    mask: np.ndarray,
    local_scale: np.ndarray,
    vertex_count: int,
) -> np.ndarray:
    """
    Transfer a single blendshape displacement from reference to target.
    No distance falloff — if a vertex has a valid correspondence, it gets
    the full displacement (scaled by local geometry).
    """
    target_disp = np.zeros((vertex_count, 3), dtype=np.float32)

    # Look up the reference displacement for each target vertex
    mapped_disp = ref_displacement[indices]

    # Apply local scale
    scaled_disp = mapped_disp * local_scale[:, np.newaxis]

    # Apply only to vertices with valid correspondence — full strength
    target_disp[mask] = scaled_disp[mask]

    return target_disp


def transfer_all_blendshapes(
    ref_positions: np.ndarray,
    target_positions: np.ndarray,
    ref_blendshapes: dict,
    max_distance: float = 0.15,
    falloff_distance: float = 0.08,
) -> dict:
    """
    Transfer all blendshapes from reference to target mesh.
    Auto-aligns the reference to the target before computing correspondence.
    """
    # Auto-align reference head to target head region
    aligned_ref = _align_ref_to_target(ref_positions, target_positions)

    print(f"  Building correspondence: {ref_positions.shape[0]} ref -> "
          f"{target_positions.shape[0]} target vertices")

    indices, distances, mask = build_correspondence(
        aligned_ref, target_positions, max_distance
    )

    valid_pct = np.sum(mask) / len(mask) * 100
    print(f"  Valid correspondences: {np.sum(mask)}/{len(mask)} ({valid_pct:.1f}%)")

    if valid_pct < 10:
        print("  WARNING: Very low correspondence. Trying with larger distance...")
        indices, distances, mask = build_correspondence(
            aligned_ref, target_positions, max_distance * 2
        )
        valid_pct = np.sum(mask) / len(mask) * 100
        print(f"  Retry correspondences: {np.sum(mask)}/{len(mask)} ({valid_pct:.1f}%)")

    local_scale = compute_local_scale(
        aligned_ref, target_positions, indices
    )

    result = {}
    for name, ref_disp in ref_blendshapes.items():
        target_disp = transfer_blendshape(
            ref_disp, indices, mask, local_scale, target_positions.shape[0]
        )
        result[name] = target_disp

    return result