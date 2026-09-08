/**
 * Pure grouping/labeling logic for ChatMemoryPanel.svelte, extracted so the
 * panel's scope layout is unit-testable without a component-rendering
 * harness (this project has none).
 */
import type { MemoryNote, MemoryScope } from '$lib/types/chat';

export interface MemoryGroupSpec {
	scope: MemoryScope;
	ref: string | null;
	available: boolean;
}

export interface MemoryGroupContext {
	presetId: string | null;
	modelId: string | null;
	modeId: string | null;
}

/**
 * The panel's scope groups, in display order: global, this mode, preset,
 * model. `mode` is available whenever the chat session has resolved a mode
 * id — which is always, once the session exists (see chatSession.ts's
 * DEFAULT_CHAT_MODE) — the same way `global` is always available.
 */
export function buildMemoryGroups(ctx: MemoryGroupContext): MemoryGroupSpec[] {
	return [
		{ scope: 'global', ref: null, available: true },
		{ scope: 'mode', ref: ctx.modeId, available: !!ctx.modeId },
		{ scope: 'preset', ref: ctx.presetId, available: !!ctx.presetId },
		{ scope: 'model', ref: ctx.modelId, available: !!ctx.modelId }
	];
}

/** Notes belonging to one group: same scope, and (for a scoped group) the same scope_ref. */
export function notesForGroup(notes: MemoryNote[], group: MemoryGroupSpec): MemoryNote[] {
	return notes.filter(
		(n) => n.scope === group.scope && (group.scope === 'global' || n.scope_ref === group.ref)
	);
}

export interface MemoryGroupLabels {
	presetName: string | null;
	modelName: string | null;
	modeLabel: string | null;
}

/** The group header text, e.g. "Global · 3 notes", "Mode · lora-dataset", "Preset". */
export function memoryGroupTitle(group: MemoryGroupSpec, count: number, labels: MemoryGroupLabels): string {
	if (group.scope === 'global') return `Global · ${count} note${count === 1 ? '' : 's'}`;
	if (group.scope === 'mode') return labels.modeLabel ? `Mode · ${labels.modeLabel}` : 'Mode';
	if (group.scope === 'preset') return labels.presetName ? `Preset · ${labels.presetName}` : 'Preset';
	return labels.modelName ? `Model · ${labels.modelName}` : 'Model';
}
