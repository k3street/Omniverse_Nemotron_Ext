/**
 * Phase 23 snap engine hardening — vitest fixture suite.
 * Gate: ≥ 20 fixture cases, all passing.
 */

import { describe, it, expect } from "vitest";
import {
    snapToGridSimple,
    snapRotation,
    clusterDensityAt,
    wallProximitySnap,
    compositeSnapV2,
    type Wall,
} from "./snap";

// ─── snapToGridSimple ──────────────────────────────────────────────────────

describe("snapToGridSimple", () => {
    it("T01 snaps to nearest 0.1m grid point", () => {
        const result = snapToGridSimple({ x: 0.14, y: 0.26 }, 0.1);
        expect(result.x).toBeCloseTo(0.1, 10);
        expect(result.y).toBeCloseTo(0.3, 10);
    });

    it("T02 snaps exactly on grid stays on grid", () => {
        const result = snapToGridSimple({ x: 0.5, y: 1.0 }, 0.5);
        expect(result.x).toBeCloseTo(0.5, 10);
        expect(result.y).toBeCloseTo(1.0, 10);
    });

    it("T03 sub-mm delta preserved — 0.0005 rounds to nearest, not truncated", () => {
        // 0.1005 → nearest 0.1 grid = 0.1 (delta 0.0005 < 0.05 half-cell)
        const result = snapToGridSimple({ x: 0.1005, y: 0.0 }, 0.1);
        expect(result.x).toBeCloseTo(0.1, 10);
    });

    it("T04 sub-mm delta on the far side snaps up", () => {
        // 0.0999 → nearest 0.1 grid = 0.1 (delta -0.0001 < 0.05)
        const result = snapToGridSimple({ x: 0.0999, y: 0.0 }, 0.1);
        expect(result.x).toBeCloseTo(0.1, 10);
    });

    it("T05 negative coordinates snap correctly", () => {
        const result = snapToGridSimple({ x: -0.24, y: -0.76 }, 0.5);
        expect(result.x).toBeCloseTo(0.0, 10);
        expect(result.y).toBeCloseTo(-1.0, 10);
    });

    it("T06 non-uniform grid 0.25m", () => {
        const result = snapToGridSimple({ x: 0.37, y: 0.13 }, 0.25);
        expect(result.x).toBeCloseTo(0.25, 10);
        expect(result.y).toBeCloseTo(0.25, 10);
    });

    it("T07 gridSize ≤ 0 returns position unchanged", () => {
        const result = snapToGridSimple({ x: 1.23, y: 4.56 }, 0);
        expect(result.x).toBeCloseTo(1.23, 10);
        expect(result.y).toBeCloseTo(4.56, 10);
    });
});

// ─── snapRotation ─────────────────────────────────────────────────────────

describe("snapRotation", () => {
    it("T08 snaps 17° to nearest 15° → 15°", () => {
        expect(snapRotation(17)).toBeCloseTo(15, 10);
    });

    it("T09 snaps 22.5° to 15° (halfway rounds up to 30°)", () => {
        // Math.round(22.5/15)*15 = Math.round(1.5)*15 = 2*15 = 30
        expect(snapRotation(22.5)).toBeCloseTo(30, 10);
    });

    it("T10 snaps 0° to 0°", () => {
        expect(snapRotation(0)).toBeCloseTo(0, 10);
    });

    it("T11 snaps 359° → 360° → normalises to 0°", () => {
        expect(snapRotation(359)).toBeCloseTo(0, 10);
    });

    it("T12 custom 45° increment snaps 50° to 45°", () => {
        expect(snapRotation(50, 45)).toBeCloseTo(45, 10);
    });

    it("T13 custom 90° increment snaps 91° to 90°", () => {
        expect(snapRotation(91, 90)).toBeCloseTo(90, 10);
    });

    it("T14 negative input normalises to [0,360)", () => {
        const r = snapRotation(-10);
        expect(r).toBeGreaterThanOrEqual(0);
        expect(r).toBeLessThan(360);
    });
});

// ─── clusterDensityAt ────────────────────────────────────────────────────

