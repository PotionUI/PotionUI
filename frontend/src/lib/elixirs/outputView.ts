import { coreOverlays, type Mountable, type OverlayRegistry } from './overlays';
import { fullSource, tileKind, tileName } from './tiles';
import type { OutputEntry } from './types';

type Entry = Partial<OutputEntry> | null | undefined;

export interface SendToRow {
	id: string;
	label: string;
	locked?: boolean;
	selected?: boolean;
}

export interface SendTo {
	rows?: SendToRow[];
	isEligible?: (rowId: string, entry: OutputEntry) => boolean;
	isSelected?: (rowId: string, entry: OutputEntry) => boolean;
	onToggle?: (rowId: string, entry: OutputEntry, checked: boolean) => void;
}

export interface FileChange {
	generationId: string;
	fileId?: string | number | null;
	fileIndex?: number;
}

export interface HistoryGroup {
	generationId: string;
	index: number;
}

export interface ExtraSection {
	id: string;
	title: string;
	rows: Array<{ id: string; label: string; checked: boolean; disabled: boolean; onToggle: (checked: boolean) => void }>;
}

export interface OutputViewHandle {
	close: () => void;
	update: (patch: { outputs?: OutputEntry[]; sendTo?: SendTo | null }) => void;
}

export interface OpenOutputViewOptions {
	outputs?: OutputEntry[];
	index?: number;
	sendTo?: SendTo | null;
	onclose?: () => void;
}

const DETAILS = 'GenerationDetailsModal';
const PREVIEW = 'MediaPreviewModal';

function isHistory(entry: Entry): entry is OutputEntry & { generation_id: string } {
	return !!entry && entry.kind !== 'library' && !!entry.generation_id;
}

export function clampIndex(value: unknown, total: number): number {
	return Math.min(Math.max(0, Number.isInteger(value) ? (value as number) : 0), Math.max(0, total - 1));
}

export function historyGroups(outputs: OutputEntry[] | null | undefined): HistoryGroup[] {
	const groups: HistoryGroup[] = [];
	const seen = new Set<string>();
	(outputs ?? []).forEach((entry, index) => {
		if (!isHistory(entry) || seen.has(entry.generation_id)) return;
		seen.add(entry.generation_id);
		groups.push({ generationId: entry.generation_id, index });
	});
	return groups;
}

export function matchEntry(outputs: OutputEntry[] | null | undefined, change: FileChange | null | undefined): OutputEntry | null {
	if (!change) return null;
	const inGeneration = (outputs ?? []).filter((entry) => isHistory(entry) && entry.generation_id === change.generationId);
	if (change.fileId !== null && change.fileId !== undefined) {
		const byId = inGeneration.find((entry) => String(entry.file_id) === String(change.fileId));
		if (byId) return byId;
	}
	return inGeneration.find((entry) => entry.file_index === change.fileIndex) ?? null;
}

export function eligibleRows(sendTo: SendTo | null | undefined, entry: Entry): SendToRow[] {
	const rows = sendTo?.rows ?? [];
	if (!sendTo || !entry || typeof sendTo.isEligible !== 'function') return rows;
	return rows.filter((row) => !!sendTo.isEligible?.(row.id, entry as OutputEntry));
}

export function rowChecked(sendTo: SendTo | null | undefined, row: SendToRow, entry: Entry): boolean {
	if (typeof sendTo?.isSelected === 'function') return !!sendTo.isSelected(row.id, entry as OutputEntry);
	return typeof row.selected === 'boolean' ? row.selected : false;
}

export function extraSectionsFor(sendTo: SendTo | null | undefined, entry: Entry): ExtraSection[] {
	if (!entry) return [];
	const rows = eligibleRows(sendTo, entry).map((row) => ({
		id: row.id,
		label: row.label,
		checked: rowChecked(sendTo, row, entry),
		disabled: !!row.locked,
		onToggle: (checked: boolean) => sendTo?.onToggle?.(row.id, entry as OutputEntry, checked)
	}));
	return rows.length ? [{ id: 'send-to', title: 'Send to', rows }] : [];
}

