/**
 * Persistent chat input ribbon — bottom of viewport (spec §11.2).
 *
 * The full chat thread lives in the omni.ui Kit panel; the SPA shows
 * only the latest agent line plus the input box, since users on the
 * canvas are dragging objects with their right hand and want a quick
 * way to type a refinement without switching panels.
 *
 * Submission posts to `/api/v1/chat/{session}/message` and displays
 * the most recent agent response inline.  Full transcript stays in Kit.
 */
import { useRef, useState } from "react";
import { useFloorPlanStore } from "../store/floorPlanStore";
import { transition } from "../canvas/motionTokens";
import { createCanvasApi } from "../api/floorPlanApi";

const BG = "#171A1E";
const BORDER = "#2E3237";
const TEXT = "#DDDDDD";
const TEXT_DIM = "#8A8E92";
const ACCENT = "#76B900";
const INPUT_BG = "#0F1216";
const api = createCanvasApi("");

export function ChatRibbon() {
    const sessionId = useFloorPlanStore((s) => s.sessionId);
    const revision = useFloorPlanStore((s) => s.revision);
    const setSpec = useFloorPlanStore((s) => s.setSpec);
    const [value, setValue] = useState("");
    const [latestAgent, setLatestAgent] = useState<string | null>(null);
    const [busy, setBusy] = useState(false);
    const [importBusy, setImportBusy] = useState(false);
    const [viewportBusy, setViewportBusy] = useState(false);
    const fileInputRef = useRef<HTMLInputElement | null>(null);

    const submit = async () => {
        const text = value.trim();
        if (!text || busy) return;
        setBusy(true);
        setValue("");
        try {
            const r = await fetch(
                `/api/v1/chat/${encodeURIComponent(sessionId)}/message`,
                {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ text }),
                },
            );
            if (r.ok) {
                const body = await r.json();
                if (body.text) setLatestAgent(String(body.text));
            }
        } catch (e) {
            console.warn("[chat] submit failed:", e);
        } finally {
            setBusy(false);
        }
    };

    const importSceneFromImage = async (file: File | null) => {
        if (!file || importBusy || viewportBusy) return;
        setImportBusy(true);
        try {
            const imageBase64 = await readFileBase64(file);
            const response = await api.cosmosObserve(sessionId, {
                prompt: value.trim() || "Reconstruct this robotics scene as an Isaac Sim floor plan.",
                image_base64: imageBase64,
                mime_type: file.type || "image/png",
                input_kind: "photo",
                parent_revision: revision,
            });
            setSpec(response.spec, response.revision);
            setLatestAgent(`Imported ${response.spec.objects?.length ?? 0} proposed objects from image.`);
            setValue("");
        } catch (e) {
            console.warn("[cosmos] image import failed:", e);
            setLatestAgent(`Image import failed: ${String(e)}`);
        } finally {
            setImportBusy(false);
            if (fileInputRef.current) fileInputRef.current.value = "";
        }
    };

    const importSceneFromViewport = async () => {
        if (viewportBusy) return;
        setViewportBusy(true);
        try {
            const response = await api.cosmosObserveViewport(sessionId, {
                prompt: value.trim() || "Reconstruct the current Isaac Sim viewport as a robotics floor plan.",
                max_dim: 1280,
                parent_revision: revision,
            });
            setSpec(response.spec, response.revision);
            const capture = response.viewport_capture;
            const captureLabel = capture?.width && capture?.height
                ? ` from ${capture.width}x${capture.height} viewport`
                : " from viewport";
            setLatestAgent(`Imported ${response.spec.objects?.length ?? 0} proposed objects${captureLabel}.`);
            setValue("");
        } catch (e) {
            console.warn("[cosmos] viewport import failed:", e);
            setLatestAgent(`Viewport import failed: ${String(e)}`);
        } finally {
            setViewportBusy(false);
        }
    };

    const anyImportBusy = importBusy || viewportBusy;

    return (
        <div
            style={{
                background: BG,
                borderTop: `1px solid ${BORDER}`,
                display: "flex",
                flexDirection: "column",
                padding: "6px 12px 8px",
                gap: 4,
            }}
            data-testid="chat-ribbon"
        >
            {latestAgent && (
                <div
                    style={{
                        color: TEXT_DIM,
                        fontSize: 11,
                        lineHeight: 1.3,
                        maxHeight: 32,
                        overflow: "hidden",
                        textOverflow: "ellipsis",
                        transition: transition("arrive"),
                    }}
                >
                    <span style={{ color: ACCENT, marginRight: 6 }}>Isaac</span>
                    {latestAgent}
                </div>
            )}
            <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
                <input
                    ref={fileInputRef}
                    type="file"
                    accept="image/png,image/jpeg,image/webp"
                    style={{ display: "none" }}
                    onChange={(e) => {
                        void importSceneFromImage(e.currentTarget.files?.[0] ?? null);
                    }}
                />
                <button
                    type="button"
                    title="Import scene from image"
                    onClick={() => fileInputRef.current?.click()}
                    disabled={anyImportBusy}
                    style={{
                        width: 32,
                        height: 32,
                        background: anyImportBusy ? "#2E3237" : "#252A30",
                        color: anyImportBusy ? TEXT_DIM : TEXT,
                        border: `1px solid ${BORDER}`,
                        borderRadius: 4,
                        fontSize: 15,
                        cursor: anyImportBusy ? "default" : "pointer",
                        display: "flex",
                        alignItems: "center",
                        justifyContent: "center",
                    }}
                >
                    {importBusy ? "..." : "▣"}
                </button>
                <button
                    type="button"
                    title="Import scene from current Isaac viewport"
                    onClick={() => void importSceneFromViewport()}
                    disabled={anyImportBusy}
                    style={{
                        width: 36,
                        height: 32,
                        background: viewportBusy ? "#2E3237" : "#252A30",
                        color: viewportBusy ? TEXT_DIM : TEXT,
                        border: `1px solid ${BORDER}`,
                        borderRadius: 4,
                        fontSize: 11,
                        fontWeight: 700,
                        cursor: anyImportBusy ? "default" : "pointer",
                        display: "flex",
                        alignItems: "center",
                        justifyContent: "center",
                        letterSpacing: 0,
                    }}
                >
                    {viewportBusy ? "..." : "VP"}
                </button>
                <input
                    type="text"
                    value={value}
                    placeholder="Describe a change, e.g. 'add a second franka panda mirror of the first'"
                    onChange={(e) => setValue(e.target.value)}
                    onKeyDown={(e) => {
                        if (e.key === "Enter" && !e.shiftKey) {
                            e.preventDefault();
                            submit();
                        }
                    }}
                    disabled={busy}
                    style={{
                        flex: 1,
                        background: INPUT_BG,
                        border: `1px solid ${BORDER}`,
                        borderRadius: 4,
                        color: TEXT,
                        fontSize: 12,
                        padding: "7px 10px",
                        outline: "none",
                        fontFamily: "inherit",
                    }}
                />
                <button
                    type="button"
                    onClick={submit}
                    disabled={busy || !value.trim()}
                    style={{
                        background: busy ? "#2E3237" : ACCENT,
                        color: busy ? TEXT_DIM : "#000",
                        border: "none",
                        borderRadius: 4,
                        fontSize: 12,
                        fontWeight: 600,
                        padding: "7px 16px",
                        cursor: busy ? "default" : "pointer",
                    }}
                >
                    Send
                </button>
            </div>
        </div>
    );
}

function readFileBase64(file: File): Promise<string> {
    return new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onerror = () => reject(reader.error ?? new Error("Failed to read image"));
        reader.onload = () => {
            const value = String(reader.result ?? "");
            resolve(value.includes(",") ? value.split(",", 2)[1] : value);
        };
        reader.readAsDataURL(file);
    });
}
