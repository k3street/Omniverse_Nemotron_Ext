/**
 * Floor Plan SPA — Block 1A.3 entry point.
 *
 * Composition (top → bottom, left → right):
 *
 *   ┌─ Header ────────────────────────────────────────────────────┐
 *   ├─Toolbar─┬─Palette─┬─CanvasViewport────────┬─PropertiesPanel─┤
 *   │         │         │  (Konva + Transformer)│                 │
 *   │         │         │  ConfirmBar (overlay) │                 │
 *   │         │         │                       │                 │
 *   ├─────────┴─────────┴───────ChatRibbon──────┴─────────────────┤
 *   ├─StatusBar──────────────────────────────────────────────────┤
 *   └─────────────────────────────────────────────────────────────┘
 *
 * Spec §11.2 + §11.3 + §13.
 */
import { useEffect, useState } from "react";
import { BuildResponse, CanvasGetResponse, RenderingMode } from "./api/types";
import {
    CanvasApi,
    createCanvasApi,
    CanvasConflictError,
    CanvasValidationError,
} from "./api/floorPlanApi";
import { useFloorPlanStore } from "./store/floorPlanStore";
import { schedulePatch, startSSE, flushPendingPatch } from "./store/sync";
import { Toolbar } from "./components/Toolbar";
import { Palette } from "./components/Palette";
import { CanvasViewport } from "./components/CanvasViewport";
import { PropertiesPanel } from "./components/PropertiesPanel";
import { ConfirmBar } from "./components/ConfirmBar";
import { ChatRibbon } from "./components/ChatRibbon";
import { KEYFRAMES_BREATHE } from "./canvas/motionTokens";

const TEXT_PRIMARY = "#DDDDDD";
const TEXT_SECONDARY = "#8A8E92";
const ACCENT = "#76B900";
const BG = "#111214";

const api: CanvasApi = createCanvasApi("");

