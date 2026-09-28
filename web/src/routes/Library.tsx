import {
  createColumnHelper,
  flexRender,
  getCoreRowModel,
  useReactTable,
} from "@tanstack/react-table"
import { Film, Loader2, Play, Plus, Search, Trash2 } from "lucide-react"
import { useMemo, useState } from "react"
import { Link } from "react-router-dom"
import { stateLabel, TASK_STATE, type EpisodeSummary } from "@/api/client"
import { useCreateProject, useDeleteTask, useEpisodes, useProjects } from "@/api/hooks"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"

const PAGE_SIZE = 20
const ALL_PROJECTS = "all"

const columnHelper = createColumnHelper<EpisodeSummary>()

export function Library() {
  const [search, setSearch] = useState("")
  const [query, setQuery] = useState("")
  const [project, setProject] = useState<string>(ALL_PROJECTS)
  const [page, setPage] = useState(0)
  const [preview, setPreview] = useState<EpisodeSummary | null>(null)

  const projectId = project === ALL_PROJECTS ? undefined : Number(project)
  const episodes = useEpisodes({
    limit: PAGE_SIZE,
    offset: page * PAGE_SIZE,
    projectId,
    query,
  })
  const projects = useProjects()
  const deleteTask = useDeleteTask()

  const columns = useMemo(
    () => [
      columnHelper.accessor("subject", {
        header: "Subject",
        cell: (info) => (
          <Link
            to={`/episodes/${info.row.original.task_id}`}
            className="line-clamp-2 font-medium hover:underline"
          >
            {info.getValue() || info.row.original.task_id}
          </Link>
        ),
      }),
      columnHelper.accessor("state", {
        header: "Status",
        cell: (info) => {
          const row = info.row.original
          const { label, variant } = stateLabel(info.getValue())
          return (
            <div className="flex items-center gap-2">
              <Badge variant={variant}>{label}</Badge>
              {info.getValue() === TASK_STATE.PROCESSING && (
                <span className="text-xs text-muted-foreground">{row.progress}%</span>
              )}
            </div>
          )
        },
      }),
      columnHelper.accessor("mtime", {
        header: "Updated",
        cell: (info) => (
          <span className="whitespace-nowrap text-muted-foreground">
            {new Date(info.getValue() * 1000).toLocaleString()}
          </span>
        ),
      }),
      columnHelper.display({
        id: "actions",
        header: "",
        cell: (info) => {
          const row = info.row.original
          return (
            <div className="flex justify-end gap-1">
              <Button
                variant="ghost"
                size="icon"
                title={row.video_url ? "Play" : "No video yet"}
                disabled={!row.video_url}
                onClick={() => setPreview(row)}
              >
                <Play />
              </Button>
              <Button variant="ghost" size="icon" title="Details" asChild>
                <Link to={`/episodes/${row.task_id}`}>
                  <Film />
                </Link>
              </Button>
              <Button
                variant="ghost"
                size="icon"
                title="Delete"
                onClick={() => {
                  if (confirm(`Delete "${row.subject || row.task_id}" and its files?`)) {
                    deleteTask.mutate(row.task_id)
                  }
                }}
              >
                <Trash2 />
              </Button>
            </div>
          )
        },
      }),
    ],
    [deleteTask],
  )

  const table = useReactTable({
    data: episodes.data?.episodes ?? [],
    columns,
    getCoreRowModel: getCoreRowModel(),
  })

  const total = episodes.data?.total ?? 0
  const lastPage = Math.max(0, Math.ceil(total / PAGE_SIZE) - 1)

  return (
    <div className="mx-auto max-w-6xl p-6">
      <header className="mb-6 flex flex-wrap items-center gap-3">
        <h1 className="mr-auto text-xl font-semibold">Library</h1>
        <NewProjectButton />
        <Button asChild>
          <Link to="/create">
            <Plus /> New video
          </Link>
        </Button>
      </header>

      <div className="mb-4 flex flex-wrap items-center gap-3">
        <form
          className="relative flex-1 min-w-64"
          onSubmit={(event) => {
            event.preventDefault()
            setPage(0)
            setQuery(search)
          }}
        >
          <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            className="pl-9"
            placeholder="Search subject and script…"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
          />
        </form>
        <Select
          value={project}
          onValueChange={(value) => {
            setPage(0)
            setProject(value)
          }}
        >
          <SelectTrigger className="w-52">
            <SelectValue placeholder="All projects" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={ALL_PROJECTS}>All projects</SelectItem>
            {projects.data?.projects.map((item) => (
              <SelectItem key={item.id} value={String(item.id)}>
                {item.name} ({item.episodes})
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      <div className="rounded-lg border border-border">
        <Table>
          <TableHeader>
            {table.getHeaderGroups().map((group) => (
              <TableRow key={group.id}>
                {group.headers.map((header) => (
                  <TableHead key={header.id}>
                    {flexRender(header.column.columnDef.header, header.getContext())}
                  </TableHead>
                ))}
              </TableRow>
            ))}
          </TableHeader>
          <TableBody>
            {episodes.isPending ? (
              <TableRow>
                <TableCell colSpan={columns.length} className="py-10 text-center">
                  <Loader2 className="mx-auto size-5 animate-spin text-muted-foreground" />
                </TableCell>
              </TableRow>
            ) : episodes.isError ? (
              <TableRow>
                <TableCell
                  colSpan={columns.length}
                  className="py-10 text-center text-destructive"
                >
                  {(episodes.error as Error).message}
                </TableCell>
              </TableRow>
            ) : table.getRowModel().rows.length === 0 ? (
              <TableRow>
                <TableCell
                  colSpan={columns.length}
                  className="py-10 text-center text-muted-foreground"
                >
                  No episodes match.
                </TableCell>
              </TableRow>
            ) : (
              table.getRowModel().rows.map((row) => (
                <TableRow key={row.id}>
                  {row.getVisibleCells().map((cell) => (
                    <TableCell key={cell.id}>
                      {flexRender(cell.column.columnDef.cell, cell.getContext())}
                    </TableCell>
                  ))}
                </TableRow>
              ))
            )}
          </TableBody>
        </Table>
      </div>

      <div className="mt-4 flex items-center gap-3 text-sm text-muted-foreground">
        <span className="mr-auto">
          {total} episode{total === 1 ? "" : "s"}
        </span>
        <Button
          variant="outline"
          size="sm"
          disabled={page === 0}
          onClick={() => setPage((current) => current - 1)}
        >
          Previous
        </Button>
        <span>
          {page + 1} / {lastPage + 1}
        </span>
        <Button
          variant="outline"
          size="sm"
          disabled={page >= lastPage}
          onClick={() => setPage((current) => current + 1)}
        >
          Next
        </Button>
      </div>

      <Dialog open={preview !== null} onOpenChange={(open) => !open && setPreview(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle className="pr-8">{preview?.subject}</DialogTitle>
          </DialogHeader>
          {preview?.video_url && (
            // The video is served by the /tasks StaticFiles mount, which
            // supports range requests so the browser can seek.
            <video src={preview.video_url} controls autoPlay className="w-full rounded-md" />
          )}
        </DialogContent>
      </Dialog>
    </div>
  )
}

function NewProjectButton() {
  const [open, setOpen] = useState(false)
  const [name, setName] = useState("")
  const createProject = useCreateProject()

  return (
    <>
      <Button variant="outline" onClick={() => setOpen(true)}>
        <Plus /> New project
      </Button>
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="max-w-sm">
          <DialogHeader>
            <DialogTitle>New project</DialogTitle>
          </DialogHeader>
          <form
            className="flex gap-2"
            onSubmit={(event) => {
              event.preventDefault()
              if (!name.trim()) return
              createProject.mutate(name, {
                onSuccess: () => {
                  setName("")
                  setOpen(false)
                },
              })
            }}
          >
            <Input
              autoFocus
              placeholder="Project name"
              value={name}
              onChange={(event) => setName(event.target.value)}
            />
            <Button type="submit" disabled={createProject.isPending}>
              Create
            </Button>
          </form>
          {createProject.isError && (
            <p className="mt-2 text-sm text-destructive">
              {(createProject.error as Error).message}
            </p>
          )}
        </DialogContent>
      </Dialog>
    </>
  )
}
