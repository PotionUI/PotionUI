import { EDIT_SOURCE_NAME_MAX, type EditSource, type EditStateDoc } from '$lib/services/api/edits';
import type { MediaRef } from '$lib/types/tabs';
import { nameStem } from '$lib/tool-tabs/sources';
import { readImageState, writeImageState } from './state';

export interface EditLink {
	id: string;
	revision: number;
}

function isRecord(value: unknown): value is Record<string, unknown> {
	return typeof value === 'object' && value !== null && !Array.isArray(value);
}

export function readEditLink(raw: unknown): EditLink | null {
	if (!isRecord(raw) || !isRecord(raw.edit)) return null;
	const { id, revision } = raw.edit;
	if (typeof id !== 'string' || !id) return null;
	if (typeof revision !== 'number' || !Number.isInteger(revision) || revision < 1) return null;
	return { id, revision };
}

export function withEditLink(payload: EditStateDoc, link: EditLink | null): EditStateDoc {
	const next: EditStateDoc = { ...payload };
	delete next.edit;
	if (link) next.edit = { id: link.id, revision: link.revision };
	return next;
}

function sourceNameOf(source: MediaRef): string | null {
	const title = (source.title ?? '').trim();
	if (title) return title.slice(0, EDIT_SOURCE_NAME_MAX);
	const name = (source.name ?? '').trim();
	return name && nameStem(name) ? name.slice(0, EDIT_SOURCE_NAME_MAX) : null;
}

export function editSourceOf(source: MediaRef | null): EditSource | null {
	if (!source) return null;
	const name = sourceNameOf(source);
	const named = (kind: EditSource['kind'], id: string): EditSource => (name ? { kind, id, name } : { kind, id });
	if (source.artifact_id) return named('upload', source.artifact_id);
	if (source.item_id) return named('library', source.item_id);
	if (source.generation_file_id) return named('history', source.generation_file_id);
	return null;
}

export function contentKey(raw: EditStateDoc): string {
	const normal = writeImageState(readImageState(raw));
	return JSON.stringify([normal.stack, normal.mask, normal.saved]);
}

export type RestoreAction = 'keep' | 'push-local' | 'adopt-server' | 'conflict' | 'unlink';

export function decideRestore(localBase: number, serverRevision: number | null, unsent: boolean): RestoreAction {
	if (serverRevision === null) return 'unlink';
	if (serverRevision < localBase) return 'push-local';
	if (serverRevision === localBase) return unsent ? 'push-local' : 'keep';
	return unsent ? 'conflict' : 'adopt-server';
}
