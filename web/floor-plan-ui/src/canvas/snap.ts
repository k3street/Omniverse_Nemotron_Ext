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
