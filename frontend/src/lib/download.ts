/**
 * Trigger a browser download from an already-fetched Blob.
 *
 * The backend's download routes all require the bearer token, so a plain
 * `<a href>` would 401. The established pattern (5.3) is therefore: fetch the
 * bytes with auth, wrap them in an object URL, and click a synthetic anchor.
 *
 * That pattern was previously copy-pasted per call site. Notebooks and
 * resource files now both need it on both the instructor and student pages,
 * so it lives here once — the fetch stays in `lib/api`, only the
 * blob-to-browser step is shared.
 */
export function triggerBlobDownload(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  // Revoking immediately can cancel an in-flight save in some browsers.
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
