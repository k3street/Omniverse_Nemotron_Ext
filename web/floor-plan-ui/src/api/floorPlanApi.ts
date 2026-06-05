/**
 * Typed REST client for the canvas API.  Mirrors backend endpoints in
 * service/isaac_assist_service/multimodal/routes.py.
 *
 * Errors:
 * - 409 Conflict on `patch` carries `ConflictDetail` for three-way merge UI.
 * - Validation failure on `patch` returns 200 with `valid: false`; treat as
 *   error in UI flow.
 */
import {
    CanvasGetResponse,
    ConflictDetail,
    LayoutSpec,
    PatchSuccessResponse,
    PatchValidationFailureResponse,
} from "./types";

export class CanvasConflictError extends Error {
    constructor(public detail: ConflictDetail) {
        super(
            `revision conflict: expected ${detail.expected_revision}, ` +
                `actual ${detail.actual_revision}`,
        );
        this.name = "CanvasConflictError";
    }
}

export class CanvasValidationError extends Error {
    constructor(public detail: PatchValidationFailureResponse) {
        super(
            `LayoutSpec validation failed: ` +
                detail.issues.map((i) => `[${i.code}] ${i.message}`).join("; "),
        );
        this.name = "CanvasValidationError";
    }
}

export interface CanvasApi {
    get(sessionId: string): Promise<CanvasGetResponse>;
    patch(sessionId: string, spec: LayoutSpec, parentRevision: number): Promise<PatchSuccessResponse>;
    commit(sessionId: string): Promise<{ committed: true; revision: number }>;
    previewRender(sessionId: string): Promise<{ rendered: true; path: string; revision: number }>;
    build(sessionId: string, opts?: { template_id?: string; force_freeform?: boolean }): Promise<unknown>;
    delete(sessionId: string): Promise<{ deleted: true; removed_revisions: number }>;
    reportClientError(sessionId: string, message: string, stack?: string): Promise<void>;
}

export function createCanvasApi(baseUrl: string = ""): CanvasApi {
    const url = (path: string) => `${baseUrl}/api/v1/canvas${path}`;
    return {
        async get(sessionId) {
            const r = await fetch(url(`/${encodeURIComponent(sessionId)}`));
            if (!r.ok) throw new Error(`GET canvas failed: ${r.status}`);
            return r.json();
        },
        async patch(sessionId, spec, parentRevision) {
            const r = await fetch(url(`/${encodeURIComponent(sessionId)}/patch`), {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ spec, parent_revision: parentRevision }),
            });
            if (r.status === 409) {
                const detail = (await r.json()).detail as ConflictDetail;
                throw new CanvasConflictError(detail);
            }
            if (!r.ok) throw new Error(`POST patch failed: ${r.status}`);
            const body = await r.json();
            if (!body.valid) {
                throw new CanvasValidationError(body as PatchValidationFailureResponse);
            }
            return body as PatchSuccessResponse;
        },
        async commit(sessionId) {
            const r = await fetch(url(`/${encodeURIComponent(sessionId)}/commit`), {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: "{}",
            });
            if (!r.ok) throw new Error(`POST commit failed: ${r.status}`);
            return r.json();
        },
        async previewRender(sessionId) {
            const r = await fetch(url(`/${encodeURIComponent(sessionId)}/preview_render`), {
                method: "POST",
            });
            if (!r.ok) throw new Error(`POST preview_render failed: ${r.status}`);
            return r.json();
        },
        async build(sessionId, opts = {}) {
            const r = await fetch(url(`/${encodeURIComponent(sessionId)}/build`), {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(opts),
            });
            if (!r.ok) throw new Error(`POST build failed: ${r.status}`);
            return r.json();
        },
        async delete(sessionId) {
            const r = await fetch(url(`/${encodeURIComponent(sessionId)}`), {
                method: "DELETE",
            });
            if (!r.ok) throw new Error(`DELETE failed: ${r.status}`);
            return r.json();
        },
        async reportClientError(sessionId, message, stack) {
            await fetch(url(`/${encodeURIComponent(sessionId)}/client_error`), {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ message, stack }),
            });
        },
    };
}
