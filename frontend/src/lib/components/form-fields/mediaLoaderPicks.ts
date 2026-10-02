import type { UploadFileInfo } from '$lib/services/api/media';
import type { MediaCandidate } from './mediaLoaderAcceptance';
import { kindFromDeclared, kindFromFilename } from './mediaLoaderKind';
import type { MediaKind } from './mediaLoaderConfig';
import type { MediaItemMetadata } from './mediaLoaderMeta';

export interface GenerationFileLike {
	file_path: string;
	file_type?: unknown;
	width?: number | null;
	height?: number | null;
	duration_seconds?: number | null;
	fps?: number | null;
	file_size?: number | null;
}

export interface MediaPick {
	kind: MediaKind | null;
	item: Record<string, unknown>;
	candidate: MediaCandidate;
}

const FALLBACK_EXTENSION: Record<MediaKind, string> = { image: 'png', video: 'mp4', audio: 'mp3' };

export function pickFromGeneration(file: GenerationFileLike): MediaPick {
	const segments = file.file_path.split('/').filter((segment) => segment);
	const filename = segments[segments.length - 1];
	const generationId = segments[segments.length - 2];
	const kind = kindFromDeclared(file.file_type) ?? kindFromFilename(filename);
	const metadata: MediaItemMetadata = {
		width: file.width,
		height: file.height,
		duration_seconds: file.duration_seconds,
		fps: file.fps,
		size: file.file_size
	};
	const resolvedName = filename || `media.${FALLBACK_EXTENSION[kind ?? 'image']}`;

	return {
		kind,
		item: {
			path: file.file_path,
			relative_path: file.file_path,
			url: `/api/media/generations/${generationId}/${filename}`,
			name: resolvedName,
			type: kind,
			metadata
		},
		candidate: {
			name: filename || 'file',
			kind,
			mimeType: null,
			sizeBytes: file.file_size ?? null,
			width: file.width ?? null,
			height: file.height ?? null,
			durationSeconds: file.duration_seconds ?? null
		}
	};
}

export function pickFromUpload(upload: UploadFileInfo): MediaPick {
	const kind = kindFromDeclared(upload.media_type) ?? kindFromFilename(upload.filename);
	const displayName = (upload.original_filename ?? '').trim();
	const resolvedName = displayName || 'Upload';
	const relativePath = `uploads/${upload.filename}`;

	return {
		kind,
		item: {
			path: relativePath,
			relative_path: relativePath,
			url: upload.url,
			name: resolvedName,
			type: kind,
			metadata: {
				width: upload.width,
				height: upload.height,
				duration_seconds: upload.duration_seconds,
				fps: upload.fps,
				size: upload.size
			},
			...(displayName ? { label: displayName } : {})
		},
		candidate: {
			name: resolvedName,
			kind,
			mimeType: null,
			sizeBytes: upload.size ?? null,
			width: upload.width ?? null,
			height: upload.height ?? null,
			durationSeconds: upload.duration_seconds ?? null
		}
	};
}
