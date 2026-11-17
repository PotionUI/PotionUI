import { CORE_OWNER } from '$lib/registries/registry';
import { shortcutFor } from '$lib/tool-tabs/frame/keymap';
import { menuItems, type EditorMenuContext, type MenuChild, type MenuItemEntry } from './menuItems';
import type { EditorKind } from './types';
import { ensurePaintMenu } from './paintMenu';

type Core = Omit<MenuItemEntry, 'editor' | 'id'> & { id: string; only?: EditorKind };

const sourceRows = (ctx: EditorMenuContext): MenuChild[] =>
	ctx.sources.map((source) => ({
		id: source.id,
		label: source.label,
		icon: source.icon,
		run: () => ctx.commands.replaceWith(source.id)
	}));

const recentRows = (ctx: EditorMenuContext): MenuChild[] => {
	const rows: MenuChild[] = [];
	if (ctx.recentEdits === null) rows.push({ id: 'recent-loading', label: 'Loading', icon: 'clock', disabled: 'Loading your edits', run: () => undefined });
	else if (!ctx.recentEdits.length) rows.push({ id: 'recent-none', label: 'No edits yet', icon: 'clock', disabled: 'Your edits will be listed here', run: () => undefined });
	else
		for (const edit of ctx.recentEdits)
			rows.push({ id: edit.id, label: edit.label, sub: edit.meta, icon: 'editor-layers', run: () => ctx.commands.openRecent(edit.id) });
	rows.push({ id: 'recent-sep', separator: true });
	rows.push({ id: 'recent-all', label: 'See all edits', icon: 'folder', run: () => ctx.commands.openAllEdits() });
	return rows;
};

const sendRows = (ctx: EditorMenuContext): MenuChild[] => {
	const rows: MenuChild[] = [];
	if (ctx.sendTargets.length || ctx.newGeneration) rows.push({ id: 'send-head', heading: 'Send to a generation' });
	for (const target of ctx.sendTargets)
		rows.push({
			id: target.id,
			label: target.name,
			sub: target.detail,
			icon: 'editor-send',
			badge: target.badge,
			dot: target.badge ? undefined : target.color,
			run: () => ctx.commands.save({ kind: 'send', target: target.id })
		});
	if (ctx.newGeneration)
		rows.push({
			id: 'new-generation',
			label: 'New generation tab…',
			sub: ctx.newGenerationHint ?? 'Pick a preset with a matching field',
			icon: 'plus',
			run: () => ctx.commands.save({ kind: 'new-generation' })
		});
	return rows;
};

const saveReason = (ctx: EditorMenuContext): true | string => (ctx.canSave ? true : 'Nothing to save yet');

