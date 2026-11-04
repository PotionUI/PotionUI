import type { OutputEntry } from './types';

export type TileKind = 'image' | 'video' | 'audio';

type Entry = Partial<OutputEntry> | null | undefined;

const KINDS = new Set<string>(['image', 'video', 'audio']);

export function tileKind(entry: Entry): TileKind {
	const kind = String(entry?.media_type || '').toLowerCase();
	return KINDS.has(kind) ? (kind as TileKind) : 'image';
}

export function tileKey(entry: Entry): string {
	if (entry?.kind === 'text') return `text:${entry.text_id}`;
	if (entry?.kind === 'library') return `library:${entry.item_id}`;
	return `history:${entry?.generation_id ?? ''}:${entry?.file_id ?? ''}`;
}

export function tileName(entry: Entry): string {
	return entry?.original_filename || (entry?.kind === 'library' ? 'Library file' : 'Generated file');
}

export function fullSource(entry: Entry): string | null {
	if (entry?.media_url) return entry.media_url;
	const preview = entry?.preview_url;
	if (!preview) return null;
	const [path, query = ''] = preview.split('?');
	const kept = query
		.split('&')
		.filter((part) => part && !part.startsWith('size='))
		.join('&');
	return kept ? `${path}?${kept}` : path;
}

export function stillSource(entry: Entry): string | null {
	return tileKind(entry) === 'video' ? null : entry?.preview_url || fullSource(entry);
}

export function videoSource(entry: Entry): string | null {
	const source = fullSource(entry);
	return source ? `${source}#t=0.1` : null;
}

export function formatDuration(seconds: unknown): string {
	if (typeof seconds !== 'number' || !Number.isFinite(seconds) || seconds < 0) return '';
	const whole = Math.round(seconds);
	const minutes = Math.floor(whole / 60);
	const rest = String(whole % 60).padStart(2, '0');
	if (minutes < 60) return `${minutes}:${rest}`;
	return `${Math.floor(minutes / 60)}:${String(minutes % 60).padStart(2, '0')}:${rest}`;
}
