/**
 * Properties panel — right-dock mutation surface (spec §11.3.4).
 *
 * Single-select: full editor (name, position, size, rotation, color,
 * notes, layer, locked).  Multi-select: shared values shown; mixed
 * fields display "(multiple)" placeholder; bulk-edit applies to all.
 *
 * All mutations route through the Zustand store's command-pattern API
 * so undo/redo works.  Numeric inputs commit on Enter or blur.
 */
import { useEffect, useState } from "react";
import { useFloorPlanStore } from "../store/floorPlanStore";
import {
    ActorPreset,
    CameraPreset,
    CampaignPlanResponse,
    CircumstancePreset,
    LightingPreset,
    ScenarioVariants,
    CampaignLaunchResponse,
    SpatialRelation,
    SpatialRelationKind,
    TypedObject,
} from "../api/types";
import { createCanvasApi, DEFAULT_SCENARIO_VARIANTS } from "../api/floorPlanApi";
import { flushPendingPatch } from "../store/sync";
import { buildAssetOptions, assetResolutionFeedback } from "../canvas/assetResolver";
import { CLASS_META } from "../canvas/objectClasses";

const PANEL_BG = "#171A1E";
const SECTION_BG = "#1E2228";
const TEXT = "#DDDDDD";
const TEXT_DIM = "#8A8E92";
const BORDER = "#2E3237";
const ACCENT = "#76B900";
const ASSET_OPTIONS = buildAssetOptions();
const api = createCanvasApi("");
type ParentRelationKind = Exclude<SpatialRelationKind, "contains" | "supports">;
type ParentSpatialRelation = SpatialRelation & { relation: ParentRelationKind };
const PARENT_RELATION_OPTIONS: Array<{ value: ParentRelationKind; label: string }> = [
    { value: "inside", label: "inside" },
    { value: "on_top_of", label: "on top of" },
    { value: "mounted_to", label: "mounted to" },
    { value: "beside", label: "beside" },
    { value: "attached_to", label: "attached to" },
    { value: "near", label: "near" },
    { value: "left_of", label: "left of" },
    { value: "right_of", label: "right of" },
    { value: "front_of", label: "in front of" },
    { value: "behind", label: "behind" },
    { value: "stacked_above", label: "stacked above" },
];
const LIGHTING_OPTIONS: Array<{ value: LightingPreset; label: string }> = [
    { value: "studio", label: "studio" },
    { value: "warehouse_dim", label: "warehouse dim" },
    { value: "warehouse_bright", label: "warehouse bright" },
    { value: "backlit", label: "backlit" },
    { value: "dome_overcast", label: "dome overcast" },
    { value: "low_angle", label: "low angle" },
];
const CAMERA_OPTIONS: Array<{ value: CameraPreset; label: string }> = [
    { value: "overhead", label: "overhead" },
    { value: "robot_view", label: "robot view" },
    { value: "side_view", label: "side view" },
    { value: "wide_context", label: "wide context" },
];
const ACTOR_OPTIONS: Array<{ value: ActorPreset; label: string }> = [
    { value: "human_observer", label: "human observer" },
    { value: "forklift_nearby", label: "forklift nearby" },
    { value: "mobile_robot_crossing", label: "mobile robot crossing" },
];
const CIRCUMSTANCE_OPTIONS: Array<{ value: CircumstancePreset; label: string }> = [
    { value: "nominal", label: "nominal" },
    { value: "occluded_target", label: "occluded target" },
    { value: "distractor_objects", label: "distractor objects" },
    { value: "moved_target", label: "moved target" },
    { value: "tight_clearance", label: "tight clearance" },
];

export function PropertiesPanel() {
    const selectedIds = useFloorPlanStore((s) => s.selectedIds);
    const objects = useFloorPlanStore((s) => s.spec?.objects ?? []);
    const spec = useFloorPlanStore((s) => s.spec);
    const selectedObjects = objects.filter((o) => selectedIds.includes(o.id));

    return (
        <div
            style={{
                width: 280,
                background: PANEL_BG,
                borderLeft: `1px solid ${BORDER}`,
                color: TEXT,
                fontSize: 12,
                overflowY: "auto",
                display: "flex",
                flexDirection: "column",
            }}
            data-testid="properties-panel"
        >
            <Tab title="PROPERTIES" />
            {selectedObjects.length === 0 ? (
                <SceneSummary />
            ) : selectedObjects.length === 1 ? (
                <SingleObjectEditor obj={selectedObjects[0]} />
            ) : (
                <MultiObjectEditor objs={selectedObjects} />
            )}

            <Tab title="LAYERS" />
            <LayersPanel />

            <Tab title="CONSTRAINTS" />
            <ConstraintsPanel />

            <Tab title="RELATIONS" />
            <RelationsPanel />

            <Tab title="SCENARIO VARIANTS" />
            <ScenarioVariantsPanel />

            <Tab title="SCENE" />
            {spec && <IntentSummary />}
        </div>
    );
}