describe("clusterDensityAt", () => {
    it("T15 empty list → density 0", () => {
        expect(clusterDensityAt({ x: 0, y: 0 }, [])).toBe(0);
    });

    it("T16 no neighbours within default 0.1m → 0", () => {
        const others = [{ x: 0.5, y: 0.5 }, { x: -1, y: 0 }];
        expect(clusterDensityAt({ x: 0, y: 0 }, others)).toBe(0);
    });

    it("T17 5 points within 0.1m → density 5", () => {
        const cluster: { x: number; y: number }[] = [
            { x: 0.05, y: 0 },
            { x: -0.05, y: 0 },
            { x: 0, y: 0.05 },
            { x: 0, y: -0.05 },
            { x: 0.07, y: 0.07 },
        ];
        expect(clusterDensityAt({ x: 0, y: 0 }, cluster)).toBe(5);
    });

    it("T18 query point itself excluded (dist=0 not counted)", () => {
        const pts = [{ x: 0, y: 0 }, { x: 0.02, y: 0 }];
        // first point is exactly at query position — excluded
        expect(clusterDensityAt({ x: 0, y: 0 }, pts)).toBe(1);
    });

    it("T19 custom radius 0.5m counts farther points", () => {
        const pts = [{ x: 0.4, y: 0 }, { x: -0.4, y: 0 }, { x: 0, y: 0.4 }];
        expect(clusterDensityAt({ x: 0, y: 0 }, pts, 0.5)).toBe(3);
    });
});

// ─── wallProximitySnap ───────────────────────────────────────────────────

describe("wallProximitySnap", () => {
    it("T20 no walls → position unchanged", () => {
        const result = wallProximitySnap({ x: 1, y: 1 }, [], { width: 0.5, depth: 0.5 });
        expect(result.x).toBeCloseTo(1, 10);
        expect(result.y).toBeCloseTo(1, 10);
    });

    it("T21 vertical wall at x=0, object to the right — snapped x = half-width", () => {
        const wall: Wall = { start: { x: 0, y: 0 }, end: { x: 0, y: 5 } };
        const result = wallProximitySnap({ x: 0.3, y: 2 }, [wall], { width: 0.6, depth: 0.4 });
        // Object is right of wall; snapped to wall + half footprint.width = 0.3
        expect(result.x).toBeCloseTo(0.3, 5);
    });

    it("T22 horizontal wall at y=0, object above — snapped y = half-depth", () => {
        const wall: Wall = { start: { x: 0, y: 0 }, end: { x: 5, y: 0 } };
        const result = wallProximitySnap({ x: 2, y: 0.2 }, [wall], { width: 0.4, depth: 0.6 });
        // Object above horizontal wall; snapped to wall + half footprint.depth = 0.3
        expect(result.y).toBeCloseTo(0.3, 5);
    });

    it("T23 offset prevents clipping — result.x > wall.x for right-side object", () => {
        const wall: Wall = { start: { x: 0, y: 0 }, end: { x: 0, y: 5 } };
        const footprint = { width: 0.8, depth: 0.4 };
        const result = wallProximitySnap({ x: 0.1, y: 2 }, [wall], footprint);
        // Must NOT be inside the wall (x should be >= 0)
        expect(result.x).toBeGreaterThanOrEqual(0);
    });

    it("T24 nearest wall wins when multiple walls present", () => {
        const wallA: Wall = { start: { x: 0, y: 0 }, end: { x: 0, y: 10 } };   // at x=0
        const wallB: Wall = { start: { x: 10, y: 0 }, end: { x: 10, y: 10 } }; // at x=10
        const result = wallProximitySnap({ x: 0.2, y: 5 }, [wallA, wallB], { width: 0.4, depth: 0.4 });
        // Closer to wallA (distance 0.2) than wallB (distance 9.8)
        expect(result.x).toBeLessThan(5);
    });
});

// ─── compositeSnapV2 ────────────────────────────────────────────────────

