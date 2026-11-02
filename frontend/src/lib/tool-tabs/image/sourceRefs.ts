import type { MediaToolArtifact } from '$lib/media-tools/types';
import type { LibraryItem } from '$lib/services/api/library';
import type { GenerationFile, GenerationHistoryItem } from '$lib/types/history';
import type { MediaRef } from '$lib/types/tabs';

function sized<T extends MediaRef>(ref: T, width?: number | null, height?: number | null): T {
	if (typeof width === 'number' && width > 0 && typeof height === 'number' && height > 0) return { ...ref, width, height };
	return ref;
}

export function historyFileRef(file: Pick<GenerationFile, 'id' | 'file_path' | 'width' | 'height'>): MediaRef {
	const segments = file.file_path.split('/').filter(Boolean);
	const filename = segments[segments.length - 1] ?? 'image.png';
	const generationId = segments[segments.length - 2] ?? '';
	return sized(
		{
			path: file.file_path,
			relative_path: file.file_path,
			url: `/api/media/generations/${generationId}/${filename}`,
			name: filename,
			type: 'image',
			generation_file_id: String(file.id)
		},
		file.width,
		file.height
	);
}

export function libraryItemRef(item: Pick<LibraryItem, 'id' | 'filename' | 'original_filename' | 'url' | 'width' | 'height'>): MediaRef {
	const path = `uploads/${item.filename}`;
	return sized(
		{
			path,
			relative_path: path,
			url: item.url,
			name: (item.original_filename ?? '').trim() || item.filename,
			type: 'image',
			item_id: item.id
		},
		item.width,
		item.height
	);
}

export function artifactRef(artifact: Pick<MediaToolArtifact, 'id' | 'url' | 'width' | 'height'>, name: string): MediaRef {
	return sized({ path: artifact.url, url: artifact.url, name, type: 'image', artifact_id: artifact.id }, artifact.width, artifact.height);
}

export function isImageFile(file: Pick<GenerationFile, 'file_type' | 'file_path'>): boolean {
	if (file.file_type === 'image') return true;
	return /\.(png|jpe?g|webp|gif|bmp|avif)$/i.test(file.file_path);
}

export function recentHistoryPictures(generations: readonly GenerationHistoryItem[], limit = 6): MediaRef[] {
	const refs: MediaRef[] = [];
	for (const generation of generations) {
		for (const file of generation.files ?? []) {
			if (!file.is_final || !isImageFile(file)) continue;
			refs.push(historyFileRef(file));
			if (refs.length >= limit) return refs;
		}
	}
	return refs;
}

export function pastedImage(items: DataTransferItemList | null | undefined): File | null {
	if (!items) return null;
	for (const item of Array.from(items)) {
		if (item.kind === 'file' && item.type.startsWith('image/')) return item.getAsFile();
	}
	return null;
}