function Tab({ title }: { title: string }) {
    return (
        <div
            style={{
                fontSize: 10,
                fontWeight: 700,
                letterSpacing: 0.6,
                color: TEXT_DIM,
                padding: "10px 12px 6px",
                borderBottom: `1px solid ${BORDER}`,
                background: PANEL_BG,
                position: "sticky",
                top: 0,
                zIndex: 1,
            }}
        >
            {title}
        </div>
    );
}

function SceneSummary() {
    const objCount = useFloorPlanStore((s) => (s.spec?.objects ?? []).length);
    return (
        <div style={{ padding: "12px", color: TEXT_DIM, lineHeight: 1.5 }}>
            <div>{objCount} object{objCount === 1 ? "" : "s"} on canvas</div>
            <div style={{ marginTop: 8, fontSize: 11 }}>
                Select an object on the canvas to edit its properties, or drag from the palette to add.
            </div>
        </div>
    );
}

// ─── Single-object editor ────────────────────────────────────────────

function SingleObjectEditor({ obj }: { obj: TypedObject }) {
    const setAttr = useFloorPlanStore((s) => s.setAttr);
    const moveObject = useFloorPlanStore((s) => s.moveObject);
    const resizeObject = useFloorPlanStore((s) => s.resizeObject);
    const rotateObject = useFloorPlanStore((s) => s.rotateObject);
    const deleteObjects = useFloorPlanStore((s) => s.deleteObjects);

    return (
        <div style={{ padding: "8px 12px 16px", background: SECTION_BG }}>
            <AssetResolverReview obj={obj} />
            <SelectField
                label="Build asset"
                value={obj.class}
                options={ASSET_OPTIONS}
                onCommit={(v) => v !== obj.class && setAttr(obj.id, "class", obj.class, v)}
            />
            <TextField
                label="Name"
                value={obj.name}
                onCommit={(v) => v !== obj.name && setAttr(obj.id, "name", obj.name, v)}
                placeholder="Name"
            />
            <Row>
                <NumField
                    label="x (m)"
                    value={obj.position.x}
                    onCommit={(v) => v !== obj.position.x && moveObject(obj.id, obj.position, { x: v, y: obj.position.y })}
                    step={0.05}
                />
                <NumField
                    label="y (m)"
                    value={obj.position.y}
                    onCommit={(v) => v !== obj.position.y && moveObject(obj.id, obj.position, { x: obj.position.x, y: v })}
                    step={0.05}
                />
            </Row>
            <Row>
                <NumField
                    label="w (m)"
                    value={obj.size.w}
                    onCommit={(v) => v > 0 && v !== obj.size.w && resizeObject(obj.id, obj.size, { w: v, h: obj.size.h })}
                    step={0.05}
                    min={0.01}
                />
                <NumField
                    label="h (m)"
                    value={obj.size.h}
                    onCommit={(v) => v > 0 && v !== obj.size.h && resizeObject(obj.id, obj.size, { w: obj.size.w, h: v })}
                    step={0.05}
                    min={0.01}
                />
            </Row>
            <NumField
                label="Rotation (°)"
                value={obj.rotation}
                onCommit={(v) => {
                    const norm = ((v % 360) + 360) % 360;
                    if (norm !== obj.rotation) rotateObject(obj.id, obj.rotation, norm);
                }}
                step={5}
                disabled={CLASS_META[obj.class]?.rotationLocked}
            />
            <TextField
                label="Color (hex)"
                value={obj.color ?? ""}
                onCommit={(v) => {
                    const next = v.trim() || undefined;
                    if (next !== obj.color) setAttr(obj.id, "color", obj.color, next);
                }}
                placeholder="#RRGGBB"
            />
            <TextField
                label="Layer"
                value={obj.layer}
                onCommit={(v) => v !== obj.layer && setAttr(obj.id, "layer", obj.layer, v)}
            />
            <PlacementRelationEditor obj={obj} />
            <TextField
                label="Notes"
                value={obj.notes}
                onCommit={(v) => v !== obj.notes && setAttr(obj.id, "notes", obj.notes, v)}
                multiline
            />
            <Row>
                <CheckField
                    label="Locked"
                    value={obj.locked}
                    onChange={(v) => setAttr(obj.id, "locked", obj.locked, v)}
                />
                <button
                    type="button"
                    onClick={() => deleteObjects([obj.id])}
                    style={{
                        marginLeft: "auto",
                        background: "transparent",
                        color: "#FF6B6B",
                        border: "1px solid #4A2424",
                        borderRadius: 4,
                        fontSize: 11,
                        padding: "4px 10px",
                        cursor: "pointer",
                    }}
                >
                    Delete
                </button>
            </Row>
        </div>
    );
}

