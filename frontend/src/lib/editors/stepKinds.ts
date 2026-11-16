import { get } from 'svelte/store';
import type { MediaToolStep, ToolDTO } from '$lib/media-tools/types';
import type { EditStep } from '$lib/tool-tabs/frame/editStack';
import { guard, guardAsync } from './guard';
import { createEditorRegistry, entryKey } from './registry';
import { editorExtensionsReady } from './ready';
import type { ComponentRef, EditorEntryBase, EditorKind, PixelSize } from './types';

export interface StepContext<P = unknown> {
	editor: EditorKind;
	input: PixelSize | null;
	beneath: readonly EditStep[];
	catalog: readonly ToolDTO[];
	step: EditStep<P>;
	baked?: string | null;
	maxSide?: number;
}

export interface PreviewFrame {
	canvas: HTMLCanvasElement;
	size: PixelSize;
}

export type StepState =
	| { kind: 'done' }
	| { kind: 'busy'; label: string; progress?: number }
	| { kind: 'skipped'; tone: 'muted' | 'warning' | 'danger'; label: string; detail?: string; action?: { id: string; label: string } };

export interface RowAction {
	id: string;
	label: string;
	icon: string;
}

export interface RowExtras {
	actions?: RowAction[];
	hideToggle?: boolean;
	removable?: boolean;
	thumb?: string | null;
}

export interface StepKind<P = unknown> extends EditorEntryBase {
	label: string;
	icon: string;
	lane?: string;
	version?: number;
	migrate?(params: unknown, from: number): P;
	summary(params: P, ctx: StepContext<P>): string;
	outputSize?(params: P, ctx: StepContext<P>): PixelSize | null;
	preview?(frame: PreviewFrame, params: P, ctx: StepContext<P>, signal: AbortSignal): Promise<PreviewFrame>;
	payload(params: P, ctx: StepContext<P>): MediaToolStep[];
	state?(step: EditStep<P>, ctx: StepContext<P>): StepState;
	shapeBasis?(params: P): string | null;
	onAction?(step: EditStep<P>, action: string, ctx: StepContext<P>): void;
	row?: ComponentRef;
	extras?(step: EditStep<P>, state: StepState): RowExtras;
	reopenTool?: string | ((params: P) => string | null);
}

export function reopenToolOf(kind: StepKind<any>, params: unknown): string | null {
	const wanted = kind.reopenTool;
	if (typeof wanted === 'function') return callKind(kind, 'reopenTool', () => wanted(params), null);
	return wanted ?? null;
}

export const stepKinds = createEditorRegistry<StepKind<any>>('stepKinds');

export type StepResolution =
	| { status: 'kind'; kind: StepKind<any>; params: unknown }
	| { status: 'loading' }
	| { status: 'unavailable'; reason: 'missing' | 'newer' };

export function paramsVersion(params: unknown): number {
	const version = (params as { v?: unknown } | null)?.v;
	return typeof version === 'number' && Number.isFinite(version) ? version : 1;
}

export function stepKindKey(kind: Pick<StepKind, 'editor' | 'id'>): string {
	return entryKey(kind.editor, kind.id);
}

export function callKind<T>(kind: StepKind<any>, what: string, run: () => T, fallback: T): T {
	return kind.pluginId ? guard(stepKindKey(kind), what, run, fallback) : run();
}

export async function callKindAsync<T>(kind: StepKind<any>, what: string, run: () => Promise<T>, fallback: T): Promise<T> {
	return kind.pluginId ? guardAsync(stepKindKey(kind), what, run, fallback) : run();
}

export function resolveStepKind(editor: EditorKind, step: Pick<EditStep, 'tool' | 'params'>, pending = false): StepResolution {
	const kind = stepKinds.get(editor, step.tool);
	if (!kind) return !get(editorExtensionsReady) || pending ? { status: 'loading' } : { status: 'unavailable', reason: 'missing' };
	const from = paramsVersion(step.params);
	const known = kind.version ?? 1;
	if (from > known) return { status: 'unavailable', reason: 'newer' };
	if (from < known && kind.migrate) {
		const migrated = callKind(kind, 'migrate', () => kind.migrate!(step.params, from), step.params);
		return { status: 'kind', kind, params: migrated };
	}
	return { status: 'kind', kind, params: step.params };
}

export const UNAVAILABLE_LABEL = 'Not available';
export const LOADING_LABEL = 'Loading';

export function unavailableState(resolution: Exclude<StepResolution, { status: 'kind' }>): StepState {
	if (resolution.status === 'loading') return { kind: 'busy', label: LOADING_LABEL };
	return { kind: 'skipped', tone: 'muted', label: resolution.reason === 'newer' ? 'Made with a newer version' : UNAVAILABLE_LABEL };
}

export interface StepRowInfo extends RowExtras {
	state: StepState;
	locked: boolean;
	entry?: string;
	row?: ComponentRef;
}
