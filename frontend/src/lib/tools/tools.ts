import type { ComponentType } from 'svelte';
import type { GenerationFile, GenerationHistoryItem } from '$lib/types/history';
import type { LibraryItem } from '$lib/services/api/library';
import {
	isAudioFileType,
	isImageFileType,
	isMeshFileType,
	isVideoFileType
} from '$lib/utils/fileType';

export type MediaKind = 'image' | 'video' | 'audio' | 'mesh';

/** Which page's Tools menu a tool is offered on. */
export type MediaToolScope = 'history' | 'library' | 'field';

/** One selected file, resolved to a stable url plus enough to key a
 * generation's per-file params by. `generationId`/`paramIndex` are present
 * only for a history-scoped item - a library item is a standalone resource. */
export interface MediaToolItem {
	id: string;
	kind: MediaKind;
	url: string;
	filename: string;
	width?: number;
	height?: number;
	durationSeconds?: number | null;
	fps?: number | null;
	hasAlpha?: boolean | null;
	itemId?: string | null;
	generationId?: string;
	generationFileId?: string | null;
	paramIndex?: number;
}

/** A history-scoped selected file paired with the generation it came from -
 * kept alongside `items` for the tools (Stitch, Compare) that still need the
 * full generation record, not just the resolved url. */
export interface MediaToolFile {
	generation: GenerationHistoryItem;
	file: GenerationFile;
	/** Position of the file within `generation.files` - the index generation params are keyed by. */
	index: number;
	kind: MediaKind;
}

export interface MediaToolContext {
	scope: MediaToolScope;
	items: MediaToolItem[];
	kinds: Set<MediaKind>;
	collectionId: string | null;
	/** History-scope only; empty on the library scope. */
	generations: GenerationHistoryItem[];
	generationIds: string[];
	files: MediaToolFile[];
	entry?: MediaToolEntryInfo;
}

export type MediaToolEntryStatus = 'completed' | 'failed' | 'running';

export interface MediaToolEntryInfo {
	status: MediaToolEntryStatus;
	stackCells: number | null;
	looseCell: boolean;
	error: string | null;
	favorite?: boolean;
}

export type MediaToolSurface = 'entry' | 'selection' | 'bar';

export interface ToolRunHost {
	open?: (ctx: MediaToolContext) => void;
	openGrid?: (ctx: MediaToolContext) => void;
	reuse?: (ctx: MediaToolContext) => void;
	remove?: (ctx: MediaToolContext) => void;
	cancel?: (ctx: MediaToolContext) => void;
	deselect?: (ctx: MediaToolContext, entryId?: string) => void;
	rate?: (ctx: MediaToolContext, rating: number) => void;
	favorite?: (ctx: MediaToolContext) => void;
	copyToLibrary?: (ctx: MediaToolContext) => void;
	addToCollection?: (ctx: MediaToolContext, collectionId: string) => void;
	addToKit?: (ctx: MediaToolContext) => void;
}

export interface ToolRunExtra {
	rating?: number;
	collectionId?: string;
}

export interface MediaToolAvailability {
	enabled: boolean;
	hidden?: boolean;
	/** Shown as the disabled menu item's tooltip, e.g. "Select exactly 2 generations". */
	reason?: string;
}

export interface MediaToolCategory {
	id: string;
	label: string;
	order: number;
}

/**
 * The props every core tool modal is mounted with. The two callbacks say what
 * the tool did, not what the host should render: `onDone` means it changed
 * data, and the page clears the selection behind it; `onClose` means nothing
 * changed, and the selection survives.
 */
export interface MediaToolModalProps {
	context: MediaToolContext;
	onClose: () => void;
	onDone: () => void;
}

export interface MediaTool {
	id: string;
	label: string;
	description?: string;
	icon: string;
	shortcut?: string;
	category: string;
	source: 'core' | 'plugin';
	/** Pages this tool is offered on. */
	scopes: MediaToolScope[];
	applies: (ctx: MediaToolContext) => MediaToolAvailability;
	component?: ComponentType | null;
	componentRef?: string | null;
	selection?: { min?: number; max?: number };
	run?: (ctx: MediaToolContext, host: ToolRunHost, extra?: ToolRunExtra) => void | Promise<void>;
	surfaces?: MediaToolSurface[];
	entryStates?: MediaToolEntryStatus[];
	kinds?: MediaKind[];
	requires?: keyof ToolRunHost;
	control?: 'rating' | 'collection';
	tone?: 'danger';
	labelFor?: (ctx: MediaToolContext) => string;
	catalogToolId?: string;
	pluginId?: string;
	builtInEditor?: boolean;
	lookId?: string;
	componentProps?: Record<string, unknown>;
	note?: string;
	after?: string;
}

