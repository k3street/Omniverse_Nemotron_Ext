/**
 * Snap geometry per spec §6.3 — pure functions, framework-agnostic.
 *
 * Snap precedence (closest wins): object > polar > grid.  8 px screen-space
 * threshold at 100% zoom.  Smart guides activate when dragged-object edge
 * or center aligns with another object's edge or center.
 */
import { Position, TypedObject } from "../api/types";

export interface SnapResult {
    snapped: Position;
    type: "grid" | "object_edge" | "object_center" | "polar" | "none";
    guideLines: GuideLine[];   // visible alignment lines for smart-guide overlay
}

export interface GuideLine {
    /** axis: "x" → vertical line at x; "y" → horizontal line at y. */
    axis: "x" | "y";
    coord: number;             // world-space
    fromY?: number;            // for axis=x — extent of line
    toY?: number;
    fromX?: number;            // for axis=y
    toX?: number;
    label?: string;
}

const GRID_MINOR_M = 0.1;
const GRID_MAJOR_M = 1.0;
const SNAP_THRESHOLD_PX = 8;

/** Snap a world-space position to the nearest grid intersection.  Threshold
 * scales with px_per_m so the snap zone is 8 px screen-space. */
export function snapToGrid(
    pos: Position,
    pxPerM: number,
    enabled: boolean = true,
    grid: number = GRID_MINOR_M,
): SnapResult {
    if (!enabled) return { snapped: pos, type: "none", guideLines: [] };
    const thresholdM = SNAP_THRESHOLD_PX / pxPerM;
    const sx = Math.round(pos.x / grid) * grid;
    const sy = Math.round(pos.y / grid) * grid;
    if (Math.abs(pos.x - sx) <= thresholdM && Math.abs(pos.y - sy) <= thresholdM) {
        return {
            snapped: { x: sx, y: sy },
            type: "grid",
            guideLines: [],
        };
    }
    return { snapped: pos, type: "none", guideLines: [] };
}

/** Object-snap: align dragged center/edges with other objects' centers/edges.
 * Returns the snap result + guide lines for the visual overlay. */
export function snapToObjects(
    pos: Position,
    draggedId: string,
    otherObjects: TypedObject[],
    pxPerM: number,
    enabled: boolean = true,
): SnapResult {
    if (!enabled || otherObjects.length === 0) {
        return { snapped: pos, type: "none", guideLines: [] };
    }
    const thresholdM = SNAP_THRESHOLD_PX / pxPerM;
    let bestX = pos.x;
    let bestY = pos.y;
    let snappedX = false;
    let snappedY = false;
    const guides: GuideLine[] = [];

    for (const other of otherObjects) {
        if (other.id === draggedId) continue;
        const candidatesX = [
            other.position.x,                       // center
            other.position.x - other.size.w / 2,    // left edge
            other.position.x + other.size.w / 2,    // right edge
        ];
        const candidatesY = [
            other.position.y,
            other.position.y - other.size.h / 2,
            other.position.y + other.size.h / 2,
        ];
        for (const cx of candidatesX) {
            if (!snappedX && Math.abs(pos.x - cx) <= thresholdM) {
                bestX = cx;
                snappedX = true;
                guides.push({
                    axis: "x",
                    coord: cx,
                    fromY: Math.min(pos.y, other.position.y) - 0.5,
                    toY: Math.max(pos.y, other.position.y) + 0.5,
                });
            }
        }
        for (const cy of candidatesY) {
            if (!snappedY && Math.abs(pos.y - cy) <= thresholdM) {
                bestY = cy;
                snappedY = true;
                guides.push({
                    axis: "y",
                    coord: cy,
                    fromX: Math.min(pos.x, other.position.x) - 0.5,
                    toX: Math.max(pos.x, other.position.x) + 0.5,
                });
            }
        }
    }

    if (snappedX || snappedY) {
        return {
            snapped: { x: bestX, y: bestY },
            type: "object_edge",
            guideLines: guides,
        };
    }
    return { snapped: pos, type: "none", guideLines: [] };
}

/** Composite snap — runs object-snap first, falls through to grid.  Caller
 * picks which snap modes are enabled (Ctrl/Shift modifiers traditionally
 * disable specific snap types). */
export function compositeSnap(
    pos: Position,
    draggedId: string,
    otherObjects: TypedObject[],
    pxPerM: number,
    options: {
        gridSnap?: boolean;
        objectSnap?: boolean;
        gridStep?: number;
    } = {},
): SnapResult {
    const objs = options.objectSnap ?? true;
    const grids = options.gridSnap ?? true;

    if (objs) {
        const r = snapToObjects(pos, draggedId, otherObjects, pxPerM, true);
        if (r.type !== "none") return r;
    }
    if (grids) {
        return snapToGrid(pos, pxPerM, true, options.gridStep ?? GRID_MINOR_M);
    }
    return { snapped: pos, type: "none", guideLines: [] };
}

