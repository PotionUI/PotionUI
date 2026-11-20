import { get, writable, type Readable } from 'svelte/store';
import { logger, getErrorMessage } from '$lib/utils/logger';
import { listMediaTools } from '$lib/services/api/mediaTools';
import type { MediaToolSource, ToolDTO, ToolScope } from './types';

const catalog = writable<ToolDTO[]>([]);
export const mediaToolCatalog: Readable<ToolDTO[]> = { subscribe: catalog.subscribe };

export const mediaToolCatalogRevision = writable(0);

export function isMediaToolCatalogLoaded(): boolean {
	return loaded;
}

let loaded = false;
let loading: Promise<ToolDTO[]> | null = null;
let unwatch: (() => void) | null = null;
let warnedMissingRunnable = false;

function publish(tools: ToolDTO[]) {
	catalog.set(tools);
	loaded = true;
	mediaToolCatalogRevision.update((tick) => tick + 1);
}

async function fetchCatalog(): Promise<ToolDTO[]> {
	try {
		const tools = await listMediaTools();
		publish(tools);
		return tools;
	} catch (error) {
		logger.error('Failed to load the media tool catalog:', getErrorMessage(error));
		mediaToolCatalogRevision.update((tick) => tick + 1);
		return get(catalog);
	} finally {
		loading = null;
	}
}

async function watchPluginChanges() {
	if (unwatch) return;
	try {
		const { extensionRegistrations } = await import('$lib/plugin-api/extensionRefresh');
		let first = true;
		unwatch = extensionRegistrations.subscribe(() => {
			if (first) {
				first = false;
				return;
			}
			void refreshMediaToolCatalog();
		});
	} catch (error) {
		logger.error('Could not watch plugin changes for the tool catalog:', getErrorMessage(error));
	}
}

export function loadMediaToolCatalog(): Promise<ToolDTO[]> {
	if (loaded) return Promise.resolve(get(catalog));
	if (!loading) {
		loading = fetchCatalog().then((tools) => {
			if (loaded) void watchPluginChanges();
			return tools;
		});
	}
	return loading;
}

export function refreshMediaToolCatalog(): Promise<ToolDTO[]> {
	if (!loading) loading = fetchCatalog();
	return loading;
}

export function currentCatalog(): ToolDTO[] {
	return get(catalog);
}

export function resetMediaToolCatalog(tools: ToolDTO[] = []) {
	unwatch?.();
	unwatch = null;
	loading = null;
	loaded = false;
	warnedMissingRunnable = false;
	catalog.set(tools);
	mediaToolCatalogRevision.set(0);
}

export function toolById(id: string, tools: readonly ToolDTO[] = get(catalog)): ToolDTO | null {
	return tools.find((tool) => tool.id === id) ?? null;
}

export function toolsFor(
	kinds: Iterable<string>,
	scope: ToolScope,
	tools: readonly ToolDTO[] = get(catalog)
): ToolDTO[] {
	const wanted = [...new Set(kinds)];
	if (wanted.length === 0) return [];
	return tools.filter(
		(tool) =>
			(tool.scopes.length === 0 || tool.scopes.includes(scope)) &&
			wanted.every((kind) => (tool.accepts as string[]).includes(kind))
	);
}

export function rememberToolParams(toolId: string, params: Record<string, unknown>) {
	catalog.update((tools) =>
		tools.map((tool) => (tool.id === toolId ? { ...tool, last_params: { ...params } } : tool))
	);
}

export function isRunnable(tool: ToolDTO): boolean {
	if (typeof tool.runnable !== 'boolean') {
		if (!warnedMissingRunnable) {
			warnedMissingRunnable = true;
			logger.warn('The media tool catalog has no runnable flag, so no tool can be run:', tool.id);
		}
		return false;
	}
	return tool.runnable;
}

export const MAX_RUN_INPUTS = 50;

export function selectionBounds(tool: ToolDTO): { min: number; max: number } {
	const fallbackMin = tool.cardinality === 'many_to_one' ? 2 : 1;
	return {
		min: Math.max(1, tool.selection?.min ?? fallbackMin),
		max: tool.selection?.max ?? Number.POSITIVE_INFINITY
	};
}

function lacksAlpha(source: MediaToolSource): boolean {
	if (typeof source.hasAlpha === 'boolean') return !source.hasAlpha;
	const name = (source.filename || source.url || '').split('?')[0].toLowerCase();
	return /\.(jpe?g|bmp)$/.test(name);
}

export interface ToolAvailability {
	enabled: boolean;
	reason?: string;
}

export function toolAvailability(
	tool: ToolDTO,
	sources: readonly MediaToolSource[]
): ToolAvailability {
	if (sources.length === 0) return { enabled: false, reason: 'Select an item' };
	if (sources.length > MAX_RUN_INPUTS) {
		return { enabled: false, reason: `Select up to ${MAX_RUN_INPUTS} items` };
	}

	const { min, max } = selectionBounds(tool);
	if (sources.length < min || sources.length > max) {
		const range = Number.isFinite(max) ? (min === max ? `${min}` : `${min} to ${max}`) : `${min} or more`;
		return { enabled: false, reason: `Select ${range} items` };
	}

	const rejected = sources.find((source) => !(tool.accepts as string[]).includes(source.kind));
	if (rejected) return { enabled: false, reason: `Not available for ${rejected.kind}` };

	const { requires } = tool;
	if (requires.has_alpha && sources.some(lacksAlpha)) {
		return { enabled: false, reason: 'Needs a transparent image' };
	}
	if (requires.has_audio && sources.some((source) => source.hasAudio === false)) {
		return { enabled: false, reason: 'Needs a clip with sound' };
	}
	if (
		typeof requires.min_duration === 'number' &&
		sources.some(
			(source) =>
				typeof source.durationSeconds === 'number' &&
				source.durationSeconds > 0 &&
				source.durationSeconds < requires.min_duration!
		)
	) {
		return { enabled: false, reason: `Needs at least ${requires.min_duration}s of media` };
	}
	return { enabled: true };
}
