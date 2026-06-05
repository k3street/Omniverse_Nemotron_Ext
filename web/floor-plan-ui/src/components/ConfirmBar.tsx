/**
 * Floating confirm bar — shown when the most recent commit on the
 * undo stack is a `BulkUpdate` from an agent write (description starts
 * with "Agent: ").  Spec §6.5 + §11.2.
 *
 * Accept: clears undo entry from "review" status (no-op — already
 * applied; just dismisses the bar).
 * Reject: pops the BulkUpdate off undo, restoring previous state.
 *
 * The bar slides in at the top of the canvas viewport with arrive-tier
 * motion (360 ms ease-out-expo).
 */
import { useFloorPlanStore } from "../store/floorPlanStore";
import { transition } from "../canvas/motionTokens";

const ACCENT = "#76B900";
const REJECT = "#FF6B6B";
const TEXT = "#DDDDDD";
const BG = "#1F242AF2";
const BORDER = "#4A5560";

export function ConfirmBar() {
    const undoStack = useFloorPlanStore((s) => s.undoStack);
    const undo = useFloorPlanStore((s) => s.undo);
    const last = undoStack[undoStack.length - 1];
    const isAgentBulk =
        last !== undefined &&
        last.type === "bulk_update" &&
        last.description.startsWith("Agent:");

    if (!isAgentBulk) return null;

    return (
        <div
            style={{
                position: "absolute",
                top: 12,
                left: "50%",
                transform: "translateX(-50%)",
                background: BG,
                border: `1px solid ${BORDER}`,
                borderRadius: 6,
                padding: "8px 12px",
                display: "flex",
                alignItems: "center",
                gap: 12,
                color: TEXT,
                fontSize: 12,
                boxShadow: "0 6px 24px rgba(0,0,0,0.4)",
                zIndex: 50,
                backdropFilter: "blur(6px)",
                transition: transition("arrive"),
            }}
            data-testid="confirm-bar"
        >
            <span style={{ color: ACCENT, fontWeight: 600 }}>● Proposed change</span>
            <span style={{ color: "#8A8E92", fontSize: 11 }}>{last.description}</span>
            <div style={{ display: "flex", gap: 6, marginLeft: 8 }}>
                <button
                    type="button"
                    onClick={() => undo()}
                    style={{
                        background: "transparent",
                        color: REJECT,
                        border: `1px solid ${REJECT}55`,
                        borderRadius: 4,
                        fontSize: 11,
                        padding: "4px 12px",
                        cursor: "pointer",
                    }}
                >
                    Reject
                </button>
                <button
                    type="button"
                    onClick={() => {
                        // Accept = clear from review status by no-op; the
                        // BulkUpdate stays on the undo stack so user can
                        // still undo manually later.
                        // Future: mark accepted in metadata so bar dismisses.
                        useFloorPlanStore.setState({});
                    }}
                    style={{
                        background: ACCENT,
                        color: "#000",
                        border: "none",
                        borderRadius: 4,
                        fontSize: 11,
                        fontWeight: 600,
                        padding: "4px 14px",
                        cursor: "pointer",
                    }}
                >
                    Accept
                </button>
            </div>
        </div>
    );
}
