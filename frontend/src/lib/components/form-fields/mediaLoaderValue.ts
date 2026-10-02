import { kindFromDeclared, kindFromFilename, kindOfMediaItem } from './mediaLoaderKind';
import type { MediaKind } from './mediaLoaderConfig';
import { mediaPathPreviewUrl } from './mediaLoaderPreview';
import type { MediaItemMetadata } from './mediaLoaderMeta';

export interface SingleValue {
	previewUrl: string | null;
	fileName: string | null;
	fileType: MediaKind | null;
	metadata: MediaItemMetadata | null;
}

export function readSingleValue(value: unknown): SingleValue | null {
	if (value && typeof value === 'object') {
		const record = value as Record<string, any>;
		return {
			previewUrl: record.url || null,
			fileName: record.name || null,
			fileType: kindFromDeclared(record.type) ?? kindOfMediaItem(record),
			metadata: record.metadata || null
		};
	}
	if (value && typeof value === 'string') {
		const filename = value.split('/').pop() || '';
		return {
			previewUrl: mediaPathPreviewUrl(value),
			fileName: filename,
			fileType: kindFromFilename(filename),
			metadata: null
		};
	}
	return null;
}