function PlacementRelationEditor({ obj }: { obj: TypedObject }) {
    const objects = useFloorPlanStore((s) => s.spec?.objects ?? []);
    const relations = useFloorPlanStore((s) => s.spec?.relations ?? []);
    const setRelations = useFloorPlanStore((s) => s.setRelations);
    const relation = findParentRelation(obj.id, relations);
    const [targetId, setTargetId] = useState(relation?.object_id ?? "");
    const [kind, setKind] = useState<ParentRelationKind>(relation?.relation ?? "on_top_of");

    useEffect(() => {
        setTargetId(relation?.object_id ?? "");
        setKind(relation?.relation ?? "on_top_of");
    }, [relation?.object_id, relation?.relation]);

    const targetOptions = objects
        .filter((candidate) => candidate.id !== obj.id)
        .map((candidate) => ({ value: candidate.id, label: candidate.name }));
    const currentSummary = relation
        ? `${relationLabel(relation.relation)} ${objects.find((candidate) => candidate.id === relation.object_id)?.name ?? relation.object_id}`
        : "No parent relation";
    const commit = () => {
        if (!targetId) return;
        const next = withoutParentRelation(obj.id, relations);
        next.push({
            subject_id: obj.id,
            relation: kind,
            object_id: targetId,
            confidence: 1,
            source: "user_explicit",
            metadata: {},
        });
        setRelations(next);
    };
    const clear = () => {
        setRelations(withoutParentRelation(obj.id, relations));
    };

    return (
        <div
            style={{
                border: `1px solid ${BORDER}`,
                borderRadius: 4,
                padding: 8,
                marginBottom: 10,
                background: "#15191E",
            }}
        >
            <div style={{ fontSize: 10, color: TEXT_DIM, marginBottom: 6 }}>Placement relation</div>
            <div style={{ color: TEXT_DIM, fontSize: 11, marginBottom: 8 }}>
                {currentSummary}
            </div>
            <Row>
                <div style={{ flex: 1 }}>
                    <SelectField
                        label="Relation"
                        value={kind}
                        options={PARENT_RELATION_OPTIONS}
                        onCommit={(v) => setKind(v as ParentRelationKind)}
                    />
                </div>
                <div style={{ flex: 1 }}>
                    <SelectField
                        label="Target"
                        value={targetId}
                        options={targetOptions}
                        onCommit={setTargetId}
                        emptyLabel="Choose target"
                    />
                </div>
            </Row>
            <Row>
                <button
                    type="button"
                    onClick={commit}
                    disabled={!targetId}
                    style={{
                        background: targetId ? ACCENT : "#2B3036",
                        color: targetId ? "#071007" : TEXT_DIM,
                        border: "none",
                        borderRadius: 4,
                        fontSize: 11,
                        fontWeight: 700,
                        padding: "5px 10px",
                        cursor: targetId ? "pointer" : "default",
                    }}
                >
                    Apply
                </button>
                <button
                    type="button"
                    onClick={clear}
                    disabled={!relation}
                    style={{
                        background: "transparent",
                        color: relation ? TEXT_DIM : "#555B62",
                        border: `1px solid ${BORDER}`,
                        borderRadius: 4,
                        fontSize: 11,
                        padding: "5px 10px",
                        cursor: relation ? "pointer" : "default",
                    }}
                >
                    Clear
                </button>
            </Row>
        </div>
    );
}

function AssetResolverReview({ obj }: { obj: TypedObject }) {
    const feedback = assetResolutionFeedback(obj);
    if (feedback.source !== "cosmos") return null;
    return (
        <div
            style={{
                border: `1px solid ${feedback.needsReview ? "#5C4B1F" : BORDER}`,
                background: feedback.needsReview ? "#241F13" : "#151A18",
                borderRadius: 4,
                padding: "8px",
                marginBottom: 10,
                lineHeight: 1.45,
            }}
        >
            <div style={{ color: feedback.needsReview ? "#FFCC66" : ACCENT, fontSize: 11, fontWeight: 700 }}>
                {feedback.needsReview ? "Review asset match" : "Cosmos asset match"}
            </div>
            <div style={{ color: TEXT, fontSize: 12, marginTop: 4 }}>{feedback.summary}</div>
            <div style={{ color: TEXT_DIM, fontSize: 11, marginTop: 4 }}>
                {feedback.assetHint && <KV k="hint" v={feedback.assetHint} />}
                {feedback.confidence !== null && <KV k="confidence" v={`${Math.round(feedback.confidence * 100)}%`} />}
                <KV k="class" v={feedback.selectedClass} />
            </div>
        </div>
    );
}

// ─── Multi-object editor ─────────────────────────────────────────────

