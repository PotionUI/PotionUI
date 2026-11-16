import { flushSync, tick } from 'svelte';

export async function openEditorMenu(root: ParentNode, menu: string): Promise<HTMLElement | null> {
	Array.from(root.querySelectorAll<HTMLElement>('[data-menu-tab]')).find((el) => el.dataset.menuTab === menu)!.click();
	flushSync();
	await tick();
	await tick();
	return document.querySelector<HTMLElement>('[data-editor-menu]');
}

export function menuRow(id: string): HTMLElement | null {
	return Array.from(document.querySelectorAll<HTMLElement>('[data-menu-row]')).find((el) => el.dataset.rowId === id) ?? null;
}

export async function pickEditorMenuItem(root: ParentNode, menu: string, id: string): Promise<void> {
	await openEditorMenu(root, menu);
	menuRow(id)!.click();
	flushSync();
	await tick();
}

export function isRowDisabled(id: string): boolean {
	return menuRow(id)?.getAttribute('aria-disabled') === 'true';
}
