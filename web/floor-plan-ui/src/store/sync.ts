/**
 * Backend sync — debounced PATCH to FastAPI + SSE listener.
 *
 * Per spec §2.5 + §13:
 * - Client mutations debounce 200ms before issuing POST patch
 * - SSE connection auto-reconnects with last-event-id on disconnect
 * - 409 conflict opens three-way merge UI (handled in App layer)
 */
import { CanvasApi } from "../api/floorPlanApi";
import { LayoutSpec } from "../api/types";
import { useFloorPlanStore } from "./floorPlanStore";

const PATCH_DEBOUNCE_MS = 200;

let _patchTimer: ReturnType<typeof setTimeout> | null = null;
let _pendingSpec: LayoutSpec | null = null;
let _pendingResolves: Array<(ok: boolean) => void> = [];

/** Schedule a debounced backend save.  Returns a Promise that resolves when
 * the actual POST round-trip completes (or rejects on conflict/error). */
export function schedulePatch(api: CanvasApi, spec: LayoutSpec): Promise<boolean> {
    _pendingSpec = spec;
    return new Promise<boolean>((resolve) => {
        _pendingResolves.push(resolve);
        if (_patchTimer !== null) clearTimeout(_patchTimer);
        _patchTimer = setTimeout(() => {
            _patchTimer = null;
            void flushPatch(api);
        }, PATCH_DEBOUNCE_MS);
    });
}

async function flushPatch(api: CanvasApi): Promise<void> {
    if (_pendingSpec === null) return;
    const spec = _pendingSpec;
    _pendingSpec = null;
    const resolves = _pendingResolves;
    _pendingResolves = [];

    const state = useFloorPlanStore.getState();
    try {
        const result = await api.patch(state.sessionId, spec, state.revision);
        useFloorPlanStore.setState({ revision: result.revision });
        resolves.forEach((r) => r(true));
    } catch (e) {
        // Conflict and validation errors propagate via App-layer handler;
        // here we just signal "did not save"
        console.warn("[sync] patch failed:", e);
        resolves.forEach((r) => r(false));
    }
}

/** Force-flush any pending debounced patch (called by Send button or
 * route navigation). */
export function flushPendingPatch(api: CanvasApi): Promise<void> {
    if (_patchTimer !== null) {
        clearTimeout(_patchTimer);
        _patchTimer = null;
    }
    return flushPatch(api);
}

// ─── SSE listener ────────────────────────────────────────────────────

export interface SSEEvent {
    type: string;
    payload: Record<string, unknown>;
    id?: string;
}

export type SSEHandler = (evt: SSEEvent) => void;

export function startSSE(sessionId: string, handler: SSEHandler): () => void {
    let lastEventId: string | undefined;
    let stopped = false;
    let source: EventSource | null = null;

    const connect = () => {
        if (stopped) return;
        const url = `/api/v1/chat/stream/${encodeURIComponent(sessionId)}` +
            (lastEventId ? `?last_event_id=${encodeURIComponent(lastEventId)}` : "");
        source = new EventSource(url);
        source.addEventListener("message", (msg) => {
            try {
                const data = JSON.parse(msg.data);
                lastEventId = msg.lastEventId || lastEventId;
                handler({ type: data.type ?? "unknown", payload: data.payload ?? {}, id: msg.lastEventId });
            } catch (e) {
                console.warn("[sse] bad message:", e);
            }
        });
        source.addEventListener("error", () => {
            source?.close();
            // Reconnect with backoff (linear, capped at 5s)
            if (!stopped) {
                setTimeout(connect, 1000);
            }
        });
    };
    connect();

    return () => {
        stopped = true;
        source?.close();
    };
}
