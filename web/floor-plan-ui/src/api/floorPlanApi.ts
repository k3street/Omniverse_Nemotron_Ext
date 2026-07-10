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
    BuildResponse,
    CanvasGetResponse,
    CampaignLaunchResponse,
    CampaignPlanResponse,
    ConflictDetail,
    CosmosGenerateRequest,
    CosmosGenerateResponse,
    CosmosObserveRequest,
    CosmosObserveResponse,
    CosmosViewportObserveRequest,
    CosmosViewportObserveResponse,
    LayoutSpec,
    LocalAssetOptionsResponse,
    PatchSuccessResponse,
    PatchValidationFailureResponse,
    RenderingMode,
    RenderingModeResponse,
    TypedObject,
    ScenarioVariants,
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
    build(sessionId: string, opts?: {
        template_id?: string;
        force_freeform?: boolean;
        dry_run?: boolean;
        execute_direct?: boolean;
    }): Promise<BuildResponse>;
    assetOptions(opts?: { q?: string; limit?: number }): Promise<LocalAssetOptionsResponse>;
    planCampaign(sessionId: string, opts?: { workspace_root?: string }): Promise<CampaignPlanResponse>;
    materializeCampaign(sessionId: string, opts?: { workspace_root?: string }): Promise<CampaignPlanResponse>;
    launchCampaign(sessionId: string, opts?: {
        workspace_root?: string;
        variant_index?: number;
        variant_id?: string;
        dry_run?: boolean;
        wait?: boolean;
        startup_grace_s?: number;
    }): Promise<CampaignLaunchResponse>;
    cosmosObserve(sessionId: string, req: CosmosObserveRequest): Promise<CosmosObserveResponse>;
    cosmosObserveViewport(
        sessionId: string,
        req: CosmosViewportObserveRequest,
    ): Promise<CosmosViewportObserveResponse>;
    cosmosGenerate(sessionId: string, req: CosmosGenerateRequest): Promise<CosmosGenerateResponse>;
    getRenderingMode(): Promise<RenderingModeResponse>;
    setRenderingMode(mode: RenderingMode): Promise<RenderingModeResponse>;
    delete(sessionId: string): Promise<{ deleted: true; removed_revisions: number }>;
    reportClientError(sessionId: string, message: string, stack?: string): Promise<void>;
}

type BackendTypedObject = Record<string, unknown> & {
    object_class?: string;
    class?: string;
};

export const DEFAULT_SCENARIO_VARIANTS: ScenarioVariants = {
    enabled: false,
    variant_count: 1,
    seed: 1,
    lighting: ["studio"],
    cameras: ["overhead"],
    actors: [],
    circumstances: ["nominal"],
    perturbations: {
        enabled: true,
        pose_jitter_m: 0.03,
        rotation_jitter_deg: 5,
        material_randomization: true,
        sensor_noise: false,
    },
    validation: {
        require_relations: true,
        require_visibility: true,
        require_physics: true,
    },
};

export function normalizeLayoutSpec(spec: LayoutSpec | null): LayoutSpec | null {
    if (!spec) return spec;
    return {
        ...spec,
        scenario_variants: {
            ...DEFAULT_SCENARIO_VARIANTS,
            ...(spec.scenario_variants ?? {}),
            perturbations: {
                ...DEFAULT_SCENARIO_VARIANTS.perturbations,
                ...(spec.scenario_variants?.perturbations ?? {}),
            },
            validation: {
                ...DEFAULT_SCENARIO_VARIANTS.validation,
                ...(spec.scenario_variants?.validation ?? {}),
            },
        },
        objects: (spec.objects ?? []).map((raw) => {
            const obj = raw as unknown as BackendTypedObject;
            const objectClass = obj.class ?? obj.object_class;
            const normalized = { ...obj, class: objectClass } as BackendTypedObject;
            delete normalized.object_class;
            return normalized as unknown as TypedObject;
        }),
    };
}

function normalizePatchResponse<T extends { spec: LayoutSpec }>(body: T): T {
    return {
        ...body,
        spec: normalizeLayoutSpec(body.spec) as LayoutSpec,
    };
}

export function createCanvasApi(baseUrl: string = ""): CanvasApi {
    const url = (path: string) => `${baseUrl}/api/v1/canvas${path}`;
    const settingsUrl = (path: string) => `${baseUrl}/api/v1/settings${path}`;
    return {
        async get(sessionId) {
            const r = await fetch(url(`/${encodeURIComponent(sessionId)}`));
            if (!r.ok) throw new Error(`GET canvas failed: ${r.status}`);
            const body = (await r.json()) as CanvasGetResponse;
            return { ...body, spec: normalizeLayoutSpec(body.spec) };
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
            return normalizePatchResponse(body as PatchSuccessResponse);
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
        async assetOptions(opts = {}) {
            const params = new URLSearchParams();
            if (opts.q) params.set("q", opts.q);
            if (opts.limit) params.set("limit", String(opts.limit));
            const suffix = params.toString() ? `?${params.toString()}` : "";
            const r = await fetch(url(`/assets/options${suffix}`));
            if (!r.ok) throw new Error(`GET asset options failed: ${r.status}`);
            return r.json();
        },
        async planCampaign(sessionId, opts = {}) {
            const r = await fetch(url(`/${encodeURIComponent(sessionId)}/campaign/plan`), {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(opts),
            });
            if (!r.ok) throw new Error(`POST campaign plan failed: ${r.status}`);
            return r.json();
        },
        async materializeCampaign(sessionId, opts = {}) {
            const r = await fetch(url(`/${encodeURIComponent(sessionId)}/campaign/materialize`), {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(opts),
            });
            if (!r.ok) throw new Error(`POST campaign materialize failed: ${r.status}`);
            return r.json();
        },
        async launchCampaign(sessionId, opts = {}) {
            const r = await fetch(url(`/${encodeURIComponent(sessionId)}/campaign/launch`), {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(opts),
            });
            if (!r.ok) throw new Error(`POST campaign launch failed: ${r.status}`);
            return r.json();
        },
        async cosmosObserve(sessionId, req) {
            const r = await fetch(url(`/${encodeURIComponent(sessionId)}/cosmos/observe`), {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(req),
            });
            if (!r.ok) {
                const err = await r.text();
                throw new Error(`POST cosmos/observe failed: ${r.status} ${err}`);
            }
            return normalizePatchResponse((await r.json()) as CosmosObserveResponse);
        },
        async cosmosObserveViewport(sessionId, req) {
            const r = await fetch(url(`/${encodeURIComponent(sessionId)}/cosmos/observe_viewport`), {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(req),
            });
            if (!r.ok) {
                const err = await r.text();
                throw new Error(`POST cosmos/observe_viewport failed: ${r.status} ${err}`);
            }
            return normalizePatchResponse((await r.json()) as CosmosViewportObserveResponse);
        },
        async cosmosGenerate(sessionId, req) {
            const r = await fetch(url(`/${encodeURIComponent(sessionId)}/cosmos/generate`), {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(req),
            });
            if (!r.ok) {
                const err = await r.text();
                throw new Error(`POST cosmos/generate failed: ${r.status} ${err}`);
            }
            return r.json();
        },
        async getRenderingMode() {
            const r = await fetch(settingsUrl("/rendering_mode"));
            if (!r.ok) throw new Error(`GET rendering_mode failed: ${r.status}`);
            return r.json();
        },
        async setRenderingMode(mode) {
            const r = await fetch(settingsUrl("/rendering_mode"), {
                method: "PUT",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ mode }),
            });
            if (!r.ok) throw new Error(`PUT rendering_mode failed: ${r.status}`);
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
