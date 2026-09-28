import { ArrowLeft, Loader2, Settings, Sparkles, Wand2 } from "lucide-react"
import { useEffect, useState } from "react"
import { Link, useNavigate } from "react-router-dom"
import type { VideoRequest } from "@/api/client"
import {
  useCatalog,
  useCreateVideo,
  useGenerateScript,
  useGenerateTerms,
  useSettings,
  useVoices,
} from "@/api/hooks"
import { SettingsDialog } from "@/components/SettingsDialog"
import { Button } from "@/components/ui/button"
import { Field } from "@/components/ui/label"
import { Input } from "@/components/ui/input"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Switch } from "@/components/ui/switch"
import { Textarea } from "@/components/ui/textarea"
import { cn } from "@/lib/utils"

const STEPS = ["Script", "Video", "Audio", "Subtitles"] as const

// Taken from the generated schema, so a new enum member on the server shows up
// here as a type error instead of a silently missing option.
type Aspect = NonNullable<VideoRequest["video_aspect"]>
type Transition = NonNullable<VideoRequest["video_transition_mode"]>

const ASPECTS: { value: Aspect; label: string }[] = [
  { value: "9:16", label: "9:16 — portrait" },
  { value: "16:9", label: "16:9 — landscape" },
  { value: "1:1", label: "1:1 — square" },
]

const TRANSITIONS: Transition[] = [
  "None",
  "Shuffle",
  "FadeIn",
  "FadeOut",
  "SlideIn",
  "SlideOut",
  "ZoomIn",
  "ZoomOut",
]

const SOURCE_GROUP_LABELS: Record<string, string> = {
  stock_video: "Stock video",
  ai_video: "AI video",
  ai_image: "AI image",
  local: "Local files",
}

/**
 * Mirrors the defaults in `VideoParams` (app/models/schema.py). Anything the
 * user has saved before overrides these, read from the `[ui]` config section
 * exactly as the Streamlit form does.
 */
const FALLBACK: Partial<VideoRequest> = {
  video_subject: "",
  video_script: "",
  video_terms: "",
  video_language: "",
  video_source: "pexels",
  video_aspect: "9:16",
  video_concat_mode: "random",
  video_transition_mode: "None",
  video_clip_duration: 5,
  video_count: 1,
  voice_name: "",
  voice_volume: 1,
  voice_rate: 1,
  bgm_type: "random",
  bgm_volume: 0.2,
  subtitle_enabled: true,
  font_name: "",
  subtitle_position: "bottom",
  font_size: 60,
  text_fore_color: "#FFFFFF",
  stroke_color: "#000000",
  stroke_width: 1.5,
  paragraph_number: 1,
}

/** The saved `[ui]` keys that map onto a VideoParams field of the same name. */
const UI_KEYS = [
  "video_language",
  "video_source",
  "video_aspect",
  "video_concat_mode",
  "video_transition_mode",
  "video_clip_duration",
  "video_count",
  "voice_name",
  "voice_volume",
  "voice_rate",
  "bgm_type",
  "bgm_volume",
  "subtitle_enabled",
  "font_name",
  "subtitle_position",
  "font_size",
  "text_fore_color",
  "stroke_color",
  "stroke_width",
  "paragraph_number",
] as const

