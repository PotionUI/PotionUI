import {
	buildHistoryToolContext,
	buildLibraryToolContext,
	groupToolsFor,
	type MediaTool,
	type MediaToolContext,
	type MediaToolEntryStatus,
	type MediaToolGroup,
	type ToolRunExtra,
	type ToolRunHost
} from '$lib/tools/tools';
import type { GenerationFile, GenerationHistoryItem } from '$lib/types/history';
import type { LibraryItem } from '$lib/services/api/library';
import { isStackEntry, isLooseCell } from '$lib/generation/compare/view/historyGrid';

export interface EntryMenuCollection {
	id: string;
	name: string;
}

export interface EntryMenuModel {
	ctx: MediaToolContext;
	groups: MediaToolGroup[];
	selectionCount: number;
	rating: number;
	collections: EntryMenuCollection[];
	title: string;
	subtitle: string;
}

export interface EntryMenuController<Entry, File> {
	model: (entry: Entry, file?: File | null) => EntryMenuModel;
	pick: (
		entry: Entry,
		tool: MediaTool,
		ctx: MediaToolContext,
		extra?: ToolRunExtra
	) => void | Promise<void>;
}

export function entryStatus(generation: GenerationHistoryItem): MediaToolEntryStatus {
	if (generation.status === 'pending' || generation.status === 'running') return 'running';
	if (generation.status === 'failed' || generation.status === 'cancelled') return 'failed';
	return 'completed';
}

export function narrowToFile(ctx: MediaToolContext, fileIndex: number): MediaToolContext {
	const files = ctx.files.filter((entry) => entry.index === fileIndex);
	const items = ctx.items.filter((item) => item.paramIndex === fileIndex);
	return { ...ctx, files, items, kinds: new Set(items.map((item) => item.kind)) };
}

export function buildHistoryEntryContext(
	generation: GenerationHistoryItem,
	file: GenerationFile | null | undefined,
	collectionId: string | null
): MediaToolContext {
	const base = buildHistoryToolContext([generation], [generation.id], collectionId);
	const stack = isStackEntry(generation);
	const fileIndex = file ? generation.files.indexOf(file) : -1;
	const ctx = !stack && fileIndex >= 0 ? narrowToFile(base, fileIndex) : base;
	const error = generation.error_user_message || generation.error_message || null;
	return {
		...ctx,
		generations: [generation],
		generationIds: [generation.id],
		entry: {
			status: entryStatus(generation),
			stackCells: stack ? (generation.grid?.cell_count ?? 0) : null,
			looseCell: isLooseCell(generation),
			error
		}
	};
}

export function buildLibraryEntryContext(item: LibraryItem, collectionId: string | null): MediaToolContext {
	const ctx = buildLibraryToolContext([item], collectionId);
	return {
		...ctx,
		entry: { status: 'completed', stackCells: null, looseCell: false, error: null }
	};
}

function hostFor(host: ToolRunHost, entryId: string): ToolRunHost {
	return { ...host, deselect: host.deselect ? (ctx) => host.deselect?.(ctx, entryId) : undefined };
}

function runnableOnly(groups: MediaToolGroup[], enabled: boolean): MediaToolGroup[] {
	if (!enabled) return groups;
	return groups
		.map((group) => ({ ...group, tools: group.tools.filter(({ tool }) => !!tool.run) }))
		.filter((group) => group.tools.length > 0);
}

function dimensions(item: { width?: number | null; height?: number | null }): string {
	return item.width && item.height ? `${item.width}×${item.height}` : '';
}

export interface HistoryEntryMenuOptions {
	generations: () => GenerationHistoryItem[];
	selectedIds: () => string[];
	collectionId?: () => string | null;
	collections?: () => EntryMenuCollection[];
	host: ToolRunHost;
	onTool?: (tool: MediaTool, ctx: MediaToolContext) => void;
}

export function createHistoryEntryMenu(
	options: HistoryEntryMenuOptions
): EntryMenuController<GenerationHistoryItem, GenerationFile> {
	const collectionId = () => options.collectionId?.() ?? null;
	return {
		model(generation, file) {
			const selected = options.selectedIds();
			const acts = selected.length > 1 && selected.includes(generation.id);
			const ctx = acts
				? buildHistoryToolContext(options.generations(), selected, collectionId())
				: buildHistoryEntryContext(generation, file, collectionId());
			const groups = runnableOnly(
				groupToolsFor(ctx, {
					surface: acts ? 'selection' : 'entry',
					host: options.host
				}),
				!options.onTool
			);
			return {
				ctx,
				groups,
				selectionCount: acts ? ctx.generations.length : 0,
				rating: generation.rating ?? 0,
				collections: options.collections?.() ?? [],
				title: dimensions(file ?? {}) || generation.preset_name || 'Generation',
				subtitle: generation.preset_name ?? ''
			};
		},
		async pick(generation, tool, ctx, extra) {
			if (tool.run) await tool.run(ctx, hostFor(options.host, generation.id), extra);
			else options.onTool?.(tool, ctx);
		}
	};
}

export interface LibraryEntryMenuOptions {
	items: () => LibraryItem[];
	selectedIds: () => string[];
	collectionId?: () => string | null;
	collections?: () => EntryMenuCollection[];
	host: ToolRunHost;
	onTool?: (tool: MediaTool, ctx: MediaToolContext) => void;
}

export function createLibraryEntryMenu(
	options: LibraryEntryMenuOptions
): EntryMenuController<LibraryItem, never> {
	const collectionId = () => options.collectionId?.() ?? null;
	return {
		model(item) {
			const selected = options.selectedIds();
			const acts = selected.length > 1 && selected.includes(item.id);
			const ctx = acts
				? buildLibraryToolContext(
						options.items().filter((candidate) => selected.includes(candidate.id)),
						collectionId()
					)
				: buildLibraryEntryContext(item, collectionId());
			const groups = runnableOnly(
				groupToolsFor(ctx, {
					surface: acts ? 'selection' : 'entry',
					host: options.host
				}),
				!options.onTool
			);
			return {
				ctx,
				groups,
				selectionCount: acts ? ctx.items.length : 0,
				rating: 0,
				collections: options.collections?.() ?? [],
				title: dimensions(item) || item.original_filename || item.filename,
				subtitle: item.original_filename ?? item.filename
			};
		},
		async pick(item, tool, ctx, extra) {
			if (tool.run) await tool.run(ctx, hostFor(options.host, item.id), extra);
			else options.onTool?.(tool, ctx);
		}
	};
}