function MultiObjectEditor({ objs }: { objs: TypedObject[] }) {
    const setAttr = useFloorPlanStore((s) => s.setAttr);
    const deleteObjects = useFloorPlanStore((s) => s.deleteObjects);

    const sharedLayer = objs.every((o) => o.layer === objs[0].layer) ? objs[0].layer : null;
    const sharedColor = objs.every((o) => o.color === objs[0].color) ? objs[0].color ?? "" : null;
    const sharedLocked = objs.every((o) => o.locked === objs[0].locked) ? objs[0].locked : null;

    return (
        <div style={{ padding: "8px 12px 16px", background: SECTION_BG }}>
            <div style={{ color: TEXT_DIM, marginBottom: 8 }}>
                {objs.length} objects selected
            </div>
            <TextField
                label="Layer"
                value={sharedLayer ?? ""}
                placeholder={sharedLayer === null ? "(multiple)" : ""}
                onCommit={(v) => {
                    objs.forEach((o) => v !== o.layer && setAttr(o.id, "layer", o.layer, v));
                }}
            />
            <TextField
                label="Color"
                value={sharedColor ?? ""}
                placeholder={sharedColor === null ? "(multiple)" : "#RRGGBB"}
                onCommit={(v) => {
                    const next = v.trim() || undefined;
                    objs.forEach((o) => next !== o.color && setAttr(o.id, "color", o.color, next));
                }}
            />
            <CheckField
                label="Locked"
                value={sharedLocked ?? false}
                onChange={(v) => objs.forEach((o) => setAttr(o.id, "locked", o.locked, v))}
            />
            <Row>
                <button
                    type="button"
                    onClick={() => deleteObjects(objs.map((o) => o.id))}
                    style={{
                        marginLeft: "auto",
                        background: "transparent",
                        color: "#FF6B6B",
                        border: "1px solid #4A2424",
                        borderRadius: 4,
                        fontSize: 11,
                        padding: "4px 10px",
                        cursor: "pointer",
                    }}
                >
                    Delete all
                </button>
            </Row>
        </div>
    );
}

// ─── Layers panel ────────────────────────────────────────────────────

function LayersPanel() {
    const objects = useFloorPlanStore((s) => s.spec?.objects ?? []);
    const setSelection = useFloorPlanStore((s) => s.setSelection);
    const layers = Array.from(new Set(objects.map((o) => o.layer || "default"))).sort();

    if (layers.length === 0) {
        return <div style={{ padding: "8px 12px", color: TEXT_DIM }}>No layers yet.</div>;
    }
    return (
        <div style={{ padding: "8px 12px 12px" }}>
            {layers.map((layer) => {
                const ids = objects.filter((o) => (o.layer || "default") === layer).map((o) => o.id);
                return (
                    <div
                        key={layer}
                        onClick={() => setSelection(ids)}
                        style={{
                            display: "flex",
                            justifyContent: "space-between",
                            padding: "4px 6px",
                            cursor: "pointer",
                            borderRadius: 3,
                        }}
                    >
                        <span>{layer}</span>
                        <span style={{ color: TEXT_DIM }}>{ids.length}</span>
                    </div>
                );
            })}
        </div>
    );
}

// ─── Constraints panel (read-only stub) ──────────────────────────────

function ConstraintsPanel() {
    const constraints = useFloorPlanStore((s) => s.spec?.constraints ?? []);
    if (constraints.length === 0) {
        return (
            <div style={{ padding: "8px 12px", color: TEXT_DIM, fontSize: 11 }}>
                No constraints. v1.1 will add typed constraint editing.
            </div>
        );
    }
    return (
        <div style={{ padding: "8px 12px" }}>
            {constraints.map((c, i) => (
                <div key={i} style={{ marginBottom: 6, fontSize: 11, color: TEXT_DIM }}>
                    {JSON.stringify(c).slice(0, 80)}
                </div>
            ))}
        </div>
    );
}

function RelationsPanel() {
    const objects = useFloorPlanStore((s) => s.spec?.objects ?? []);
    const relations = useFloorPlanStore((s) => s.spec?.relations ?? []);
    const setRelations = useFloorPlanStore((s) => s.setRelations);
    const setSelection = useFloorPlanStore((s) => s.setSelection);
    const nameById = new Map(objects.map((obj) => [obj.id, obj.name]));

    if (relations.length === 0) {
        return (
            <div style={{ padding: "8px 12px", color: TEXT_DIM, fontSize: 11 }}>
                No spatial relations yet.
            </div>
        );
    }

    return (
        <div style={{ padding: "8px 12px 12px", color: TEXT_DIM, fontSize: 11, lineHeight: 1.45 }}>
            {relations.map((rel, index) => (
                <RelationRow
                    key={`${rel.subject_id}-${rel.relation}-${rel.object_id}-${index}`}
                    relation={rel}
                    nameById={nameById}
                    onSelect={() => setSelection([relationDisplaySubject(rel)])}
                    onDelete={() => setRelations(relations.filter((_, i) => i !== index))}
                />
            ))}
        </div>
    );
}