export function Create() {
  const navigate = useNavigate()
  const catalog = useCatalog()
  const settings = useSettings()
  const createVideo = useCreateVideo()
  const generateScript = useGenerateScript()
  const generateTerms = useGenerateTerms()

  const [step, setStep] = useState(0)
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [params, setParams] = useState<Partial<VideoRequest>>(FALLBACK)
  const [hydrated, setHydrated] = useState(false)

  // Pre-fill from what the user saved last time, once. Re-running this on every
  // settings refetch would overwrite edits made since the form opened.
  useEffect(() => {
    if (hydrated || !settings.data) return
    const ui = settings.data.sections.ui ?? {}
    const saved: Record<string, unknown> = {}
    for (const key of UI_KEYS) {
      if (ui[key] !== undefined && ui[key] !== null) saved[key] = ui[key]
    }
    setParams((current) => ({ ...current, ...saved }))
    setHydrated(true)
  }, [settings.data, hydrated])

  const ttsServer = String(settings.data?.sections.ui?.tts_server ?? "azure-tts-v1")
  const voices = useVoices(ttsServer, Boolean(settings.data))

  const set = <K extends keyof VideoRequest>(key: K, value: VideoRequest[K]) =>
    setParams((current) => ({ ...current, [key]: value }))

  const sourceGroups = catalog.data?.video_sources ?? {}
  const hasSubject = Boolean(params.video_subject?.trim())
  // The pipeline writes the script from the subject, and the keywords from
  // whichever of the two is present, so either one is enough to start.
  const canGenerateTerms = hasSubject || Boolean(params.video_script?.trim())
  const needsScript = !canGenerateTerms

  const submit = () => {
    createVideo.mutate(params, {
      onSuccess: (data) => navigate(`/episodes/${data.task_id}`),
    })
  }

  if (catalog.isPending || settings.isPending) {
    return (
      <div className="p-10 text-center">
        <Loader2 className="mx-auto size-5 animate-spin text-muted-foreground" />
      </div>
    )
  }

  return (
    <div className="mx-auto max-w-3xl p-6">
      <div className="flex items-center">
        <Button variant="ghost" size="sm" asChild>
          <Link to="/">
            <ArrowLeft /> Library
          </Link>
        </Button>
        <Button
          variant="ghost"
          size="sm"
          className="ml-auto"
          onClick={() => setSettingsOpen(true)}
        >
          <Settings /> Settings
        </Button>
      </div>

      <h1 className="mb-6 mt-4 text-xl font-semibold">New video</h1>

      <ol className="mb-6 flex gap-2">
        {STEPS.map((name, index) => (
          <li key={name} className="flex-1">
            <button
              type="button"
              onClick={() => setStep(index)}
              className={cn(
                "w-full rounded-md border px-3 py-2 text-sm transition-colors",
                index === step
                  ? "border-primary bg-secondary font-medium"
                  : "border-border text-muted-foreground hover:bg-accent",
              )}
            >
              {index + 1}. {name}
            </button>
          </li>
        ))}
      </ol>

      <div className="space-y-4 rounded-lg border border-border p-5">
        {step === 0 && (
          <>
            <Field
              label="Video subject"
              hint="What the video is about. The script is written from this."
            >
              <Textarea
                autoFocus
                rows={3}
                value={params.video_subject ?? ""}
                onChange={(event) => set("video_subject", event.target.value)}
                placeholder="e.g. Three habits that improve sleep"
              />
            </Field>
            <div className="grid gap-4 sm:grid-cols-2">
              <Field label="Script language" hint="Leave blank to follow the subject.">
                <Input
                  value={params.video_language ?? ""}
                  onChange={(event) => set("video_language", event.target.value)}
                  placeholder="auto"
                />
              </Field>
              <Field label="Paragraphs" hint="Longer script, longer video.">
                <Input
                  type="number"
                  min={1}
                  max={10}
                  value={params.paragraph_number ?? 1}
                  onChange={(event) =>
                    set("paragraph_number", Number(event.target.value))
                  }
                />
              </Field>
            </div>

            <Field
              label="Script"
              hint="Write it yourself, or generate it from the subject. Leaving it blank generates one at submit time too."
            >
              <Textarea
                rows={6}
                value={params.video_script ?? ""}
                onChange={(event) => set("video_script", event.target.value)}
              />
            </Field>
            <div className="flex flex-wrap items-center gap-2">
              <Button
                variant="outline"
                size="sm"
                disabled={!hasSubject || generateScript.isPending}
                title={hasSubject ? undefined : "Enter a subject first"}
                onClick={() =>
                  generateScript.mutate(
                    {
                      video_subject: params.video_subject ?? "",
                      video_language: params.video_language ?? "",
                      paragraph_number: params.paragraph_number ?? 1,
                    },
                    { onSuccess: (data) => set("video_script", data.video_script) },
                  )
                }
              >
                {generateScript.isPending ? (
                  <Loader2 className="animate-spin" />
                ) : (
                  <Wand2 />
                )}
                Generate script
              </Button>
              {generateScript.isError && (
                <span className="text-sm text-destructive">
                  {(generateScript.error as Error).message}
                </span>
              )}
            </div>

            <Field
              label="Keywords"
              hint="Comma separated. These are what material is searched for."
            >
              <Input
                // VideoParams accepts a string or a list here; the server splits
                // a string on commas, so the form always sends a string.
                value={
                  Array.isArray(params.video_terms)
                    ? params.video_terms.join(", ")
                    : (params.video_terms ?? "")
                }
                onChange={(event) => set("video_terms", event.target.value)}
              />
            </Field>
            <div className="flex flex-wrap items-center gap-2">
              <Button
                variant="outline"
                size="sm"
                disabled={!canGenerateTerms || generateTerms.isPending}
                title={
                  canGenerateTerms ? undefined : "Enter a subject or a script first"
                }
                onClick={() =>
                  generateTerms.mutate(
                    {
                      video_subject: params.video_subject ?? "",
                      video_script: params.video_script ?? "",
                      amount: 5,
                    },
                    {
                      onSuccess: (data) =>
                        set("video_terms", data.video_terms.join(", ")),
                    },
                  )
                }
              >
                {generateTerms.isPending ? (
                  <Loader2 className="animate-spin" />
                ) : (
                  <Wand2 />
                )}
                Generate keywords
              </Button>
              {generateTerms.isError && (
                <span className="text-sm text-destructive">
                  {(generateTerms.error as Error).message}
                </span>
              )}
            </div>
          </>
        )}

        {step === 1 && (
          <>
            <Field label="Video source">
              <Select
                value={params.video_source ?? "pexels"}
                onValueChange={(value) => set("video_source", value)}
              >
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {Object.entries(sourceGroups).map(([group, sources]) => (
                    <div key={group}>
                      <p className="px-2 py-1.5 text-xs font-medium text-muted-foreground">
                        {SOURCE_GROUP_LABELS[group] ?? group}
                      </p>
                      {sources.map((source) => (
                        <SelectItem key={source} value={source}>
                          {source}
                        </SelectItem>
                      ))}
                    </div>
                  ))}
                </SelectContent>
              </Select>
            </Field>
            <div className="grid gap-4 sm:grid-cols-2">
              <Field label="Aspect ratio">
                <Select
                  value={params.video_aspect ?? "9:16"}
                  onValueChange={(value) => set("video_aspect", value as Aspect)}
                >
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {ASPECTS.map((aspect) => (
                      <SelectItem key={aspect.value} value={aspect.value}>
                        {aspect.label}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </Field>
              <Field label="Clip length (seconds)">
                <Input
                  type="number"
                  min={2}
                  max={30}
                  value={params.video_clip_duration ?? 5}
                  onChange={(event) =>
                    set("video_clip_duration", Number(event.target.value))
                  }
                />
              </Field>
              <Field label="How many videos">
                <Input
                  type="number"
                  min={1}
                  max={5}
                  value={params.video_count ?? 1}
                  onChange={(event) => set("video_count", Number(event.target.value))}
                />
              </Field>
              <Field label="Transition">
                <Select
                  value={params.video_transition_mode ?? "None"}
                  onValueChange={(value) => set("video_transition_mode", value as Transition)}
                >
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {TRANSITIONS.map((mode) => (
                      <SelectItem key={mode} value={mode}>
                        {mode}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </Field>
            </div>
          </>
        )}

        {step === 2 && (
          <>
            <Field
              label="Voice"
              hint={`From ${ttsServer}. Change the provider in Settings.`}
            >
              <Select
                value={params.voice_name ?? ""}
                onValueChange={(value) => set("voice_name", value)}
              >
                <SelectTrigger>
                  <SelectValue placeholder={voices.isPending ? "Loading…" : "Pick a voice"} />
                </SelectTrigger>
                <SelectContent>
                  {voices.data?.voices.map((item) => (
                    <SelectItem key={item.value} value={item.value}>
                      {item.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </Field>
            <div className="grid gap-4 sm:grid-cols-2">
              <Field label="Speaking rate">
                <Input
                  type="number"
                  step={0.1}
                  min={0.5}
                  max={2}
                  value={params.voice_rate ?? 1}
                  onChange={(event) => set("voice_rate", Number(event.target.value))}
                />
              </Field>
              <Field label="Voice volume">
                <Input
                  type="number"
                  step={0.1}
                  min={0}
                  max={2}
                  value={params.voice_volume ?? 1}
                  onChange={(event) => set("voice_volume", Number(event.target.value))}
                />
              </Field>
            </div>
            <Field label="Background music">
              <Select
                value={params.bgm_type ?? "random"}
                onValueChange={(value) => set("bgm_type", value)}
              >
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="">None</SelectItem>
                  <SelectItem value="random">Random preset</SelectItem>
                  {(catalog.data?.songs ?? []).length > 0 && (
                    <SelectItem value="custom">Pick a preset song</SelectItem>
                  )}
                </SelectContent>
              </Select>
            </Field>
            {params.bgm_type === "custom" && (
              <Field label="Preset song">
                <Select
                  value={params.bgm_file ?? ""}
                  onValueChange={(value) => set("bgm_file", value)}
                >
                  <SelectTrigger>
                    <SelectValue placeholder="Pick a song" />
                  </SelectTrigger>
                  <SelectContent>
                    {catalog.data?.songs.map((song) => (
                      <SelectItem key={song} value={song}>
                        {song}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </Field>
            )}
            <Field label="Music volume">
              <Input
                type="number"
                step={0.1}
                min={0}
                max={1}
                value={params.bgm_volume ?? 0.2}
                onChange={(event) => set("bgm_volume", Number(event.target.value))}
              />
            </Field>
          </>
        )}

        {step === 3 && (
          <>
            <div className="flex items-center justify-between">
              <span className="text-sm font-medium">Burn in subtitles</span>
              <Switch
                checked={params.subtitle_enabled ?? true}
                onCheckedChange={(value) => set("subtitle_enabled", value)}
              />
            </div>
            {params.subtitle_enabled !== false && (
              <>
                <Field label="Font">
                  <Select
                    value={params.font_name ?? ""}
                    onValueChange={(value) => set("font_name", value)}
                  >
                    <SelectTrigger>
                      <SelectValue placeholder="Default" />
                    </SelectTrigger>
                    <SelectContent>
                      {catalog.data?.fonts.map((font) => (
                        <SelectItem key={font} value={font}>
                          {font}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </Field>
                <div className="grid gap-4 sm:grid-cols-2">
                  <Field label="Position">
                    <Select
                      value={params.subtitle_position ?? "bottom"}
                      onValueChange={(value) => set("subtitle_position", value)}
                    >
                      <SelectTrigger>
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        {["top", "center", "bottom"].map((position) => (
                          <SelectItem key={position} value={position}>
                            {position}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </Field>
                  <Field label="Font size">
                    <Input
                      type="number"
                      min={20}
                      max={200}
                      value={params.font_size ?? 60}
                      onChange={(event) => set("font_size", Number(event.target.value))}
                    />
                  </Field>
                  <Field label="Text colour">
                    <Input
                      type="color"
                      className="h-9 p-1"
                      value={params.text_fore_color ?? "#FFFFFF"}
                      onChange={(event) => set("text_fore_color", event.target.value)}
                    />
                  </Field>
                  <Field label="Outline colour">
                    <Input
                      type="color"
                      className="h-9 p-1"
                      value={params.stroke_color ?? "#000000"}
                      onChange={(event) => set("stroke_color", event.target.value)}
                    />
                  </Field>
                </div>
              </>
            )}
          </>
        )}
      </div>

      {createVideo.isError && (
        <p className="mt-4 rounded-md border border-destructive/40 p-3 text-sm text-destructive">
          {(createVideo.error as Error).message}
        </p>
      )}

      <div className="mt-5 flex items-center gap-3">
        <Button
          variant="outline"
          disabled={step === 0}
          onClick={() => setStep((current) => current - 1)}
        >
          Back
        </Button>
        {step < STEPS.length - 1 ? (
          <Button className="ml-auto" onClick={() => setStep((current) => current + 1)}>
            Next
          </Button>
        ) : (
          <Button
            className="ml-auto"
            disabled={needsScript || createVideo.isPending}
            title={needsScript ? "Enter a subject or a script first" : undefined}
            onClick={submit}
          >
            {createVideo.isPending ? <Loader2 className="animate-spin" /> : <Sparkles />}
            Generate
          </Button>
        )}
      </div>

      <SettingsDialog open={settingsOpen} onOpenChange={setSettingsOpen} />
    </div>
  )
}
