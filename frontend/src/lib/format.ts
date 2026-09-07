/** Small display helpers shared by the instructor and student pages. */

/**
 * The backend sends naive ISO timestamps (no timezone suffix), so they are
 * rendered as-is in local terms rather than being shifted by a UTC assumption.
 */
export function formatDate(iso: string): string {
  const parsed = new Date(iso);
  if (Number.isNaN(parsed.getTime())) return iso;
  return parsed.toLocaleDateString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}
