import { describe, expect, it } from "vitest";
import { normalizeLayoutSpec } from "./floorPlanApi";
import { LayoutSpec, TypedObject } from "./types";

function baseSpec(): LayoutSpec {
    return {
        version: "1.0",
        intent: {
            pattern_hint: "pick_place",
            counts: {
                robots: 0,
                conveyors: 0,
                bins: 0,
                cubes: 0,
                sensors: 0,
                humans: 0,
            },
            structural_features: {
                n_robot_stations: 1,
                n_handoffs: 0,
                n_destinations: 1,
                destination_kind: "single_bin",
                routing_axis: null,
                uses_conveyor_transport: false,
                uses_navigation: false,
                has_color_routing: false,
                has_orientation_requirement: false,
                has_bounded_footprint: false,
                has_passive_intermediate_station: false,
                has_active_intermediate_station: false,
                has_human_in_workspace: false,
                has_floor_transitions: false,
                footprint_xy_max_m: null,
                upright_dot_threshold: null,
                human_safety_distance_m: null,
            },
            structural_tags: [],
        },
        objects: [],
        parameters: {},
        source: {
            modality: "photo",
            confidence: 0.8,
            timestamp: "2026-06-05T00:00:00Z",
            metadata: {},
        },
        revision: 1,
    };
}

describe("normalizeLayoutSpec", () => {
    it("maps backend object_class fields to frontend class aliases", () => {
        const spec = baseSpec();
        spec.objects = [
            {
                id: "obj-1",
                object_class: "franka_panda",
                name: "Franka_1",
                position: { x: 0, y: 0 },
                rotation: 0,
                size: { w: 0.4, h: 0.4 },
                notes: "",
                notes_sensitive: false,
                metadata: {},
                locked: false,
                layer: "cosmos_proposal",
            } as unknown as TypedObject,
        ];

        const normalized = normalizeLayoutSpec(spec);

        expect(normalized?.objects?.[0].class).toBe("franka_panda");
        expect("object_class" in (normalized?.objects?.[0] ?? {})).toBe(false);
    });
});
