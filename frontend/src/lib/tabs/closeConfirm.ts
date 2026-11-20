import { get, writable } from 'svelte/store';
import { tabsStore } from '$lib/stores/tabs';
import { tabHasUnsavedWork } from '$lib/utils/newWorkspace';
import type { GenerationTab, Tab, ToolTab } from '$lib/types/tabs';
import { isToolTab } from '$lib/types/tabs';
import { getToolKind } from '$lib/tool-tabs/registry';
import { closeLossMessage, hasToolTabCloseGuard, runToolTabCloseGuard, type CloseLoss } from '$lib/tool-tabs/closeGuard';

const pending = writable<string | null>(null);
let pendingLoss: { tabId: string; loss: CloseLoss } | null = null;
const guarding = new Set<string>();

export const pendingTabClose = { subscribe: pending.subscribe };

export function tabIsRunning(tab: GenerationTab): boolean {
	return tab.generation.isGenerating || (tab.generation.queue?.length ?? 0) > 0;
}

function closeNow(tab: Tab): void {
	tabsStore.removeTab(tab.id);
	if (isToolTab(tab)) getToolKind(tab.tool)?.onClosed?.(tab);
}

export function requestCloseTab(id: string): void {
	const { tabs } = get(tabsStore);
	const tab = tabs.find((t) => t.id === id);
	if (tabs.length < 2 || !tab) return;
	if (isToolTab(tab) && hasToolTabCloseGuard(tab.id)) {
		void guardedClose(tab);
		return;
	}
	if (isToolTab(tab) && !(tab.dirty ?? 0) && getToolKind(tab.tool)?.confirmClose === false) {
		closeNow(tab);
		return;
	}
	pending.set(id);
}

async function guardedClose(tab: ToolTab): Promise<void> {
	if (guarding.has(tab.id)) return;
	guarding.add(tab.id);
	try {
		const loss = await runToolTabCloseGuard(tab.id);
		const current = get(tabsStore).tabs.find((t) => t.id === tab.id);
		if (!current) return;
		if (!loss) {
			closeNow(current);
			return;
		}
		pendingLoss = { tabId: tab.id, loss };
		pending.set(tab.id);
	} finally {
		guarding.delete(tab.id);
	}
}

export function confirmCloseTab(): void {
	const id = get(pending);
	pending.set(null);
	const tab = id ? get(tabsStore).tabs.find((t) => t.id === id) : undefined;
	if (tab) closeNow(tab);
}

export function cancelCloseTab(): void {
	pending.set(null);
}

export function tabHasUnsavedVariables(tab: GenerationTab): boolean {
	return Object.keys(tab.variables ?? {}).length > 0 && !tab.selectedSessionId && !tab.sourcePromptId;
}

function describeToolTabClose(tab: ToolTab): { title: string; message: string; confirmLabel?: string } {
	const loss = pendingLoss?.tabId === tab.id ? pendingLoss.loss : null;
	const edits = loss ? loss.edits : (tab.dirty ?? 0);
	const lines = [`“${tab.name}” will be closed.`];
	if (loss) {
		if (edits === 0) return { title: 'Discard unsaved work?', message: [...lines, closeLossMessage(loss)].join('\n'), confirmLabel: 'Discard and close' };
		lines.push(`${edits} ${edits === 1 ? 'edit' : 'edits'} will be lost. ${closeLossMessage(loss)}`);
	}
	if (edits === 0) return { title: 'Close tab?', message: lines.join('\n') };
	return {
		title: `Discard ${edits} unsaved ${edits === 1 ? 'edit' : 'edits'}?`,
		message: lines.join('\n'),
		confirmLabel: 'Discard and close'
	};
}

export function describeTabClose(tab: Tab): { title: string; message: string; confirmLabel?: string } {
	if (isToolTab(tab)) return describeToolTabClose(tab);
	const lines = [`“${tab.name}” will be closed.`];
	if (tabIsRunning(tab)) lines.push('It has a generation running or queued.');
	if (tabHasUnsavedWork(tab)) lines.push('It has unsaved changes.');
	if (tabHasUnsavedVariables(tab)) lines.push("This tab's variables aren't saved anywhere else.");
	return { title: 'Close tab?', message: lines.join('\n') };
}