const core: Core[] = [
	{
		id: 'file.replace',
		menu: 'file',
		group: 'source',
		order: 10,
		label: (ctx) => ctx.replaceLabel,
		icon: 'editor-replace',
		visible: (ctx) => ctx.hasSource,
		available: (ctx) => (ctx.replaceBlocked ? 'Wait until the current save finishes' : true),
		children: sourceRows,
		childrenWidth: 220
	},
	{
		id: 'file.recent',
		menu: 'file',
		group: 'source',
		order: 20,
		label: 'Recent edits',
		icon: 'clock',
		only: 'image',
		children: recentRows,
		childrenWidth: 260
	},
	{
		id: 'file.save',
		menu: 'file',
		group: 'save',
		order: 10,
		label: 'Save',
		icon: 'check',
		keybind: shortcutFor('save'),
		visible: (ctx) => ctx.saved,
		available: saveReason,
		run: (ctx) => ctx.commands.save({ kind: 'save' })
	},
	{
		id: 'file.save-new',
		menu: 'file',
		group: 'save',
		order: 20,
		label: (ctx) => ctx.saveNewLabel ?? 'Save as new',
		icon: (ctx) => (ctx.saved ? 'plus' : 'check'),
		keybind: (ctx) => (ctx.saved ? undefined : shortcutFor('save')),
		available: saveReason,
		run: (ctx) => ctx.commands.save({ kind: 'save-new' })
	},
	{
		id: 'file.replace-original',
		menu: 'file',
		group: 'save',
		order: 30,
		label: 'Replace original',
		icon: 'editor-replace',
		available: (ctx) => (ctx.replace.allowed ? saveReason(ctx) : (ctx.replace.reason ?? 'Not available for this file')),
		run: (ctx) => ctx.commands.save({ kind: 'replace' })
	},
	{
		id: 'file.send',
		menu: 'file',
		group: 'out',
		order: 10,
		label: 'Send to',
		icon: 'editor-send',
		visible: (ctx) => ctx.sendTargets.length > 0 || ctx.newGeneration,
		children: sendRows,
		childrenWidth: 340
	},
	{
		id: 'file.download',
		menu: 'file',
		group: 'out',
		order: 20,
		label: 'Download',
		icon: 'download',
		keybind: (ctx) => ctx.downloadFormat,
		available: saveReason,
		run: (ctx) => ctx.commands.save({ kind: 'download' })
	},
	{
		id: 'file.copy',
		menu: 'file',
		group: 'out',
		order: 30,
		label: (ctx) => ctx.copyLabel,
		icon: 'copy',
		available: (ctx) => ctx.copyReason ?? saveReason(ctx),
		run: (ctx) => ctx.commands.save({ kind: 'copy' })
	},
	{
		id: 'file.close',
		menu: 'file',
		group: 'close',
		order: 10,
		label: 'Close',
		icon: 'close',
		run: (ctx) => ctx.commands.closeEditor()
	},
	{
		id: 'edit.undo',
		menu: 'edit',
		group: 'history',
		order: 10,
		label: (ctx) => (ctx.undoLabel ? `Undo ${ctx.undoLabel}` : 'Undo'),
		icon: 'editor-undo',
		keybind: shortcutFor('undo'),
		available: (ctx) => (ctx.canUndo ? true : 'Nothing to undo'),
		run: (ctx) => ctx.commands.undo()
	},
	{
		id: 'edit.redo',
		menu: 'edit',
		group: 'history',
		order: 20,
		label: (ctx) => (ctx.redoLabel ? `Redo ${ctx.redoLabel}` : 'Redo'),
		icon: 'editor-redo',
		keybind: shortcutFor('redo'),
		available: (ctx) => (ctx.canRedo ? true : 'Nothing to redo'),
		run: (ctx) => ctx.commands.redo()
	},
	{
		id: 'edit.apply',
		menu: 'edit',
		group: 'tool',
		order: 10,
		label: (ctx) => ctx.apply?.label ?? 'Apply',
		icon: 'check',
		keybind: shortcutFor('apply'),
		visible: (ctx) => !!ctx.apply,
		available: (ctx) => (ctx.apply?.enabled ? true : (ctx.apply?.reason ?? 'Nothing to apply yet')),
		run: (ctx) => ctx.commands.apply()
	},
	{
		id: 'edit.reset',
		menu: 'edit',
		group: 'tool',
		order: 20,
		label: (ctx) => ctx.reset?.label ?? 'Reset',
		icon: (ctx) => ctx.reset?.icon ?? 'rotate',
		visible: (ctx) => !!ctx.reset,
		available: (ctx) => (ctx.reset?.enabled ? true : (ctx.reset?.reason ?? 'Nothing to reset')),
		run: (ctx) => ctx.commands.reset()
	},
	{
		id: 'edit.hide-step',
		menu: 'edit',
		group: 'step',
		order: 10,
		label: (ctx) => (ctx.step?.hidden ? 'Show step' : 'Hide step'),
		icon: (ctx) => (ctx.step?.hidden ? 'editor-eye' : 'editor-eye-off'),
		available: (ctx) => (ctx.step ? true : 'Select an edit first'),
		run: (ctx) => ctx.commands.toggleStepHidden()
	},
	{
		id: 'edit.remove-step',
		menu: 'edit',
		group: 'step',
		order: 20,
		label: 'Remove step',
		icon: 'trash',
		available: (ctx) => (ctx.step ? true : 'Select an edit first'),
		run: (ctx) => ctx.commands.removeStep()
	},
	{
		id: 'edit.remove-all',
		menu: 'edit',
		group: 'danger',
		order: 10,
		label: 'Remove all edits',
		icon: 'trash',
		tone: 'danger',
		available: (ctx) => (ctx.editCount > 0 ? true : 'There are no edits to remove'),
		run: (ctx) => ctx.commands.removeAll()
	},
	{
		id: 'view.compare',
		menu: 'view',
		group: 'compare',
		order: 10,
		label: 'Before / after',
		icon: 'editor-compare',
		keybind: shortcutFor('compare'),
		check: (ctx) => ctx.compare,
		available: (ctx) => (ctx.canCompare ? true : 'Make an edit to compare'),
		run: (ctx) => ctx.commands.toggleCompare()
	},
	{
		id: 'view.zoom-in',
		menu: 'view',
		group: 'zoom',
		order: 10,
		label: 'Zoom in',
		icon: 'plus',
		visible: (ctx) => !ctx.phone,
		available: (ctx) => (!ctx.zoom ? 'Zoom is not available here' : ctx.zoom.canIn ? true : 'Already at the largest zoom'),
		run: (ctx) => ctx.commands.zoomIn()
	},
	{
		id: 'view.zoom-out',
		menu: 'view',
		group: 'zoom',
		order: 20,
		label: 'Zoom out',
		icon: 'minus',
		visible: (ctx) => !ctx.phone,
		available: (ctx) => (!ctx.zoom ? 'Zoom is not available here' : ctx.zoom.canOut ? true : 'Already at the smallest zoom'),
		run: (ctx) => ctx.commands.zoomOut()
	},
	{
		id: 'view.fit',
		menu: 'view',
		group: 'zoom',
		order: 30,
		label: 'Fit',
		icon: 'fit',
		visible: (ctx) => !ctx.phone,
		check: (ctx) => !!ctx.zoom?.fit,
		available: (ctx) => (ctx.zoom ? true : 'Zoom is not available here'),
		run: (ctx) => ctx.commands.fit()
	},
	{
		id: 'view.actual',
		menu: 'view',
		group: 'zoom',
		order: 40,
		label: 'Actual size 1:1',
		icon: 'expand',
		visible: (ctx) => !ctx.phone,
		check: (ctx) => !!ctx.zoom?.actual,
		available: (ctx) => (ctx.zoom ? true : 'Zoom is not available here'),
		run: (ctx) => ctx.commands.actualSize()
	},
	{
		id: 'view.edits',
		menu: 'view',
		group: 'panels',
		order: 10,
		label: (ctx) => (ctx.editsShown ? 'Hide edits' : 'Show edits'),
		icon: 'editor-layers',
		run: (ctx) => ctx.commands.toggleEdits()
	}
];

let registered = false;

export function ensureCoreEditorMenu(): void {
	if (registered) return;
	registered = true;
	for (const spec of core) {
		const { only, ...rest } = spec;
		const editors: EditorKind[] = only ? [only] : ['image', 'video'];
		for (const editor of editors) menuItems.register({ ...rest, editor }, CORE_OWNER);
	}
	ensurePaintMenu();
}
