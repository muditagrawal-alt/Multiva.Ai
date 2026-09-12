/* ===========================================================================
   The bits that only exist inside the desktop window.

   The interface is the same files whether the window or anything else is
   showing them, so every native call has to be optional: ask the window if it
   is there, use it if so, and fall back to something that works everywhere
   if not. Nothing here throws for want of a window.
   =========================================================================== */

/** True when the page is being shown by the Tauri window, not a browser. */
export function inWindow(): boolean {
  return typeof window !== "undefined"
    && "__TAURI_INTERNALS__" in window;
}

/**
 * A native "choose a folder" sheet, or null.
 *
 * Returns null both when the person cancels and when there is no window to
 * ask - the caller cannot tell the two apart, and does not need to: in both
 * cases the text field stays the way to type a path.
 */
export async function pickFolder(startIn?: string): Promise<string | null> {
  if (!inWindow()) return null;
  try {
    const { open } = await import("@tauri-apps/plugin-dialog");
    const picked = await open({
      directory: true,
      multiple: false,
      defaultPath: startIn || undefined,
      title: "Save this output to",
    });
    return typeof picked === "string" ? picked : null;
  } catch (err) {
    // A missing plugin or a denied capability: say so once in the console
    // and let the text field carry on.
    console.warn("Folder picker unavailable, falling back to a path field:", err);
    return null;
  }
}
