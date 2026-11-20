import { api } from '$lib/services/api/index';
import { logger } from '$lib/utils/logger';
import {
	generationLocatorFromPath,
	matchGenerationFileId,
	resolveEditableResource
} from '$lib/media/editorSource';
import type { MediaToolInputRef, MediaToolSource, ToolMediaKind, ToolScope } from './types';

export interface ResolvedToolInput {
	ref: MediaToolInputRef | null;
	reason: string | null;
}

export const NOT_IN_LIBRARY =
	'This media is not in your library and could not be added, so there is nothing to edit yet.';

export function isEditableKind(kind: string): kind is ToolMediaKind {
	return kind === 'image' || kind === 'video' || kind === 'audio';
}

export function normalizeSources(
	items: readonly MediaToolSource[],
	scope: ToolScope
): MediaToolSource[] {
	return items.map((item) => {
		if (scope === 'library' && !item.generationId && !item.itemId && !item.generationFileId) {
			return { ...item, itemId: item.id };
		}
		return item;
	});
}

async function findGenerationFileId(source: MediaToolSource): Promise<string | null> {
	const located = source.generationId
		? { generationId: source.generationId, filename: filenameOf(source) }
		: generationLocatorFromPath(source.storedPath ?? source.url);
	if (!located) return null;
	try {
		const listing = await api.listGenerationMedia(located.generationId);
		if (!listing.success || !listing.data) return null;
		return matchGenerationFileId(listing.data.media, located.filename);
	} catch (error) {
		logger.error('Failed to look up a generated file for editing:', error);
		return null;
	}
}

function filenameOf(source: MediaToolSource): string {
	const tail = (source.url || '').split('?')[0].split('/').filter(Boolean).pop();
	return tail ? decodeURIComponent(tail) : source.filename;
}

export async function resolveToolInput(source: MediaToolSource): Promise<ResolvedToolInput> {
	if (source.generationFileId) {
		return { ref: { generation_file_id: source.generationFileId }, reason: null };
	}
	if (source.itemId) return { ref: { item_id: source.itemId }, reason: null };

	if (source.generationId) {
		const fileId = await findGenerationFileId(source);
		return fileId
			? { ref: { generation_file_id: fileId }, reason: null }
			: { ref: null, reason: NOT_IN_LIBRARY };
	}

	if (!isEditableKind(source.kind)) return { ref: null, reason: NOT_IN_LIBRARY };
	const resolved = await resolveEditableResource(null, source.storedPath ?? null, source.kind);
	if (resolved.itemId) return { ref: { item_id: resolved.itemId }, reason: null };
	if (resolved.generationFileId) {
		return { ref: { generation_file_id: resolved.generationFileId }, reason: null };
	}
	return { ref: null, reason: resolved.reason ?? NOT_IN_LIBRARY };
}

export async function resolveToolInputs(
	sources: readonly MediaToolSource[]
): Promise<{ refs: MediaToolInputRef[]; reason: string | null }> {
	const resolved = await Promise.all(sources.map(resolveToolInput));
	const refs: MediaToolInputRef[] = [];
	for (const entry of resolved) {
		if (!entry.ref) return { refs: [], reason: entry.reason ?? NOT_IN_LIBRARY };
		refs.push(entry.ref);
	}
	return { refs, reason: null };
}

export function canReplace(refs: readonly MediaToolInputRef[]): boolean {
	return refs.length === 1 && 'item_id' in refs[0];
}
