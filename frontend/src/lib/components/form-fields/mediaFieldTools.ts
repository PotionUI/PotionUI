import {
	hasEditor,
	isSurfaceEditorKind,
	toolDialogKind,
	type MediaEditorKind
} from '$lib/media/editors/types';
import { currentCatalog, isRunnable, toolAvailability } from '$lib/media-tools/catalog';
import type { MediaToolSource, ToolDTO } from '$lib/media-tools/types';
import { hasIcon } from '$lib/utils/IconLibrary';
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
	| 'open-in-editor'
	| 'mask'
	| 'clear-mask'
	| 'full'
	| 'earlier'
	| 'later'
	| 'replace'
	| 'remove'
	| 'remove-all'
	| (string & {});

const BUILTIN_FIELD_EDITORS: Record<string, MediaEditorKind> = {
	edit: 'paint',
	mask: 'mask'
};

const CATALOG_SHORTCUTS: Record<string, string> = { crop: 'C', trim: 'T', frame: 'F' };

export function fieldToolEditorKind(
	toolId: string,
	tools: readonly ToolDTO[] = currentCatalog()
): MediaEditorKind | null {
	if (toolId in BUILTIN_FIELD_EDITORS) return BUILTIN_FIELD_EDITORS[toolId];
	const tool = tools.find((candidate) => candidate.id === toolId);
	if (!tool || !isRunnable(tool)) return null;
	return isSurfaceEditorKind(tool.editor) ? (tool.editor as MediaEditorKind) : toolDialogKind(tool.id);
}

export const FIELD_TOOL_EDITORS: Partial<Record<FieldToolId, MediaEditorKind>> = new Proxy(
	{} as Partial<Record<FieldToolId, MediaEditorKind>>,
	{
		get: (_target, key) =>
			typeof key === 'string' ? (fieldToolEditorKind(key) ?? undefined) : undefined
	}
);

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
	catalogTools?: ToolDTO[];
	source?: MediaToolSource;
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

function catalogDefinition(
	entry: ToolDTO,
	options: FieldToolOptions
): { tool: MediaTool; availability: MediaToolAvailability } {
	const availability: MediaToolAvailability = options.missing
		? { enabled: false, reason: MISSING_REASON }
		: options.source
			? toolAvailability(entry, [options.source])
			: { enabled: true };
	return {
		tool: {
			id: entry.id,
			label: entry.label,
			icon: entry.icon && hasIcon(entry.icon) ? entry.icon : 'extension',
			shortcut: CATALOG_SHORTCUTS[entry.id],
			category: 'edit',
			source: entry.source === 'core' ? 'core' : 'plugin',
			scopes: ['field'],
			componentRef: isRunnable(entry) ? undefined : entry.component,
			applies: () => availability
		},
		availability
	};
}

export function buildFieldToolGroups(options: FieldToolOptions): MediaToolGroup[] {
	const { kind } = options;
	if (!kind) return [];

	const edit: MediaToolGroup['tools'] = [];
	const offers = (editor: MediaEditorKind) => hasEditor(editor, kind);

	if (offers('paint')) {
		edit.push(definition('edit', 'Edit image', 'paint-brush', 'edit', mediaAvailability(options, 'paint'), 'E'));
	}
	if (kind === 'image' || kind === 'video') {
		edit.push(
			definition(
				'open-in-editor',
				kind === 'video' ? 'Open in Video editor' : 'Open in Image editor',
				'external-link',
				'edit',
				options.missing ? { enabled: false, reason: MISSING_REASON } : { enabled: true }
			)
		);
	}
	for (const entry of options.catalogTools ?? []) {
		edit.push(catalogDefinition(entry, options));
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
	catalog?: ToolDTO[];
}

export interface FieldToolTarget {
	kind: MediaKind | null;
	item: Record<string, unknown> | null;
	flatIndex: number;
	inRow: boolean;
	missing: boolean;
}

export function fieldCatalogTools(tools: readonly ToolDTO[], kind: string): ToolDTO[] {
	return tools.filter(
		(tool) =>
			(tool.scopes.length === 0 || tool.scopes.includes('field')) &&
			tool.cardinality !== 'many_to_one' &&
			(tool.accepts as string[]).includes(kind)
	);
}

export function fieldToolGroupsFor(env: FieldToolEnv, target: FieldToolTarget): MediaToolGroup[] {
	const { kind, item, flatIndex } = target;
	if (!kind || !item) return [];
	const group = env.multiple ? env.groups.find((candidate) => candidate.kind === kind) : undefined;
	const entry = group?.items.find((candidate) => candidate.flatIndex === flatIndex);
	const url = typeof item.url === 'string' ? item.url : '';
	const filename = typeof item.name === 'string' ? item.name : '';
	const metadata = (item.metadata && typeof item.metadata === 'object' ? item.metadata : {}) as Record<string, unknown>;
	const durationSeconds = typeof metadata.duration_seconds === 'number' ? metadata.duration_seconds : null;
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
		inRow: target.inRow,
		catalogTools: fieldCatalogTools(env.catalog ?? currentCatalog(), kind),
		source: { id: 'field', kind, url, filename, durationSeconds }
	});
	return [...core, ...listToolGroups(buildFieldToolContext({ id: 'field', kind, url, filename }))];
}
