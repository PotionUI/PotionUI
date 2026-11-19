import { afterEach, describe, expect, it } from 'vitest';
import { pluginOwner } from '$lib/editors/registry';
import { railTools } from '$lib/editors/railTools';
import { tasks } from '$lib/editors/tasks';
import type { EditorTaskInfo } from '$lib/services/api/editorTasks';
import { GENERATE_GROUP, syncServerTasks } from './register';

const INFO: EditorTaskInfo = { id: 'acme.relight', label: 'Relight', icon: 'extension', region: 'optional', prompt: 'optional', fallback: null } as EditorTaskInfo;
const drops: Array<() => void> = [];

afterEach(() => {
	syncServerTasks([]);
	drops.splice(0).forEach((drop) => drop());
});

function declareFromManifest() {
	drops.push(
		tasks.register({ id: INFO.id, editor: 'image', label: INFO.label, icon: 'extension', tool: '', region: 'optional', prompt: 'optional', stepKind: 'generate' }, pluginOwner('acme'))
	);
}

describe('syncServerTasks', () => {
	it('gives a task a plugin manifest already declared the same rail tool as a core task', () => {
		declareFromManifest();
		syncServerTasks([INFO]);
		const tool = railTools.get('image', 'acme.relight');
		expect(tool?.group).toBe(GENERATE_GROUP);
		expect(tool?.task).toBe('acme.relight');
		expect(tool?.options).toBeTruthy();
	});

	it('removes that rail tool when the server stops listing the task, leaving the manifest task', () => {
		declareFromManifest();
		syncServerTasks([INFO]);
		syncServerTasks([]);
		expect(railTools.get('image', 'acme.relight')).toBeUndefined();
		expect(tasks.get('image', 'acme.relight')).toBeDefined();
	});

	it('gives an internal task no rail tool and drops it from the registry listing', () => {
		syncServerTasks([{ ...INFO, id: 'find_subject', internal: true }, { ...INFO, id: 'plain_task' }]);
		expect(railTools.get('image', 'find_subject')).toBeUndefined();
		expect(railTools.get('image', 'plain_task')).toBeDefined();
	});

	it('puts Replace the background in the Generate group right after Remove an object, with its own panel', () => {
		syncServerTasks([]);
		const group = railTools.list('image').filter((tool) => tool.group === GENERATE_GROUP).sort((a, b) => (a.order ?? 0) - (b.order ?? 0)).map((tool) => tool.id);
		expect(group.indexOf('replace_background')).toBe(group.indexOf('remove_object') + 1);
		const tool = railTools.get('image', 'replace_background');
		expect(tool?.task).toBe('replace_background');
		expect(tool?.label).toBe('Replace the background');
		const panel = (id: string) => (railTools.get('image', id)?.options as { component?: unknown } | undefined)?.component;
		expect(panel('replace_background')).toBeTruthy();
		expect(panel('replace_background')).not.toBe(panel('remove_object'));
		expect(tasks.get('image', 'replace_background')).toMatchObject({ region: 'required', prompt: 'required' });
	});

	it('still registers both the task and its rail tool for a task no manifest declared', () => {
		syncServerTasks([{ ...INFO, id: 'plain_task' }]);
		expect(tasks.get('image', 'plain_task')).toBeDefined();
		expect(railTools.get('image', 'plain_task')?.task).toBe('plain_task');
	});
});
