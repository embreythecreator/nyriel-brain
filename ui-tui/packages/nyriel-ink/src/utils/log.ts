export function logError(error: unknown): void {
  if (!process.env.NYRIEL_INK_DEBUG_ERRORS) {
    return
  }

  console.error(error)
}
