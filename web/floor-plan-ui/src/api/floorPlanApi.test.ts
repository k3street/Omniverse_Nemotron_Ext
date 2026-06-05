import { afterEach, describe, expect, it, vi } from "vitest";
import { createCanvasApi, normalizeLayoutSpec } from "./floorPlanApi";
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

afterEach(() => {
    vi.restoreAllMocks();
});

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

describe("createCanvasApi", () => {
    it("posts viewport observation requests to the Cosmos viewport route", async () => {
        const spec = baseSpec();
        const fetchMock = vi.fn().mockResolvedValue({
            ok: true,
            json: async () => ({
                valid: true,
                revision: 2,
                spec,
                observation: {},
                viewport_capture: {
                    width: 1280,
                    height: 720,
                    max_dim: 1280,
                },
            }),
        });
        vi.stubGlobal("fetch", fetchMock);

        const api = createCanvasApi("");
        const response = await api.cosmosObserveViewport("session one", {
            prompt: "Seed this from the live scene",
            max_dim: 1280,
            parent_revision: 1,
        });

        expect(fetchMock).toHaveBeenCalledWith(
            "/api/v1/canvas/session%20one/cosmos/observe_viewport",
            {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    prompt: "Seed this from the live scene",
                    max_dim: 1280,
                    parent_revision: 1,
                }),
            },
        );
        expect(response.revision).toBe(2);
        expect(response.viewport_capture?.height).toBe(720);
    });
});
