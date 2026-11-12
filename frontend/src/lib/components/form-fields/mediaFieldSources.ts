import { primaryModifier } from '$lib/utils/platform';
import type { MediaKind } from './mediaLoaderConfig';

export type MediaSource = 'browse' | 'paste' | 'history' | 'library' | 'kits' | 'draw';

export interface MediaSourceEntry {
	source: MediaSource;
	label: string;
	icon: string;
	shortcut?: string;
}

export interface SourceOptions {
	kind: MediaKind | null;
	offersDraw: boolean;
	offersKits?: boolean;
}

export function sourceEntries(options: SourceOptions): MediaSourceEntry[] {
	const entries: MediaSourceEntry[] = [{ source: 'browse', label: 'Browse files', icon: 'folder' }];
	if (options.kind === null || options.kind === 'image') {
		entries.push({ source: 'paste', label: 'Paste from clipboard', icon: 'clipboard-list', shortcut: `${primaryModifier()} V` });
	}
	entries.push({ source: 'history', label: 'From history', icon: 'clock' });
	entries.push({ source: 'library', label: 'From library', icon: 'grid' });
	if (options.offersKits) entries.push({ source: 'kits', label: 'From a kit', icon: 'layers' });
	if (options.offersDraw && (options.kind === null || options.kind === 'image')) {
		entries.push({ source: 'draw', label: 'Draw a new image', icon: 'brush' });
	}
	return entries;
}
