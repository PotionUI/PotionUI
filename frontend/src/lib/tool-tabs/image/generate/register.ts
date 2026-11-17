import { editorToolIcons } from '$lib/utils/iconGlyphs';
import { CORE_OWNER } from '$lib/registries/registry';
import GenerateOptions from '$lib/components/tool-tabs/generate/GenerateOptions.svelte';
import GenerateSettings from '$lib/components/tool-tabs/generate/GenerateSettings.svelte';
import { railGroups, railTools } from '$lib/editors/railTools';
import { stepKinds } from '$lib/editors/stepKinds';
import type { RailToolEntry } from '$lib/editors/railTools';
import { tasks, type TaskEntry } from '$lib/editors/tasks';
import type { ComponentRef } from '$lib/editors/types';
import type { EditorTaskInfo } from '$lib/services/api/editorTasks';
import { GENERATE_TOOL, generateStepKind } from './generateStep';

export const GENERATE_GROUP = 'generate';
export const REMOVE_PROMPT = 'Remove the marked object. Fill the area so it matches its surroundings.';

type TaskSeed = Pick<TaskEntry, 'id' | 'label' | 'icon' | 'region' | 'prompt' | 'fallback'>;

export const CORE_TASKS: readonly TaskSeed[] = [
	{ id: 'edit_region', label: 'Edit a region', icon: editorToolIcons.edit_region, region: 'optional', prompt: 'required' },
	{ id: 'remove_object', label: 'Remove an object', icon: editorToolIcons.remove_object, region: 'required', prompt: 'none', fallback: { task: 'edit_region', prompt: REMOVE_PROMPT } }
];

export function settingsOf(tool: RailToolEntry | undefined): ComponentRef | undefined {
	if (!tool) return undefined;
	return tool.settings ?? (tool.task ? { component: GenerateSettings } : undefined);
}

const dynamic = new Map<string, () => void>();
let registered = false;

function registerTask(seed: TaskSeed, order: number, owner?: string): () => void {
	const entry: TaskEntry = { id: seed.id, editor: 'image', label: seed.label, icon: seed.icon, tool: seed.id, region: seed.region, prompt: seed.prompt, stepKind: GENERATE_TOOL };
	if (seed.fallback) entry.fallback = seed.fallback;
	const drops: Array<() => void> = [];
	if (!tasks.get('image', seed.id)) drops.push(tasks.register(entry, owner));
	const taken = railTools.list('image').some((tool) => tool.task === seed.id);
	if (!taken) {
		drops.push(
			railTools.register(
				{ id: seed.id, editor: 'image', group: GENERATE_GROUP, label: seed.label, icon: seed.icon, order, options: { component: GenerateOptions }, settings: { component: GenerateSettings }, task: seed.id, ai: true },
				owner
			)
		);
	}
	return () => drops.forEach((drop) => drop());
}

export function ensureGenerateEditor(): void {
	if (registered) return;
	registered = true;
	railGroups.register({ id: GENERATE_GROUP, editor: 'image', label: 'Generate', order: 50, fold: 8 });
	stepKinds.register(generateStepKind);
	CORE_TASKS.forEach((seed, index) => registerTask(seed, (index + 1) * 10, CORE_OWNER));
}

export function syncServerTasks(infos: readonly EditorTaskInfo[]): void {
	ensureGenerateEditor();
	const wanted = new Map(infos.map((info) => [info.id, info]));
	for (const [id, drop] of dynamic) {
		if (wanted.has(id)) continue;
		drop();
		dynamic.delete(id);
	}
	let order = 100;
	for (const info of infos) {
		order += 10;
		if (dynamic.has(info.id)) continue;
		dynamic.set(info.id, registerTask({ id: info.id, label: info.label, icon: info.icon, region: info.region, prompt: info.prompt, fallback: info.fallback ?? undefined }, order, CORE_OWNER));
	}
}
