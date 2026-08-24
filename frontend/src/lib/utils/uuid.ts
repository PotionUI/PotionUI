// crypto.randomUUID() only exists in SECURE contexts (https or localhost).
// The app is routinely served over plain http on a LAN ip, where it is
// undefined and any module calling it crashes at load (the tabs store took
// the whole frontend down this way). crypto.getRandomValues() has no such
// restriction, so fall back to building a spec-compliant v4 UUID from it —
// ids stay globally unique (tab ids route queued generations back to their
// tab, so uniqueness is load-bearing, not cosmetic).
export function randomUUID(): string {
	const c = globalThis.crypto;
	if (c?.randomUUID) return c.randomUUID();
	const b = c.getRandomValues(new Uint8Array(16));
	b[6] = (b[6] & 0x0f) | 0x40; // version 4
	b[8] = (b[8] & 0x3f) | 0x80; // RFC 4122 variant
	const h = Array.from(b, (x) => x.toString(16).padStart(2, '0')).join('');
	return `${h.slice(0, 8)}-${h.slice(8, 12)}-${h.slice(12, 16)}-${h.slice(16, 20)}-${h.slice(20)}`;
}
