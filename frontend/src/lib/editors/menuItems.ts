import type { RailGroup, ReplaceOption, SaveAction, SendTarget } from '$lib/tool-tabs/frame/types';
import { guard } from './guard';
import { createEditorRegistry, entryKey } from './registry';
import type { EditorEntryBase, EditorKind } from './types';

export const CORE_MENUS = ['file', 'edit', 'view', 'tools'] as const;

export const CORE_MENU_LABELS: Record<string, string> = { file: 'File', edit: 'Edit', view: 'View', tools: 'Tools' };

export interface MenuChoice {
	id: string;
	label: string;
	sub?: string;
	icon?: string;
	badge?: string;
	dot?: string;
	checked?: boolean;
	disabled?: string;
	run(): void;
}

export type MenuChild = MenuChoice | { id: string; separator: true } | { id: string; heading: string };

export interface MenuSource {
	id: string;
	label: string;
	icon: string;
}

export interface MenuRecentEdit {
	id: string;
	label: string;
	meta?: string;
}

export interface MenuStep {
	id: string;
	label: string;
	hidden: boolean;
}

export interface MenuAction {
	label: string;
	icon?: string;
	enabled: boolean;
	reason?: string;
}

export interface EditorMenuCommands {
	save(action: SaveAction): void;
	closeEditor(): void;
	replaceWith(id: string): void;
	openRecent(id: string): void;
	openAllEdits(): void;
	undo(): void;
	redo(): void;
	apply(): void;
	reset(): void;
	toggleStepHidden(): void;
	removeStep(): void;
	removeAll(): void;
	toggleCompare(): void;
	zoomIn(): void;
	zoomOut(): void;
	fit(): void;
	actualSize(): void;
	toggleEdits(): void;
	selectTool(id: string): void;
}

export interface EditorMenuContext {
	editor: EditorKind;
	hasSource: boolean;
	phone: boolean;
	busy: boolean;
	tool: string | null;
	rail: RailGroup[];
	groupedTools?: boolean;
	appliedTools?: string[];
	saved: boolean;
	canSave: boolean;
	replace: ReplaceOption;
	sendTargets: SendTarget[];
	newGeneration: boolean;
	newGenerationHint?: string;
	downloadFormat?: string;
	copyLabel: string;
	copyReason?: string;
	saveNewLabel?: string;
	sources: MenuSource[];
	currentSource: string | null;
	replaceLabel: string;
	replaceBlocked: boolean;
	recentEdits: MenuRecentEdit[] | null;
	canUndo: boolean;
	undoLabel: string | null;
	canRedo: boolean;
	redoLabel: string | null;
	apply: MenuAction | null;
	reset: MenuAction | null;
	step: MenuStep | null;
	editCount: number;
	compare: boolean;
	canCompare: boolean;
	zoom: { fit: boolean; actual: boolean; canIn: boolean; canOut: boolean } | null;
	editsShown: boolean;
	commands: EditorMenuCommands;
}

type Text = string | ((ctx: EditorMenuContext) => string);

export interface MenuItemEntry extends EditorEntryBase {
	menu: string;
	menuLabel?: string;
	group: string;
	order?: number;
	label: Text;
	icon: Text;
	keybind?: string | ((ctx: EditorMenuContext) => string | undefined);
	tone?: 'danger';
	check?(ctx: EditorMenuContext): boolean;
	visible?(ctx: EditorMenuContext): boolean;
	available?(ctx: EditorMenuContext): true | string;
	run?(ctx: EditorMenuContext): void;
	children?(ctx: EditorMenuContext): MenuChild[];
	childrenWidth?: number;
}

export const menuItems = createEditorRegistry<MenuItemEntry>('menuItems');

export interface MenuRow {
	id: string;
	label: string;
	icon: string;
	keybind?: string;
	danger: boolean;
	checked: boolean | null;
	reason: string | null;
	children: MenuChild[] | null;
	childrenWidth: number;
	run(): void;
}

export interface MenuModel {
	id: string;
	label: string;
	groups: MenuRow[][];
}

const FIRST_PLUGIN_GROUP_ORDER = 10_000;

function order(entry: { order?: number }): number {
	return entry.order ?? Number.MAX_SAFE_INTEGER;
}

function call<T>(entry: MenuItemEntry, editor: EditorKind, what: string, run: () => T, fallback: T): T {
	return entry.pluginId ? guard(entryKey(editor, entry.id), what, run, fallback) : run();
}

function text(entry: MenuItemEntry, editor: EditorKind, value: Text, ctx: EditorMenuContext, what: string, fallback: string): string {
	if (typeof value === 'string') return value;
	const result = call(entry, editor, what, () => value(ctx), fallback);
	return typeof result === 'string' && result ? result : fallback;
}

function rowOf(entry: MenuItemEntry, ctx: EditorMenuContext): MenuRow | null {
	const editor = ctx.editor;
	if (entry.visible && !call(entry, editor, 'visible', () => entry.visible!(ctx), true)) return null;
	const verdict = entry.available ? call(entry, editor, 'available', () => entry.available!(ctx), true as true | string) : true;
	const reason = verdict === true ? null : typeof verdict === 'string' && verdict ? verdict : 'Not available right now';
	const children = entry.children ? call(entry, editor, 'children', () => entry.children!(ctx), [] as MenuChild[]) : null;
	const keybind = typeof entry.keybind === 'function' ? call(entry, editor, 'keybind', () => (entry.keybind as (c: EditorMenuContext) => string | undefined)(ctx), undefined) : entry.keybind;
	const row: MenuRow = {
		id: entry.id,
		label: text(entry, editor, entry.label, ctx, 'label', entry.id),
		icon: text(entry, editor, entry.icon, ctx, 'icon', 'extension'),
		danger: entry.tone === 'danger',
		checked: entry.check ? call(entry, editor, 'check', () => !!entry.check!(ctx), false) : null,
		reason,
		children,
		childrenWidth: entry.childrenWidth ?? 240,
		run: () => {
			if (reason || !entry.run) return;
			call(entry, editor, 'run', () => entry.run!(ctx), undefined);
		}
	};
	if (keybind) row.keybind = keybind;
	return row;
}