function ScenarioVariantsPanel() {
    const sessionId = useFloorPlanStore((s) => s.sessionId);
    const variants = useFloorPlanStore((s) => s.spec?.scenario_variants) ?? DEFAULT_SCENARIO_VARIANTS;
    const setScenarioVariants = useFloorPlanStore((s) => s.setScenarioVariants);
    const [plan, setPlan] = useState<CampaignPlanResponse | null>(null);
    const [launch, setLaunch] = useState<CampaignLaunchResponse["launch"] | null>(null);
    const [planState, setPlanState] = useState<"idle" | "loading" | "failed">("idle");
    const [materializeState, setMaterializeState] = useState<"idle" | "loading" | "failed">("idle");
    const [launchState, setLaunchState] = useState<"idle" | "loading" | "failed">("idle");
    const update = (patch: Partial<ScenarioVariants>) => {
        setScenarioVariants({
            ...variants,
            ...patch,
            perturbations: {
                ...variants.perturbations,
                ...(patch.perturbations ?? {}),
            },
            validation: {
                ...variants.validation,
                ...(patch.validation ?? {}),
            },
        });
    };
    const toggleValue = <T extends string,>(key: "lighting" | "cameras" | "actors" | "circumstances", value: T) => {
        const current = variants[key] as string[];
        const next = current.includes(value)
            ? current.filter((item) => item !== value)
            : [...current, value];
        if (next.length === 0 && key !== "actors") return;
        update({ [key]: next } as Partial<ScenarioVariants>);
    };
    const requestPlan = async () => {
        setPlanState("loading");
        try {
            await flushPendingPatch(api);
            const response = await api.planCampaign(sessionId);
            setPlan(response);
            setLaunch(null);
            setPlanState("idle");
        } catch {
            setPlanState("failed");
        }
    };
    const materialize = async () => {
        setMaterializeState("loading");
        try {
            await flushPendingPatch(api);
            const response = await api.materializeCampaign(sessionId);
            setPlan(response);
            setLaunch(null);
            setMaterializeState("idle");
        } catch {
            setMaterializeState("failed");
        }
    };
    const launchFirst = async () => {
        setLaunchState("loading");
        try {
            await flushPendingPatch(api);
            const response = await api.launchCampaign(sessionId, {
                variant_index: 1,
                dry_run: false,
                wait: false,
            });
            setPlan(response.campaign);
            setLaunch(response.launch);
            setLaunchState("idle");
        } catch {
            setLaunchState("failed");
        }
    };

    return (
        <div style={{ padding: "8px 12px 14px", color: TEXT_DIM, fontSize: 11 }}>
            <CheckField
                label="Enable variant campaign"
                value={variants.enabled}
                onChange={(enabled) => update({ enabled })}
            />
            <Row>
                <NumField
                    label="Variants"
                    value={variants.variant_count}
                    onCommit={(v) => update({ variant_count: Math.max(1, Math.min(500, Math.round(v))) })}
                    step={1}
                    min={1}
                />
                <NumField
                    label="Seed"
                    value={variants.seed}
                    onCommit={(v) => update({ seed: Math.max(0, Math.round(v)) })}
                    step={1}
                    min={0}
                />
            </Row>
            <OptionGroup
                label="Lighting"
                options={LIGHTING_OPTIONS}
                selected={variants.lighting}
                onToggle={(value) => toggleValue("lighting", value)}
            />
            <OptionGroup
                label="Cameras"
                options={CAMERA_OPTIONS}
                selected={variants.cameras}
                onToggle={(value) => toggleValue("cameras", value)}
            />
            <OptionGroup
                label="Actors"
                options={ACTOR_OPTIONS}
                selected={variants.actors}
                onToggle={(value) => toggleValue("actors", value)}
            />
            <OptionGroup
                label="Circumstances"
                options={CIRCUMSTANCE_OPTIONS}
                selected={variants.circumstances}
                onToggle={(value) => toggleValue("circumstances", value)}
            />
            <div style={{ borderTop: `1px solid ${BORDER}`, marginTop: 10, paddingTop: 10 }}>
                <CheckField
                    label="Pose/material perturbations"
                    value={variants.perturbations.enabled}
                    onChange={(enabled) => update({ perturbations: { enabled } as ScenarioVariants["perturbations"] })}
                />
                <Row>
                    <NumField
                        label="Pose jitter (m)"
                        value={variants.perturbations.pose_jitter_m}
                        onCommit={(v) => update({ perturbations: { pose_jitter_m: Math.max(0, v) } as ScenarioVariants["perturbations"] })}
                        step={0.01}
                        min={0}
                    />
                    <NumField
                        label="Rot jitter (deg)"
                        value={variants.perturbations.rotation_jitter_deg}
                        onCommit={(v) => update({ perturbations: { rotation_jitter_deg: Math.max(0, v) } as ScenarioVariants["perturbations"] })}
                        step={1}
                        min={0}
                    />
                </Row>
                <CheckField
                    label="Randomize materials"
                    value={variants.perturbations.material_randomization}
                    onChange={(material_randomization) => update({ perturbations: { material_randomization } as ScenarioVariants["perturbations"] })}
                />
                <CheckField
                    label="Sensor noise"
                    value={variants.perturbations.sensor_noise}
                    onChange={(sensor_noise) => update({ perturbations: { sensor_noise } as ScenarioVariants["perturbations"] })}
                />
            </div>
            <div style={{ borderTop: `1px solid ${BORDER}`, marginTop: 10, paddingTop: 10 }}>
                <CheckField
                    label="Validate relations"
                    value={variants.validation.require_relations}
                    onChange={(require_relations) => update({ validation: { require_relations } as ScenarioVariants["validation"] })}
                />
                <CheckField
                    label="Validate visibility"
                    value={variants.validation.require_visibility}
                    onChange={(require_visibility) => update({ validation: { require_visibility } as ScenarioVariants["validation"] })}
                />
                <CheckField
                    label="Validate physics"
                    value={variants.validation.require_physics}
                    onChange={(require_physics) => update({ validation: { require_physics } as ScenarioVariants["validation"] })}
                />
            </div>
            <button
                type="button"
                onClick={() => void requestPlan()}
                style={{
                    width: "100%",
                    marginTop: 10,
                    background: ACCENT,
                    color: "#071007",
                    border: "none",
                    borderRadius: 4,
                    fontSize: 11,
                    fontWeight: 700,
                    padding: "6px 8px",
                    cursor: "pointer",
                }}
            >
                {planState === "loading" ? "Planning..." : "Plan campaign"}
            </button>
            <button
                type="button"
                onClick={() => void materialize()}
                style={{
                    width: "100%",
                    marginTop: 6,
                    background: "#20252C",
                    color: TEXT,
                    border: `1px solid ${BORDER}`,
                    borderRadius: 4,
                    fontSize: 11,
                    fontWeight: 700,
                    padding: "6px 8px",
                    cursor: "pointer",
                }}
            >
                {materializeState === "loading" ? "Materializing..." : "Materialize campaign"}
            </button>
            {planState === "failed" && (
                <div style={{ marginTop: 8, color: "#FF6B6B" }}>
                    Campaign planning failed. Save the canvas and try again.
                </div>
            )}
            {materializeState === "failed" && (
                <div style={{ marginTop: 8, color: "#FF6B6B" }}>
                    Campaign materialization failed. Check the backend log.
                </div>
            )}
            {launchState === "failed" && (
                <div style={{ marginTop: 8, color: "#FF6B6B" }}>
                    Scene launch failed. Check the backend log.
                </div>
            )}
            {plan && (
                <div
                    style={{
                        marginTop: 10,
                        padding: 8,
                        background: "#0F1216",
                        border: `1px solid ${BORDER}`,
                        borderRadius: 4,
                        lineHeight: 1.45,
                    }}
                >
                    <div style={{ color: TEXT, fontWeight: 700 }}>{plan.campaign_id}</div>
                    <KV k="variants" v={plan.variant_count} />
                    <KV k="status" v={plan.execution.status} />
                    <KV k="workspace" v={plan.workspace_dir} />
                    {plan.variants[0] && (
                        <div style={{ marginTop: 6, color: TEXT_DIM }}>
                            <div style={{ color: ACCENT }}>first launch</div>
                            <code style={{ wordBreak: "break-word" }}>{plan.variants[0].launch_command}</code>
                            <button
                                type="button"
                                onClick={() => void launchFirst()}
                                disabled={launchState === "loading"}
                                style={{
                                    width: "100%",
                                    marginTop: 8,
                                    background: launchState === "loading" ? "#2E3237" : ACCENT,
                                    color: launchState === "loading" ? TEXT_DIM : "#071007",
                                    border: "none",
                                    borderRadius: 4,
                                    fontSize: 11,
                                    fontWeight: 700,
                                    padding: "6px 8px",
                                    cursor: launchState === "loading" ? "default" : "pointer",
                                }}
                            >
                                {launchState === "loading" ? "Launching..." : "Launch scene"}
                            </button>
                        </div>
                    )}
                    {launch && (
                        <div style={{ marginTop: 8, color: TEXT_DIM }}>
                            <KV k="launch" v={launch.status} />
                            {launch.pid !== undefined && <KV k="pid" v={launch.pid} />}
                            <KV k="log" v={launch.log_path} />
                        </div>
                    )}
                </div>
            )}
        </div>
    );
}

