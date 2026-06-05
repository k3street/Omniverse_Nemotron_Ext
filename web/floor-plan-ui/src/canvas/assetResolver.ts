import { TypedObject } from "../api/types";
import { BUILD_ASSET_CLASSES, CLASS_META } from "./objectClasses";

export interface AssetResolutionFeedback {
    source: "cosmos" | "manual";
    label: string | null;
    assetHint: string | null;
    confidence: number | null;
    selectedClass: string;
    selectedLabel: string;
    needsReview: boolean;
    summary: string;
}

function asString(value: unknown): string | null {
    return typeof value === "string" && value.trim() ? value.trim() : null;
}

function asNumber(value: unknown): number | null {
    return typeof value === "number" && Number.isFinite(value) ? value : null;
}

export function assetResolutionFeedback(obj: TypedObject): AssetResolutionFeedback {
    const metadata = obj.metadata ?? {};
    const label = asString(metadata.cosmos_label);
    const assetHint = asString(metadata.cosmos_asset_hint);
    const confidence = asNumber(metadata.cosmos_confidence);
    const selectedLabel = CLASS_META[obj.class]?.label ?? obj.class;
    const source = label || assetHint || confidence !== null ? "cosmos" : "manual";
    const lowConfidence = confidence !== null && confidence < 0.7;
    const fallbackClass = obj.class === "obstacle_box";
    const hintMismatch = Boolean(assetHint && !obj.class.toLowerCase().includes(assetHint.toLowerCase()));
    const needsReview = source === "cosmos" && (lowConfidence || fallbackClass || hintMismatch);
    const summary = source === "cosmos"
        ? `${label ?? "Cosmos object"} -> ${selectedLabel}`
        : `Manual object -> ${selectedLabel}`;

    return {
        source,
        label,
        assetHint,
        confidence,
        selectedClass: obj.class,
        selectedLabel,
        needsReview,
        summary,
    };
}

export function buildAssetOptions(): Array<{ value: string; label: string }> {
    return BUILD_ASSET_CLASSES.map((value) => ({
        value,
        label: CLASS_META[value]?.label ?? value,
    }));
}
