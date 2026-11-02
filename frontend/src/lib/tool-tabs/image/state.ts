import { createStack, deserialize, serialize, type EditStackState } from '$lib/tool-tabs/frame/editStack';
import { fitViewport, type Viewport } from '$lib/tool-tabs/frame/viewport';
import { readMask, type MaskChannel } from './mask';

export const IMAGE_STATE_VERSION = 1;

export interface SavedCopy {
	itemId: string;
	signature: string;
}

export interface ImageTabState {
	stack: EditStackState<unknown>;
	activeTool: string | null;
	viewport: Viewport;
	mask: MaskChannel | null;
	saved: SavedCopy | null;
}

export function emptyImageState(activeTool: string | null = null): ImageTabState {
	return { stack: createStack(), activeTool, viewport: fitViewport(), mask: null, saved: null };
}

function isRecord(value: unknown): value is Record<string, unknown> {
	return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function readViewport(raw: unknown): Viewport {
	if (!isRecord(raw)) return fitViewport();
	const { fit, zoom, panX, panY } = raw;
	if (fit === true || typeof zoom !== 'number' || typeof panX !== 'number' || typeof panY !== 'number') return fitViewport();
	if (![zoom, panX, panY].every(Number.isFinite) || zoom <= 0) return fitViewport();
	return { fit: false, zoom, panX, panY };
}

function readSaved(raw: unknown): SavedCopy | null {
	if (!isRecord(raw) || typeof raw.itemId !== 'string' || !raw.itemId) return null;
	return { itemId: raw.itemId, signature: typeof raw.signature === 'string' ? raw.signature : '' };
}

export function readImageState(raw: unknown, activeTool: string | null = null): ImageTabState {
	if (!isRecord(raw) || raw.v !== IMAGE_STATE_VERSION) return emptyImageState(activeTool);
	return {
		stack: deserialize(raw.stack),
		activeTool: activeTool ?? (typeof raw.activeTool === 'string' ? raw.activeTool : null),
		viewport: readViewport(raw.viewport),
		mask: readMask(raw.mask),
		saved: readSaved(raw.saved)
	};
}

export function writeImageState(state: ImageTabState): Record<string, unknown> {
	return {
		v: IMAGE_STATE_VERSION,
		stack: serialize(state.stack),
		activeTool: state.activeTool,
		viewport: { ...state.viewport },
		mask: state.mask ? { ...state.mask } : null,
		saved: state.saved ? { ...state.saved } : null
	};
}

export function stackSignature(stack: EditStackState<unknown>): string {
	return JSON.stringify(stack.steps.slice(0, stack.cursor).map((step) => [step.tool, step.params, step.hidden]));
}

export function unsavedEdits(stack: EditStackState<unknown>, saved: SavedCopy | null): number {
	const applied = stack.cursor + (stack.pending ? 1 : 0);
	if (saved && !stack.pending && saved.signature === stackSignature(stack)) return 0;
	return applied;
}