describe("compositeSnapV2", () => {
    it("T25 sparse scene with no walls → grid snap fires", () => {
        const result = compositeSnapV2({
            position: { x: 0.14, y: 0.26 },
            rotation: 0,
            allPositions: [],
            walls: [],
            footprint: { width: 0.5, depth: 0.5 },
            gridSize: 0.1,
        });
        expect(result.applied).toContain("grid");
        expect(result.position.x).toBeCloseTo(0.1, 10);
        expect(result.position.y).toBeCloseTo(0.3, 10);
    });

    it("T26 dense cluster (≥5 within 0.1m) fires grid_dense_cluster", () => {
        // Cluster neighbours all within 0.1m of the query position {x:0.14, y:0.14}
        const qx = 0.14, qy = 0.14;
        const cluster = [
            { x: qx + 0.05, y: qy },
            { x: qx - 0.05, y: qy },
            { x: qx, y: qy + 0.05 },
            { x: qx, y: qy - 0.05 },
            { x: qx + 0.06, y: qy + 0.06 },
        ];
        const result = compositeSnapV2({
            position: { x: qx, y: qy },
            rotation: 0,
            allPositions: cluster,
            walls: [],
            footprint: { width: 0.5, depth: 0.5 },
            gridSize: 0.1,
        });
        expect(result.applied).toContain("grid_dense_cluster");
        expect(result.applied).not.toContain("wall");
    });

    it("T27 wall close enough fires wall snap in sparse scene", () => {
        const wall: Wall = { start: { x: 0, y: 0 }, end: { x: 0, y: 10 } };
        const result = compositeSnapV2({
            position: { x: 0.04, y: 5 },   // 0.04m from wall, gridSize/2 = 0.05
            rotation: 0,
            allPositions: [],
            walls: [wall],
            footprint: { width: 0.4, depth: 0.4 },
            gridSize: 0.1,
        });
        expect(result.applied).toContain("wall");
    });

    it("T28 rotation snap fires when angle is off-increment", () => {
        const result = compositeSnapV2({
            position: { x: 0, y: 0 },
            rotation: 17,
            allPositions: [],
            walls: [],
            footprint: { width: 0.5, depth: 0.5 },
            gridSize: 0.1,
        });
        expect(result.applied).toContain("rotation");
        expect(result.rotation).toBeCloseTo(15, 10);
    });

    it("T29 applied list reflects all fired modes in pipeline", () => {
        const wall: Wall = { start: { x: 0, y: 0 }, end: { x: 0, y: 10 } };
        const result = compositeSnapV2({
            position: { x: 0.04, y: 5 },
            rotation: 17,
            allPositions: [],
            walls: [wall],
            footprint: { width: 0.4, depth: 0.4 },
            gridSize: 0.1,
        });
        expect(result.applied.length).toBeGreaterThanOrEqual(2);
        expect(result.applied).toContain("wall");
        expect(result.applied).toContain("rotation");
    });

    it("T30 sub-mm delta (0.0005) survives through full pipeline", () => {
        // Position is 0.0005 above a clean 0.1 grid point
        const result = compositeSnapV2({
            position: { x: 0.1005, y: 0.2005 },
            rotation: 0,
            allPositions: [],
            walls: [],
            footprint: { width: 0.4, depth: 0.4 },
            gridSize: 0.1,
        });
        // snapToGridSimple rounds 0.1005 to 0.1 — the float must be exact, not truncated
        expect(result.position.x).toBeCloseTo(0.1, 12);
        expect(result.position.y).toBeCloseTo(0.2, 12);
        // Verify float not truncated to integer
        expect(Number.isInteger(result.position.x)).toBe(false);
    });

    it("T31 exactly on-grid position returns empty or grid in applied", () => {
        const result = compositeSnapV2({
            position: { x: 0.5, y: 1.0 },
            rotation: 0,
            allPositions: [],
            walls: [],
            footprint: { width: 0.5, depth: 0.5 },
            gridSize: 0.5,
        });
        // Position already on grid — grid snap diff is ≤ EPSILON, so not pushed
        // Rotation is 0° which is on-increment — not pushed
        expect(result.applied).toHaveLength(0);
        expect(result.position.x).toBeCloseTo(0.5, 10);
        expect(result.position.y).toBeCloseTo(1.0, 10);
    });

    it("T32 custom density_threshold=3 fires dense-cluster with 3 neighbours", () => {
        // Use a position that's off-grid so grid snap diff > EPSILON and label gets pushed
        const qx = 0.53, qy = 0.53;
        const cluster = [
            { x: qx + 0.05, y: qy },
            { x: qx - 0.05, y: qy },
            { x: qx, y: qy + 0.05 },
        ];
        const result = compositeSnapV2({
            position: { x: qx, y: qy },
            rotation: 0,
            allPositions: cluster,
            walls: [],
            footprint: { width: 0.5, depth: 0.5 },
            gridSize: 0.1,
            density_threshold: 3,
        });
        expect(result.applied).toContain("grid_dense_cluster");
    });

    it("T33 custom rotation_increment=45° snaps 50° to 45°", () => {
        const result = compositeSnapV2({
            position: { x: 0, y: 0 },
            rotation: 50,
            allPositions: [],
            walls: [],
            footprint: { width: 0.5, depth: 0.5 },
            gridSize: 0.5,
            rotation_increment: 45,
        });
        expect(result.rotation).toBeCloseTo(45, 10);
    });

    it("T34 dense cluster suppresses wall snap", () => {
        const qx = 0.04, qy = 0.04;
        const cluster = [
            { x: qx + 0.05, y: qy },
            { x: qx - 0.05, y: qy },
            { x: qx, y: qy + 0.05 },
            { x: qx, y: qy - 0.05 },
            { x: qx + 0.07, y: qy + 0.07 },
        ];
        const wall: Wall = { start: { x: 0, y: 0 }, end: { x: 0, y: 10 } };
        const result = compositeSnapV2({
            position: { x: qx, y: qy },
            rotation: 0,
            allPositions: cluster,
            walls: [wall],
            footprint: { width: 0.4, depth: 0.4 },
            gridSize: 0.1,
        });
        // Dense cluster path skips wall snap
        expect(result.applied).not.toContain("wall");
        expect(result.applied).toContain("grid_dense_cluster");
    });
});
