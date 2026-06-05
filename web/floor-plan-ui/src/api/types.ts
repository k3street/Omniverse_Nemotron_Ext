/**
 * TypeScript mirror of service/isaac_assist_service/multimodal/types.py.
 *
 * Manually kept in sync with the Pydantic LayoutSpec schema.  Backend is the
 * source of truth; this file translates it into TypeScript shapes for the
 * Konva SPA.  When the backend schema bumps version, update both.
 */

// ─── L0 — closed enums ────────────────────────────────────────────────

export type PatternHint =
    | "pick_place"
    | "sort"
    | "reorient"
    | "navigate";

export type Modality =
    | "text"
    | "sketch"
    | "drag_drop"
    | "photo"
    | "voice"
    | "viewport";

export type BindingSource =
    | "user_explicit"
    | "modality_emitted"
    | "disambiguator"
    | "user_correction";

// ─── L0 — counts ──────────────────────────────────────────────────────

export interface Counts {
    robots: number;
    conveyors: number;
    bins: number;
    cubes: number;
    sensors: number;
    humans: number;
}

// ─── L1 — structural features ─────────────────────────────────────────

export type DestinationKind = "single_bin" | "n_bins_routed" | "shelf" | "fixture";
export type RoutingAxis = "color" | "size" | "shape" | "label";

export interface StructuralFeatures {
    n_robot_stations: number;
    n_handoffs: number;
    n_destinations: number;
    destination_kind: DestinationKind;
    routing_axis: RoutingAxis | null;
    uses_conveyor_transport: boolean;
    uses_navigation: boolean;
    has_color_routing: boolean;
    has_orientation_requirement: boolean;
    has_bounded_footprint: boolean;
    has_passive_intermediate_station: boolean;
    has_active_intermediate_station: boolean;
    has_human_in_workspace: boolean;
    has_floor_transitions: boolean;
    footprint_xy_max_m: [number, number] | null;
    upright_dot_threshold: number | null;
    human_safety_distance_m: number | null;
}

// ─── L2 — namespaced tags ─────────────────────────────────────────────

/**
 * Format: `<namespace>:<segment>(.<segment>)*` where namespace ∈
 * {`isaac`, `cad`, `user`}.  Backend validates registry membership for
 * isaac:/cad:; user:-namespace tags pass through as observability-only.
 */
export type StructuralTag = string;

export const STRUCTURAL_TAG_FORMAT = /^(isaac|cad|user):[a-z0-9_]+(\.[a-z0-9_]+)*$/;

// ─── Intent + objects + bindings ──────────────────────────────────────

export interface Intent {
    pattern_hint: PatternHint;
    counts: Counts;
    structural_features: StructuralFeatures;
    structural_tags: StructuralTag[];
}

export interface Position { x: number; y: number; }
export interface Size { w: number; h: number; }

export interface TypedObject {
    id: string;
    class: string;
    name: string;
    position: Position;
    rotation: number;          // degrees, [0, 360)
    size: Size;
    color?: string;            // hex
    notes: string;
    notes_sensitive: boolean;
    metadata: Record<string, unknown>;
    role_hint?: string;
    locked: boolean;
    layer: string;
}

export interface RoleBinding {
    object_id: string;
    source: BindingSource;
    confidence: number;
    timestamp: string;         // ISO 8601
}

export interface Source {
    modality: Modality;
    confidence: number;
    timestamp: string;
    raw_input?: unknown;
    metadata: Record<string, unknown>;
}

// ─── Top-level LayoutSpec ─────────────────────────────────────────────

export interface LayoutSpec {
    version: "1.0";
    intent: Intent;
    objects?: TypedObject[];
    constraints?: unknown[];   // typed in v1.1
    bindings?: Record<string, RoleBinding>;
    parameters: Record<string, unknown>;
    source: Source;
    revision: number;
}

// ─── API response shapes ──────────────────────────────────────────────

export interface CanvasGetResponse {
    session_id: string;
    spec: LayoutSpec | null;
    revision: number;
}

export interface PatchSuccessResponse {
    valid: true;
    revision: number;
    spec: LayoutSpec;
}

export interface PatchValidationFailureResponse {
    valid: false;
    issues: Array<{ code: string; severity: string; message: string }>;
}

export interface ConflictDetail {
    conflict: true;
    expected_revision: number;
    actual_revision: number;
    current_spec: LayoutSpec | null;
}
