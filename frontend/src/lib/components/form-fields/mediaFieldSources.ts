import type { MediaKind } from './mediaLoaderConfig';

export type MediaSource = 'browse' | 'paste' | 'history' | 'library' | 'draw';

export interface MediaSourceEntry {
	source: MediaSource;
	label: string;
	icon: string;
	shortcut?: string;
}

export interface SourceOptions {
	kind: MediaKind | null;
	offersDraw: boolean;
}

export function sourceEntries(options: SourceOptions): MediaSourceEntry[] {
	const entries: MediaSourceEntry[] = [{ source: 'browse', label: 'Browse files', icon: 'folder' }];
	if (options.kind === null || options.kind === 'image') {
		entries.push({ source: 'paste', label: 'Paste from clipboard', icon: 'clipboard-list', shortcut: 'Ctrl V' });
	}
	entries.push({ source: 'history', label: 'From history', icon: 'clock' });
	entries.push({ source: 'library', label: 'From library', icon: 'grid' });
	if (options.offersDraw && (options.kind === null || options.kind === 'image')) {
		entries.push({ source: 'draw', label: 'Draw a new image', icon: 'brush' });
	}
	return entries;
}
