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
import { useState } from "react";
import { useFloorPlanStore } from "../store/floorPlanStore";
import { transition } from "../canvas/motionTokens";

const BG = "#171A1E";
const BORDER = "#2E3237";
const TEXT = "#DDDDDD";
const TEXT_DIM = "#8A8E92";
const ACCENT = "#76B900";
const INPUT_BG = "#0F1216";

export function ChatRibbon() {
    const sessionId = useFloorPlanStore((s) => s.sessionId);
    const [value, setValue] = useState("");
    const [latestAgent, setLatestAgent] = useState<string | null>(null);
    const [busy, setBusy] = useState(false);

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
