import type { APIResponse } from '$lib/types/api';
import type { EditClip, EditSource, EditStateDoc } from '$lib/services/api/edits';
import type { LibraryItem } from '$lib/services/api/library';
import type { MediaRef } from '$lib/types/tabs';
import { readVideoState, writeVideoState, type LoadedVideoState } from './videoState';

export interface ClipLibraryPort {
	getLibraryItem(id: string): Promise<APIResponse<LibraryItem>>;
}

function positive(value: unknown): number | null {
	return typeof value === 'number' && Number.isFinite(value) && value > 0 ? value : null;
}

export function clipFacts(duration: unknown, width: unknown, height: unknown): EditClip | null {
	const clip: EditClip = {};
	const length = positive(duration);
	const wide = positive(width);
	const high = positive(height);
	if (length !== null) clip.duration_seconds = Math.round(length * 1000) / 1000;
	if (wide !== null && high !== null) {
		clip.width = Math.round(wide);
		clip.height = Math.round(high);
	}
	return Object.keys(clip).length ? clip : null;
}

export function videoEditDoc(state: LoadedVideoState, clip: EditClip | null): EditStateDoc {
	const doc: EditStateDoc = {
		...writeVideoState({ ...state, renderSignature: null, renderMode: null, renderStartedAt: null })
	};
	if (clip) doc.clip = clip;
	return doc;
}

export function videoContentKey(raw: EditStateDoc): string {
	const normal = writeVideoState(readVideoState(raw));
	return JSON.stringify([normal.stack, normal.saved, normal.savedSignature]);
}

export function clipAspect(clip: EditClip | null | undefined): number | null {
	const width = positive(clip?.width);
	const height = positive(clip?.height);
	return width !== null && height !== null ? width / height : null;
}

export function formatClipDuration(seconds: number | null | undefined): string {
	const total = positive(seconds);
	if (total === null) return '';
	const whole = Math.max(1, Math.round(total));
	const hours = Math.floor(whole / 3600);
	const minutes = Math.floor((whole % 3600) / 60);
	const secs = String(whole % 60).padStart(2, '0');
	return hours > 0 ? `${hours}:${String(minutes).padStart(2, '0')}:${secs}` : `${minutes}:${secs}`;
}

export async function resolveClipSourceRef(source: EditSource, library: ClipLibraryPort): Promise<MediaRef | null> {
	const name = (source.name ?? '').trim();
	if (source.kind === 'history') {
		const url = `/api/media/files/${encodeURIComponent(source.id)}`;
		return { path: url, relative_path: url, url, name: name || 'clip', type: 'video', generation_file_id: source.id };
	}
	try {
		const item = (await library.getLibraryItem(source.id)).data;
		if (!item) return null;
		const path = `uploads/${item.filename}`;
		const ref: MediaRef & { metadata?: Record<string, number> } = {
			path,
			relative_path: path,
			url: item.url,
			name: (item.original_filename ?? '').trim() || item.filename,
			type: 'video',
			item_id: item.id
		};
		const metadata: Record<string, number> = {};
		if (positive(item.width) && positive(item.height)) {
			metadata.width = item.width as number;
			metadata.height = item.height as number;
		}
		const length = positive(item.duration_seconds);
		if (length !== null) metadata.duration_seconds = length;
		const rate = positive(item.fps);
		if (rate !== null) metadata.fps = rate;
		if (Object.keys(metadata).length) ref.metadata = metadata;
		return ref;
	} catch {
		return null;
	}
}
