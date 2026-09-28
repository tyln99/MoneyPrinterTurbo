import type { components } from "./schema"

type Schemas = components["schemas"]

export type EpisodeSummary = Schemas["EpisodeSummary"]
export type EpisodeDetail = Schemas["EpisodeDetailData"]
export type Scene = Schemas["SceneData"]
export type Asset = Schemas["AssetData"]
export type Render = Schemas["RenderData"]
export type Project = Schemas["ProjectData"]
export type TaskStatus = Schemas["TaskStatusData"]
export type VideoRequest = Schemas["TaskVideoRequest"]
export type Catalog = Schemas["CatalogData"]
export type VoiceOption = Schemas["VoiceOption"]
export type LlmProvider = Schemas["LlmProviderOption"]

/**
 * A credential is never sent to the client; `read_settings` replaces it with
 * this shape. Writing it back unchanged means sending UNCHANGED_SECRET, not
 * the mask -- see app/services/ui_settings.py.
 */
export type MaskedSecret = { set: boolean; count: number; hint: string }
export const UNCHANGED_SECRET = "__unchanged__"

export function isMaskedSecret(value: unknown): value is MaskedSecret {
  return typeof value === "object" && value !== null && "set" in value && "hint" in value
}

/**
 * The API wraps everything in `{status, message, data}` and, on a validation
 * error, answers 400 rather than 422 with the errors in `data` (see the
 * RequestValidationError handler in app/asgi.py). `utils.get_response` also
 * drops `data` and `message` when they are falsy, so neither can be assumed
 * present. This unwraps all of that into a value or a thrown ApiError.
 */
export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
    readonly detail?: unknown,
  ) {
    super(message)
    this.name = "ApiError"
  }
}

type Envelope<T> = { status?: number; message?: string; data?: T }

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: {
      ...(init?.body ? { "Content-Type": "application/json" } : {}),
      ...init?.headers,
    },
  })

  let body: Envelope<T> | undefined
  try {
    body = (await response.json()) as Envelope<T>
  } catch {
    // A proxy or the static mount can answer with something that is not JSON.
    throw new ApiError(response.status, `${response.status} ${response.statusText}`)
  }

  if (!response.ok) {
    throw new ApiError(response.status, body?.message ?? response.statusText, body?.data)
  }
  return (body?.data ?? ({} as T)) as T
}

export const api = {
  episodes: (params: { limit?: number; offset?: number; project_id?: number; query?: string }) => {
    const search = new URLSearchParams()
    if (params.limit != null) search.set("limit", String(params.limit))
    if (params.offset != null) search.set("offset", String(params.offset))
    if (params.project_id != null) search.set("project_id", String(params.project_id))
    if (params.query) search.set("query", params.query)
    return request<{
      episodes: EpisodeSummary[]
      total: number
      limit: number
      offset: number
    }>(`/api/v1/episodes?${search}`)
  },
  episode: (id: string) => request<EpisodeDetail>(`/api/v1/episodes/${id}`),
  scenes: (id: string) => request<{ scenes: Scene[] }>(`/api/v1/episodes/${id}/scenes`),
  assets: (id: string) => request<{ assets: Asset[] }>(`/api/v1/episodes/${id}/assets`),
  renders: (id: string) => request<{ renders: Render[] }>(`/api/v1/episodes/${id}/renders`),
  logs: (id: string) => request<{ task_id: string; logs: string[] }>(`/api/v1/tasks/${id}/logs`),
  task: (id: string) => request<TaskStatus>(`/api/v1/tasks/${id}`),
  projects: () => request<{ projects: Project[] }>("/api/v1/projects"),
  createProject: (name: string) =>
    request<Project>("/api/v1/projects", { method: "POST", body: JSON.stringify({ name }) }),
  moveEpisode: (id: string, projectId: number) =>
    request<null>(`/api/v1/episodes/${id}`, {
      method: "PATCH",
      body: JSON.stringify({ project_id: projectId }),
    }),
  deleteTask: (id: string) => request<null>(`/api/v1/tasks/${id}`, { method: "DELETE" }),

  catalog: () => request<Catalog>("/api/v1/catalog"),
  voices: (ttsServer: string) =>
    request<{ tts_server: string; voices: VoiceOption[] }>(
      `/api/v1/catalog/voices?tts_server=${encodeURIComponent(ttsServer)}`,
    ),
  settings: () => request<{ sections: Record<string, Record<string, unknown>> }>("/api/v1/settings"),
  saveSettings: (sections: Record<string, Record<string, unknown>>) =>
    request<{ changed: string[] }>("/api/v1/settings", {
      method: "PUT",
      body: JSON.stringify({ sections }),
    }),
  // The LLM steps the wizard offers before submitting. Both are synchronous
  // calls that can take ten seconds or more on a slow provider.
  generateScript: (body: {
    video_subject: string
    video_language?: string
    paragraph_number?: number
  }) => request<{ video_script: string }>("/api/v1/scripts", {
    method: "POST",
    body: JSON.stringify(body),
  }),
  generateTerms: (body: {
    video_subject: string
    video_script: string
    amount?: number
    match_materials_to_script?: boolean
  }) => request<{ video_terms: string[] }>("/api/v1/terms", {
    method: "POST",
    body: JSON.stringify(body),
  }),
  createVideo: (params: Partial<VideoRequest>) =>
    request<{ task_id: string }>("/api/v1/videos", {
      method: "POST",
      body: JSON.stringify(params),
    }),
}

/** app/models/const.py */
export const TASK_STATE = { FAILED: -1, COMPLETE: 1, PROCESSING: 4 } as const

export function stateLabel(state: number | null | undefined) {
  switch (state) {
    case TASK_STATE.FAILED:
      return { label: "Failed", variant: "destructive" as const }
    case TASK_STATE.COMPLETE:
      return { label: "Complete", variant: "success" as const }
    case TASK_STATE.PROCESSING:
      return { label: "Processing", variant: "default" as const }
    default:
      // The DB leaves `state` NULL for imported tasks; the UI calls that history.
      return { label: "History", variant: "secondary" as const }
  }
}
