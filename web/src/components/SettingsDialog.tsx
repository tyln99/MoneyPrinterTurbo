import { ExternalLink, Loader2 } from "lucide-react"
import { useEffect, useState } from "react"
import { isMaskedSecret, UNCHANGED_SECRET } from "@/api/client"
import { useCatalog, useSaveSettings, useSettings } from "@/api/hooks"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Field } from "@/components/ui/label"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"

/** Material providers, keyed by the `[app]` config key each one reads. */
const MATERIAL_KEYS: { key: string; label: string; hint?: string }[] = [
  { key: "pexels_api_keys", label: "Pexels", hint: "Free. Comma-separate several keys." },
  { key: "pixabay_api_keys", label: "Pixabay", hint: "Free." },
  { key: "coverr_api_keys", label: "Coverr" },
  { key: "openai_image_api_key", label: "OpenAI-compatible image" },
  { key: "volcengine_seedance_api_key", label: "Volcengine Seedance" },
  { key: "metaso_minimax_api_key", label: "Metaso MiniMax" },
  { key: "ofox_api_key", label: "OfoxAI" },
  { key: "muapi_api_key", label: "MuAPI" },
  { key: "wavespeed_api_keys", label: "WaveSpeed" },
]

export function SettingsDialog({
  open,
  onOpenChange,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
}) {
  const settings = useSettings()
  const catalog = useCatalog()
  const save = useSaveSettings()

  // Edits are held locally and only flushed on Save, so a half-typed API key is
  // never written to the shared config.toml.
  const [draft, setDraft] = useState<Record<string, Record<string, unknown>>>({})

  useEffect(() => {
    if (open) setDraft({})
  }, [open])

  const app = settings.data?.sections.app ?? {}
  const ui = settings.data?.sections.ui ?? {}
  const provider = String(draft.app?.llm_provider ?? app.llm_provider ?? "moonshot")
  const spec = catalog.data?.llm_providers.find((item) => item.provider_id === provider)

  const edit = (section: string, key: string, value: unknown) =>
    setDraft((current) => ({
      ...current,
      [section]: { ...current[section], [key]: value },
    }))

  const stored = (section: string, key: string) =>
    settings.data?.sections[section]?.[key]

  /** A secret renders as its mask; only a typed replacement is sent. */
  const secretValue = (section: string, key: string) => {
    const edited = draft[section]?.[key]
    return edited === undefined ? "" : String(edited)
  }
  const secretPlaceholder = (section: string, key: string) => {
    const value = stored(section, key)
    if (isMaskedSecret(value) && value.set) {
      return value.count > 1 ? `${value.count} keys saved (${value.hint})` : `Saved ${value.hint}`
    }
    return "Not set"
  }

  const textValue = (section: string, key: string, fallback = "") => {
    const edited = draft[section]?.[key]
    if (edited !== undefined) return String(edited)
    const value = stored(section, key)
    return value == null || isMaskedSecret(value) ? fallback : String(value)
  }

  const submit = () => {
    // Every untouched secret is sent as the sentinel rather than omitted, so a
    // provider switch cannot appear to clear the keys it did not touch.
    save.mutate(draft, { onSuccess: () => onOpenChange(false) })
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[85vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle>Settings</DialogTitle>
          <DialogDescription>
            Saved to config.toml, shared with the Streamlit UI. Existing keys are shown
            masked — leave a field blank to keep the key that is already saved.
          </DialogDescription>
        </DialogHeader>

        {settings.isPending || catalog.isPending ? (
          <Loader2 className="mx-auto my-8 size-5 animate-spin text-muted-foreground" />
        ) : (
          <Tabs defaultValue="llm">
            <TabsList>
              <TabsTrigger value="llm">LLM</TabsTrigger>
              <TabsTrigger value="material">Material APIs</TabsTrigger>
              <TabsTrigger value="voice">Voice</TabsTrigger>
            </TabsList>

            <TabsContent value="llm" className="space-y-4">
              <Field label="Provider">
                <Select
                  value={provider}
                  onValueChange={(value) => edit("app", "llm_provider", value)}
                >
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {catalog.data?.llm_providers.map((item) => (
                      <SelectItem key={item.provider_id} value={item.provider_id}>
                        {item.label}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </Field>

              {spec?.show_api_key && (
                <Field
                  label="API key"
                  hint={spec.api_key_url ? undefined : "Required to generate a script."}
                >
                  <Input
                    type="password"
                    autoComplete="off"
                    placeholder={secretPlaceholder("app", `${provider}_api_key`)}
                    value={secretValue("app", `${provider}_api_key`)}
                    onChange={(event) =>
                      edit("app", `${provider}_api_key`, event.target.value)
                    }
                  />
                  {spec.api_key_url && (
                    <a
                      className="mt-1 inline-flex items-center gap-1 text-xs text-muted-foreground hover:underline"
                      href={spec.api_key_url}
                      target="_blank"
                      rel="noreferrer"
                    >
                      Get a key <ExternalLink className="size-3" />
                    </a>
                  )}
                </Field>
              )}
              {spec?.requires_model_name && (
                <Field label="Model">
                  <Input
                    placeholder={spec.default_model || "Model name"}
                    value={textValue("app", `${provider}_model_name`)}
                    onChange={(event) =>
                      edit("app", `${provider}_model_name`, event.target.value)
                    }
                  />
                </Field>
              )}
              {spec?.show_base_url && (
                <Field label="Base URL">
                  <Input
                    placeholder={spec.default_base_url || "Leave blank for the default"}
                    value={textValue("app", `${provider}_base_url`)}
                    onChange={(event) =>
                      edit("app", `${provider}_base_url`, event.target.value)
                    }
                  />
                </Field>
              )}
              {spec?.extra_fields.map((extra) => (
                <Field key={extra.config_suffix} label={extra.config_suffix}>
                  <Input
                    type={extra.secret ? "password" : "text"}
                    autoComplete="off"
                    placeholder={
                      extra.secret
                        ? secretPlaceholder("app", `${provider}_${extra.config_suffix}`)
                        : extra.default_value
                    }
                    value={
                      extra.secret
                        ? secretValue("app", `${provider}_${extra.config_suffix}`)
                        : textValue("app", `${provider}_${extra.config_suffix}`)
                    }
                    onChange={(event) =>
                      edit("app", `${provider}_${extra.config_suffix}`, event.target.value)
                    }
                  />
                </Field>
              ))}
            </TabsContent>

            <TabsContent value="material" className="space-y-4">
              {MATERIAL_KEYS.map((item) => (
                <Field key={item.key} label={item.label} hint={item.hint}>
                  <Input
                    type="password"
                    autoComplete="off"
                    placeholder={secretPlaceholder("app", item.key)}
                    value={secretValue("app", item.key)}
                    onChange={(event) => edit("app", item.key, event.target.value)}
                  />
                </Field>
              ))}
            </TabsContent>

            <TabsContent value="voice" className="space-y-4">
              <Field
                label="TTS provider"
                hint="Edge TTS (Azure V1) is free and needs no key."
              >
                <Select
                  value={String(draft.ui?.tts_server ?? ui.tts_server ?? "azure-tts-v1")}
                  onValueChange={(value) => edit("ui", "tts_server", value)}
                >
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {catalog.data?.tts_servers.map((item) => (
                      <SelectItem key={item.value} value={item.value}>
                        {item.label}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </Field>
              <Field label="Azure speech key">
                <Input
                  type="password"
                  autoComplete="off"
                  placeholder={secretPlaceholder("azure", "speech_key")}
                  value={secretValue("azure", "speech_key")}
                  onChange={(event) => edit("azure", "speech_key", event.target.value)}
                />
              </Field>
              <Field label="Azure region">
                <Input
                  value={textValue("azure", "speech_region")}
                  onChange={(event) => edit("azure", "speech_region", event.target.value)}
                />
              </Field>
              <Field label="ElevenLabs API key">
                <Input
                  type="password"
                  autoComplete="off"
                  placeholder={secretPlaceholder("elevenlabs", "api_key")}
                  value={secretValue("elevenlabs", "api_key")}
                  onChange={(event) => edit("elevenlabs", "api_key", event.target.value)}
                />
              </Field>
            </TabsContent>
          </Tabs>
        )}

        {save.isError && (
          <p className="mt-3 text-sm text-destructive">{(save.error as Error).message}</p>
        )}

        <div className="mt-5 flex justify-end gap-2">
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button
            disabled={save.isPending || Object.keys(draft).length === 0}
            onClick={submit}
          >
            {save.isPending && <Loader2 className="animate-spin" />}
            Save
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  )
}

export { UNCHANGED_SECRET }
