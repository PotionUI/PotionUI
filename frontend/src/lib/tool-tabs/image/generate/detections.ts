import type { NormRect } from '$lib/services/api/editorTasks';

export const FIX_FACES_TASK = 'fix_faces';

export type DetectionKind = 'face' | 'hand';

export interface DetectionItem {
	id: string;
	kind: DetectionKind;
	box: NormRect;
	score: number;
	on: boolean;
}

export type DetectionStatus = 'idle' | 'loading' | 'ready' | 'unavailable' | 'failed';

export interface DetectionState {
	status: DetectionStatus;
	picture: { width: number; height: number } | null;
	items: DetectionItem[];
	hover: string | null;
	error: string | null;
}

export interface StrengthBounds {
	default: number;
	min: number;
	max: number;
}

const detecting = new Set<string>([FIX_FACES_TASK]);

export function isDetectingTask(taskId: string | null | undefined): boolean {
	return !!taskId && detecting.has(taskId);
}

export function markDetectingTasks(ids: Iterable<string>): void {
	for (const id of ids) detecting.add(id);
}

export function idleDetections(): DetectionState {
	return { status: 'idle', picture: null, items: [], hover: null, error: null };
}

export function tickedBoxes(state: DetectionState | undefined): NormRect[] {
	return state && state.status === 'ready' ? state.items.filter((item) => item.on).map((item) => item.box) : [];
}

export function withTicked(state: DetectionState, id: string, on: boolean): DetectionState {
	return { ...state, items: state.items.map((item) => (item.id === id ? { ...item, on } : item)) };
}

export function withAllTicked(state: DetectionState, on: boolean): DetectionState {
	return { ...state, items: state.items.map((item) => ({ ...item, on })) };
}

export function kindLabel(kind: DetectionKind): string {
	return kind === 'face' ? 'Face' : 'Hand';
}

export function scoreText(score: number): string {
	return `${Math.round(Math.max(0, Math.min(1, score)) * 100)}%`;
}

export function clampStrength(value: number, bounds: StrengthBounds): number {
	if (!Number.isFinite(value)) return bounds.default;
	return Math.min(bounds.max, Math.max(bounds.min, value));
}

export function strengthText(value: number): string {
	return (Math.round(value * 100) / 100).toFixed(2);
}

export function boxPixels(box: NormRect, size: { width: number; height: number }): { x: number; y: number; width: number; height: number } {
	const x = Math.max(0, Math.min(size.width - 1, Math.round(box.x * size.width)));
	const y = Math.max(0, Math.min(size.height - 1, Math.round(box.y * size.height)));
	return {
		x,
		y,
		width: Math.max(1, Math.min(size.width - x, Math.round(box.width * size.width))),
		height: Math.max(1, Math.min(size.height - y, Math.round(box.height * size.height)))
	};
}

export function paintBoxes(context: { fillStyle: unknown; fillRect(x: number, y: number, width: number, height: number): void }, boxes: readonly NormRect[], size: { width: number; height: number }): void {
	context.fillStyle = 'white';
	for (const box of boxes) {
		const pixels = boxPixels(box, size);
		context.fillRect(pixels.x, pixels.y, pixels.width, pixels.height);
	}
}
