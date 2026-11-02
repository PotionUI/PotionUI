import { createStack, deserialize, serialize, type EditStackState, type SerializedStack } from '$lib/tool-tabs/frame/editStack';
import type { MediaRef } from '$lib/types/tabs';
import type { MediaToolInputRef, MediaToolRun } from '$lib/media-tools/types';
import { isRailToolId, type RailToolId, type VideoParams } from './videoSteps';

export const VIDEO_STATE_VERSION = 1;

export interface SavedCopy {
	itemId: string;
	url: string | null;
	name: string | null;
	path: string | null;
}

export interface VideoEditorState {
	v: number;
	stack: SerializedStack;
	tool: RailToolId | null;
	playhead: number;
	loop: boolean;
	savedSignature: string;
	saved: SavedCopy | null;
	renderSignature: string | null;
	renderMode: string | null;
	renderStartedAt: number | null;
}

export interface LoadedVideoState {
	stack: EditStackState<VideoParams>;
	tool: RailToolId | null;
	playhead: number;
	loop: boolean;
	savedSignature: string;
	saved: SavedCopy | null;
	renderSignature: string | null;
	renderMode: string | null;
	renderStartedAt: number | null;
}

export const EMPTY_SIGNATURE = '[]';

function isRecord(value: unknown): value is Record<string, unknown> {
	return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function finite(value: unknown, fallback: number): number {
	return typeof value === 'number' && Number.isFinite(value) ? value : fallback;
}

function str(value: unknown): string | null {
	return typeof value === 'string' && value ? value : null;
}

export function readVideoState(raw: unknown): LoadedVideoState {
	const empty: LoadedVideoState = {
		stack: createStack<VideoParams>(),
		tool: null,
		playhead: 0,
		loop: true,
		savedSignature: EMPTY_SIGNATURE,
		saved: null,
		renderSignature: null,
		renderMode: null,
		renderStartedAt: null
	};
	if (!isRecord(raw) || raw.v !== VIDEO_STATE_VERSION) return empty;
	const saved = isRecord(raw.saved) && str(raw.saved.itemId)
		? { itemId: str(raw.saved.itemId)!, url: str(raw.saved.url), name: str(raw.saved.name), path: str(raw.saved.path) }
		: null;
	return {
		stack: deserialize<VideoParams>(raw.stack),
		tool: isRailToolId(raw.tool) ? raw.tool : null,
		playhead: Math.max(0, finite(raw.playhead, 0)),
		loop: raw.loop !== false,
		savedSignature: str(raw.savedSignature) ?? EMPTY_SIGNATURE,
		saved,
		renderSignature: str(raw.renderSignature),
		renderMode: str(raw.renderMode),
		renderStartedAt: typeof raw.renderStartedAt === 'number' ? raw.renderStartedAt : null
	};
}

export function writeVideoState(state: LoadedVideoState): VideoEditorState {
	return {
		v: VIDEO_STATE_VERSION,
		stack: serialize(state.stack),
		tool: state.tool,
		playhead: Math.round(state.playhead * 1000) / 1000,
		loop: state.loop,
		savedSignature: state.savedSignature,
		saved: state.saved,
		renderSignature: state.renderSignature,
		renderMode: state.renderMode,
		renderStartedAt: state.renderStartedAt
	};
}

export function dirtyCount(signature: string, savedSignature: string, visibleSteps: number): number {
	if (signature === savedSignature) return 0;
	return Math.max(1, visibleSteps);
}

export type RefWithIds = MediaRef & { item_id?: string; generation_file_id?: string; itemId?: string; generationFileId?: string };

export function directInputRef(ref: MediaRef | null): MediaToolInputRef | null {
	if (!ref) return null;
	const raw = ref as RefWithIds;
	const itemId = raw.item_id ?? raw.itemId;
	if (typeof itemId === 'string' && itemId) return { item_id: itemId };
	const fileId = raw.generation_file_id ?? raw.generationFileId;
	if (typeof fileId === 'string' && fileId) return { generation_file_id: fileId };
	return null;
}

const PAD = (value: number) => String(value).padStart(2, '0');

export function formatEditorTime(seconds: number): string {
	const safe = Number.isFinite(seconds) && seconds > 0 ? seconds : 0;
	const centis = Math.floor(safe * 100 + 1e-6);
	const minutes = Math.floor(centis / 6000);
	const secs = Math.floor((centis % 6000) / 100);
	return `${PAD(minutes)}:${PAD(secs)}.${PAD(centis % 100)}`;
}

export function parseEditorTime(text: string): number | null {
	const trimmed = text.trim();
	if (!trimmed) return null;
	const parts = trimmed.split(':');
	if (parts.length > 2) return null;
	const secs = Number(parts[parts.length - 1]);
	const minutes = parts.length === 2 ? Number(parts[0]) : 0;
	if (!Number.isFinite(secs) || !Number.isFinite(minutes) || secs < 0 || minutes < 0) return null;
	return minutes * 60 + secs;
}

export function settleState(state: LoadedVideoState, run: MediaToolRun): LoadedVideoState {
	const base: LoadedVideoState = { ...state, renderSignature: null, renderMode: null, renderStartedAt: null };
	if (run.status !== 'completed') return base;
	const items = run.items ?? [];
	const first = items.length === 1 ? items[0] : null;
	let saved: SavedCopy | null = null;
	if (state.renderMode === 'replace') saved = state.saved;
	else if (first) {
		saved = {
			itemId: first.id,
			url: first.url ?? null,
			name: first.original_filename ?? first.filename ?? null,
			path: first.filename ? `uploads/${first.filename}` : null
		};
	}
	return { ...base, savedSignature: state.renderSignature ?? state.savedSignature, saved };
}