function OptionGroup<T extends string>({
    label,
    options,
    selected,
    onToggle,
}: {
    label: string;
    options: Array<{ value: T; label: string }>;
    selected: T[];
    onToggle: (value: T) => void;
}) {
    return (
        <Field label={label}>
            <div style={{ display: "flex", flexWrap: "wrap", gap: 4 }}>
                {options.map((option) => {
                    const active = selected.includes(option.value);
                    return (
                        <button
                            key={option.value}
                            type="button"
                            onClick={() => onToggle(option.value)}
                            style={{
                                background: active ? "#263A17" : "#0F1216",
                                border: `1px solid ${active ? ACCENT : BORDER}`,
                                color: active ? TEXT : TEXT_DIM,
                                borderRadius: 4,
                                padding: "3px 6px",
                                fontSize: 10,
                                cursor: "pointer",
                                fontFamily: "inherit",
                            }}
                        >
                            {option.label}
                        </button>
                    );
                })}
            </div>
        </Field>
    );
}

function RelationRow({
    relation,
    nameById,
    onSelect,
    onDelete,
}: {
    relation: SpatialRelation;
    nameById: Map<string, string>;
    onSelect: () => void;
    onDelete: () => void;
}) {
    let subject = nameById.get(relation.subject_id) ?? relation.subject_id;
    let object = nameById.get(relation.object_id) ?? relation.object_id;
    let label = relation.relation.replaceAll("_", " ");
    if (relation.relation === "contains") {
        subject = nameById.get(relation.object_id) ?? relation.object_id;
        object = nameById.get(relation.subject_id) ?? relation.subject_id;
        label = "inside";
    } else if (relation.relation === "supports") {
        subject = nameById.get(relation.object_id) ?? relation.object_id;
        object = nameById.get(relation.subject_id) ?? relation.subject_id;
        label = "on top of";
    }
    return (
        <div
            style={{
                display: "grid",
                gridTemplateColumns: "1fr auto",
                gap: 6,
                alignItems: "center",
                padding: "5px 6px",
                marginBottom: 4,
                borderRadius: 3,
                background: "#0F1216",
                border: `1px solid ${BORDER}`,
            }}
        >
            <button
                type="button"
                onClick={onSelect}
                style={{
                    minWidth: 0,
                    background: "transparent",
                    border: "none",
                    padding: 0,
                    color: TEXT_DIM,
                    textAlign: "left",
                    fontSize: 11,
                    cursor: "pointer",
                    fontFamily: "inherit",
                }}
                title="Select relation subject"
            >
                <span style={{ color: TEXT }}>{subject}</span>
                <span> {label} </span>
                <span style={{ color: TEXT }}>{object}</span>
            </button>
            <button
                type="button"
                onClick={onDelete}
                style={{
                    width: 22,
                    height: 22,
                    background: "#171A1E",
                    color: TEXT_DIM,
                    border: `1px solid ${BORDER}`,
                    borderRadius: 3,
                    cursor: "pointer",
                    fontSize: 12,
                    lineHeight: "18px",
                }}
                title="Remove relation"
            >
                x
            </button>
        </div>
    );
}