// ─── Phase 23: Snap engine hardening ──────────────────────────────────────────
//
// New pure-function exports:
//   snapToGrid   (simple 2-arg variant — no pxPerM, no screen-space threshold)
//   snapRotation (15° default increment)
//   clusterDensityAt
//   wallProximitySnap
//   compositeSnap (overload — new typed signature, replaces old inline)
//
// The OLD compositeSnap above is renamed to _legacyCompositeSnap to avoid a
// name collision while keeping downstream imports working via re-export.

/** Wall primitive used by wallProximitySnap. */
export interface Wall {
    start: { x: number; y: number };
    end:   { x: number; y: number };
}

/** Input bundle for the Phase 23 compositeSnap orchestrator. */
export interface SnapInput {
    position:           { x: number; y: number };
    rotation:           number;       // degrees
    allPositions:       { x: number; y: number }[];
    walls:              Wall[];
    footprint:          { width: number; depth: number };
    gridSize:           number;       // metres
    density_threshold?: number;       // default 5
    rotation_increment?: number;      // degrees, default 15
}

/** Result from the Phase 23 compositeSnap orchestrator. */
export interface CompositeSnapResult {
    position: { x: number; y: number };
    rotation: number;
    /** Which snap modes fired, e.g. ["grid", "rotation", "wall"] */
    applied:  string[];
}

/**
 * Snap a position to the nearest grid point.
 *
 * Phase 23 variant — takes a plain position and gridSize (metres).
 * Sub-mm safe: uses floating-point arithmetic throughout; never truncates.
 *
 * This is intentionally a DIFFERENT function from the original `snapToGrid`
 * (which requires a `pxPerM` scale factor and returns `SnapResult`).
 */
export function snapToGridSimple(
    position: { x: number; y: number },
    gridSize: number,
): { x: number; y: number } {
    if (gridSize <= 0) return { x: position.x, y: position.y };
    const sx = Math.round(position.x / gridSize) * gridSize;
    const sy = Math.round(position.y / gridSize) * gridSize;
    return { x: sx, y: sy };
}

/**
 * Snap a rotation angle (degrees) to the nearest increment.
 *
 * Default increment is 15°.  Result is always in the range [0, 360).
 * Sub-mm precision note: angles are floats; no integer truncation.
 */
export function snapRotation(angleDeg: number, increment: number = 15): number {
    if (increment <= 0) return ((angleDeg % 360) + 360) % 360;
    const snapped = Math.round(angleDeg / increment) * increment;
    return ((snapped % 360) + 360) % 360;
}

/**
 * Count how many points in `allPositions` fall within `radius` metres of
 * `position` (Euclidean distance, exclusive of the query point itself).
 *
 * Used to decide whether a region is "dense" and should prefer grid snap.
 */
export function clusterDensityAt(
    position: { x: number; y: number },
    allPositions: { x: number; y: number }[],
    radius: number = 0.1,
): number {
    let count = 0;
    for (const p of allPositions) {
        const dx = p.x - position.x;
        const dy = p.y - position.y;
        const dist = Math.sqrt(dx * dx + dy * dy);
        if (dist <= radius + Number.EPSILON && dist > Number.EPSILON) {
            count++;
        }
    }
    return count;
}

/**
 * Snap `position` to the nearest wall edge, offset by half the object's
 * footprint so the object touches — but does not clip — the wall.
 *
 * For each wall segment the function computes the closest point on the
 * segment, then offsets the position perpendicular to the wall by
 * `footprint.width/2` (for vertical-ish walls) or `footprint.depth/2`
 * (for horizontal-ish walls).  The wall with the smallest raw distance wins.
 *
 * Returns the original position unchanged if `walls` is empty.
 */
export function wallProximitySnap(
    position: { x: number; y: number },
    walls:    Wall[],
    footprint: { width: number; depth: number },
): { x: number; y: number } {
    if (walls.length === 0) return { x: position.x, y: position.y };

    let bestDist = Infinity;
    let bestPos: { x: number; y: number } = { x: position.x, y: position.y };

    for (const wall of walls) {
        const wx = wall.end.x - wall.start.x;
        const wy = wall.end.y - wall.start.y;
        const wallLen = Math.sqrt(wx * wx + wy * wy);
        if (wallLen < Number.EPSILON) continue;

        // Project position onto the wall segment
        const tx = position.x - wall.start.x;
        const ty = position.y - wall.start.y;
        const t = Math.max(0, Math.min(1, (tx * wx + ty * wy) / (wallLen * wallLen)));
        const closestX = wall.start.x + t * wx;
        const closestY = wall.start.y + t * wy;

        const rawDx = position.x - closestX;
        const rawDy = position.y - closestY;
        const rawDist = Math.sqrt(rawDx * rawDx + rawDy * rawDy);

        if (rawDist < bestDist) {
            bestDist = rawDist;

            // Wall unit direction + perpendicular (normal) pointing away from wall
            const wdx = wx / wallLen;
            const wdy = wy / wallLen;
            const nx = -wdy;   // perpendicular (left-hand normal)
            const ny =  wdx;

            // Determine which side of the wall we're on
            const side = (rawDx * nx + rawDy * ny) >= 0 ? 1 : -1;

            // Choose offset based on whether the wall is more horizontal or vertical
            const isHorizontal = Math.abs(wdx) > Math.abs(wdy);
            const halfOffset = isHorizontal ? footprint.depth / 2 : footprint.width / 2;

            bestPos = {
                x: closestX + side * nx * halfOffset,
                y: closestY + side * ny * halfOffset,
            };
        }
    }

    return bestPos;
}

