import { createEditorRegistry } from './registry';
import type { EditorEntryBase } from './types';

export interface TaskEntry extends EditorEntryBase {
	label: string;
	icon: string;
	tool: string;
	region: 'required' | 'optional' | 'none';
	prompt: 'required' | 'optional' | 'none';
	stepKind: string;
	fallback?: { task: string; prompt: string };
}

export const tasks = createEditorRegistry<TaskEntry>('tasks');
