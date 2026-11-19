import { createEditorRegistry } from './registry';
import type { EditorEntryBase } from './types';

export interface TaskEntry extends EditorEntryBase {
	label: string;
	icon: string;
	tool: string;
	region: 'required' | 'optional' | 'none';
	prompt: 'required' | 'optional' | 'none';
	stepKind: string;
	range?: 'required' | 'optional' | 'none';
	result?: 'replace' | 'append';
	fallback?: { task: string; prompt: string };
	grows?: boolean;
}

export const tasks = createEditorRegistry<TaskEntry>('tasks');
