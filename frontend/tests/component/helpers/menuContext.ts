import type { EditorMenuCommands, EditorMenuContext } from '$lib/editors/menuItems';

export function menuCommands(log: string[]): EditorMenuCommands {
	const make = (name: string) => () => void log.push(name);
	return {
		save: (action) => void log.push(`save:${action.kind}${'target' in action ? `:${action.target}` : ''}`),
		closeEditor: make('close'),
		replaceWith: (id) => void log.push(`replace:${id}`),
		openRecent: (id) => void log.push(`recent:${id}`),
		openAllEdits: make('all-edits'),
		undo: make('undo'),
		redo: make('redo'),
		apply: make('apply'),
		reset: make('reset'),
		toggleStepHidden: make('hide'),
		removeStep: make('remove-step'),
		removeAll: make('remove-all'),
		toggleCompare: make('compare'),
		zoomIn: make('zoom-in'),
		zoomOut: make('zoom-out'),
		fit: make('fit'),
		actualSize: make('actual'),
		toggleEdits: make('edits'),
		selectTool: (id) => void log.push(`tool:${id}`)
	};
}

export function menuContext(log: string[] = [], patch: Partial<EditorMenuContext> = {}): EditorMenuContext {
	return {
		editor: 'image',
		hasSource: true,
		phone: false,
		busy: false,
		tool: 'crop',
		rail: [
			{ id: 'shape', label: 'Shape', tools: [{ id: 'crop', label: 'Crop', icon: 'editor-crop' }, { id: 'resize', label: 'Resize', icon: 'editor-resize' }] },
			{ id: 'look', label: 'Look', tools: [{ id: 'effects', label: 'Looks', icon: 'editor-effects' }] }
		],
		saved: false,
		canSave: true,
		replace: { allowed: false, reason: 'Files from History stay as generated' },
		sendTargets: [{ id: 't1', name: 'Generation 1', detail: 'Qwen · Source', badge: 'MASK' }],
		newGeneration: false,
		downloadFormat: 'PNG',
		copyLabel: 'Copy image',
		sources: [
			{ id: 'history', label: 'History', icon: 'clock' },
			{ id: 'library', label: 'Library', icon: 'photo' }
		],
		currentSource: null,
		replaceLabel: 'Replace picture',
		replaceBlocked: false,
		recentEdits: [{ id: 'e1', label: 'dockside', meta: 'dock.png' }],
		canUndo: true,
		undoLabel: 'Matte',
		canRedo: false,
		redoLabel: null,
		apply: { label: 'Apply crop', enabled: true },
		reset: { label: 'Reset', icon: 'rotate', enabled: true },
		step: { id: 's1', label: 'Matte', hidden: false },
		editCount: 2,
		compare: false,
		canCompare: true,
		zoom: { fit: true, actual: false, canIn: true, canOut: true },
		editsShown: true,
		commands: menuCommands(log),
		...patch
	};
}
