import { locateMediaPath } from '$lib/components/form-fields/mediaLoaderPreview';
import { api } from '$lib/services/api/index';
import { logger } from '$lib/utils/logger';
import type { EditorMediaKind } from './editors/types';

const PAGE_SIZE = 100;
const MAX_PAGES = 10;

export function uploadFilenameFromPath(path: string | null | undefined): string | null {
	if (!path || typeof path !== 'string') return null;
	const segments = path.replace(/\\/g, '/').split('/').filter((segment) => segment.length > 0);
	if (segments.length < 2) return null;

	const parent = segments[segments.length - 2];
	if (parent !== 'uploads') return null;

	const filename = segments[segments.length - 1];
	return filename || null;
}

export function generationLocatorFromPath(
	path: string | null | undefined
): { generationId: string; filename: string } | null {
	if (!path || typeof path !== 'string') return null;
	const location = locateMediaPath(path);
	if (location?.kind !== 'generation') return null;
	return { generationId: location.generationId, filename: location.filename };
}

export function matchUploadId(
	uploads: readonly { id: string; filename: string }[],
	filename: string
): string | null {
	const match = uploads.find((upload) => upload.filename === filename);
	return match ? match.id : null;
}

export function matchGenerationFileId(
	media: readonly { id: string; filename: string }[],
	filename: string
): string | null {
	const match = media.find((file) => file.filename === filename);
	return match ? match.id : null;
}

export type ResourceOrigin = 'given' | 'found' | 'pending';

export interface ResolvedResource {
	itemId: string | null;
	generationFileId: string | null;
	origin: ResourceOrigin | null;
	reason: string | null;
}

const NOT_RESOLVED =
	'This media is not in your library and could not be added, so there is nothing to edit yet.';

export async function resolveEditableResource(
	itemId: string | null | undefined,
	storedPath: string | null | undefined,
	mediaKind: EditorMediaKind,
	generationFileId?: string | null
): Promise<ResolvedResource> {
	if (itemId) return { itemId, generationFileId: null, origin: 'given', reason: null };
	if (generationFileId) return { itemId: null, generationFileId, origin: 'pending', reason: null };

	const uploadFilename = uploadFilenameFromPath(storedPath);
	if (uploadFilename) {
		const found = await findUploadId(uploadFilename, mediaKind);
		return found
			? { itemId: found, generationFileId: null, origin: 'found', reason: null }
			: unresolved();
	}

	const locator = generationLocatorFromPath(storedPath);
	if (locator) {
		const fileId = await findGeneratedFileId(locator.generationId, locator.filename);
		return fileId
			? { itemId: null, generationFileId: fileId, origin: 'pending', reason: null }
			: unresolved();
	}

	return unresolved();
}

function unresolved(): ResolvedResource {
	return { itemId: null, generationFileId: null, origin: null, reason: NOT_RESOLVED };
}

async function findUploadId(filename: string, mediaKind: EditorMediaKind): Promise<string | null> {
	for (let page = 0; page < MAX_PAGES; page += 1) {
		try {
			const response = await api.listUploads({
				mediaType: mediaKind,
				limit: PAGE_SIZE,
				offset: page * PAGE_SIZE
			});
			if (!response.success || !response.data) return null;

			const found = matchUploadId(response.data.uploads, filename);
			if (found) return found;

			if (response.data.uploads.length < PAGE_SIZE) return null;
		} catch (error) {
			logger.error('Failed to resolve the library row for an upload:', error);
			return null;
		}
	}
	return null;
}

async function findGeneratedFileId(
	generationId: string,
	filename: string
): Promise<string | null> {
	try {
		const listing = await api.listGenerationMedia(generationId);
		if (!listing.success || !listing.data) return null;
		return matchGenerationFileId(listing.data.media, filename);
	} catch (error) {
		logger.error('Failed to look up a generated file for editing:', error);
		return null;
	}
}
