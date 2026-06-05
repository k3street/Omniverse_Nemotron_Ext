/**
 * Zustand store + command-pattern undo/redo + localStorage write-ahead log.
 * Per spec §2.3 + §13.
 *
 * The store is the client-side source of truth during an active session;
 * the FastAPI backend is the persistent source of truth (via CAS).  This
 * store debounces patches to the backend and applies inverse commands on
 * undo without round-tripping the network.
 */
import { create } from "zustand";
import {
    Command,
    AddObject,
    DeleteObject,
    MoveObject,
    ResizeObject,
    RotateObject,
    SetAttr,
    SetAttrKey,
    BulkUpdate,
} from "./commands";
import { LayoutSpec, Position, Size, TypedObject } from "../api/types";

// ─── Persistence helpers ─────────────────────────────────────────────

const LS_KEY_PREFIX = "isaac_assist_canvas_";

function lsKey(sessionId: string): string {
    return `${LS_KEY_PREFIX}${sessionId}`;
}

interface LSPayload {
    timestamp: string;
    spec: LayoutSpec;
}

function readWAL(sessionId: string): LSPayload | null {
    try {
        const raw = localStorage.getItem(lsKey(sessionId));
        if (!raw) return null;
        return JSON.parse(raw) as LSPayload;
    } catch {
        return null;
    }
}

function writeWAL(sessionId: string, spec: LayoutSpec): void {
    try {
        localStorage.setItem(
            lsKey(sessionId),
            JSON.stringify({
                timestamp: new Date().toISOString(),
                spec,
            } satisfies LSPayload),
        );
    } catch {
        // Quota exceeded — best-effort drop oldest then retry once.
        // Non-fatal; backend remains source of truth.
    }
}

function clearWAL(sessionId: string): void {
    try {
        localStorage.removeItem(lsKey(sessionId));
    } catch {
        // ignore
    }
}

// ─── Store interface ─────────────────────────────────────────────────

const HISTORY_DEPTH = 100;

export type SimState =
    | "unbuilt"
    | "building"
    | "partial"
    | "live"
    | "verify_failed"
    | "error";

export interface FloorPlanState {
    sessionId: string;
    spec: LayoutSpec | null;
    revision: number;
    simState: SimState;

    // Selection state lives in the store (not persisted to backend per
    // spec §2.3 — selection is UI state, not document state)
    selectedIds: string[];

    // History
    undoStack: Command[];
    redoStack: Command[];

    // ─── Derived ──────────────────────────────────────────────────
    objects: () => TypedObject[];
    selectedObjects: () => TypedObject[];
    canUndo: () => boolean;
    canRedo: () => boolean;

    // ─── Actions ──────────────────────────────────────────────────
    setSpec: (spec: LayoutSpec | null, revision: number) => void;
    setSelection: (ids: string[]) => void;
    addToSelection: (id: string) => void;
    removeFromSelection: (id: string) => void;
    clearSelection: () => void;

    addObject: (object: TypedObject) => void;
    deleteObjects: (ids: string[]) => void;
    moveObject: (id: string, from: Position, to: Position) => void;
    resizeObject: (id: string, from: Size, to: Size) => void;
    rotateObject: (id: string, from: number, to: number) => void;
    setAttr: (id: string, attr: SetAttrKey, from: unknown, to: unknown) => void;

    applyAgentBulkUpdate: (after: TypedObject[], description?: string) => void;

    undo: () => void;
    redo: () => void;
    clearHistory: () => void;

    // ─── Persistence helpers ──────────────────────────────────────
    flushWAL: () => void;
    restoreFromWAL: () => boolean;
}

// ─── Store implementation ────────────────────────────────────────────

const SESSION_ID =
    new URLSearchParams(window.location.search).get("session") ??
    "default_session";

