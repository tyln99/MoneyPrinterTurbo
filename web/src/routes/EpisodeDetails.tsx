import { ArrowLeft, Loader2 } from "lucide-react"
import { useEffect, useRef } from "react"
import { Link, useParams } from "react-router-dom"
import { stateLabel, TASK_STATE } from "@/api/client"
import { useAssets, useEpisode, useLogs, useRenders, useScenes } from "@/api/hooks"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"

function formatMs(ms: number | null | undefined) {
  if (ms == null) return "—"
  const totalSeconds = Math.floor(ms / 1000)
  const minutes = String(Math.floor(totalSeconds / 60)).padStart(2, "0")
  const seconds = String(totalSeconds % 60).padStart(2, "0")
  return `${minutes}:${seconds}.${String(ms % 1000).padStart(3, "0")}`
}

export function EpisodeDetails() {
  const { id = "" } = useParams()
  const episode = useEpisode(id)
  const live = episode.data?.state === TASK_STATE.PROCESSING

  const scenes = useScenes(id)
  const assets = useAssets(id)
  const renders = useRenders(id, Boolean(live))
  const logs = useLogs(id, Boolean(live))

  if (episode.isPending) {
    return (
      <div className="p-10 text-center">
        <Loader2 className="mx-auto size-5 animate-spin text-muted-foreground" />
      </div>
    )
  }
  if (episode.isError) {
    return (
      <div className="mx-auto max-w-4xl p-6">
        <BackLink />
        <p className="mt-6 text-destructive">{(episode.error as Error).message}</p>
      </div>
    )
  }

  const detail = episode.data
  const { label, variant } = stateLabel(detail.state)

  return (
    <div className="mx-auto max-w-5xl p-6">
      <BackLink />

      <header className="mb-6 mt-4">
        <h1 className="text-xl font-semibold">{detail.title || detail.topic || id}</h1>
        <div className="mt-2 flex items-center gap-3 text-sm text-muted-foreground">
          <Badge variant={variant}>{label}</Badge>
          {live && <span>{detail.progress}%</span>}
          <code className="text-xs">{id}</code>
        </div>
      </header>

      <Tabs defaultValue="renders">
        <TabsList>
          <TabsTrigger value="renders">Videos ({renders.data?.renders.length ?? 0})</TabsTrigger>
          <TabsTrigger value="scenes">Scenes ({scenes.data?.scenes.length ?? 0})</TabsTrigger>
          <TabsTrigger value="assets">Materials ({assets.data?.assets.length ?? 0})</TabsTrigger>
          <TabsTrigger value="script">Script</TabsTrigger>
          <TabsTrigger value="logs">Logs</TabsTrigger>
        </TabsList>

        <TabsContent value="renders">
          {renders.data?.renders.length ? (
            <div className="grid gap-4 sm:grid-cols-2">
              {renders.data.renders.map((render) => (
                <figure key={render.file_name} className="rounded-lg border border-border p-3">
                  <video src={render.url} controls className="w-full rounded-md" />
                  <figcaption className="mt-2 flex justify-between text-xs text-muted-foreground">
                    <span>{render.file_name}</span>
                    {render.size_bytes != null && (
                      <span>{(render.size_bytes / 1e6).toFixed(1)} MB</span>
                    )}
                  </figcaption>
                </figure>
              ))}
            </div>
          ) : (
            <Empty>No finished video yet.</Empty>
          )}
        </TabsContent>

        <TabsContent value="scenes">
          {scenes.data?.scenes.length ? (
            <div className="rounded-lg border border-border">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead className="w-12">#</TableHead>
                    <TableHead>Narration</TableHead>
                    <TableHead className="w-40">Keyword</TableHead>
                    <TableHead className="w-32">Start</TableHead>
                    <TableHead className="w-32">End</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {scenes.data.scenes.map((scene) => (
                    <TableRow key={scene.idx}>
                      <TableCell className="text-muted-foreground">{scene.idx}</TableCell>
                      <TableCell>{scene.narration}</TableCell>
                      <TableCell className="text-muted-foreground">
                        {scene.search_term || "—"}
                      </TableCell>
                      <TableCell className="tabular-nums">{formatMs(scene.start_ms)}</TableCell>
                      <TableCell className="tabular-nums">{formatMs(scene.end_ms)}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          ) : (
            <Empty>
              No scenes recorded. Only imported episodes carry scene rows today — the live
              pipeline does not write them yet.
            </Empty>
          )}
        </TabsContent>

        <TabsContent value="assets">
          {assets.data?.assets.length ? (
            <div className="rounded-lg border border-border">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>File</TableHead>
                    <TableHead className="w-40">Provider</TableHead>
                    <TableHead className="w-48">Keyword</TableHead>
                    <TableHead className="w-28">Size</TableHead>
                    <TableHead className="w-20">Length</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {assets.data.assets.map((asset) => (
                    <TableRow key={asset.file_name}>
                      <TableCell className="font-mono text-xs">{asset.file_name}</TableCell>
                      <TableCell className="text-muted-foreground">{asset.provider || "—"}</TableCell>
                      <TableCell className="text-muted-foreground">
                        {asset.search_term || "—"}
                      </TableCell>
                      <TableCell className="text-muted-foreground">
                        {asset.width ? `${asset.width}×${asset.height}` : "—"}
                      </TableCell>
                      <TableCell className="text-muted-foreground">
                        {asset.duration_s != null ? `${asset.duration_s}s` : "—"}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          ) : (
            <Empty>No materials recorded.</Empty>
          )}
        </TabsContent>

        <TabsContent value="script">
          {detail.script ? (
            <pre className="whitespace-pre-wrap rounded-lg border border-border p-4 text-sm leading-relaxed">
              {detail.script}
            </pre>
          ) : (
            <Empty>No script recorded.</Empty>
          )}
        </TabsContent>

        <TabsContent value="logs">
          <LogView lines={logs.data?.logs ?? []} live={Boolean(live)} />
        </TabsContent>
      </Tabs>
    </div>
  )
}

function LogView({ lines, live }: { lines: string[]; live: boolean }) {
  const bottom = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (live) bottom.current?.scrollIntoView({ behavior: "smooth" })
  }, [lines.length, live])

  if (lines.length === 0) {
    return (
      <Empty>
        No logs for this task. The buffer holds only the 20 most recent tasks and lives in
        the process that ran them, so a task started from the Streamlit page is not visible
        here.
      </Empty>
    )
  }
  return (
    <div className="max-h-[32rem] overflow-auto rounded-lg border border-border bg-muted/40 p-3">
      <pre className="whitespace-pre-wrap font-mono text-xs leading-relaxed">
        {lines.join("\n")}
      </pre>
      <div ref={bottom} />
    </div>
  )
}

function Empty({ children }: { children: React.ReactNode }) {
  return (
    <p className="rounded-lg border border-dashed border-border p-8 text-center text-sm text-muted-foreground">
      {children}
    </p>
  )
}

function BackLink() {
  return (
    <Button variant="ghost" size="sm" asChild>
      <Link to="/">
        <ArrowLeft /> Library
      </Link>
    </Button>
  )
}
