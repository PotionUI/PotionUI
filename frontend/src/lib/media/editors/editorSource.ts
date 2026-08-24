/**
 * Finding the library resource behind whatever a host handed the editors.
 *
 * The edit API addresses a resource by its `uploads` row id, and every edit
 * goes through it. A Library page already has that id. A MediaLoader value does
 * not: it carries a PATH, because the upload response it was built from never
 * included the row id.
 *
 * Two paths, then:
 *
 * - `uploads/<uuid>.mp4` is already a row; it just has to be found. The listing
 *   is newest-first, which is what makes that cheap in practice - a
 *   just-uploaded file and a just-picked library item are both near the front -
 *   and the page cap bounds the worst case rather than walking a whole library.
 * - `generations/<date>/<id>/0.png` is not a row at all. A generated file has
 *   no resource to edit, so one is made: the same copy-into-library the history
 *   view offers, run on the user's behalf. A copy, never a move - the
 *   generation and its files stay exactly as they were.
 */

import { api } from '$lib/services/api/index';
import { logger } from '$lib/utils/logger';
import type { EditorMediaKind } from './types';

const PAGE_SIZE = 100;
const MAX_PAGES = 10;

/**
 * The on-disk filename a stored path names, or null when the path does not
 * point into the uploads tree.
 *
 * Both conventions the field writes are accepted: the relative
 * `uploads/<name>` it stores, and the absolute `/…/uploads/<name>` an older
 * value may carry. A generation path is not an upload and answers null.
 */
export function uploadFilenameFromPath(path: string | null | undefined): string | null {
	if (!path || typeof path !== 'string') return null;
	const segments = path.split('/').filter((segment) => segment.length > 0);
	if (segments.length < 2) return null;

	const parent = segments[segments.length - 2];
	if (parent !== 'uploads') return null;

	const filename = segments[segments.length - 1];
	return filename || null;
}

/**
 * The generation a stored path belongs to, or null.
 *
 * The id is the second-to-last segment, matching the convention the field's own
 * `parseMediaLocator` reads. A path under `uploads/` or `tmp/` is not
 * generation media however many segments it has.
 */
export function generationLocatorFromPath(
	path: string | null | undefined
): { generationId: string; filename: string } | null {
	if (!path || typeof path !== 'string') return null;
	if (path.includes('/tmp/')) return null;

	const segments = path.split('/').filter((segment) => segment.length > 0);
	if (segments.length < 2) return null;

	const filename = segments[segments.length - 1];
	const generationId = segments[segments.length - 2];
	if (!filename || !generationId) return null;
	if (generationId === 'uploads' || generationId === 'tmp' || segments[0] === 'tmp') return null;

	return { generationId, filename };
}

/** The id of the listed upload stored under `filename`, or null. */
export function matchUploadId(
	uploads: readonly { id: string; filename: string }[],
	filename: string
): string | null {
	const match = uploads.find((upload) => upload.filename === filename);
	return match ? match.id : null;
}

/** The id of the listed generation file with this name, or null. */
export function matchGenerationFileId(
	media: readonly { id: string; filename: string }[],
	filename: string
): string | null {
	const match = media.find((file) => file.filename === filename);
	return match ? match.id : null;
}

/** How a source came to have a row, so the editor can say what it did. */
export type ResourceOrigin = 'given' | 'found' | 'copied';

export interface ResolvedResource {
	itemId: string | null;
	origin: ResourceOrigin | null;
	/** Why there is no resource, when there is none. */
	reason: string | null;
}

const NOT_RESOLVED =
	'This media is not in your library and could not be added, so there is nothing to edit yet.';

/**
 * The `uploads` row id for a source, making one if the source is a generated
 * file. `null` with a reason when neither is possible.
 */
export async function resolveEditableResource(
	itemId: string | null | undefined,
	storedPath: string | null | undefined,
	mediaKind: EditorMediaKind
): Promise<ResolvedResource> {
	if (itemId) return { itemId, origin: 'given', reason: null };

	const uploadFilename = uploadFilenameFromPath(storedPath);
	if (uploadFilename) {
		const found = await findUploadId(uploadFilename, mediaKind);
		return found
			? { itemId: found, origin: 'found', reason: null }
			: { itemId: null, origin: null, reason: NOT_RESOLVED };
	}

	const locator = generationLocatorFromPath(storedPath);
	if (locator) {
		const copied = await copyGeneratedFile(locator.generationId, locator.filename);
		return copied
			? { itemId: copied, origin: 'copied', reason: null }
			: { itemId: null, origin: null, reason: NOT_RESOLVED };
	}

	return { itemId: null, origin: null, reason: NOT_RESOLVED };
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

async function copyGeneratedFile(
	generationId: string,
	filename: string
): Promise<string | null> {
	try {
		const listing = await api.listGenerationMedia(generationId);
		if (!listing.success || !listing.data) return null;

		const fileId = matchGenerationFileId(listing.data.media, filename);
		if (!fileId) return null;

		const copied = await api.copyGenerationFileToLibrary(fileId);
		if (!copied.success || !copied.data) return null;
		return copied.data.item.id;
	} catch (error) {
		logger.error('Failed to copy a generated file into the library for editing:', error);
		return null;
	}
}