function sortGroups(rows: { group: string; order: number; row: MenuRow }[], rank: Map<string, number>): MenuRow[][] {
	for (const item of rows) rank.set(item.group, Math.min(rank.get(item.group) ?? Number.MAX_SAFE_INTEGER, item.order));
	const groups = new Map<string, { order: number; row: MenuRow }[]>();
	for (const item of rows) groups.set(item.group, [...(groups.get(item.group) ?? []), item]);
	return [...groups.entries()]
		.sort((a, b) => (rank.get(a[0]) ?? 0) - (rank.get(b[0]) ?? 0))
		.map(([, items]) => items.sort((a, b) => a.order - b.order).map((item) => item.row));
}

function toolRows(ctx: EditorMenuContext): { group: string; order: number; row: MenuRow }[] {
	const out: { group: string; order: number; row: MenuRow }[] = [];
	const applied = new Set(ctx.appliedTools ?? []);
	ctx.rail.forEach((group, g) => {
		const rowOf = (tool: RailGroup['tools'][number], t: number) => ({
			group: group.id,
			order: (g + 1) * 100 + t,
			row: {
				id: `tool:${tool.id}`,
				label: tool.label,
				icon: tool.icon,
				danger: false,
				checked: ctx.tool === tool.id,
				reason: tool.disabled || tool.later ? (tool.reason ?? (tool.later ? 'Later' : 'Not available right now')) : null,
				children: null,
				childrenWidth: 240,
				run: () => ctx.commands.selectTool(tool.id)
			} satisfies MenuRow
		});
		if (!ctx.groupedTools || group.tools.length < 2) {
			group.tools.forEach((tool, t) => out.push(rowOf(tool, t)));
			return;
		}
		const current = group.tools.find((tool) => tool.id === ctx.tool) ?? group.tools[0];
		const children: MenuChild[] = group.tools.map((tool) => {
			const child: MenuChoice = {
				id: `tool:${tool.id}`,
				label: tool.label,
				icon: tool.icon,
				checked: ctx.tool === tool.id,
				run: () => ctx.commands.selectTool(tool.id)
			};
			if (applied.has(tool.id)) child.dot = 'rgb(var(--signal))';
			if (tool.disabled || tool.later) child.disabled = tool.reason ?? (tool.later ? 'Later' : 'Not available right now');
			return child;
		});
		out.push({
			group: group.id,
			order: (g + 1) * 100,
			row: {
				id: `toolgroup:${group.id}`,
				label: group.label,
				icon: current.icon,
				danger: false,
				checked: null,
				reason: null,
				children,
				childrenWidth: 252,
				run: () => undefined
			}
		});
	});
	return out;
}

function groupBase(ctx: EditorMenuContext, group: string): number | null {
	const index = ctx.rail.findIndex((candidate) => candidate.id === group);
	return index < 0 ? null : (index + 1) * 100;
}

export function menuEntries(editor: EditorKind): MenuItemEntry[] {
	return menuItems.list(editor);
}

export function menuTabs(editor: EditorKind): { id: string; label: string }[] {
	const entries = menuEntries(editor);
	const tabs = CORE_MENUS.map((id) => ({ id: id as string, label: CORE_MENU_LABELS[id] }));
	const seen = new Set<string>(CORE_MENUS);
	for (const entry of [...entries].sort((a, b) => order(a) - order(b))) {
		if (seen.has(entry.menu)) continue;
		seen.add(entry.menu);
		tabs.push({ id: entry.menu, label: entry.menuLabel ?? entry.menu });
	}
	return tabs;
}

export function buildMenu(menuId: string, ctx: EditorMenuContext): MenuModel {
	const tabs = menuTabs(ctx.editor);
	const label = tabs.find((tab) => tab.id === menuId)?.label ?? menuId;
	const items: { group: string; order: number; row: MenuRow }[] = menuId === 'tools' ? toolRows(ctx) : [];
	const rank = new Map<string, number>();
	const place = (entry: MenuItemEntry): number => {
		const base = menuId === 'tools' ? groupBase(ctx, entry.group) : null;
		return base === null ? order(entry) + (menuId === 'tools' ? FIRST_PLUGIN_GROUP_ORDER : 0) : base + 50 + (entry.order ?? 0) / 1000;
	};
	for (const entry of menuEntries(ctx.editor)) {
		if (entry.menu !== menuId) continue;
		rank.set(entry.group, Math.min(rank.get(entry.group) ?? Number.MAX_SAFE_INTEGER, place(entry)));
		const row = rowOf(entry, ctx);
		if (row) items.push({ group: entry.group, order: place(entry), row });
	}
	return { id: menuId, label, groups: sortGroups(items, rank) };
}

export function buildMenus(ctx: EditorMenuContext): MenuModel[] {
	return menuTabs(ctx.editor).map((tab) => buildMenu(tab.id, ctx)).filter((menu) => menu.groups.length > 0);
}

export function flatRows(menu: MenuModel): MenuRow[] {
	return menu.groups.flat();
}
