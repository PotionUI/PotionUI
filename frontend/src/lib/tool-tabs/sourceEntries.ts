import { sameMedia } from './sources';
import { confirmDialog } from '$lib/stores/confirm';
import type { MediaRef } from '$lib/types/tabs';
import type { ToolTabHost } from './types';

export type SourceEntryId = 'library' | 'history' | 'browse';

export interface SourceEntry {
	id: SourceEntryId;
	label: string;
	icon: string;
}

export type SourceKind = 'image' | 'video';

export function sourceEntries(kind: SourceKind, phone = false): SourceEntry[] {
	const shared: SourceEntry[] = [
		{ id: 'library', label: 'Library', icon: kind === 'image' ? 'image' : 'library' },
		{ id: 'history', label: 'History', icon: 'clock' }
	];
	if (kind === 'video') return shared;
	return [...shared, { id: 'browse', label: phone ? 'Photos' : 'Browse files', icon: 'folder' }];
}

export function discardTitle(edits: number): string {
	return `Discard ${edits} unsaved ${edits === 1 ? 'edit' : 'edits'}?`;
}

export async function replaceSource(
	host: Pick<ToolTabHost, 'replaceSource'>,
	current: MediaRef | null,
	next: MediaRef,
	edits: number,
	noun: 'picture' | 'clip'
): Promise<boolean> {
	if (current && sameMedia(current, next)) return false;
	if (edits > 0) {
		const proceed = await confirmDialog({
			title: discardTitle(edits),
			message: `Replacing this ${noun} drops its edits.`,
			variant: 'warning',
			confirmLabel: 'Replace',
			cancelLabel: 'Keep editing'
		});
		if (!proceed) return false;
	}
	host.replaceSource(next);
	return true;
}