export function App() {
    const sessionId = useFloorPlanStore((s) => s.sessionId);
    const spec = useFloorPlanStore((s) => s.spec);
    const revision = useFloorPlanStore((s) => s.revision);
    const setSpec = useFloorPlanStore((s) => s.setSpec);
    const restoreFromWAL = useFloorPlanStore((s) => s.restoreFromWAL);
    const applyAgentBulkUpdate = useFloorPlanStore((s) => s.applyAgentBulkUpdate);

    const [bootStatus, setBootStatus] = useState<"loading" | "ready" | "error">("loading");
    const [bootError, setBootError] = useState<string | null>(null);
    const [conflict, setConflict] = useState<{ message: string } | null>(null);
    const [saveState, setSaveState] = useState<"idle" | "saving" | "saved" | "failed">("idle");
    const [buildState, setBuildState] = useState<"idle" | "previewing" | "ready" | "failed">("idle");
    const [buildResult, setBuildResult] = useState<BuildResponse | null>(null);
    const [buildError, setBuildError] = useState<string | null>(null);
    const [renderingMode, setRenderingMode] = useState<RenderingMode>("real");
    const [renderingState, setRenderingState] = useState<"idle" | "saving" | "failed">("idle");

    const previewBuild = async () => {
        if (!spec || buildState === "previewing") return;
        setBuildState("previewing");
        setBuildError(null);
        try {
            await flushPendingPatch(api);
            const result = await api.build(sessionId, { dry_run: true });
            setBuildResult(result);
            setBuildState("ready");
        } catch (e) {
            console.warn("[build] preview failed:", e);
            setBuildError(String(e));
            setBuildState("failed");
        }
    };

    // ─── Boot: GET spec, fall back to WAL on error ───────────────────
    useEffect(() => {
        (async () => {
            try {
                const res: CanvasGetResponse = await api.get(sessionId);
                if (res.spec) {
                    setSpec(res.spec, res.revision);
                } else {
                    // No spec yet — try WAL restore for offline-first
                    if (!restoreFromWAL()) {
                        setSpec(null, res.revision);
                    }
                }
                setBootStatus("ready");
            } catch (e) {
                console.warn("[boot] GET failed, attempting WAL restore:", e);
                if (restoreFromWAL()) {
                    setBootStatus("ready");
                } else {
                    setBootError(String(e));
                    setBootStatus("error");
                }
            }
        })();
    }, [sessionId, setSpec, restoreFromWAL]);

    // ─── Auto-patch on local mutations ───────────────────────────────
    useEffect(() => {
        if (!spec || bootStatus !== "ready") return;
        setSaveState("saving");
        schedulePatch(api, spec)
            .then((ok) => {
                setSaveState(ok ? "saved" : "failed");
                if (ok) setTimeout(() => setSaveState("idle"), 1500);
            })
            .catch((e) => {
                if (e instanceof CanvasConflictError) {
                    setConflict({
                        message:
                            `Server has revision ${e.detail.actual_revision}, ` +
                            `you have ${e.detail.expected_revision}. Reloading…`,
                    });
                    if (e.detail.current_spec) {
                        setSpec(e.detail.current_spec, e.detail.actual_revision);
                    }
                } else if (e instanceof CanvasValidationError) {
                    setSaveState("failed");
                }
            });
    }, [spec, bootStatus, setSpec]);

    // ─── SSE listener — agent writes ─────────────────────────────────
    useEffect(() => {
        if (bootStatus !== "ready") return;
        const stop = startSSE(sessionId, (evt) => {
            if (evt.type === "canvas/agent_write" && evt.payload?.spec) {
                const newSpec = evt.payload.spec as typeof spec;
                if (newSpec && newSpec.objects) {
                    applyAgentBulkUpdate(
                        newSpec.objects,
                        `Agent: ${evt.payload.summary ?? "scene update"}`,
                    );
                }
            } else if (evt.type === "canvas/revision_bump" && evt.payload?.revision) {
                useFloorPlanStore.setState({ revision: evt.payload.revision as number });
            }
        });
        return stop;
    }, [bootStatus, sessionId, applyAgentBulkUpdate]);

    // ─── Force-flush pending patch on unmount ────────────────────────
    useEffect(() => {
        return () => {
            void flushPendingPatch(api);
        };
    }, []);

    useEffect(() => {
        api.getRenderingMode()
            .then((res) => {
                if (res.mode === "fast" || res.mode === "real") {
                    setRenderingMode(res.mode);
                }
            })
            .catch((e) => {
                // Initial read failure: keep the default mode and leave the
                // banner idle. "failed" is reserved for save (PUT) failures.
                console.warn("[rendering mode] GET failed:", e);
            });
    }, []);

    const updateRenderingMode = async (mode: RenderingMode) => {
        if (mode === renderingMode || renderingState === "saving") return;
        const previous = renderingMode;
        setRenderingMode(mode);
        setRenderingState("saving");
        try {
            const res = await api.setRenderingMode(mode);
            setRenderingMode(res.mode);
            setRenderingState("idle");
        } catch (e) {
            console.warn("[rendering mode] PUT failed:", e);
            setRenderingMode(previous);
            setRenderingState("failed");
        }
    };

    return (
        <div
            style={{
                display: "flex",
                flexDirection: "column",
                width: "100vw",
                height: "100vh",
                background: BG,
                color: TEXT_PRIMARY,
                fontFamily: "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif",
                fontSize: 13,
                overflow: "hidden",
            }}
        >
            <style>{KEYFRAMES_BREATHE}</style>
            <Header
                disabled={!spec || bootStatus !== "ready" || buildState === "previewing"}
                onPreviewBuild={() => void previewBuild()}
                state={buildState}
                renderingMode={renderingMode}
                renderingState={renderingState}
                onRenderingModeChange={(mode) => void updateRenderingMode(mode)}
            />
            <div style={{ flex: 1, display: "flex", overflow: "hidden", minHeight: 0 }}>
                <Toolbar />
                <Palette />
                <div
                    style={{
                        flex: 1,
                        display: "flex",
                        flexDirection: "column",
                        position: "relative",
                        minWidth: 0,
                    }}
                >
                    <div
                        style={{
                            flex: 1,
                            position: "relative",
                            display: "flex",
                            minHeight: 0,
                        }}
                    >
                        <CanvasViewport />
                        <ConfirmBar />
                        {bootStatus === "loading" && <BootOverlay text="Loading layout…" />}
                        {bootStatus === "error" && (
                            <BootOverlay text={`Boot failed: ${bootError}`} color="#FF4444" />
                        )}
                        {conflict && (
                            <BootOverlay text={conflict.message} color="#FFA800" />
                        )}
                        {(buildResult || buildError || buildState === "previewing") && (
                            <BuildPreviewPanel
                                state={buildState}
                                result={buildResult}
                                error={buildError}
                                onApply={async () => {
                                    await flushPendingPatch(api);
                                    return api.build(sessionId, {
                                        dry_run: false,
                                        execute_direct: true,
                                    });
                                }}
                                onClose={() => {
                                    setBuildResult(null);
                                    setBuildError(null);
                                    setBuildState("idle");
                                }}
                            />
                        )}
                    </div>
                    <ChatRibbon />
                </div>
                <PropertiesPanel />
            </div>
            <StatusBar revision={revision} saveState={saveState} sessionId={sessionId} />
        </div>
    );
}

