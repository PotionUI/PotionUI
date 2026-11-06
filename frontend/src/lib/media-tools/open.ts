import { mount, unmount, type Component } from 'svelte';
import { toasts } from '$lib/stores/toast';
import MediaEditors from '$lib/media/editors/MediaEditors.svelte';
import MediaToolDialog from './MediaToolDialog.svelte';
import type { AnnounceOptions, AnnounceSetting } from './announce';
import ToolHost from '$lib/components/tools/ToolHost.svelte';
import {
	isSurfaceEditorKind,
	type MediaEditorKind,
	type MediaEditorResult
} from '$lib/media/editors/types';
import type { MediaTool, MediaToolContext, MediaKind } from '$lib/tools/tools';
import { isRunnable, loadMediaToolCatalog, toolAvailability, toolById } from './catalog';
import { isEditableKind, normalizeSources, resolveToolInput } from './inputs';
import type { MediaToolItem, MediaToolSource, ToolDTO, ToolScope } from './types';

export type MediaToolRoute = 'editor' | 'dialog' | 'component';

export interface MediaToolResultInfo {
	toolId: string;
	replaced: boolean;
}

export interface OpenMediaToolOptions {
	toolId: string;
	items: readonly MediaToolSource[];
	scope: ToolScope;
	onResult?: (items: MediaToolItem[], info: MediaToolResultInfo) => void | Promise<void>;
	onClose?: () => void;
	context?: MediaToolContext;
	announce?: false | AnnounceOptions;
}

export function routeFor(tool: ToolDTO): MediaToolRoute {
	if (!isRunnable(tool)) return 'component';
	return isSurfaceEditorKind(tool.editor) ? 'editor' : 'dialog';
}

function mountHost(component: Component<any>, props: Record<string, unknown>): () => void {
	const target = document.body.appendChild(document.createElement('div'));
	let instance: Record<string, unknown> | null = mount(component, { target, props, intro: true });
	return () => {
		if (!instance) return;
		const current = instance;
		instance = null;
		void unmount(current, { outro: true }).finally(() => target.remove());
	};
}

function toEditorSource(source: MediaToolSource) {
	return {
		url: source.url,
		kind: source.kind as 'image' | 'video' | 'audio',
		fileName: source.filename,
		key: source.id,
		itemId: source.itemId ?? null,
		generationFileId: source.generationFileId ?? null,
		storedPath: source.storedPath ?? null,
		width: source.width ?? null,
		height: source.height ?? null,
		durationSeconds: source.durationSeconds ?? null,
		fps: source.fps ?? null
	};
}

function toToolItem(source: MediaToolSource) {
	return {
		id: source.id,
		kind: source.kind as MediaKind,
		url: source.url,
		filename: source.filename,
		width: source.width ?? undefined,
		height: source.height ?? undefined,
		generationId: source.generationId,
		paramIndex: source.paramIndex
	};
}

function contextFor(scope: ToolScope, sources: readonly MediaToolSource[]): MediaToolContext {
	const items = sources.map(toToolItem);
	return {
		scope,
		items,
		kinds: new Set(items.map((item) => item.kind)),
		collectionId: null,
		generations: [],
		generationIds: [...new Set(items.flatMap((item) => (item.generationId ? [item.generationId] : [])))],
		files: []
	};
}

function hostTool(tool: ToolDTO, scope: ToolScope): MediaTool {
	return {
		id: tool.id,
		label: tool.label,
		icon: tool.icon,
		category: 'plugins',
		source: tool.source === 'core' ? 'core' : 'plugin',
		scopes: [scope],
		componentRef: tool.component,
		applies: () => ({ enabled: true })
	};
}

function editorResultItems(result: MediaEditorResult): { items: MediaToolItem[]; replaced: boolean } | null {
	if (result.type === 'item') return { items: [result.item], replaced: result.replaced };
	if (result.type === 'items') return { items: result.items, replaced: false };
	return null;
}

export async function openMediaTool(options: OpenMediaToolOptions): Promise<boolean> {
	const { toolId, scope, onResult, onClose } = options;
	const announce: AnnounceSetting =
		scope === 'field' ? false : options.announce === false ? 'failures' : (options.announce ?? true);
	await loadMediaToolCatalog();
	const tool = toolById(toolId);
	if (!tool) {
		toasts.error('This tool is no longer available.');
		return false;
	}

	const route = routeFor(tool);
	const sources = normalizeSources(
		route === 'component' ? options.items : options.items.filter((item) => isEditableKind(item.kind)),
		scope
	);

	if (route === 'component' && !tool.component) {
		toasts.error('This tool has no surface to open.');
		return false;
	}

	if (route === 'component') {
		const context = options.context ?? contextFor(scope, sources);
		let close: () => void = () => undefined;
		close = mountHost(ToolHost, {
			tool: hostTool(tool, scope),
			context,
			onClose: () => {
				close();
				onClose?.();
			},
			onDone: () => {
				close();
				void onResult?.([], { toolId: tool.id, replaced: false });
			}
		});
		return true;
	}

	const availability = toolAvailability(tool, sources);
	if (!availability.enabled) {
		toasts.error(availability.reason ?? 'This tool cannot be used on this selection.');
		return false;
	}

	const finish = (items: MediaToolItem[], replaced: boolean) =>
		onResult?.(items, { toolId: tool.id, replaced });

	if (route === 'editor') {
		if (sources.length !== 1) {
			toasts.error('Select one item to use this tool.');
			return false;
		}
		const resolved = await resolveToolInput(sources[0]);
		if (!resolved.ref) {
			toasts.error(resolved.reason ?? 'This media could not be prepared for editing.');
			return false;
		}
		const source = {
			...sources[0],
			itemId: 'item_id' in resolved.ref ? resolved.ref.item_id : null,
			generationFileId: 'generation_file_id' in resolved.ref ? resolved.ref.generation_file_id : null
		};
		let close: () => void = () => undefined;
		close = mountHost(MediaEditors, {
			request: { kind: tool.editor as MediaEditorKind, source: toEditorSource(source), itemIndex: null },
			detach: scope !== 'field',
			announce,
			scope,
			onClose: () => {
				close();
				onClose?.();
			},
			onResult: (result: MediaEditorResult) => {
				const mapped = editorResultItems(result);
				return mapped ? finish(mapped.items, mapped.replaced) : undefined;
			}
		});
		return true;
	}

	let close: () => void = () => undefined;
	close = mountHost(MediaToolDialog, {
		tool,
		items: sources,
		scope,
		detach: scope !== 'field',
		announce,
		onClose: () => {
			close();
			onClose?.();
		},
		onResult: (result: { items: MediaToolItem[]; replaced: boolean }) => finish(result.items, result.replaced)
	});
	return true;
}
