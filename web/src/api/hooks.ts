import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { api, TASK_STATE } from "./client"

const LIVE_POLL_MS = 2000

export function useEpisodes(params: { limit: number; offset: number; projectId?: number; query: string }) {
  return useQuery({
    queryKey: ["episodes", params],
    queryFn: () =>
      api.episodes({
        limit: params.limit,
        offset: params.offset,
        project_id: params.projectId,
        query: params.query,
      }),
    // Matches the Streamlit task panel's 2s fragment. There is no websocket or
    // SSE on the server, so progress is poll-only.
    refetchInterval: (query) =>
      query.state.data?.episodes.some((e) => e.state === TASK_STATE.PROCESSING)
        ? LIVE_POLL_MS
        : false,
    placeholderData: (previous) => previous,
  })
}

export function useProjects() {
  return useQuery({ queryKey: ["projects"], queryFn: api.projects })
}

export function useEpisode(id: string) {
  return useQuery({
    queryKey: ["episode", id],
    queryFn: () => api.episode(id),
    refetchInterval: (query) =>
      query.state.data?.state === TASK_STATE.PROCESSING ? LIVE_POLL_MS : false,
  })
}

export function useScenes(id: string) {
  return useQuery({ queryKey: ["scenes", id], queryFn: () => api.scenes(id) })
}

export function useAssets(id: string) {
  return useQuery({ queryKey: ["assets", id], queryFn: () => api.assets(id) })
}

export function useRenders(id: string, live: boolean) {
  return useQuery({
    queryKey: ["renders", id],
    queryFn: () => api.renders(id),
    refetchInterval: live ? LIVE_POLL_MS : false,
  })
}

export function useLogs(id: string, live: boolean) {
  return useQuery({
    queryKey: ["logs", id],
    queryFn: () => api.logs(id),
    refetchInterval: live ? LIVE_POLL_MS : false,
  })
}

export function useCreateProject() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: api.createProject,
    onSuccess: () => client.invalidateQueries({ queryKey: ["projects"] }),
  })
}

export function useMoveEpisode() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: ({ id, projectId }: { id: string; projectId: number }) =>
      api.moveEpisode(id, projectId),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: ["episodes"] })
      client.invalidateQueries({ queryKey: ["projects"] })
    },
  })
}

export function useDeleteTask() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: api.deleteTask,
    onSuccess: () => client.invalidateQueries({ queryKey: ["episodes"] }),
  })
}