function Header({
    disabled,
    onPreviewBuild,
    state,
    renderingMode,
    renderingState,
    onRenderingModeChange,
}: {
    disabled: boolean;
    onPreviewBuild: () => void;
    state: "idle" | "previewing" | "ready" | "failed";
    renderingMode: RenderingMode;
    renderingState: "idle" | "saving" | "failed";
    onRenderingModeChange: (mode: RenderingMode) => void;
}) {
    const renderButton = (mode: RenderingMode, label: string) => {
        const active = renderingMode === mode;
        return (
            <button
                type="button"
                onClick={() => onRenderingModeChange(mode)}
                disabled={renderingState === "saving"}
                title={mode === "fast" ? "Fast verification: skip per-frame rendering" : "Real rendering: render frames for viewport/WebRTC"}
                style={{
                    height: 22,
                    padding: "0 10px",
                    background: active ? ACCENT : "#24282D",
                    color: active ? "#000" : TEXT_SECONDARY,
                    border: "1px solid #3A3F45",
                    borderColor: active ? ACCENT : "#3A3F45",
                    borderRadius: 4,
                    fontSize: 11,
                    fontWeight: 700,
                    cursor: renderingState === "saving" ? "default" : "pointer",
                    fontFamily: "inherit",
                }}
            >
                {label}
            </button>
        );
    };

    return (
        <div
            style={{
                height: 36,
                background: "#1A1C1F",
                borderBottom: "1px solid #2E3237",
                display: "flex",
                alignItems: "center",
                paddingLeft: 12,
                color: TEXT_PRIMARY,
                fontSize: 13,
                fontWeight: 600,
                gap: 8,
            }}
        >
            <span>Isaac Assist · Floor Plan</span>
            <span style={{ color: TEXT_SECONDARY, fontSize: 11, fontWeight: 400 }}>
                multimodal canvas v1.0
            </span>
            <div
                role="radiogroup"
                aria-label="Rendering mode"
                style={{
                    display: "flex",
                    alignItems: "center",
                    gap: 4,
                    marginLeft: "auto",
                }}
            >
                {renderButton("fast", "Fast")}
                {renderButton("real", "Real")}
            </div>
            {renderingState === "failed" && (
                <span style={{ color: "#FF6B6B", fontSize: 11 }}>render mode failed</span>
            )}
            <button
                type="button"
                onClick={onPreviewBuild}
                disabled={disabled}
                title="Generate reviewed Kit code without mutating Isaac Sim"
                style={{
                    marginRight: 12,
                    height: 24,
                    padding: "0 12px",
                    background: disabled ? "#2E3237" : ACCENT,
                    color: disabled ? TEXT_SECONDARY : "#000",
                    border: "none",
                    borderRadius: 4,
                    fontSize: 12,
                    fontWeight: 700,
                    cursor: disabled ? "default" : "pointer",
                    fontFamily: "inherit",
                }}
            >
                {state === "previewing" ? "Previewing..." : "Preview Build"}
            </button>
        </div>
    );
}

