/**
 * The History page's selection tools: what the Tools menu offers for the
 * generations currently selected.
 *
 * Core registers its own tools here (`routes/history/tools/coreTools.ts`) and
 * plugins register theirs from the served `history_tools[]` manifest section
 * (`pluginTools.ts`). Nothing in this module fetches or mounts anything - a
 * tool is data plus an `applies()` predicate, so the whole catalog is
 * decidable from a context object.
 */
import type { ComponentType } from 'svelte';
import type { GenerationFile, GenerationHistoryItem } from '$lib/types/history';
import { hasIcon } from '$lib/utils/IconLibrary';
import {
	isAudioFileType,
	isImageFileType,
	isMeshFileType,
	isVideoFileType
} from '$lib/utils/fileType';

export type HistoryMediaKind = 'image' | 'video' | 'audio' | 'mesh';

export interface HistoryToolFile {
	generation: GenerationHistoryItem;
	file: GenerationFile;
	/** Position of the file within `generation.files` - the index generation params are keyed by. */
	index: number;
	kind: HistoryMediaKind;
}

export interface HistoryToolContext {
	generations: GenerationHistoryItem[];
	generationIds: string[];
	files: HistoryToolFile[];
	kinds: Set<HistoryMediaKind>;
	collectionId: string | null;
}

export interface HistoryToolAvailability {
	enabled: boolean;
	/** Shown as the disabled menu item's tooltip, e.g. "Select exactly 2 generations". */
	reason?: string;
}

export interface HistoryToolCategory {
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
export interface HistoryToolModalProps {
	context: HistoryToolContext;
	onClose: () => void;
	onDone: () => void;
}

export interface HistoryTool {
	id: string;
	label: string;
	description?: string;
	icon: string;
	category: string;
	source: 'core' | 'plugin';
	applies: (ctx: HistoryToolContext) => HistoryToolAvailability;
	component?: ComponentType | null;
	componentRef?: string | null;
}

export const HISTORY_TOOL_CATEGORIES: HistoryToolCategory[] = [
	{ id: 'analyze', label: 'Analyze', order: 10 },
	{ id: 'compose', label: 'Compose', order: 20 },
	{ id: 'export', label: 'Export', order: 30 }
];

const categories = new Map<string, HistoryToolCategory>(
	HISTORY_TOOL_CATEGORIES.map((category) => [category.id, category])
);

// Insertion-ordered: two tools in the same category keep their registration
// order in the menu, and re-registering an id replaces it in place.
const tools = new Map<string, HistoryTool>();

export function registerHistoryTool(tool: HistoryTool): void {
	tools.set(tool.id, tool);
}

export function registerHistoryToolCategory(category: HistoryToolCategory): void {
	categories.set(category.id, category);
}

/** Every registered tool, in registration order. */
export function listHistoryTools(): HistoryTool[] {
	return [...tools.values()];
}

/** Drops every tool `predicate` accepts. Used to swap a plugin snapshot in. */
export function unregisterHistoryTools(predicate: (tool: HistoryTool) => boolean): void {
	for (const [id, tool] of [...tools]) {
		if (predicate(tool)) tools.delete(id);
	}
}

function mediaKind(file: GenerationFile): HistoryMediaKind | null {
	if (isImageFileType(file.file_type)) return 'image';
	if (isVideoFileType(file.file_type)) return 'video';
	if (isAudioFileType(file.file_type)) return 'audio';
	if (isMeshFileType(file.file_type)) return 'mesh';
	return null;
}

/**
 * Resolves the selection into the shape every `applies()` reads. `selectedIds`
 * drives the order, and an id with no loaded generation is dropped, so
 * `generations` and `generationIds` stay parallel.
 */
export function buildHistoryToolContext(
	generations: GenerationHistoryItem[],
	selectedIds: string[],
	collectionId: string | null
): HistoryToolContext {
	const byId = new Map(generations.map((generation) => [generation.id, generation]));
	const selected = selectedIds
		.map((id) => byId.get(id))
		.filter((generation): generation is GenerationHistoryItem => !!generation);

	const files: HistoryToolFile[] = [];
	const kinds = new Set<HistoryMediaKind>();

	for (const generation of selected) {
		generation.files.forEach((file, index) => {
			if (!file.is_final) return;
			const kind = mediaKind(file);
			if (!kind) return;
			files.push({ generation, file, index, kind });
			kinds.add(kind);
		});
	}

	return {
		generations: selected,
		generationIds: selected.map((generation) => generation.id),
		files,
		kinds,
		collectionId
	};
}

function titleCase(id: string): string {
	return id
		.split(/[-_]+/)
		.filter(Boolean)
		.map((word) => word.charAt(0).toUpperCase() + word.slice(1))
		.join(' ');
}

export interface HistoryToolGroup {
	category: HistoryToolCategory;
	tools: Array<{ tool: HistoryTool; availability: HistoryToolAvailability }>;
}

/**
 * The menu: categories in `order`, empty ones omitted, tools in registration
 * order within each. A tool naming an unregistered category gets one invented
 * for it, sorted after every declared category in discovery order.
 */
export function listHistoryToolGroups(ctx: HistoryToolContext): HistoryToolGroup[] {
	const groups = new Map<string, HistoryToolGroup>();
	let invented = 0;

	for (const tool of tools.values()) {
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

export interface PluginHistoryToolAppliesTo {
	min_selection?: number | null;
	max_selection?: number | null;
	media_kinds?: HistoryMediaKind[];
}

/** One entry of `GET /api/plugins/history-tools`. */
export interface PluginHistoryToolEntry {
	id: string;
	plugin_id: string;
	label: string;
	description?: string;
	icon?: string;
	category: string;
	component: string;
	applies_to?: PluginHistoryToolAppliesTo | null;
}

function plural(count: number): string {
	return count === 1 ? 'generation' : 'generations';
}

function kindList(kinds: HistoryMediaKind[]): string {
	if (kinds.length === 1) return kinds[0];
	return `${kinds.slice(0, -1).join(', ')} or ${kinds[kinds.length - 1]}`;
}

/** The icon a plugin tool falls back to when the served name is not drawable. */
export const HISTORY_TOOL_FALLBACK_ICON = 'extension';

/**
 * Turns a served manifest entry into a tool. The backend already composes the
 * id as `<plugin_id>:<id>` and the component as `plugin:<plugin_id>:<asset>`;
 * a bare asset is prefixed here so `parseComponentRef` can read it.
 *
 * An icon name `IconLibrary` has no path for would render as the name's first
 * letter, so an unknown one is swapped for the fallback before it reaches the
 * menu - including the manifest schema's own `tool` default.
 */
export function pluginHistoryToolFromManifest(entry: PluginHistoryToolEntry): HistoryTool {
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
		icon: entry.icon && hasIcon(entry.icon) ? entry.icon : HISTORY_TOOL_FALLBACK_ICON,
		category: entry.category,
		source: 'plugin',
		componentRef,
		applies(ctx) {
			const count = ctx.generations.length;
			if (min !== null && count < min) {
				return { enabled: false, reason: `Select at least ${min} ${plural(min)}` };
			}
			if (max !== null && count > max) {
				return { enabled: false, reason: `Select at most ${max} ${plural(max)}` };
			}
			if (kinds.length > 0 && !kinds.some((kind) => ctx.kinds.has(kind))) {
				return { enabled: false, reason: `Select at least one ${kindList(kinds)}` };
			}
			return { enabled: true };
		}
	};
}