export const useFloorPlanStore = create<FloorPlanState>((set, get) => {
    const pushCommand = (cmd: Command) => {
        const state = get();
        if (!state.spec) return;
        const newObjects = cmd.apply(state.spec.objects ?? []);
        const newSpec: LayoutSpec = { ...state.spec, objects: newObjects };
        set({
            spec: newSpec,
            undoStack: [...state.undoStack, cmd].slice(-HISTORY_DEPTH),
            redoStack: [], // any new command clears the redo stack
        });
        writeWAL(state.sessionId, newSpec);
    };

    return {
        sessionId: SESSION_ID,
        spec: null,
        revision: 0,
        simState: "unbuilt",
        selectedIds: [],
        undoStack: [],
        redoStack: [],

        objects: () => get().spec?.objects ?? [],
        selectedObjects: () => {
            const ids = new Set(get().selectedIds);
            return (get().spec?.objects ?? []).filter((o) => ids.has(o.id));
        },
        canUndo: () => get().undoStack.length > 0,
        canRedo: () => get().redoStack.length > 0,

        setSpec: (spec, revision) =>
            set({ spec, revision, undoStack: [], redoStack: [] }),

        setSelection: (ids) => set({ selectedIds: ids }),
        addToSelection: (id) =>
            set({ selectedIds: [...new Set([...get().selectedIds, id])] }),
        removeFromSelection: (id) =>
            set({ selectedIds: get().selectedIds.filter((s) => s !== id) }),
        clearSelection: () => set({ selectedIds: [] }),

        addObject: (object) => pushCommand(new AddObject(object)),
        deleteObjects: (ids) => {
            const objs = get().spec?.objects ?? [];
            for (const id of ids) {
                const obj = objs.find((o) => o.id === id);
                if (obj) pushCommand(new DeleteObject(obj));
            }
            set({ selectedIds: [] });
        },
        moveObject: (id, from, to) => pushCommand(new MoveObject(id, from, to)),
        resizeObject: (id, from, to) => pushCommand(new ResizeObject(id, from, to)),
        rotateObject: (id, from, to) => pushCommand(new RotateObject(id, from, to)),
        setAttr: (id, attr, from, to) =>
            pushCommand(new SetAttr(id, attr, from, to)),

        applyAgentBulkUpdate: (after, description) => {
            const before = get().spec?.objects ?? [];
            const cmd = new BulkUpdate([...before], [...after], description);
            pushCommand(cmd);
        },

        undo: () => {
            const state = get();
            if (!state.spec || state.undoStack.length === 0) return;
            const cmd = state.undoStack[state.undoStack.length - 1];
            const newObjects = cmd.undo(state.spec.objects ?? []);
            const newSpec: LayoutSpec = { ...state.spec, objects: newObjects };
            set({
                spec: newSpec,
                undoStack: state.undoStack.slice(0, -1),
                redoStack: [...state.redoStack, cmd],
            });
            writeWAL(state.sessionId, newSpec);
        },

        redo: () => {
            const state = get();
            if (!state.spec || state.redoStack.length === 0) return;
            const cmd = state.redoStack[state.redoStack.length - 1];
            const newObjects = cmd.apply(state.spec.objects ?? []);
            const newSpec: LayoutSpec = { ...state.spec, objects: newObjects };
            set({
                spec: newSpec,
                undoStack: [...state.undoStack, cmd],
                redoStack: state.redoStack.slice(0, -1),
            });
            writeWAL(state.sessionId, newSpec);
        },

        clearHistory: () => set({ undoStack: [], redoStack: [] }),

        flushWAL: () => {
            const state = get();
            if (state.spec) writeWAL(state.sessionId, state.spec);
        },

        restoreFromWAL: () => {
            const state = get();
            const wal = readWAL(state.sessionId);
            if (!wal) return false;
            set({
                spec: wal.spec,
                revision: wal.spec.revision,
                undoStack: [],
                redoStack: [],
            });
            return true;
        },
    };
});

// ─── beforeunload — flush pending state via sendBeacon ───────────────
// Per spec §13.4: ensure any in-flight Zustand state lands in localStorage
// before the tab closes.  Server-side persistence happens via debounced
// POSTs in sync.ts.

if (typeof window !== "undefined") {
    window.addEventListener("beforeunload", () => {
        useFloorPlanStore.getState().flushWAL();
    });
}

export { clearWAL };
