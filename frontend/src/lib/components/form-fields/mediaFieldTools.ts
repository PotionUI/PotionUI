import { hasEditor, type MediaEditorKind } from '$lib/media/editors/types';
import {
	buildFieldToolContext,
	listToolGroups,
	type MediaTool,
	type MediaToolAvailability,
	type MediaToolCategory,
	type MediaToolGroup
} from '$lib/tools/tools';
import type { MediaGroup } from './mediaFieldGroups';
import type { MediaKind } from './mediaLoaderConfig';

export type FieldToolId =
	| 'edit'
	| 'crop'
	| 'mask'
	| 'clear-mask'
	| 'trim'
	| 'frame'
	| 'split'
	| 'full'
	| 'earlier'
	| 'later'
	| 'replace'
	| 'remove'
	| 'remove-all';

export const FIELD_TOOL_EDITORS: Partial<Record<FieldToolId, MediaEditorKind>> = {
	edit: 'paint',
	crop: 'crop',
	mask: 'mask',
	trim: 'trim',
	frame: 'frame',
	split: 'split'
};

export interface FieldToolOptions {
	kind: MediaKind | null;
	multiple: boolean;
	allowInpaint: boolean;
	canEmitMask: boolean;
	hasMask: boolean;
	missing: boolean;
	groupSize: number;
	kindIndex: number;
	totalItems: number;
	inRow?: boolean;
}

const CATEGORIES: Record<'edit' | 'view' | 'order' | 'source', MediaToolCategory> = {
	edit: { id: 'edit', label: 'Edit', order: 10 },
	view: { id: 'view', label: 'View', order: 20 },
	order: { id: 'order', label: 'Order', order: 30 },
	source: { id: 'source', label: 'Source', order: 40 }
};

const MISSING_REASON = 'File not found';

function definition(
	id: FieldToolId,
	label: string,
	icon: string,
	category: keyof typeof CATEGORIES,
	availability: MediaToolAvailability,
	shortcut?: string
): { tool: MediaTool; availability: MediaToolAvailability } {
	return {
		tool: {
			id,
			label,
			icon,
			shortcut,
			category,
			source: 'core',
			scopes: ['field'],
			applies: () => availability
		},
		availability
	};
}

function mediaAvailability(options: FieldToolOptions, editor: MediaEditorKind): MediaToolAvailability {
	if (options.missing) return { enabled: false, reason: MISSING_REASON };
	if (editor === 'mask' && !options.canEmitMask) return { enabled: false, reason: 'Not available in this form' };
	return { enabled: true };
}

export function fieldToolIds(options: FieldToolOptions): FieldToolId[] {
	return buildFieldToolGroups(options).flatMap((group) => group.tools.map((entry) => entry.tool.id as FieldToolId));
}

