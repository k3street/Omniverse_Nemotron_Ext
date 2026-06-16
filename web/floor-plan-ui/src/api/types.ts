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

export interface LocalAssetOption {
    label: string;
    usd_ref: string;
    source: string;
    category: string;
    relative_path: string;
    tags: string[];
    score: number;
}

export interface LocalAssetOptionsResponse {
    status: "success";
    roots: string[];
    query: string;
    count: number;
    options: LocalAssetOption[];
}

export interface RoleBinding {
    object_id: string;
    source: BindingSource;
    confidence: number;
    timestamp: string;         // ISO 8601
}

export type SpatialRelationKind =
    | "on_top_of"
    | "inside"
    | "contains"
    | "supports"
    | "attached_to"
    | "mounted_to"
    | "beside"
    | "near"
    | "left_of"
    | "right_of"
    | "front_of"
    | "behind"
    | "stacked_above";

export type LightingPreset =
    | "studio"
    | "warehouse_dim"
    | "warehouse_bright"
    | "backlit"
    | "dome_overcast"
    | "low_angle";

export type CameraPreset =
    | "overhead"
    | "robot_view"
    | "side_view"
    | "wide_context";

export type ActorPreset =
    | "human_observer"
    | "forklift_nearby"
    | "mobile_robot_crossing";

export type CircumstancePreset =
    | "nominal"
    | "occluded_target"
    | "distractor_objects"
    | "moved_target"
    | "tight_clearance";

export interface SpatialRelation {
    subject_id: string;
    relation: SpatialRelationKind;
    object_id: string;
    confidence: number;
    source: string;
    metadata: Record<string, unknown>;
}

export interface PerturbationSpec {
    enabled: boolean;
    pose_jitter_m: number;
    rotation_jitter_deg: number;
    material_randomization: boolean;
    sensor_noise: boolean;
}

export interface ValidationSpec {
    require_relations: boolean;
    require_visibility: boolean;
    require_physics: boolean;
}

export interface ScenarioVariants {
    enabled: boolean;
    variant_count: number;
    seed: number;
    lighting: LightingPreset[];
    cameras: CameraPreset[];
    actors: ActorPreset[];
    circumstances: CircumstancePreset[];
    perturbations: PerturbationSpec;
    validation: ValidationSpec;
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
    relations?: SpatialRelation[];
    scenario_variants?: ScenarioVariants;
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

export interface AssetResolutionSummary {
    object_id: string;
    object_class: string;
    usd_ref: string;
    source: string;
    needs_review: boolean;
}

export interface BuildResponse {
    ratified: boolean;
    status?: string;
    diagnostics?: unknown[];
    errors?: unknown[];
    revision: number;
    bindings?: Record<string, unknown>;
    ambiguous_roles?: unknown[];
    asset_resolutions?: AssetResolutionSummary[];
    instantiation?: {
        status: string;
        message?: string;
        build_id?: string | null;
        dry_run: boolean;
        generated_code?: string | null;
        relation_summary?: Array<{
            subject_id: string;
            subject_name: string;
            relation: string;
            object_id: string;
            object_name: string;
            source?: string;
            confidence?: number;
        }>;
        relation_diagnostics?: Array<{
            severity: string;
            code: string;
            message: string;
            subject_id?: string;
            object_id?: string;
            relation?: string;
            normalized_relation?: string | null;
        }>;
        relation_verification?: {
            status: string;
            check_count: number;
            failed_count: number;
            checks: Array<{
                subject_id: string;
                relation: string;
                object_id: string;
                status: string;
                message: string;
                error_m: number;
                expected_position?: [number, number, number] | null;
                actual_position?: [number, number, number] | null;
            }>;
            predicted_positions?: Record<string, [number, number, number]>;
            diagnostics?: unknown[];
        };
        variant_summary?: ScenarioVariants;
    };
}

export type RenderingMode = "fast" | "real";

export interface RenderingModeResponse {
    status?: string;
    mode: RenderingMode;
    render_enabled: boolean;
    control_file?: string | null;
    kit_applied?: boolean;
    kit_output?: string;
}

export interface CampaignVariantPlan {
    variant_id: string;
    index: number;
    seed: number;
    lighting: string;
    camera: string;
    actor: string;
    circumstance: string;
    perturbations: PerturbationSpec;
    validation: ValidationSpec;
    usd_path: string;
    launch_command: string;
}

export interface CampaignPlanResponse {
    campaign_id: string;
    session_id: string;
    revision: number;
    enabled: boolean;
    workspace_dir: string;
    variant_count: number;
    summary: ScenarioVariants;
    variants: CampaignVariantPlan[];
    execution: {
        status: string;
        local_supported: boolean;
        remote_supported: boolean;
        remote_note: string;
    };
}

export interface CampaignLaunchResponse {
    campaign: CampaignPlanResponse;
    launch: {
        variant_id: string;
        status: string;
        timestamp: number;
        usd_path: string;
        setup_script_path: string;
        log_path: string;
        command: string;
        pid?: number;
        returncode?: number;
        preflight: Record<string, unknown>;
        verification: Record<string, unknown>;
    };
}

export interface CosmosObserveRequest {
    prompt: string;
    image_base64?: string;
    mime_type?: string;
    input_kind?: "photo" | "screenshot" | "render" | "video_frame" | "prompt";
    parent_revision: number;
}

export interface CosmosViewportObserveRequest {
    prompt: string;
    max_dim?: number;
    parent_revision: number;
}

export interface CosmosObserveResponse {
    valid: true;
    revision: number;
    spec: LayoutSpec;
    observation: unknown;
}

export interface CosmosViewportObserveResponse extends CosmosObserveResponse {
    viewport_capture?: {
        width?: number;
        height?: number;
        max_dim: number;
    };
}

export interface ConflictDetail {
    conflict: true;
    expected_revision: number;
    actual_revision: number;
    current_spec: LayoutSpec | null;
}
