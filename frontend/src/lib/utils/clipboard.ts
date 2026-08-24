/**
 * Copy text to the clipboard, falling back to the legacy `execCommand('copy')`
 * trick when the async Clipboard API is unavailable or rejects. This matters
 * here because `navigator.clipboard` requires a secure context (https or
 * localhost) — the app is routinely served over plain http on a LAN address,
 * where `navigator.clipboard` is `undefined` (same restriction that hit
 * `crypto.randomUUID()`, see uuid.ts). SSR-safe: no-ops to `false` if neither
 * `navigator` nor `document` exist.
 */
export async function copyText(text: string): Promise<boolean> {
	const nav = typeof navigator === 'undefined' ? undefined : navigator;
	if (nav?.clipboard?.writeText) {
		try {
			await nav.clipboard.writeText(text);
			return true;
		} catch {
			// Fall through to the legacy fallback below.
		}
	}
	return legacyCopy(text);
}

function legacyCopy(text: string): boolean {
	if (typeof document === 'undefined') return false;
	const textarea = document.createElement('textarea');
	textarea.value = text;
	textarea.setAttribute('readonly', '');
	textarea.style.position = 'fixed';
	textarea.style.top = '-9999px';
	textarea.style.left = '-9999px';
	document.body.appendChild(textarea);
	textarea.select();
	textarea.setSelectionRange(0, textarea.value.length);
	let ok = false;
	try {
		ok = document.execCommand('copy');
	} catch {
		ok = false;
	}
	document.body.removeChild(textarea);
	return ok;
}
