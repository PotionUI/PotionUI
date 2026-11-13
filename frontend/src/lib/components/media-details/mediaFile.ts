import type { AudioTrack, AudioTrackType } from '$lib/types/audio';
import { resolveMeshFormat } from '$lib/components/workbench/renderers/meshUrl';

export interface StageFile {
	file_path: string;
	file_type?: string;
	width?: number | null;
	height?: number | null;
	file_size?: number | null;
	track_type?: string | null;
	duration_seconds?: number | null;
	sample_rate?: number | null;
	channels?: number | null;
}

export function isVideoFile(file: unknown): boolean {
	return (file as StageFile | undefined)?.file_type?.toLowerCase?.() === 'video';
}

export function isMeshFile(file: unknown): boolean {
	return (file as StageFile | undefined)?.file_type?.toLowerCase?.() === 'mesh';
}

export function isAudioFile(file: unknown): boolean {
	return (file as StageFile | undefined)?.file_type?.toLowerCase?.() === 'audio';
}

export function resolveAudioFormat(file: unknown): string {
	const path = (file as StageFile | undefined)?.file_path;
	const basename = typeof path === 'string' ? path.split('/').pop() : null;
	const ext = basename && basename.includes('.') ? basename.split('.').pop() : null;
	return (ext || 'wav').toLowerCase();
}

export function audioTracksFor(file: StageFile | null | undefined, url: string): AudioTrack[] {
	if (!file) return [];
	return [
		{
			type: (file.track_type as AudioTrackType) || 'mixed',
			url,
			originalUrl: url,
			duration: file.duration_seconds ?? undefined,
			sample_rate: file.sample_rate ?? undefined,
			channels: file.channels ?? undefined,
			format: resolveAudioFormat(file)
		}
	];
}

export function stageFormatLabel(file: StageFile): string {
	if (isVideoFile(file)) return 'MP4';
	if (isMeshFile(file)) return resolveMeshFormat(file as never).toUpperCase();
	if (isAudioFile(file)) return resolveAudioFormat(file).toUpperCase();
	return 'PNG';
}