function findParentRelation(objectId: string, relations: SpatialRelation[]): ParentSpatialRelation | null {
    for (const relation of relations) {
        if (relation.subject_id === objectId && isParentRelation(relation.relation)) {
            return relation as ParentSpatialRelation;
        }
        if ((relation.relation === "contains" || relation.relation === "supports") && relation.object_id === objectId) {
            return {
                ...relation,
                subject_id: relation.object_id,
                object_id: relation.subject_id,
                relation: relation.relation === "contains" ? "inside" : "on_top_of",
            } as ParentSpatialRelation;
        }
    }
    return null;
}

function withoutParentRelation(objectId: string, relations: SpatialRelation[]): SpatialRelation[] {
    return relations.filter((relation) => {
        if (relation.subject_id === objectId && isParentRelation(relation.relation)) return false;
        if ((relation.relation === "contains" || relation.relation === "supports") && relation.object_id === objectId) return false;
        return true;
    });
}

function isParentRelation(relation: SpatialRelationKind): relation is ParentRelationKind {
    return relation !== "contains" && relation !== "supports";
}

function relationDisplaySubject(relation: SpatialRelation): string {
    if (relation.relation === "contains" || relation.relation === "supports") return relation.object_id;
    return relation.subject_id;
}

function relationLabel(relation: SpatialRelationKind): string {
    if (relation === "on_top_of") return "on top of";
    if (relation === "mounted_to") return "mounted to";
    if (relation === "front_of") return "in front of";
    return relation.replaceAll("_", " ");
}

function IntentSummary() {
    const intent = useFloorPlanStore((s) => s.spec?.intent);
    if (!intent) return null;
    return (
        <div style={{ padding: "8px 12px 16px", color: TEXT_DIM, lineHeight: 1.6, fontSize: 11 }}>
            <KV k="pattern" v={intent.pattern_hint} />
            <KV k="robots" v={intent.counts.robots} />
            <KV k="conveyors" v={intent.counts.conveyors} />
            <KV k="bins" v={intent.counts.bins} />
            <KV k="cubes" v={intent.counts.cubes} />
            <KV k="sensors" v={intent.counts.sensors} />
            <KV k="humans" v={intent.counts.humans} />
            <KV k="dest_kind" v={intent.structural_features.destination_kind} />
            {intent.structural_features.routing_axis && (
                <KV k="routing_axis" v={intent.structural_features.routing_axis} />
            )}
            {intent.structural_tags.length > 0 && (
                <div style={{ marginTop: 6 }}>
                    {intent.structural_tags.slice(0, 6).map((t) => (
                        <span
                            key={t}
                            style={{
                                background: "#0F1216",
                                color: ACCENT,
                                padding: "1px 6px",
                                borderRadius: 3,
                                fontSize: 10,
                                marginRight: 3,
                                marginBottom: 3,
                                display: "inline-block",
                            }}
                        >
                            {t}
                        </span>
                    ))}
                </div>
            )}
        </div>
    );
}