export function buildFieldToolGroups(options: FieldToolOptions): MediaToolGroup[] {
	const { kind } = options;
	if (!kind) return [];

	const edit: MediaToolGroup['tools'] = [];
	const offers = (editor: MediaEditorKind) => hasEditor(editor, kind);

	if (offers('paint')) {
		edit.push(definition('edit', 'Edit image', 'paint-brush', 'edit', mediaAvailability(options, 'paint'), 'E'));
	}
	if (offers('crop')) {
		edit.push(definition('crop', 'Crop & frame', 'crop', 'edit', mediaAvailability(options, 'crop'), 'C'));
	}
	if (offers('mask') && options.allowInpaint) {
		const label = options.hasMask ? 'Edit inpainting mask' : 'Create inpainting mask';
		edit.push(definition('mask', label, 'edit', 'edit', mediaAvailability(options, 'mask'), 'M'));
		edit.push(
			definition(
				'clear-mask',
				'Clear mask',
				'close',
				'edit',
				options.hasMask ? { enabled: true } : { enabled: false, reason: 'No mask' }
			)
		);
	}
	if (offers('trim')) {
		const label = kind === 'audio' ? 'Trim on waveform' : 'Trim in / out';
		edit.push(definition('trim', label, 'scissors', 'edit', mediaAvailability(options, 'trim'), 'T'));
	}
	if (offers('frame')) {
		edit.push(definition('frame', 'Extract a frame', 'photo', 'edit', mediaAvailability(options, 'frame'), 'F'));
	}
	if (offers('split')) {
		edit.push(definition('split', 'Split into parts', 'split', 'edit', mediaAvailability(options, 'split')));
	}

	const groups: MediaToolGroup[] = [];
	if (edit.length > 0) groups.push({ category: CATEGORIES.edit, tools: edit });

	groups.push({
		category: CATEGORIES.view,
		tools: [
			definition(
				'full',
				'View full size',
				'expand',
				'view',
				options.missing ? { enabled: false, reason: MISSING_REASON } : { enabled: true }
			)
		]
	});

	if (options.multiple) {
		const first = options.kindIndex <= 0;
		const last = options.kindIndex >= options.groupSize - 1;
		groups.push({
			category: CATEGORIES.order,
			tools: [
				definition(
					'earlier',
					'Move earlier',
					'chevron-left',
					'order',
					first ? { enabled: false, reason: 'First' } : { enabled: true },
					'Alt ←'
				),
				definition(
					'later',
					'Move later',
					'chevron-right',
					'order',
					last ? { enabled: false, reason: 'Last' } : { enabled: true },
					'Alt →'
				)
			]
		});
	}

	if (options.multiple || options.inRow) {
		const source: MediaToolGroup['tools'] = [
			definition('replace', 'Replace file...', 'refresh', 'source', { enabled: true })
		];
		if (options.inRow) source.push(definition('remove', 'Remove', 'trash', 'source', { enabled: true }, 'Del'));
		if (options.multiple) {
			source.push(definition('remove-all', `Remove all ${options.totalItems}`, 'trash', 'source', { enabled: true }));
		}
		groups.push({ category: CATEGORIES.source, tools: source });
	}

	return groups;
}

export function matchToolShortcut(groups: MediaToolGroup[], key: string): MediaTool | null {
	if (key.length !== 1) return null;
	const wanted = key.toUpperCase();
	for (const group of groups) {
		for (const entry of group.tools) {
			if (entry.tool.shortcut === wanted && entry.availability.enabled) return entry.tool;
		}
	}
	return null;
}

export interface FieldToolEnv {
	multiple: boolean;
	groups: MediaGroup<unknown>[];
	allowInpaint: boolean;
	canEmitMask: boolean;
	hasMask: boolean;
	totalItems: number;
	registrations: number;
}

export interface FieldToolTarget {
	kind: MediaKind | null;
	item: Record<string, unknown> | null;
	flatIndex: number;
	inRow: boolean;
	missing: boolean;
}

export function fieldToolGroupsFor(env: FieldToolEnv, target: FieldToolTarget): MediaToolGroup[] {
	const { kind, item, flatIndex } = target;
	if (!kind || !item) return [];
	const group = env.multiple ? env.groups.find((candidate) => candidate.kind === kind) : undefined;
	const entry = group?.items.find((candidate) => candidate.flatIndex === flatIndex);
	const core = buildFieldToolGroups({
		kind,
		multiple: env.multiple,
		allowInpaint: env.allowInpaint,
		canEmitMask: env.canEmitMask,
		hasMask: env.hasMask,
		missing: target.missing,
		groupSize: group?.count ?? 1,
		kindIndex: entry?.kindIndex ?? 0,
		totalItems: env.totalItems,
		inRow: target.inRow
	});
	const url = item && typeof item.url === 'string' ? item.url : '';
	const filename = item && typeof item.name === 'string' ? item.name : '';
	return [...core, ...listToolGroups(buildFieldToolContext({ id: 'field', kind, url, filename }))];
}
