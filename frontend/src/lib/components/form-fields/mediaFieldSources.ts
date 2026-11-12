import { primaryModifier } from '$lib/utils/platform';
import type { MediaKind } from './mediaLoaderConfig';

export type MediaSource = 'browse' | 'paste' | 'history' | 'library' | 'kits' | 'draw';

export type SourceIntent = 'add' | 'change';

export interface MediaSourceEntry {
	source: MediaSource;
	label: string;
	icon: string;
	shortcut?: string;
	hint?: string;
	dividerBefore?: boolean;
}

export interface SourceOptions {
	kind: MediaKind | null;
	offersDraw: boolean;
	offersKits?: boolean;
	intent?: SourceIntent;
}

export function sourceEntries(options: SourceOptions): MediaSourceEntry[] {
	const entries: MediaSourceEntry[] = [
		{ source: 'browse', label: 'Upload file', icon: 'upload' },
		{ source: 'history', label: 'History', icon: 'clock' },
		{ source: 'library', label: 'Library', icon: 'photo' }
	];
	if (options.offersKits) {
		entries.push({
			source: 'kits',
			label: 'Kits',
			icon: 'layers',
			...(options.intent === 'change' ? { hint: 'one item' } : {})
		});
	}
	let divided = false;
	const extra = (entry: MediaSourceEntry) => {
		entries.push(divided ? entry : { ...entry, dividerBefore: true });
		divided = true;
	};
	if (options.kind === null || options.kind === 'image') {
		extra({ source: 'paste', label: 'Paste', icon: 'clipboard-list', shortcut: `${primaryModifier()} V` });
	}
	if (options.offersDraw && (options.kind === null || options.kind === 'image')) {
		extra({ source: 'draw', label: 'Draw', icon: 'brush' });
	}
	return entries;
}

export function currentSourceOf(item: unknown): MediaSource | null {
	if (!item || typeof item !== 'object') return null;
	const record = item as Record<string, unknown>;
	if (typeof record.url === 'string' && record.url.startsWith('/api/media/generations/')) return 'history';
	const stored = typeof record.relative_path === 'string' ? record.relative_path : record.path;
	if (typeof stored === 'string' && stored.startsWith('uploads/')) return 'library';
	return null;
}

export function sourceNoun(kind: MediaKind | null): string {
	return kind ?? 'media';
}
