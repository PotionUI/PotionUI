import type { Component } from 'svelte';
import { editorToolIcons } from '$lib/utils/iconGlyphs';
import { CORE_OWNER } from '$lib/registries/registry';
import GenerateOptions from '$lib/components/tool-tabs/generate/GenerateOptions.svelte';
import RangeOptions from '$lib/components/tool-tabs/video/options/RangeOptions.svelte';
import ReplaceBackgroundOptions from '$lib/components/tool-tabs/generate/ReplaceBackgroundOptions.svelte';
import GenerateSettings from '$lib/components/tool-tabs/generate/GenerateSettings.svelte';
import { railGroups, railTools } from '$lib/editors/railTools';
import { stepKinds } from '$lib/editors/stepKinds';
import type { RailToolEntry } from '$lib/editors/railTools';
import { tasks, type TaskEntry } from '$lib/editors/tasks';
import type { ComponentRef } from '$lib/editors/types';
import type { EditorTaskInfo } from '$lib/services/api/editorTasks';
import { taskToolActions } from '../canvasActions';
import { GENERATE_TOOL, generateStepKind } from './generateStep';
import { REPLACE_BACKGROUND_PROMPT, replaceBackgroundStepKind } from './backgroundStep';

export const GENERATE_GROUP = 'generate';
export { REPLACE_BACKGROUND_PROMPT };
export const REMOVE_PROMPT = 'Remove the marked object. Fill the area so it matches its surroundings.';

type TaskSeed = Pick<TaskEntry, 'id' | 'label' | 'icon' | 'region' | 'prompt' | 'fallback' | 'range' | 'result'>;

export const CORE_TASKS: readonly TaskSeed[] = [
	{ id: 'edit_region', label: 'Edit a region', icon: editorToolIcons.edit_region, region: 'optional', prompt: 'required' },
	{ id: 'remove_object', label: 'Remove an object', icon: editorToolIcons.remove_object, region: 'required', prompt: 'none', fallback: { task: 'edit_region', prompt: REMOVE_PROMPT } },
	{ id: 'replace_background', label: 'Replace the background', icon: editorToolIcons.replace_background, region: 'required', prompt: 'required', fallback: { task: 'edit_region', prompt: REPLACE_BACKGROUND_PROMPT } }
];

const CORE_OPTIONS: Readonly<Record<string, Component<any>>> = { replace_background: ReplaceBackgroundOptions as unknown as Component<any> };

export function settingsOf(tool: RailToolEntry | undefined): ComponentRef | undefined {
	if (!tool) return undefined;
	return tool.settings ?? (tool.task ? { component: GenerateSettings } : undefined);
}

const dynamic = new Map<string, () => void>();
let registered = false;

function registerTask(seed: TaskSeed, order: number, owner?: string, editor: 'image' | 'video' = 'image', options?: Component<any>): () => void {
	const entry: TaskEntry = { id: seed.id, editor, label: seed.label, icon: seed.icon, tool: seed.id, region: seed.region, prompt: seed.prompt, stepKind: GENERATE_TOOL };
	if (seed.range) entry.range = seed.range;
	if (seed.result) entry.result = seed.result;
	if (seed.fallback) entry.fallback = seed.fallback;
	const drops: Array<() => void> = [];
	if (!tasks.get(editor, seed.id)) drops.push(tasks.register(entry, owner));
	const taken = railTools.list(editor).some((tool) => tool.task === seed.id);
	if (!taken) {
		drops.push(
			railTools.register(
				{ id: seed.id, editor, group: GENERATE_GROUP, label: seed.label, icon: seed.icon, order, options: { component: options ?? GenerateOptions }, settings: { component: GenerateSettings }, task: seed.id, ai: true, ...(editor === 'image' ? { canvasActions: taskToolActions } : {}) },
				owner
			)
		);
	}
	return () => drops.forEach((drop) => drop());
}

export function ensureGenerateEditor(): void {
	if (registered) return;
	registered = true;
	railGroups.register({ id: GENERATE_GROUP, editor: 'image', label: 'Generate', order: 50, fold: 7 });
	stepKinds.register(generateStepKind);
	stepKinds.register(replaceBackgroundStepKind);
	CORE_TASKS.forEach((seed, index) => registerTask(seed, (index + 1) * 10, CORE_OWNER, 'image', CORE_OPTIONS[seed.id]));
}

export function syncServerTasks(infos: readonly EditorTaskInfo[]): void {
	ensureGenerateEditor();
	const shown = infos.filter((info) => !info.internal);
	const wanted = new Map(shown.map((info) => [info.id, info]));
	for (const [id, drop] of dynamic) {
		if (wanted.has(id)) continue;
		drop();
		dynamic.delete(id);
	}
	let order = 100;
	for (const info of shown) {
		order += 10;
		if (dynamic.has(info.id)) continue;
		dynamic.set(info.id, registerTask({ id: info.id, label: info.label, icon: info.icon, region: info.region, prompt: info.prompt, fallback: info.fallback ?? undefined }, order, CORE_OWNER));
	}
}

const videoDynamic = new Map<string, () => void>();
let videoGroup = false;

export function videoTaskInfos(infos: readonly EditorTaskInfo[]): EditorTaskInfo[] {
	return infos.filter((info) => (info.editors ?? []).includes('video') && info.offers.some((offer) => offer.qualifies));
}

export function syncVideoTasks(infos: readonly EditorTaskInfo[]): void {
	if (!videoGroup) {
		videoGroup = true;
		railGroups.register({ id: GENERATE_GROUP, editor: 'video', label: 'Generate', order: 10, fold: 7 });
	}
	const shown = videoTaskInfos(infos);
	const wanted = new Map(shown.map((info) => [info.id, info]));
	for (const [id, drop] of videoDynamic) {
		if (wanted.has(id)) continue;
		drop();
		videoDynamic.delete(id);
	}
	let order = 0;
	for (const info of shown) {
		order += 10;
		if (videoDynamic.has(info.id)) continue;
		const seed: TaskSeed = { id: info.id, label: info.label, icon: info.icon, region: info.region, prompt: info.prompt, fallback: info.fallback ?? undefined, range: info.range ?? 'none', result: info.result ?? 'replace' };
		videoDynamic.set(info.id, registerTask(seed, order, CORE_OWNER, 'video', RangeOptions as unknown as Component<any>));
	}
}