export function navigationFor(outputs: OutputEntry[] | null | undefined, generationId: string | null | undefined) {
	const groups = historyGroups(outputs);
	const at = groups.findIndex((group) => group.generationId === generationId);
	return {
		groups,
		at,
		hasPrevious: at > 0,
		hasNext: at >= 0 && at < groups.length - 1,
		position: at >= 0 ? { index: at + 1, total: groups.length } : null
	};
}

function restoreFocus(opener: Element | null, key: string | null): void {
	if (opener && typeof (opener as HTMLElement).focus === 'function' && document.contains(opener)) {
		(opener as HTMLElement).focus();
		return;
	}
	const again = key ? [...document.querySelectorAll<HTMLElement>('[data-viewer-opener]')].find((el) => el.getAttribute('data-viewer-opener') === key) : null;
	if (again) {
		again.focus();
		return;
	}
	const root = document.querySelector<HTMLElement>('.elixirs-root');
	if (!root) return;
	if (!root.hasAttribute('tabindex')) root.setAttribute('tabindex', '-1');
	root.focus({ preventScroll: true });
}

export function openOutputView({ outputs = [], index = 0, sendTo = null, onclose }: OpenOutputViewOptions = {}, registry: OverlayRegistry = coreOverlays): OutputViewHandle | null {
	if (!Array.isArray(outputs) || outputs.length === 0) return null;
		const first = outputs[clampIndex(index, outputs.length)];
	const name = isHistory(first) ? DETAILS : PREVIEW;
	const component: Mountable | undefined = registry[name];
	if (!component) return null;

	const opener = document.activeElement;
	const openerKey = opener?.getAttribute?.('data-viewer-opener') ?? null;
	const holder = document.body.appendChild(document.createElement('div'));
	const state: { outputs: OutputEntry[]; sendTo: SendTo | null | undefined; generationId: string | null; change: FileChange | null; entry: OutputEntry | null } = {
		outputs,
		sendTo,
		generationId: first.generation_id ?? null,
		change: null,
		entry: first
	};
	let handle: unknown = null;
	let closed = false;

	const close = () => {
		if (closed) return;
		closed = true;
		if (handle) component.unmount(handle);
		handle = null;
		holder.remove();
		restoreFocus(opener, openerKey);
		onclose?.();
	};

	const livePart = () => {
		const nav = navigationFor(state.outputs, state.generationId);
		return {
			extraSections: extraSectionsFor(state.sendTo, state.entry),
			hasPrevious: nav.hasPrevious,
			hasNext: nav.hasNext,
			position: nav.position
		};
	};

	const push = () => {
		if (handle && !closed) component.update(handle, livePart());
	};

	const navigate = (direction: number): boolean => {
		const nav = navigationFor(state.outputs, state.generationId);
		const target = nav.groups[nav.at + direction];
		if (!target) return false;
		const entry = state.outputs[target.index];
		state.generationId = target.generationId;
		state.entry = entry;
		state.change = null;
		if (handle && !closed) {
			component.update(handle, {
				generationId: target.generationId,
				initialFileIndex: direction < 0 ? -1 : Number.isInteger(entry.file_index) ? entry.file_index : 0,
				...livePart()
			});
		}
		return true;
	};

	const onFileChange = (change: FileChange): void => {
		if (change.generationId !== state.generationId) return;
		state.change = change;
		state.entry = matchEntry(state.outputs, change);
		push();
	};

	const props =
		name === DETAILS
			? {
					isOpen: true,
					generationId: first.generation_id,
					initialFileIndex: Number.isInteger(first.file_index) ? first.file_index : 0,
					onClose: () => setTimeout(close, 0),
					onFileChange,
					onNavigate: navigate,
					...livePart()
				}
			: {
					isOpen: true,
					kind: tileKind(first),
					url: fullSource(first),
					label: tileName(first),
					onClose: () => setTimeout(close, 0)
				};

	handle = component.mount(holder, props);

	return {
		close,
		update(patch: { outputs?: OutputEntry[]; sendTo?: SendTo | null }) {
			if (closed) return;
			if (Array.isArray(patch.outputs)) {
				if (patch.outputs.length === 0) {
					close();
					return;
				}
				state.outputs = patch.outputs;
			}
			if ('sendTo' in patch) state.sendTo = patch.sendTo;
			if (name !== DETAILS) return;
			state.entry = state.change ? matchEntry(state.outputs, state.change) : state.entry;
			push();
		}
	};
}
