/**
 * Floating confirm bar — shown when the most recent commit on the
 * undo stack is a `BulkUpdate` from an agent write (description starts
 * with "Agent: ").  Spec §6.5 + §11.2.
 *
 * Accept: clears undo entry from "review" status (no-op — already
 * applied; just dismisses the bar).  If workflowId is set, also POSTs
 * to /api/v1/canvas/{session_id}/commit with workflow_id.
 * Reject: pops the BulkUpdate off undo, restoring previous state.  If
 * workflowId is set, POSTs to /api/v1/canvas/{session_id}/reject.
 *
 * Phase 24: workflowId prop + onAccept / onReject callbacks wired to
 * workflow lifecycle endpoints so the ConfirmBar can serve both the
 * pure-UI case (no workflow) and the agent-pipeline case (active workflow).
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

// ---------------------------------------------------------------------------
// Phase 24 typed interface — the backend-wired props.
//
// Consumers that have a live workflow pass workflowId; consumers that only
// need local undo/redo behaviour can omit it (the bar degrades gracefully).
//
// onAccept: called after the commit POST succeeds (or immediately when no
//   network call is needed).  Dismiss/no-op the bar here.
// onReject: called after the reject POST succeeds.  Receives the feedback
//   string so the caller can surface it in a chat message if desired.
// ---------------------------------------------------------------------------

export interface ConfirmBarProps {
    /** Active workflow ID to forward approval/rejection to. */
    workflowId?: string;
    /** Called after the accept action completes successfully. */
    onAccept: () => void;
    /** Called after the reject action completes. Receives the feedback text. */
    onReject: (feedback: string) => void;
}

// ---------------------------------------------------------------------------
// Internal helper — POST to canvas workflow endpoints.
// Soft-failure: logs on error but never throws (the bar calls undo regardless).
// ---------------------------------------------------------------------------

async function _postCanvasWorkflow(
    sessionId: string,
    action: "commit" | "reject",
    workflowId: string,
    feedback?: string,
): Promise<void> {
    const url = `/api/v1/canvas/${encodeURIComponent(sessionId)}/${action}`;
    const body =
        action === "commit"
            ? { workflow_id: workflowId }
            : { workflow_id: workflowId, feedback: feedback ?? "" };
    try {
        await fetch(url, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(body),
        });
    } catch (err) {
        console.warn(`[ConfirmBar] canvas/${action} POST failed:`, err);
    }
}

// ---------------------------------------------------------------------------
// Component
//
// When used without props (legacy call-site), workflowId is undefined and
// onAccept/onReject default to no-ops so existing render-trees don't break.
// ---------------------------------------------------------------------------

export function ConfirmBar({
    workflowId,
    onAccept = () => {},
    onReject = () => {},
}: Partial<ConfirmBarProps> = {}) {
    const undoStack = useFloorPlanStore((s) => s.undoStack);
    const undo = useFloorPlanStore((s) => s.undo);
    const sessionId = useFloorPlanStore((s) => s.sessionId);
    const last = undoStack[undoStack.length - 1];
    const isAgentBulk =
        last !== undefined &&
        last.type === "bulk_update" &&
        last.description.startsWith("Agent:");

    if (!isAgentBulk) return null;

    const handleAccept = async () => {
        if (workflowId) {
            await _postCanvasWorkflow(sessionId, "commit", workflowId);
        }
        // Accept = clear from review status by no-op; the BulkUpdate stays
        // on the undo stack so user can still undo manually later.
        useFloorPlanStore.setState({});
        onAccept();
    };

    const handleReject = async () => {
        const feedback = last.description;
        if (workflowId) {
            await _postCanvasWorkflow(sessionId, "reject", workflowId, feedback);
        }
        undo();
        onReject(feedback);
    };

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
                    onClick={handleReject}
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
                    onClick={handleAccept}
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
