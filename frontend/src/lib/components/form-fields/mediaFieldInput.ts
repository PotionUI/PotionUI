import { pastedImageFileName } from './mediaLoaderUpload';

export type PasteOutcome = { file: File } | { error: string; detail: string | null };

export function readPastedImage(items: DataTransferItemList | null | undefined): PasteOutcome {
	if (!items) return { error: 'Unable to read the clipboard', detail: null };
	for (let i = 0; i < items.length; i++) {
		const item = items[i];
		if (item.type.indexOf('image') === -1) continue;
		const blob = item.getAsFile();
		if (blob) return { file: new File([blob], pastedImageFileName(), { type: blob.type }) };
		return { error: 'Nothing pasteable in the clipboard', detail: 'Copy an image first' };
	}
	return { error: 'Nothing pasteable in the clipboard', detail: 'Copy an image first' };
}

const LAYER_SELECTOR =
	'[class*="z-50"], [class*="z-[50]"], [class*="z-[60]"], [class*="z-[70]"], [class*="z-[80]"], [class*="z-[90]"], [class*="z-[100]"]';

export function clickIsOutside(root: HTMLElement | undefined, target: Node): boolean {
	if (!root || root.contains(target)) return false;
	const layer = root.closest(LAYER_SELECTOR);
	return !layer || !layer.contains(target);
}

export interface FileDropHandlers {
	onFiles: (files: FileList) => void;
	onActive: (active: boolean) => void;
}

function carriesFiles(event: DragEvent): boolean {
	return Array.from(event.dataTransfer?.types ?? []).includes('Files');
}

export function fileDrop(node: HTMLElement, initial: FileDropHandlers) {
	let handlers = initial;
	let depth = 0;

	const enter = (event: DragEvent) => {
		if (!carriesFiles(event)) return;
		event.preventDefault();
		depth += 1;
		handlers.onActive(true);
	};
	const leave = (event: DragEvent) => {
		if (!carriesFiles(event)) return;
		depth = Math.max(0, depth - 1);
		if (depth === 0) handlers.onActive(false);
	};
	const over = (event: DragEvent) => {
		if (carriesFiles(event)) event.preventDefault();
	};
	const drop = (event: DragEvent) => {
		if (!carriesFiles(event)) return;
		event.preventDefault();
		event.stopPropagation();
		depth = 0;
		handlers.onActive(false);
		const files = event.dataTransfer?.files;
		if (files && files.length > 0) handlers.onFiles(files);
	};

	node.addEventListener('dragenter', enter);
	node.addEventListener('dragleave', leave);
	node.addEventListener('dragover', over);
	node.addEventListener('drop', drop);

	return {
		update(next: FileDropHandlers) {
			handlers = next;
		},
		destroy() {
			node.removeEventListener('dragenter', enter);
			node.removeEventListener('dragleave', leave);
			node.removeEventListener('dragover', over);
			node.removeEventListener('drop', drop);
		}
	};
}
