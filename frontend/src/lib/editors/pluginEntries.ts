import { resolvePluginComponent } from '$lib/plugin-api/componentResolver';
import { logger } from '$lib/utils/logger';
import { pluginOwner } from './registry';
import { reportFailure } from './guard';
import { overlays, type CanvasOverlayEntry } from './overlays';
import { railGroups, railTools, type RailGroupEntry, type RailToolEntry } from './railTools';
import { stepKinds, type StepKind } from './stepKinds';
import { tasks, type TaskEntry } from './tasks';
import type { ComponentRef, EditorKind } from './types';

export interface EditorStepSnapshotEntry {
	plugin_id: string;
	id: string;
	editor: EditorKind;
	module: string;
	label: string;
	icon?: string | null;
	lane?: string | null;
}

export interface EditorToolSnapshotEntry {
	plugin_id: string;
	id: string;
	editor: EditorKind;
	group: string;
	label: string;
	icon?: string | null;
	order?: number | null;
	keys?: string | null;
	options?: string | null;
	bar?: string | null;
	overlay?: string | null;
	task?: string | null;
}

export interface EditorGroupSnapshotEntry {
	plugin_id: string;
	id: string;
	editor: EditorKind;
	label: string;
	order: number;
}

export interface EditorOverlaySnapshotEntry {
	plugin_id: string;
	id: string;
	editor: EditorKind;
	component: string;
	cursor?: 'crosshair' | 'brush' | 'default' | null;
	produces?: 'selection' | null;
	label?: string | null;
}

export interface EditorTaskSnapshotEntry {
	plugin_id: string;
	id: string;
	label: string;
	editors: EditorKind[];
	region: 'required' | 'optional' | 'none';
	prompt: 'required' | 'optional' | 'none';
	tool?: string | null;
	produces?: string | null;
	icon?: string | null;
	step_kind?: string | null;
	fallback?: { task: string; prompt: string } | null;
}

export interface EditorSnapshot {
	steps: EditorStepSnapshotEntry[];
	tools: EditorToolSnapshotEntry[];
	groups: EditorGroupSnapshotEntry[];
	overlays: EditorOverlaySnapshotEntry[];
	tasks: EditorTaskSnapshotEntry[];
}

export interface EditorRegistrationDescriptor {
	id: string;
	retain: true;
	register: () => void;
	dispose: () => void;
}

export function readEditorSnapshot(data: unknown): EditorSnapshot {
	const source = (data ?? {}) as Record<string, unknown>;
	const list = <T>(value: unknown): T[] => (Array.isArray(value) ? (value as T[]) : []);
	return {
		steps: list(source.editor_steps),
		tools: list(source.editor_tools),
		groups: list(source.editor_groups),
		overlays: list(source.editor_overlays),
		tasks: list(source.editor_tasks)
	};
}

export function emptyEditorSnapshot(): EditorSnapshot {
	return { steps: [], tools: [], groups: [], overlays: [], tasks: [] };
}

function componentRef(pluginId: string, path: string | null | undefined): ComponentRef | undefined {
	return path ? { pluginId, path } : undefined;
}

function hiddenStep(tool: string) {
	return [{ tool, params: {}, hidden: true }];
}

async function importPluginModule(pluginId: string, path: string): Promise<Partial<StepKind>> {
	const component = await resolvePluginComponent(pluginId, path);
	const declared = component?.dist;
	if (!declared || typeof declared !== 'object') throw new Error(`${path} could not be loaded`);
	return declared as Partial<StepKind>;
}

export function declaredStepKind(
	entry: EditorStepSnapshotEntry,
	load = importPluginModule
): { kind: StepKind; start: (done: (next: StepKind | null) => void) => void } {
	let loaded: Partial<StepKind> | null = null;
	let failed = false;
	const key = `${entry.editor}:${entry.id}`;

	const kind: StepKind = {
		id: entry.id,
		editor: entry.editor,
		label: entry.label,
		icon: entry.icon || 'extension',
		...(entry.lane ? { lane: entry.lane } : {}),
		summary: (params, ctx) => loaded?.summary?.(params, ctx) ?? ctx.step.summary,
		outputSize: (params, ctx) => (loaded?.outputSize ? loaded.outputSize(params, ctx) : ctx.input),
		payload: (params, ctx) => (loaded?.payload ? loaded.payload(params, ctx) : hiddenStep(ctx.step.tool)),
		preview: (frame, params, ctx, signal) => (loaded?.preview ? loaded.preview(frame, params, ctx, signal) : Promise.resolve(frame)),
		state: (step, ctx) => {
			if (failed) return { kind: 'skipped', tone: 'danger', label: 'Could not load' };
			if (!loaded) return { kind: 'busy', label: 'Loading' };
			return loaded.state?.(step, ctx) ?? { kind: 'done' };
		},
		onAction: (step, action, ctx) => loaded?.onAction?.(step, action, ctx)
	};

	return {
		kind,
		start(done) {
			load(entry.plugin_id, entry.module).then(
				(module) => {
					loaded = module;
					const next: StepKind = { ...kind };
					if (module.version !== undefined) next.version = module.version;
					if (module.migrate) next.migrate = module.migrate;
					if (module.reopenTool) next.reopenTool = module.reopenTool;
					done(next);
				},
				(error) => {
					failed = true;
					reportFailure(key, 'load', error);
					done(null);
				}
			);
		}
	};
}

