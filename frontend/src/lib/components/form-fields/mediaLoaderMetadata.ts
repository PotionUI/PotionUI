import { locateMediaPath } from './mediaLoaderPreview';
import type { MediaItemMetadata } from './mediaLoaderMeta';

interface MetadataRow {
	width?: number | null;
	height?: number | null;
	duration_seconds?: number | null;
	fps?: number | null;
	size?: number | null;
}

interface MetadataResponse<T> {
	success: boolean;
	data?: T;
}

export interface MetadataSource {
	listGenerationMedia(
		generationId: string
	): Promise<MetadataResponse<{ media: Array<MetadataRow & { filename: string }> }>>;
	getUploadInfo(filename: string): Promise<MetadataResponse<MetadataRow>>;
}

function pickMetadata(row: MetadataRow): MediaItemMetadata {
	return {
		width: row.width,
		height: row.height,
		duration_seconds: row.duration_seconds,
		fps: row.fps,
		size: row.size
	};
}

export async function fetchMediaMetadata(
	rawPath: string,
	source: MetadataSource
): Promise<MediaItemMetadata | null> {
	const location = locateMediaPath(rawPath);
	if (!location || location.kind === 'tmp') return null;

	if (location.kind === 'generation') {
		const response = await source.listGenerationMedia(location.generationId);
		const match =
			response.success && response.data
				? response.data.media.find((media) => media.filename === location.filename)
				: undefined;
		return match ? pickMetadata(match) : null;
	}

	const response = await source.getUploadInfo(location.filename);
	return response.success && response.data ? pickMetadata(response.data) : null;
}
