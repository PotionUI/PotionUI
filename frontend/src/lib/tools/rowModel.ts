import type { RecentLook } from '$lib/filters/recentLooks';
import { shortcutTargets } from '$lib/tool-tabs/shortcuts';
import type { EditorKind } from '$lib/tool-tabs/toolIds';
import { applyLookAvailability, editMediaAvailability, editMediaLabel } from './editMedia';
import {
	listToolGroups,
	type MediaTool,
	type MediaToolAvailability,
	type MediaToolContext,
	type MediaToolItem,
	type MediaToolScope,
	toolLabel
} from './tools';

export type RowToolAction = 'catalog' | 'edit-image' | 'apply-look';

export interface RowTool {
	id: string;
	label: string;
	icon: string;
	action: RowToolAction;
	enabled: boolean;
	reason: string;
	note: string;
	plugin?: boolean;
	pluginId?: string;
	lookId?: string;
	tool?: MediaTool;
	opens: EditorKind[] | null;
}

export interface RowModel {
	edit: RowTool[];
	effects: RowTool[];
	downloads: RowTool[];
	plugins: RowTool[];
}

export function buildRowContext(
	scope: Extract<MediaToolScope, 'history' | 'library'>,
	items: MediaToolItem[]
): MediaToolContext {
	return {
		scope,
		items,
		kinds: new Set(items.map((item) => item.kind)),
		collectionId: null,
		generations: [],
		generationIds: [],
		files: [],
		entry: { status: 'completed', stackCells: null, looseCell: false, error: null }
	};
}

function opensFor(id: string, source: 'core' | 'plugin', ctx: MediaToolContext): EditorKind[] | null {
	return source === 'core' ? shortcutTargets(id, ctx.items) : null;
}

function catalogEntry(tool: MediaTool, availability: MediaToolAvailability, ctx: MediaToolContext): RowTool {
	return {
		id: tool.id,
		label: toolLabel(tool, ctx),
		icon: tool.icon,
		action: 'catalog',
		enabled: availability.enabled,
		reason: availability.reason ?? '',
		note: tool.note ?? '',
		plugin: tool.source === 'plugin',
		pluginId: tool.pluginId,
		tool,
		opens: opensFor(tool.id, tool.source, ctx)
	};
}

export const DOWNLOAD_ICON = 'download';

export function buildRowModel(ctx: MediaToolContext, looks: readonly RecentLook[] = []): RowModel {
	const core: RowTool[] = [];
	const pluginEdit: RowTool[] = [];
	const catalogEffects: RowTool[] = [];
	const downloads: RowTool[] = [];
	const plugins: RowTool[] = [];

	for (const group of listToolGroups(ctx)) {
		if (!['edit', 'effects', 'plugins'].includes(group.category.id)) continue;
		for (const { tool, availability } of group.tools) {
			if (availability.hidden) continue;
			if (group.category.id === 'effects' && !tool.catalogToolId) continue;
			const entry = catalogEntry(tool, availability, ctx);
			if (group.category.id === 'effects') catalogEffects.push(entry);
			else if (group.category.id === 'edit') (entry.plugin ? pluginEdit : core).push(entry);
			else if (tool.icon === DOWNLOAD_ICON && tool.catalogToolId) downloads.push(entry);
			else if (tool.catalogToolId) plugins.push(entry);
		}
	}

	const edit = [...core];
	const editMedia = editMediaAvailability(ctx);
	const leadKind = ctx.items[0]?.kind;
	if ((leadKind === 'image' || leadKind === 'video') && ctx.items.every((item) => item.kind === leadKind)) {
		edit.push({
			id: 'edit-image',
			label: editMediaLabel(ctx),
			icon: leadKind === 'video' ? 'video' : 'sliders',
			action: 'edit-image',
			enabled: editMedia.enabled,
			reason: editMedia.reason ?? '',
			note: '',
			opens: shortcutTargets('edit-image', ctx.items)
		});
	}
	edit.push(...pluginEdit);

	const look = applyLookAvailability(ctx);
	const lookEntries: RowTool[] = look.hidden
		? []
		: [
				{
					id: 'apply-look',
					label: 'Apply a look…',
					icon: 'palette',
					action: 'apply-look',
					enabled: look.enabled,
					reason: look.reason ?? '',
					note: '',
					opens: shortcutTargets('apply-look', ctx.items)
				},
				...looks.map(
					(recent): RowTool => ({
						id: `look:${recent.id}`,
						label: recent.name,
						icon: '',
						action: 'apply-look',
						enabled: look.enabled,
						reason: look.reason ?? '',
						note: recent.group,
						lookId: recent.id,
						opens: shortcutTargets(`look:${recent.id}`, ctx.items)
					})
				)
			];

	return { edit, effects: [...catalogEffects, ...lookEntries], downloads, plugins };
}
