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
import { TypedObject } from "../api/types";
import { buildAssetOptions, assetResolutionFeedback } from "../canvas/assetResolver";
import { CLASS_META } from "../canvas/objectClasses";

const PANEL_BG = "#171A1E";
const SECTION_BG = "#1E2228";
const TEXT = "#DDDDDD";
const TEXT_DIM = "#8A8E92";
const BORDER = "#2E3237";
const ACCENT = "#76B900";
const ASSET_OPTIONS = buildAssetOptions();

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
}: {
    label: string;
    value: string;
    options: Array<{ value: string; label: string }>;
    onCommit: (v: string) => void;
}) {
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
                {!CLASS_META[value] && <option value={value}>{value}</option>}
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
