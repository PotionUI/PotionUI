import { afterEach, describe, expect, it } from 'vitest';
import { pluginOwner } from '$lib/editors/registry';
import { railTools } from '$lib/editors/railTools';
import { tasks } from '$lib/editors/tasks';
import type { EditorTaskInfo } from '$lib/services/api/editorTasks';
import { GENERATE_GROUP, syncServerTasks } from './register';
import { isResizingTask } from './upscale';

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

	it('registers upscale with its own options, a note, no region and no prompt', () => {
		syncServerTasks([{ id: 'upscale', label: 'Upscale', icon: 'mode-upscale', region: 'none', prompt: 'none', fallback: null, offers: [], default: null, resizes: true } as EditorTaskInfo]);
		const entry = tasks.get('image', 'upscale');
		expect(entry).toMatchObject({ region: 'none', prompt: 'none', stepKind: 'generate' });
		expect(entry?.note).toContain('whole picture');
		const tool = railTools.get('image', 'upscale');
		expect(tool?.group).toBe(GENERATE_GROUP);
		expect(tool?.options).toBeTruthy();
		expect(tool?.options).not.toBe(railTools.get('image', 'edit_region')?.options);
	});

	it('marks a task the server calls resizing so its step lands in the geometry lane', () => {
		syncServerTasks([{ id: 'super_res', label: 'Super', icon: 'extension', region: 'none', prompt: 'none', fallback: null, offers: [], default: null, resizes: true } as EditorTaskInfo]);
		expect(isResizingTask('super_res')).toBe(true);
	});

	it('registers fix_faces with its own options, a note and a required region', () => {
		syncServerTasks([{ id: 'fix_faces', label: 'Fix faces and hands', icon: 'mode-refine-faces', region: 'required', prompt: 'none', fallback: null, offers: [], default: null } as EditorTaskInfo]);
		expect(tasks.get('image', 'fix_faces')).toMatchObject({ region: 'required', prompt: 'none' });
		expect(tasks.get('image', 'fix_faces')?.note).toContain('face');
		expect(railTools.get('image', 'fix_faces')?.options).toBeTruthy();
		expect(railTools.get('image', 'fix_faces')?.options).not.toBe(railTools.get('image', 'upscale')?.options);
	});
});
