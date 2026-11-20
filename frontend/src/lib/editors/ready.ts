import { get, writable, type Readable } from 'svelte/store';
import { bumpEditorsRevision } from './registry';

const ready = writable(false);

export const editorExtensionsReady: Readable<boolean> = { subscribe: ready.subscribe };

export function markEditorExtensionsReady(): void {
	if (get(ready)) return;
	ready.set(true);
	bumpEditorsRevision();
}

export function resetEditorExtensionsReady(): void {
	ready.set(false);
}

export function extensionsAreReady(): boolean {
	return get(ready);
}

export function whenEditorExtensionsReady(): Promise<void> {
	if (get(ready)) return Promise.resolve();
	return new Promise((resolve) => {
		const stop = ready.subscribe((value) => {
			if (!value) return;
			queueMicrotask(() => stop());
			resolve();
		});
	});
}
