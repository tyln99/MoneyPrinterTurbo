import type * as React from "react"
import { cn } from "@/lib/utils"

/**
 * A checkbox styled as a switch. Radix's Switch would be a third dependency
 * for a control with no behaviour a checkbox lacks, and a real checkbox keeps
 * label association and keyboard handling for free.
 */
export function Switch({
  checked,
  onCheckedChange,
  className,
  ...props
}: Omit<React.ComponentProps<"input">, "onChange" | "type"> & {
  onCheckedChange?: (checked: boolean) => void
}) {
  return (
    // The track and the knob are both siblings of the input so each can use the
    // `peer-checked:` variant; a nested knob could not see the checked state.
    <label
      className={cn("relative inline-flex cursor-pointer items-center gap-2", className)}
    >
      <input
        type="checkbox"
        className="peer sr-only"
        checked={checked}
        onChange={(event) => onCheckedChange?.(event.target.checked)}
        {...props}
      />
      <span className="h-5 w-9 rounded-full bg-input transition-colors peer-checked:bg-primary peer-focus-visible:ring-2 peer-focus-visible:ring-ring" />
      <span className="pointer-events-none absolute left-0.5 size-4 rounded-full bg-background transition-transform peer-checked:translate-x-4" />
    </label>
  )
}
