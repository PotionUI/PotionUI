/**
 * One place that knows how to read a `file_type`.
 *
 * The same media file arrives with two different casings depending on where it
 * came from: the `files` DB row is serialized verbatim by the history/detail
 * API ('IMAGE'/'VIDEO'/'AUDIO'/'MESH'), while the generation WebSocket envelope
 * types everything lowercase. Every comparison against a literal therefore has
 * to normalize first - doing it at each call site is exactly how history video
 * and audio stopped rendering in the workbench.
 */

export type MediaFileType = 'image' | 'video' | 'audio' | 'mesh';

/** Lowercased, trimmed `file_type`, or '' when there isn't one. */
export function normalizeFileType(value: unknown): string {
	return typeof value === 'string' ? value.trim().toLowerCase() : '';
}

/** True when `value` names `kind`, whatever casing it arrived in. */
export function isFileType(value: unknown, kind: MediaFileType): boolean {
	return normalizeFileType(value) === kind;
}

export function isImageFileType(value: unknown): boolean {
	return isFileType(value, 'image');
}

export function isVideoFileType(value: unknown): boolean {
	return isFileType(value, 'video');
}

export function isAudioFileType(value: unknown): boolean {
	return isFileType(value, 'audio');
}

export function isMeshFileType(value: unknown): boolean {
	return isFileType(value, 'mesh');
}
