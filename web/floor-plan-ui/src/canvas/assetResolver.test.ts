import { describe, expect, it } from "vitest";
import { TypedObject } from "../api/types";
import { assetResolutionFeedback, buildAssetOptions } from "./assetResolver";

function objectWith(metadata: Record<string, unknown>, objectClass = "obstacle_box"): TypedObject {
    return {
        id: "obj-1",
        class: objectClass,
        name: "Object_1",
        position: { x: 0, y: 0 },
        rotation: 0,
        size: { w: 0.5, h: 0.5 },
        notes: "",
        notes_sensitive: false,
        metadata,
        locked: false,
        layer: "cosmos_proposal",
    };
}

describe("assetResolutionFeedback", () => {
    it("marks low-confidence Cosmos fallback classes for review", () => {
        const feedback = assetResolutionFeedback(objectWith({
            cosmos_label: "unknown fixture",
            cosmos_confidence: 0.48,
        }));

        expect(feedback.source).toBe("cosmos");
        expect(feedback.needsReview).toBe(true);
        expect(feedback.summary).toContain("unknown fixture");
    });

    it("accepts confident canonical classes", () => {
        const feedback = assetResolutionFeedback(objectWith({
            cosmos_label: "target bin",
            cosmos_asset_hint: "bin",
            cosmos_confidence: 0.91,
        }, "bin"));

        expect(feedback.needsReview).toBe(false);
        expect(feedback.selectedLabel).toBe("Bin");
    });
});

describe("buildAssetOptions", () => {
    it("includes backend Cosmos canonical classes", () => {
        const values = buildAssetOptions().map((option) => option.value);

        expect(values).toContain("cube_medium");
        expect(values).toContain("table_medium");
        expect(values).toContain("conveyor_short");
    });
});