export const MEDIA_TOOL_CATEGORIES: MediaToolCategory[] = [
	{ id: 'edit', label: 'Edit', order: -20 },
	{ id: 'effects', label: 'Effects', order: -10 },
	{ id: 'open', label: 'Open', order: 0 },
	{ id: 'analyze', label: 'Analyze', order: 10 },
	{ id: 'compose', label: 'Compose', order: 20 },
	{ id: 'export', label: 'Export', order: 30 },
	{ id: 'plugins', label: 'Plugins', order: 35 },
	{ id: 'organize', label: 'Organize', order: 40 },
	{ id: 'danger', label: 'Danger', order: 99 }
];

const ENTRY_CATEGORY_ORDER: Record<string, number> = { plugins: -5 };

const categories = new Map<string, MediaToolCategory>(
	MEDIA_TOOL_CATEGORIES.map((category) => [category.id, category])
);

// Insertion-ordered: two tools in the same category keep their registration
// order in the menu, and re-registering an id replaces it in place.
const tools = new Map<string, MediaTool>();

export function registerMediaTool(tool: MediaTool): void {
	tools.set(tool.id, tool);
}

export function registerMediaToolCategory(category: MediaToolCategory): void {
	categories.set(category.id, category);
}

/** Every registered tool, in registration order - optionally narrowed to the
 * tools offered on `scope`. */
export function orderTools(list: readonly MediaTool[]): MediaTool[] {
	const ids = new Set(list.map((tool) => tool.id));
	const out = list.filter((tool) => !tool.after || !ids.has(tool.after) || tool.after === tool.id);
	let pending = list.filter((tool) => !out.includes(tool));
	while (pending.length) {
		const before = pending.length;
		pending = pending.filter((tool) => {
			const at = out.findIndex((placed) => placed.id === tool.after);
			if (at < 0) return true;
			let end = at + 1;
			while (end < out.length && out[end].after === tool.after) end += 1;
			out.splice(end, 0, tool);
			return false;
		});
		if (pending.length === before) {
			out.push(...pending);
			break;
		}
	}
	return out;
}

export function listTools(scope?: MediaToolScope): MediaTool[] {
	const all = orderTools([...tools.values()]);
	return scope ? all.filter((tool) => tool.scopes.includes(scope)) : all;
}

/** Drops every tool `predicate` accepts. Used to swap a plugin snapshot in. */
export function unregisterMediaTools(predicate: (tool: MediaTool) => boolean): void {
	for (const [id, tool] of [...tools]) {
		if (predicate(tool)) tools.delete(id);
	}
}

function mediaKind(file: GenerationFile): MediaKind | null {
	if (isImageFileType(file.file_type)) return 'image';
	if (isVideoFileType(file.file_type)) return 'video';
	if (isAudioFileType(file.file_type)) return 'audio';
	if (isMeshFileType(file.file_type)) return 'mesh';
	return null;
}

/**
 * Resolves the History page's selection into the shape every `applies()`
 * reads. `selectedIds` drives the order, and an id with no loaded generation
 * is dropped, so `generations` and `generationIds` stay parallel.
 */
export function buildHistoryToolContext(
	generations: GenerationHistoryItem[],
	selectedIds: string[],
	collectionId: string | null
): MediaToolContext {
	const byId = new Map(generations.map((generation) => [generation.id, generation]));
	const selected = selectedIds
		.map((id) => byId.get(id))
		.filter((generation): generation is GenerationHistoryItem => !!generation);

	const files: MediaToolFile[] = [];
	const items: MediaToolItem[] = [];
	const kinds = new Set<MediaKind>();

	for (const generation of selected) {
		generation.files.forEach((file, index) => {
			if (!file.is_final) return;
			const kind = mediaKind(file);
			if (!kind) return;
			files.push({ generation, file, index, kind });
			kinds.add(kind);

			const filename = file.file_path.split('/').pop() || file.file_path;
			items.push({
				id: `${generation.id}:${index}`,
				kind,
				url: `/api/media/generations/${generation.id}/${filename}`,
				filename,
				width: file.width,
				height: file.height,
				durationSeconds: file.duration_seconds,
				fps: file.fps,
				hasAlpha: file.has_alpha,
				generationId: generation.id,
				generationFileId: String(file.id),
				paramIndex: index
			});
		});
	}

	return {
		scope: 'history',
		items,
		kinds,
		collectionId,
		generations: selected,
		generationIds: selected.map((generation) => generation.id),
		files
	};
}

/** Resolves the Library page's selection into a tool context. A library item
 * carries no generation - `generations`/`generationIds`/`files` stay empty. */