function describe<T extends { plugin_id: string; id: string }>(
	prefix: string,
	entry: T,
	revision: string,
	register: () => () => void
): EditorRegistrationDescriptor {
	let dispose: (() => void) | null = null;
	return {
		retain: true,
		id: `editor_${prefix}|${pluginOwner(entry.plugin_id)}|${JSON.stringify(entry)}|${revision}`,
		register: () => {
			dispose?.();
			dispose = register();
		},
		dispose: () => {
			dispose?.();
			dispose = null;
		}
	};
}

interface DeclaredHolder {
	current: StepKind;
	dispose: () => void;
	active: boolean;
}

const declaredHolders = new Map<string, DeclaredHolder>();

function describeStep(entry: EditorStepSnapshotEntry, revision: string): EditorRegistrationDescriptor {
	const owner = pluginOwner(entry.plugin_id);
	const id = `editor_step|${owner}|${JSON.stringify(entry)}|${revision}`;
	return {
		id,
		retain: true,
		register: () => {
			let holder = declaredHolders.get(id);
			if (!holder) {
				const declared = declaredStepKind(entry);
				const created: DeclaredHolder = { current: declared.kind, dispose: () => undefined, active: true };
				holder = created;
				declaredHolders.set(id, created);
				created.dispose = stepKinds.register(created.current, owner);
				declared.start((next) => {
					if (!created.active) return;
					if (!next) {
						stepKinds.touch();
						return;
					}
					created.current = next;
					created.dispose();
					created.dispose = stepKinds.register(next, owner);
				});
				return;
			}
			holder.dispose();
			holder.dispose = stepKinds.register(holder.current, owner);
		},
		dispose: () => {
			const holder = declaredHolders.get(id);
			if (!holder) return;
			holder.active = false;
			holder.dispose();
			declaredHolders.delete(id);
		}
	};
}

export function describeEditorEntries(snapshot: EditorSnapshot, revisionOf: (pluginId: string) => string): EditorRegistrationDescriptor[] {
	const out: EditorRegistrationDescriptor[] = [];
	const accept = (entry: { plugin_id?: string; id?: string }) => !!entry?.plugin_id && !!entry.id;

	for (const entry of snapshot.groups.filter(accept)) {
		out.push(
			describe('group', entry, revisionOf(entry.plugin_id), () =>
				railGroups.register({ id: entry.id, editor: entry.editor, label: entry.label, order: entry.order }, pluginOwner(entry.plugin_id))
			)
		);
	}
	for (const entry of snapshot.steps.filter(accept)) out.push(describeStep(entry, revisionOf(entry.plugin_id)));
	for (const entry of snapshot.tools.filter(accept)) {
		out.push(
			describe('tool', entry, revisionOf(entry.plugin_id), () => {
				const tool: RailToolEntry = {
					id: entry.id,
					editor: entry.editor,
					group: entry.group,
					label: entry.label,
					icon: entry.icon || 'extension'
				};
				if (typeof entry.order === 'number') tool.order = entry.order;
				if (entry.keys) tool.keys = entry.keys;
				if (entry.overlay) tool.overlay = entry.overlay;
				if (entry.task) tool.task = entry.task;
				const options = componentRef(entry.plugin_id, entry.options);
				if (options) tool.options = options;
				const bar = componentRef(entry.plugin_id, entry.bar);
				if (bar) tool.bar = bar;
				return railTools.register(tool, pluginOwner(entry.plugin_id));
			})
		);
	}
	for (const entry of snapshot.overlays.filter(accept)) {
		out.push(
			describe('overlay', entry, revisionOf(entry.plugin_id), () => {
				const overlay: CanvasOverlayEntry = {
					id: entry.id,
					editor: entry.editor,
					component: { pluginId: entry.plugin_id, path: entry.component }
				};
				if (entry.cursor) overlay.cursor = entry.cursor;
				if (entry.produces) overlay.produces = entry.produces;
				if (entry.label) overlay.label = entry.label;
				return overlays.register(overlay, pluginOwner(entry.plugin_id));
			})
		);
	}
	for (const entry of snapshot.tasks.filter(accept)) {
		out.push(
			describe('task', entry, revisionOf(entry.plugin_id), () => {
				const disposers = (entry.editors?.length ? entry.editors : (['image'] as EditorKind[])).map((editor) => {
					const task: TaskEntry = {
						id: entry.id,
						editor,
						label: entry.label,
						icon: entry.icon || 'extension',
						tool: entry.tool ?? '',
						region: entry.region,
						prompt: entry.prompt,
						stepKind: entry.step_kind || 'generate'
					};
					if (entry.fallback) task.fallback = entry.fallback;
					return tasks.register(task, pluginOwner(entry.plugin_id));
				});
				return () => disposers.forEach((dispose) => dispose());
			})
		);
	}
	return out;
}