/**
 * Phase 23 composite snap orchestrator.
 *
 * Precedence:
 *   1. Dense-cluster check: if ≥ density_threshold (default 5) neighbours are
 *      within 0.1 m, force grid snap (prevents chaotic object-snap in crowds).
 *   2. Wall proximity snap: if any wall is closer than gridSize/2, snap to it.
 *   3. Grid snap: always applied as the base fallback.
 *   4. Rotation snap: always applied.
 *
 * Sub-mm precision: all arithmetic uses floats; `Number.EPSILON` guards
 * equality comparisons; final coordinates are never rounded to integers.
 */
export function compositeSnapV2(input: SnapInput): CompositeSnapResult {
    const {
        position,
        rotation,
        allPositions,
        walls,
        footprint,
        gridSize,
        density_threshold = 5,
        rotation_increment = 15,
    } = input;

    let pos = { x: position.x, y: position.y };
    const applied: string[] = [];

    // 1. Dense-cluster check
    const density = clusterDensityAt(pos, allPositions, 0.1);
    const isDense = density >= density_threshold;

    if (!isDense && walls.length > 0) {
        // 2. Wall proximity snap (only in sparse scenes)
        const halfGrid = gridSize / 2;
        let nearestWallDist = Infinity;
        for (const wall of walls) {
            const wx = wall.end.x - wall.start.x;
            const wy = wall.end.y - wall.start.y;
            const wallLen = Math.sqrt(wx * wx + wy * wy);
            if (wallLen < Number.EPSILON) continue;
            const tx = pos.x - wall.start.x;
            const ty = pos.y - wall.start.y;
            const t = Math.max(0, Math.min(1, (tx * wx + ty * wy) / (wallLen * wallLen)));
            const cx = wall.start.x + t * wx;
            const cy = wall.start.y + t * wy;
            const d = Math.sqrt((pos.x - cx) ** 2 + (pos.y - cy) ** 2);
            if (d < nearestWallDist) nearestWallDist = d;
        }
        if (nearestWallDist < halfGrid) {
            pos = wallProximitySnap(pos, walls, footprint);
            applied.push("wall");
        }
    }

    // 3. Grid snap (always; also the dense-cluster path's primary snap)
    const gridSnapped = snapToGridSimple(pos, gridSize);
    if (
        Math.abs(gridSnapped.x - pos.x) > Number.EPSILON ||
        Math.abs(gridSnapped.y - pos.y) > Number.EPSILON
    ) {
        if (isDense) applied.push("grid_dense_cluster");
        else applied.push("grid");
    }
    pos = gridSnapped;

    // 4. Rotation snap
    const snappedRotation = snapRotation(rotation, rotation_increment);
    if (Math.abs(snappedRotation - rotation) > Number.EPSILON) {
        applied.push("rotation");
    }

    return { position: pos, rotation: snappedRotation, applied };
}

/** Polar tracking: angle-snap during drag at 0/45/90/135/180 degrees from
 * a fixed origin.  Returns the snapped position projected onto the nearest
 * polar ray, or unchanged if no ray within threshold. */
export function polarTrack(
    pos: Position,
    origin: Position,
    pxPerM: number,
    enabled: boolean = true,
    increments: number[] = [0, 45, 90, 135, 180, -45, -90, -135],
): SnapResult {
    if (!enabled) return { snapped: pos, type: "none", guideLines: [] };
    const dx = pos.x - origin.x;
    const dy = pos.y - origin.y;
    const dist = Math.hypot(dx, dy);
    if (dist < 0.05) return { snapped: pos, type: "none", guideLines: [] };
    const angleDeg = (Math.atan2(dy, dx) * 180) / Math.PI;
    let bestDelta = Infinity;
    let bestAngle = angleDeg;
    for (const inc of increments) {
        const delta = Math.abs(((angleDeg - inc + 540) % 360) - 180);
        if (delta < bestDelta) {
            bestDelta = delta;
            bestAngle = inc;
        }
    }
    // 5° tolerance
    if (bestDelta < 5) {
        const rad = (bestAngle * Math.PI) / 180;
        const snapped: Position = {
            x: origin.x + dist * Math.cos(rad),
            y: origin.y + dist * Math.sin(rad),
        };
        return {
            snapped,
            type: "polar",
            guideLines: [
                {
                    axis: "x",
                    coord: 0, // not used for polar — guide is a ray, not an axis line
                    fromX: origin.x,
                    toX: snapped.x,
                    fromY: origin.y,
                    toY: snapped.y,
                    label: `${bestAngle}°`,
                },
            ],
        };
    }
    return { snapped: pos, type: "none", guideLines: [] };
}