function KV({ k, v }: { k: string; v: string | number }) {
    return (
        <div style={{ display: "flex", justifyContent: "space-between" }}>
            <span>{k}</span>
            <span style={{ color: TEXT }}>{String(v)}</span>
        </div>
    );
}

// ─── Reusable form bits ──────────────────────────────────────────────

function Field({ label, children }: { label: string; children: React.ReactNode }) {
    return (
        <div style={{ marginBottom: 8 }}>
            <div style={{ fontSize: 10, color: TEXT_DIM, marginBottom: 3 }}>{label}</div>
            {children}
        </div>
    );
}

function Row({ children }: { children: React.ReactNode }) {
    return (
        <div style={{ display: "flex", gap: 8, marginBottom: 8, alignItems: "flex-end" }}>
            {children}
        </div>
    );
}

function TextField({
    label,
    value,
    onCommit,
    placeholder,
    multiline,
}: {
    label: string;
    value: string;
    onCommit: (v: string) => void;
    placeholder?: string;
    multiline?: boolean;
}) {
    const [local, setLocal] = useState(value);
    useEffect(() => setLocal(value), [value]);
    const commit = () => {
        if (local !== value) onCommit(local);
    };
    const inputStyle = {
        width: "100%",
        background: "#0F1216",
        border: `1px solid ${BORDER}`,
        borderRadius: 3,
        color: TEXT,
        fontSize: 12,
        padding: "4px 6px",
        outline: "none",
        fontFamily: "inherit",
    };
    return (
        <Field label={label}>
            {multiline ? (
                <textarea
                    value={local}
                    placeholder={placeholder}
                    onChange={(e) => setLocal(e.target.value)}
                    onBlur={commit}
                    rows={2}
                    style={{ ...inputStyle, resize: "vertical" }}
                />
            ) : (
                <input
                    type="text"
                    value={local}
                    placeholder={placeholder}
                    onChange={(e) => setLocal(e.target.value)}
                    onBlur={commit}
                    onKeyDown={(e) => e.key === "Enter" && (e.target as HTMLInputElement).blur()}
                    style={inputStyle}
                />
            )}
        </Field>
    );
}

function SelectField({
    label,
    value,
    options,
    onCommit,
    emptyLabel,
}: {
    label: string;
    value: string;
    options: Array<{ value: string; label: string }>;
    onCommit: (v: string) => void;
    emptyLabel?: string;
}) {
    const hasValueOption = options.some((option) => option.value === value);
    return (
        <Field label={label}>
            <select
                value={value}
                onChange={(e) => onCommit(e.target.value)}
                style={{
                    width: "100%",
                    background: "#0F1216",
                    border: `1px solid ${BORDER}`,
                    borderRadius: 3,
                    color: TEXT,
                    fontSize: 12,
                    padding: "5px 6px",
                    outline: "none",
                    fontFamily: "inherit",
                }}
            >
                {emptyLabel && <option value="">{emptyLabel}</option>}
                {value && !hasValueOption && <option value={value}>{value}</option>}
                {options.map((option) => (
                    <option key={option.value} value={option.value}>
                        {option.label}
                    </option>
                ))}
            </select>
        </Field>
    );
}

function NumField({
    label,
    value,
    onCommit,
    step = 1,
    min,
    disabled,
}: {
    label: string;
    value: number;
    onCommit: (v: number) => void;
    step?: number;
    min?: number;
    disabled?: boolean;
}) {
    const [local, setLocal] = useState(String(value));
    useEffect(() => setLocal(String(value)), [value]);
    const commit = () => {
        const v = parseFloat(local);
        if (!isNaN(v) && v !== value) onCommit(v);
        else setLocal(String(value));
    };
    return (
        <Field label={label}>
            <input
                type="number"
                step={step}
                min={min}
                value={local}
                onChange={(e) => setLocal(e.target.value)}
                onBlur={commit}
                onKeyDown={(e) => e.key === "Enter" && (e.target as HTMLInputElement).blur()}
                disabled={disabled}
                style={{
                    width: "100%",
                    background: "#0F1216",
                    border: `1px solid ${BORDER}`,
                    borderRadius: 3,
                    color: disabled ? TEXT_DIM : TEXT,
                    fontSize: 12,
                    padding: "4px 6px",
                    outline: "none",
                    fontFamily: "inherit",
                }}
            />
        </Field>
    );
}

function CheckField({
    label,
    value,
    onChange,
}: {
    label: string;
    value: boolean;
    onChange: (v: boolean) => void;
}) {
    return (
        <label style={{ display: "flex", alignItems: "center", gap: 6, cursor: "pointer" }}>
            <input
                type="checkbox"
                checked={value}
                onChange={(e) => onChange(e.target.checked)}
                style={{ accentColor: ACCENT }}
            />
            <span style={{ fontSize: 11, color: TEXT_DIM }}>{label}</span>
        </label>
    );
}