export function buildLibraryToolContext(
	items: LibraryItem[],
	collectionId: string | null
): MediaToolContext {
	const kinds = new Set<MediaKind>();
	const toolItems: MediaToolItem[] = items
		.map((item): MediaToolItem | null => {
			const kind = item.media_type as MediaKind;
			if (kind !== 'image' && kind !== 'video' && kind !== 'audio') return null;
			kinds.add(kind);
			return {
				id: item.id,
				kind,
				url: item.url,
				filename: item.original_filename ?? item.filename,
				width: item.width,
				height: item.height,
				durationSeconds: item.duration_seconds,
				fps: item.fps,
				hasAlpha: item.has_alpha,
				itemId: item.id
			};
		})
		.filter((item): item is MediaToolItem => item !== null);

	return {
		scope: 'library',
		items: toolItems,
		kinds,
		collectionId,
		generations: [],
		generationIds: [],
		files: []
	};
}

export function buildFieldToolContext(item: MediaToolItem | null): MediaToolContext {
	return {
		scope: 'field',
		items: item ? [item] : [],
		kinds: new Set<MediaKind>(item ? [item.kind] : []),
		collectionId: null,
		generations: [],
		generationIds: [],
		files: []
	};
}

function titleCase(id: string): string {
	return id
		.split(/[-_]+/)
		.filter(Boolean)
		.map((word) => word.charAt(0).toUpperCase() + word.slice(1))
		.join(' ');
}

export interface MediaToolGroup {
	category: MediaToolCategory;
	tools: Array<{ tool: MediaTool; availability: MediaToolAvailability }>;
}

/**
 * The menu for `ctx.scope`: categories in `order`, empty ones omitted, tools
 * in registration order within each. A tool naming an unregistered category
 * gets one invented for it, sorted after every declared category in
 * discovery order. A tool not offered on `ctx.scope` is left out entirely,
 * not just disabled.
 */
export function listToolGroups(ctx: MediaToolContext): MediaToolGroup[] {
	const groups = new Map<string, MediaToolGroup>();
	let invented = 0;

	for (const tool of orderTools([...tools.values()])) {
		if (!tool.scopes.includes(ctx.scope)) continue;

		let group = groups.get(tool.category);
		if (!group) {
			const category = categories.get(tool.category) ?? {
				id: tool.category,
				label: titleCase(tool.category),
				order: 100 + invented++
			};
			group = { category, tools: [] };
			groups.set(tool.category, group);
		}
		group.tools.push({ tool, availability: tool.applies(ctx) });
	}

	return [...groups.values()].sort((a, b) => a.category.order - b.category.order);
}

export function selectionCount(ctx: MediaToolContext): number {
	return ctx.scope === 'history' ? ctx.generations.length : ctx.items.length;
}

function selectionSatisfied(tool: MediaTool, ctx: MediaToolContext): boolean {
	const count = selectionCount(ctx);
	const min = tool.selection?.min;
	const max = tool.selection?.max;
	if (min !== undefined && count < min) return false;
	if (max !== undefined && count > max) return false;
	return true;
}

export function toolLabel(tool: MediaTool, ctx: MediaToolContext): string {
	return tool.labelFor ? tool.labelFor(ctx) : tool.label;
}

export interface GroupToolsOptions {
	surface: MediaToolSurface;
	host?: ToolRunHost;
}

function visibleOnSurface(tool: MediaTool, ctx: MediaToolContext, options: GroupToolsOptions): boolean {
	const surfaces = tool.surfaces ?? ['entry', 'selection', 'bar'];
	if (!surfaces.includes(options.surface)) return false;
	if (options.surface === 'bar') return true;
	if (options.host && tool.requires && !options.host[tool.requires]) return false;
	if (options.surface === 'selection') return true;
	if (ctx.entry?.stackCells && !tool.surfaces?.includes('entry')) return false;
	const status = ctx.entry?.status ?? 'completed';
	if (!(tool.entryStates ?? ['completed']).includes(status)) return false;
	if (status === 'completed') {
		if (!selectionSatisfied(tool, ctx)) return false;
		if (tool.kinds && !tool.kinds.some((kind) => ctx.kinds.has(kind))) return false;
	}
	return true;
}

export function groupToolsFor(ctx: MediaToolContext, options: GroupToolsOptions): MediaToolGroup[] {
	const orderOf = (group: MediaToolGroup) =>
		(options.surface === 'entry' ? ENTRY_CATEGORY_ORDER[group.category.id] : undefined) ??
		group.category.order;
	return listToolGroups(ctx)
		.map((group) => ({
			...group,
			tools: group.tools.filter(
				({ tool, availability }) => visibleOnSurface(tool, ctx, options) && !availability.hidden
			)
		}))
		.filter((group) => group.tools.length > 0)
		.sort((a, b) => orderOf(a) - orderOf(b));
}