function BuildPreviewPanel({
    state,
    result,
    error,
    onApply,
    onClose,
}: {
    state: "idle" | "previewing" | "ready" | "failed";
    result: BuildResponse | null;
    error: string | null;
    onApply: () => Promise<BuildResponse>;
    onClose: () => void;
}) {
    const [applyState, setApplyState] = useState<"idle" | "applying" | "applied" | "failed">("idle");
    const [applyMessage, setApplyMessage] = useState<string | null>(null);
    const assets = result?.asset_resolutions ?? [];
    const relations = result?.instantiation?.relation_summary ?? [];
    const relationDiagnostics = result?.instantiation?.relation_diagnostics ?? [];
    const relationVerification = result?.instantiation?.relation_verification;
    const variants = result?.instantiation?.variant_summary;
    const code = result?.instantiation?.generated_code ?? "";
    const status = result?.instantiation?.status ?? result?.status ?? state;
    const canApply = Boolean(result?.ratified && code && state !== "previewing" && applyState !== "applying");
    const applyBuild = async () => {
        if (!canApply) return;
        setApplyState("applying");
        setApplyMessage(null);
        try {
            const response = await onApply();
            const instantiation = response.instantiation;
            if (instantiation?.status === "ok") {
                setApplyState("applied");
                setApplyMessage("Applied to Isaac Sim.");
            } else {
                setApplyState("failed");
                setApplyMessage(instantiation?.message || response.status || "Apply failed.");
            }
        } catch (e) {
            setApplyState("failed");
            setApplyMessage(String(e));
        }
    };
    return (
        <div
            style={{
                position: "absolute",
                left: 16,
                right: 16,
                bottom: 16,
                maxHeight: 220,
                background: "#171A1EF2",
                border: "1px solid #2E3237",
                borderRadius: 6,
                boxShadow: "0 10px 32px rgba(0,0,0,0.45)",
                zIndex: 60,
                display: "flex",
                flexDirection: "column",
                overflow: "hidden",
            }}
            data-testid="build-preview-panel"
        >
            <div
                style={{
                    height: 34,
                    display: "flex",
                    alignItems: "center",
                    gap: 10,
                    padding: "0 10px",
                    borderBottom: "1px solid #2E3237",
                }}
            >
                <span style={{ color: ACCENT, fontWeight: 700 }}>Build preview</span>
                <span style={{ color: TEXT_SECONDARY, fontSize: 11 }}>
                    {error ? "failed" : `${status} · ${assets.length} resolved assets`}
                </span>
                {applyMessage && (
                    <span
                        style={{
                            color: applyState === "applied" ? ACCENT : "#FF6B6B",
                            fontSize: 11,
                        }}
                    >
                        {applyMessage}
                    </span>
                )}
                <button
                    type="button"
                    onClick={() => void applyBuild()}
                    disabled={!canApply}
                    title="Execute this build directly in the running Isaac Sim stage"
                    style={{
                        marginLeft: "auto",
                        background: canApply ? ACCENT : "#2E3237",
                        color: canApply ? "#000" : TEXT_SECONDARY,
                        border: "none",
                        borderRadius: 4,
                        fontSize: 11,
                        fontWeight: 700,
                        padding: "4px 10px",
                        cursor: canApply ? "pointer" : "default",
                    }}
                >
                    {applyState === "applying" ? "Applying..." : "Apply to Isaac Sim"}
                </button>
                <button
                    type="button"
                    onClick={onClose}
                    style={{
                        background: "transparent",
                        color: TEXT_SECONDARY,
                        border: "1px solid #3A3F45",
                        borderRadius: 4,
                        fontSize: 11,
                        padding: "3px 8px",
                        cursor: "pointer",
                    }}
                >
                    Close
                </button>
            </div>
            <div style={{ display: "flex", minHeight: 0 }}>
                <div
                    style={{
                        width: 260,
                        padding: 10,
                        borderRight: "1px solid #2E3237",
                        color: TEXT_SECONDARY,
                        fontSize: 11,
                        lineHeight: 1.45,
                        overflow: "auto",
                    }}
                >
                    {state === "previewing" && <div>Generating Kit code...</div>}
                    {error && <div style={{ color: "#FF6B6B" }}>{error}</div>}
                    {!error && assets.map((asset) => (
                        <div key={asset.object_id} style={{ marginBottom: 8 }}>
                            <div style={{ color: TEXT_PRIMARY }}>{asset.object_class}</div>
                            <div>{asset.source}{asset.needs_review ? " · review" : ""}</div>
                        </div>
                    ))}
                    {!error && relations.length > 0 && (
                        <div style={{ marginTop: 10, paddingTop: 8, borderTop: "1px solid #2E3237" }}>
                            <div style={{ color: ACCENT, fontWeight: 700, marginBottom: 6 }}>Relations verified</div>
                            {relations.map((rel, index) => (
                                <div key={`${rel.subject_id}-${rel.relation}-${rel.object_id}-${index}`} style={{ marginBottom: 6 }}>
                                    <span style={{ color: TEXT_PRIMARY }}>{rel.subject_name}</span>
                                    <span> {rel.relation.replaceAll("_", " ")} </span>
                                    <span style={{ color: TEXT_PRIMARY }}>{rel.object_name}</span>
                                </div>
                            ))}
                        </div>
                    )}
                    {!error && relationDiagnostics.length > 0 && (
                        <div style={{ marginTop: 10, paddingTop: 8, borderTop: "1px solid #2E3237" }}>
                            <div style={{ color: "#FFCC66", fontWeight: 700, marginBottom: 6 }}>Relation review</div>
                            {relationDiagnostics.map((diag, index) => (
                                <div key={`${diag.code}-${index}`} style={{ marginBottom: 6 }}>
                                    <span style={{ color: diag.severity === "error" ? "#FF6B6B" : "#FFCC66" }}>
                                        {diag.severity}
                                    </span>
                                    <span> · {diag.message}</span>
                                </div>
                            ))}
                        </div>
                    )}
                    {!error && relationVerification && relationVerification.check_count > 0 && (
                        <div style={{ marginTop: 10, paddingTop: 8, borderTop: "1px solid #2E3237" }}>
                            <div
                                style={{
                                    color: relationVerification.status === "pass" ? ACCENT : "#FFCC66",
                                    fontWeight: 700,
                                    marginBottom: 6,
                                }}
                            >
                                Relation geometry {relationVerification.status}
                            </div>
                            <div>
                                {relationVerification.check_count - relationVerification.failed_count}
                                /{relationVerification.check_count} checks passed
                            </div>
                        </div>
                    )}
                    {!error && variants && (
                        <div style={{ marginTop: 10, paddingTop: 8, borderTop: "1px solid #2E3237" }}>
                            <div style={{ color: ACCENT, fontWeight: 700, marginBottom: 6 }}>Variants</div>
                            <div>{variants.enabled ? "enabled" : "disabled"} · {variants.variant_count} scene{variants.variant_count === 1 ? "" : "s"}</div>
                            <div>lighting: {variants.lighting.join(", ")}</div>
                            <div>cameras: {variants.cameras.join(", ")}</div>
                            {variants.actors.length > 0 && <div>actors: {variants.actors.join(", ")}</div>}
                            <div>circumstances: {variants.circumstances.join(", ")}</div>
                        </div>
                    )}
                </div>
                <pre
                    style={{
                        margin: 0,
                        padding: 10,
                        flex: 1,
                        minHeight: 120,
                        maxHeight: 184,
                        overflow: "auto",
                        color: TEXT_PRIMARY,
                        background: "#0F1216",
                        fontSize: 11,
                        lineHeight: 1.45,
                        whiteSpace: "pre-wrap",
                    }}
                >
                    {code || (error ? "" : "Generated code will appear here.")}
                </pre>
            </div>
        </div>
    );
}

