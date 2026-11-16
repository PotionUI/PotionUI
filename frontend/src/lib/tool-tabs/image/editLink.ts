import type { EditSource, EditStateDoc } from '$lib/services/api/edits';
import type { MediaRef } from '$lib/types/tabs';
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

export function editSourceOf(source: MediaRef | null): EditSource | null {
	if (!source) return null;
	if (source.artifact_id) return { kind: 'upload', id: source.artifact_id };
	if (source.item_id) return { kind: 'library', id: source.item_id };
	if (source.generation_file_id) return { kind: 'history', id: source.generation_file_id };
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
