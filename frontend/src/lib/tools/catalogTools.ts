import { hasIcon } from '$lib/utils/IconLibrary';
import { isRunnable, selectionBounds, toolAvailability } from '$lib/media-tools/catalog';
import { isFoldedShortcut, planShortcut, shortcutLabel } from '$lib/tool-tabs/shortcuts';
import type { MediaToolSource, ToolDTO } from '$lib/media-tools/types';
import {
	registerMediaTool,
	selectionCount,
	unregisterMediaTools,
	type MediaTool,
	type MediaToolAvailability,
	type MediaToolContext,
	type MediaToolItem,
	type MediaToolScope
} from './tools';

export const MEDIA_TOOL_FALLBACK_ICON = 'extension';

const PAGE_SCOPES: MediaToolScope[] = ['history', 'library'];

function titleCase(id: string): string {
	return id
		.split(/[-_.\s]+/)
		.filter(Boolean)
		.map((word) => word.charAt(0).toUpperCase() + word.slice(1))
		.join(' ');
}

export function toolNote(tool: ToolDTO): string {
	if (tool.source !== 'core') return titleCase(tool.source);
	if (tool.cardinality === 'one_to_many') return 'Saves several files';
	if (tool.produces !== 'same' && !tool.accepts.includes(tool.produces)) {
		return `Saves one ${tool.produces}`;
	}
	return '';
}

export function selectionSources(items: readonly MediaToolItem[]): MediaToolSource[] {
	return items.map((item) => ({ ...item }));
}

function rangeReason(min: number, max: number, noun: string): string {
	if (!Number.isFinite(max)) return `Select ${min} or more ${noun}`;
	return min === max ? `Select ${min} ${noun}` : `Select ${min} to ${max} ${noun}`;
}

function nounFor(ctx: MediaToolContext): string {
	return ctx.scope === 'history' ? 'generations' : 'items';
}

function tabAvailability(tool: ToolDTO, ctx: MediaToolContext): MediaToolAvailability | null {
	if (ctx.scope === 'field' || tool.source !== 'core') return null;
	if (isFoldedShortcut(tool.id, ctx.items)) return { enabled: false, hidden: true };
	if (!planShortcut(tool.id, ctx.items) || ctx.items.some((item) => !(tool.accepts as string[]).includes(item.kind))) return null;
	for (const item of ctx.items) {
		const result = toolAvailability(tool, selectionSources([item]));
		if (!result.enabled) return { enabled: false, reason: result.reason };
	}
	return { enabled: true };
}

export function catalogAvailability(tool: ToolDTO, ctx: MediaToolContext): MediaToolAvailability {
	if (!tool.accepts.some((kind) => ctx.kinds.has(kind))) return { enabled: false, hidden: true };

	const { min, max } = selectionBounds(tool);
	const runnable = isRunnable(tool);
	const count = runnable ? ctx.items.length : selectionCount(ctx);
	const tooFew = count < min;
	const entryTooFew = tooFew && !!ctx.entry;

	if (!runnable) {
		if (tooFew || count > max) {
			return { enabled: false, hidden: entryTooFew, reason: rangeReason(min, max, nounFor(ctx)) };
		}
		return { enabled: true };
	}

	if (tool.editor && count > 1) {
		return { enabled: false, reason: 'Select one item' };
	}
	const result = toolAvailability(tool, selectionSources(ctx.items));
	if (result.enabled) return { enabled: true };
	return { enabled: false, hidden: entryTooFew, reason: result.reason };
}

export function mediaToolFromCatalog(tool: ToolDTO): MediaTool {
	const runnable = isRunnable(tool);
	const { min, max } = selectionBounds(tool);
	const scopes = tool.scopes.filter((scope): scope is MediaToolScope =>
		PAGE_SCOPES.includes(scope as MediaToolScope)
	);

	return {
		id: tool.id,
		label: tool.label,
		icon: tool.icon && hasIcon(tool.icon) ? tool.icon : MEDIA_TOOL_FALLBACK_ICON,
		category: !runnable ? 'plugins' : tool.group === 'effects' ? 'effects' : 'edit',
		source: tool.source === 'core' ? 'core' : 'plugin',
		scopes,
		selection: { min, ...(Number.isFinite(max) ? { max } : {}) },
		kinds: [...tool.accepts],
		catalogToolId: tool.id,
		pluginId: tool.source === 'core' ? undefined : tool.source,
		builtInEditor: !!tool.editor,
		note: toolNote(tool) || undefined,
		labelFor: (ctx) => (ctx.scope === 'field' || tool.source !== 'core' ? null : shortcutLabel(tool.id, ctx.items)) ?? tool.label,
		applies: (ctx) => tabAvailability(tool, ctx) ?? catalogAvailability(tool, ctx)
	};
}

export function isCatalogTool(tool: MediaTool): boolean {
	return !!tool.catalogToolId;
}

export function syncCatalogTools(tools: readonly ToolDTO[]): void {
	unregisterMediaTools((registered) => !!registered.catalogToolId);
	for (const tool of tools) {
		const mapped = mediaToolFromCatalog(tool);
		if (mapped.scopes.length > 0) registerMediaTool(mapped);
	}
}