function BootOverlay({ text, color = TEXT_SECONDARY }: { text: string; color?: string }) {
    return (
        <div
            style={{
                position: "absolute",
                inset: 0,
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                background: "rgba(17,18,20,0.85)",
                color,
                fontSize: 13,
                pointerEvents: "none",
                zIndex: 100,
            }}
        >
            {text}
        </div>
    );
}

function StatusBar({
    revision,
    saveState,
    sessionId,
}: {
    revision: number;
    saveState: "idle" | "saving" | "saved" | "failed";
    sessionId: string;
}) {
    let saveLabel = "";
    let saveColor: string = TEXT_SECONDARY;
    if (saveState === "saving") {
        saveLabel = "● saving…";
        saveColor = "#FFA800";
    } else if (saveState === "saved") {
        saveLabel = "✓ saved";
        saveColor = ACCENT;
    } else if (saveState === "failed") {
        saveLabel = "⚠ save failed";
        saveColor = "#FF4444";
    }
    return (
        <div
            style={{
                height: 22,
                background: "#181A1D",
                borderTop: "1px solid #2E3237",
                display: "flex",
                alignItems: "center",
                paddingLeft: 12,
                paddingRight: 12,
                color: TEXT_SECONDARY,
                fontSize: 11,
                gap: 16,
                flexShrink: 0,
            }}
        >
            <span style={{ color: ACCENT }}>● rev {revision}</span>
            <span>session: {sessionId}</span>
            {saveLabel && <span style={{ color: saveColor }}>{saveLabel}</span>}
            <span style={{ marginLeft: "auto", fontSize: 10 }}>
                ⌘Z undo · ⌘⇧Z redo · ⌫ delete · ⎋ deselect
            </span>
        </div>
    );
}
