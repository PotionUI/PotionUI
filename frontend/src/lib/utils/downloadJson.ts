/** Sanitizes a proposed filename: strips path separators / illegal filesystem
 *  characters, falls back to a default when nothing usable remains. */
function sanitizeFilename(name: string, fallback: string): string {
	const cleaned = name.replace(/[/\\?%*:|"<>]/g, '').trim();
	return cleaned || fallback;
}

/** Serializes `value` as pretty-printed JSON and triggers a browser download
 *  of it as `filename`. */
export function downloadJson(filename: string, value: unknown): void {
	const safeFilename = sanitizeFilename(filename, 'automation.json');
	const json = JSON.stringify(value, null, 2);
	const blob = new Blob([json], { type: 'application/json' });
	const url = URL.createObjectURL(blob);

	const anchor = document.createElement('a');
	anchor.href = url;
	anchor.download = safeFilename;
	document.body.appendChild(anchor);
	anchor.click();
	document.body.removeChild(anchor);

	URL.revokeObjectURL(url);
}
