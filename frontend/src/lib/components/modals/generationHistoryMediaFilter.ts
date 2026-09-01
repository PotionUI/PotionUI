export type GenerationHistoryMediaType = 'image' | 'video' | 'audio' | 'mesh';

interface FileTypeLike {
	file_type?: string | null;
}

function isFileType(file: FileTypeLike, type: 'image' | 'video' | 'audio' | 'mesh'): boolean {
	return file.file_type?.toLowerCase().startsWith(type) ?? false;
}

/**
 * `GenerationHistoryModal`'s per-generation file filter. 'image'/'video'/'audio'/'mesh'
 * are supported end to end (`GenerationCard` renders all four) - an
 * unrecognized `mediaType` drops every file rather than silently falling
 * into the video bucket.
 */
export function filterFilesByMediaType<T extends FileTypeLike>(
	files: T[] | null | undefined,
	mediaType: GenerationHistoryMediaType | undefined
): T[] {
	if (!files) return [];
	if (!mediaType) return files;
	if (mediaType === 'image' || mediaType === 'video' || mediaType === 'audio' || mediaType === 'mesh') {
		return files.filter((file) => isFileType(file, mediaType));
	}
	return [];
}
