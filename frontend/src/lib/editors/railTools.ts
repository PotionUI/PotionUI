import type { Readable } from 'svelte/store';
import type { MediaToolInputRef } from '$lib/media-tools/types';
import type { PendingEdit, StepDraft } from '$lib/tool-tabs/frame/editStack';
import type { RailGroup, RailTool } from '$lib/tool-tabs/frame/types';
import { guard } from './guard';
import { createEditorRegistry, entryKey } from './registry';
import type { Selection } from './overlays';
import type { ComponentRef, EditorEntryBase, EditorKind, PixelSize } from './types';

export interface ToolAvailability {
	editor: EditorKind;
	hasSource: boolean;
}

export interface RailGroupEntry extends EditorEntryBase {
	label: string;
	order: number;
	fold?: number;
}

export interface RailToolEntry extends EditorEntryBase {
	group: string;
	label: string;
	icon: string;
	order?: number;
	keys?: string;
	options?: ComponentRef;
	overlay?: string;
	bar?: ComponentRef;
	settings?: ComponentRef;
	task?: string;
	ai?: boolean;
	available?(ctx: ToolAvailability): { ok: true } | { ok: false; reason: string };
}

export interface EditorToolHost<P = unknown> {
	editor: EditorKind;
	source: { size: PixelSize | null; ref: MediaToolInputRef | null };
	pending: Readable<PendingEdit<P> | null>;
	selection: Readable<Selection | null>;
	begin(draft: StepDraft<P>): void;
	update(patch: Partial<StepDraft<P>>): void;
	apply(): void;
	discard(): void;
	live(draft: StepDraft<P>, match: (step: { id: string; tool: string; params: P }) => boolean): void;
	setApply(state: { label: string; enabled: boolean } | null): void;
	render(stepsBeneath?: number): Promise<HTMLCanvasElement>;
}

export interface EditorToolProps<P = unknown> {
	host: EditorToolHost<P>;
	phone: boolean;
}

export const railGroups = createEditorRegistry<RailGroupEntry>('railGroups');
export const railTools = createEditorRegistry<RailToolEntry>('railTools');

function byOrder<T>(order: (item: T) => number) {
	return (a: T, b: T) => order(a) - order(b);
}

export function railToolsOf(editor: EditorKind, group: string): RailToolEntry[] {
	return railTools
		.list(editor)
		.filter((tool) => tool.group === group)
		.sort(byOrder((tool) => tool.order ?? Number.MAX_SAFE_INTEGER));
}

export function railFor(editor: EditorKind, ctx: ToolAvailability, extra: Record<string, RailTool[]> = {}): RailGroup[] {
	const groups = railGroups.list(editor).sort(byOrder((group) => group.order));
	const rail: RailGroup[] = [];
	for (const group of groups) {
		const tools: RailTool[] = railToolsOf(editor, group.id).map((entry) => {
			const tool: RailTool = { id: entry.id, label: entry.label, icon: entry.icon };
			if (entry.keys) tool.keys = entry.keys;
			if (entry.ai) tool.ai = true;
			if (!entry.available) return tool;
			const verdict = entry.pluginId
				? guard(entryKey(editor, entry.id), 'available', () => entry.available!(ctx), { ok: true as const })
				: entry.available(ctx);
			return verdict.ok ? tool : { ...tool, disabled: true, reason: verdict.reason };
		});
		tools.push(...(extra[group.id] ?? []));
		if (!tools.length) continue;
		const row: RailGroup = { id: group.id, label: group.label, tools };
		if (group.fold !== undefined) row.fold = group.fold;
		rail.push(row);
	}
	return rail;
}
