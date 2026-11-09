import type { Region } from './regions';

function pyDumps(value: unknown): string {
	if (Array.isArray(value)) return `[${value.map(pyDumps).join(', ')}]`;
	if (value && typeof value === 'object') {
		const entries = Object.entries(value as Record<string, unknown>);
		return `{${entries.map(([key, v]) => `${JSON.stringify(key)}: ${pyDumps(v)}`).join(', ')}}`;
	}
	return JSON.stringify(value);
}

const SNAP = 1e-6;

function snap(value: number): number {
	const nearest = Math.round(value);
	return Math.abs(value - nearest) < SNAP ? nearest : value;
}

export function pixelFloor(value: number): number {
	return Math.floor(snap(value));
}

export function pixelCeil(value: number): number {
	return Math.ceil(snap(value));
}

const QUOTED = /"([^"]*)"|“([^”]*)”/g;

export function quotedSpans(caption: string): string[] {
	const spans: string[] = [];
	for (const match of caption.matchAll(QUOTED)) {
		const text = (match[1] ?? match[2] ?? '').trim();
		if (text) spans.push(text);
	}
	return spans;
}

export function mossBbox(region: Region, width: number, height: number): [number, number, number, number] | null {
	const clampTo = (value: number, max: number) => Math.min(max, Math.max(0, value));
	const top = clampTo(pixelFloor(region.y * height), height);
	const left = clampTo(pixelFloor(region.x * width), width);
	const bottom = clampTo(pixelCeil((region.y + region.h) * height), height);
	const right = clampTo(pixelCeil((region.x + region.w) * width), width);
	if (right <= left || bottom <= top) return null;
	return [top - height / 2, left - width / 2, bottom - height / 2, right - width / 2];
}

export function compileMossPrompt(prompt: string, items: Region[], width: number, height: number): string {
	const out: Record<string, unknown>[] = [];
	for (const item of items) {
		const caption = item.caption.trim();
		if (!caption) continue;
		const bbox = mossBbox(item, width, height);
		if (!bbox) continue;
		const spans = quotedSpans(caption);
		const entry: Record<string, unknown> = { label: spans.length > 0 ? 'text' : 'object', caption };
		if (spans.length > 0) entry.text = spans.join('\n');
		entry.bbox = bbox;
		out.push(entry);
	}
	if (out.length === 0) return prompt;
	return pyDumps({
		summary: { caption: prompt },
		items: out,
		bbox_coord_space: 'abs_centralized',
		bbox_format: 'yxyx'
	});
}
