/**
 * The media tools system: what the Tools menu offers for the media currently
 * selected, on the History page and the Library page alike.
 *
 * Core registers its own tools here (`routes/history/tools/coreTools.ts`) and
 * plugins register theirs from the served `history_tools[]` manifest section
 * (`pluginTools.ts` - the manifest key stayed `history_tools` for existing
 * plugins, even though it now feeds both pages). Nothing in this module
 * fetches or mounts anything - a tool is data plus an `applies()` predicate,
 * so the whole catalog is decidable from a context object. A tool that
 * doesn't declare `scopes` only ever shows up on the History page, matching
 * every tool registered before scopes existed.
 */
import type { ComponentType } from 'svelte';
import type { GenerationFile, GenerationHistoryItem } from '$lib/types/history';
import type { LibraryItem } from '$lib/services/api/library';
import { hasIcon } from '$lib/utils/IconLibrary';
import {
	isAudioFileType,
	isImageFileType,
	isMeshFileType,
	isVideoFileType
} from '$lib/utils/fileType';

export type MediaKind = 'image' | 'video' | 'audio' | 'mesh';

/** Which page's Tools menu a tool is offered on. */
export type MediaToolScope = 'history' | 'library';

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
	generationId?: string;
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
}

export interface MediaToolAvailability {
	enabled: boolean;
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
	category: string;
	source: 'core' | 'plugin';
	/** Pages this tool is offered on. */
	scopes: MediaToolScope[];
	applies: (ctx: MediaToolContext) => MediaToolAvailability;
	component?: ComponentType | null;
	componentRef?: string | null;
}

export const MEDIA_TOOL_CATEGORIES: MediaToolCategory[] = [
	{ id: 'analyze', label: 'Analyze', order: 10 },
	{ id: 'compose', label: 'Compose', order: 20 },
	{ id: 'export', label: 'Export', order: 30 }
];

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
export function listTools(scope?: MediaToolScope): MediaTool[] {
	const all = [...tools.values()];
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
				generationId: generation.id,
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
				height: item.height
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

	for (const tool of tools.values()) {
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

export interface PluginMediaToolAppliesTo {
	min_selection?: number | null;
	max_selection?: number | null;
	media_kinds?: MediaKind[];
}

/** One entry of `GET /api/plugins/history-tools`. */
export interface PluginMediaToolEntry {
	id: string;
	plugin_id: string;
	label: string;
	description?: string;
	icon?: string;
	category: string;
	component: string;
	applies_to?: PluginMediaToolAppliesTo | null;
	/** Absent on a manifest written before scopes existed - defaults to `['history']`. */
	scopes?: MediaToolScope[] | null;
}

function plural(count: number, scope: MediaToolScope): string {
	const noun = scope === 'history' ? 'generation' : 'item';
	return count === 1 ? noun : `${noun}s`;
}

function kindList(kinds: MediaKind[]): string {
	if (kinds.length === 1) return kinds[0];
	return `${kinds.slice(0, -1).join(', ')} or ${kinds[kinds.length - 1]}`;
}

/** The icon a plugin tool falls back to when the served name is not drawable. */
export const MEDIA_TOOL_FALLBACK_ICON = 'extension';

/**
 * Turns a served manifest entry into a tool. The backend already composes the
 * id as `<plugin_id>:<id>` and the component as `plugin:<plugin_id>:<asset>`;
 * a bare asset is prefixed here so `parseComponentRef` can read it.
 *
 * An icon name `IconLibrary` has no path for would render as the name's first
 * letter, so an unknown one is swapped for the fallback before it reaches the
 * menu - including the manifest schema's own `tool` default.
 *
 * `applies()` only ever reads `ctx.generations`/`ctx.kinds`, so it behaves the
 * same whether `ctx` came from `buildHistoryToolContext` or
 * `buildLibraryToolContext` - a plugin tool scoped to `library` sees an empty
 * `generations` there and so must declare bounds that don't require one.
 */
export function pluginMediaToolFromManifest(entry: PluginMediaToolEntry): MediaTool {
	const bounds = entry.applies_to ?? {};
	const min = bounds.min_selection ?? null;
	const max = bounds.max_selection ?? null;
	const kinds = bounds.media_kinds ?? [];
	const componentRef = entry.component.startsWith('plugin:')
		? entry.component
		: `plugin:${entry.plugin_id}:${entry.component}`;

	return {
		id: entry.id,
		label: entry.label,
		description: entry.description || undefined,
		icon: entry.icon && hasIcon(entry.icon) ? entry.icon : MEDIA_TOOL_FALLBACK_ICON,
		category: entry.category,
		source: 'plugin',
		scopes: entry.scopes && entry.scopes.length > 0 ? entry.scopes : ['history'],
		componentRef,
		applies(ctx) {
			const count = ctx.scope === 'history' ? ctx.generations.length : ctx.items.length;
			if (min !== null && count < min) {
				return { enabled: false, reason: `Select at least ${min} ${plural(min, ctx.scope)}` };
			}
			if (max !== null && count > max) {
				return { enabled: false, reason: `Select at most ${max} ${plural(max, ctx.scope)}` };
			}
			if (kinds.length > 0 && !kinds.some((kind) => ctx.kinds.has(kind))) {
				return { enabled: false, reason: `Select at least one ${kindList(kinds)}` };
			}
			return { enabled: true };
		}
	};
}