export interface PluginEditorRegistration {
	pluginId: string;
}

export interface PluginEditorLookup<T> {
	get(editor: EditorKind, id: string): T | undefined;
	list(editor: EditorKind): T[];
}

export interface PluginEditorsApi {
	registerStepKind(entry: StepKind<any>, options: PluginEditorRegistration): () => void;
	registerRailGroup(entry: RailGroupEntry, options: PluginEditorRegistration): () => void;
	registerRailTool(entry: RailToolEntry, options: PluginEditorRegistration): () => void;
	registerOverlay(entry: CanvasOverlayEntry, options: PluginEditorRegistration): () => void;
	registerTask(entry: TaskEntry, options: PluginEditorRegistration): () => void;
	stepKinds: PluginEditorLookup<StepKind<any>>;
	railGroups: PluginEditorLookup<RailGroupEntry>;
	railTools: PluginEditorLookup<RailToolEntry>;
	overlays: PluginEditorLookup<CanvasOverlayEntry>;
	tasks: PluginEditorLookup<TaskEntry>;
}

interface RuntimeRecord {
	pluginId: string;
	register: () => () => void;
	dispose: (() => void) | null;
}

const runtimeRecords = new Set<RuntimeRecord>();

export function reconcileRuntimeEditorEntries(enabled: ReadonlySet<string>): void {
	for (const record of runtimeRecords) {
		const live = record.dispose !== null;
		if (!enabled.has(record.pluginId) && live) {
			record.dispose?.();
			record.dispose = null;
		} else if (enabled.has(record.pluginId) && !live) {
			record.dispose = record.register();
		}
	}
}

function owned<T extends { id: string; editor: EditorKind }>(
	registry: { register(entry: T, owner?: string): () => void },
	entry: T,
	options: PluginEditorRegistration | undefined
): () => void {
	const pluginId = options?.pluginId;
	if (typeof pluginId !== 'string' || !pluginId) {
		logger.warn(`Editor registration for "${entry?.id}" needs a pluginId`);
		return () => undefined;
	}
	const record: RuntimeRecord = { pluginId, register: () => registry.register(entry, pluginOwner(pluginId)), dispose: null };
	record.dispose = record.register();
	runtimeRecords.add(record);
	return () => {
		runtimeRecords.delete(record);
		record.dispose?.();
		record.dispose = null;
	};
}

function lookup<T extends { id: string; editor: EditorKind }>(registry: PluginEditorLookup<T>): PluginEditorLookup<T> {
	return { get: (editor, id) => registry.get(editor, id), list: (editor) => registry.list(editor) };
}

export function createHostEditorsApi(): PluginEditorsApi {
	return {
		registerStepKind: (entry, options) => owned(stepKinds, entry, options),
		registerRailGroup: (entry, options) => owned(railGroups, entry, options),
		registerRailTool: (entry, options) => owned(railTools, entry, options),
		registerOverlay: (entry, options) => owned(overlays, entry, options),
		registerTask: (entry, options) => owned(tasks, entry, options),
		stepKinds: lookup(stepKinds),
		railGroups: lookup(railGroups),
		railTools: lookup(railTools),
		overlays: lookup(overlays),
		tasks: lookup(tasks)
	};
}
