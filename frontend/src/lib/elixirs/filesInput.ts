import { fileDrop } from '$lib/components/form-fields/mediaFieldInput';
import { filesFromClipboard, nameClipboardFiles } from './filesUpload';

export interface FilesInputOptions {
	onfiles: (files: File[]) => void;
	onactive?: (active: boolean) => void;
	enabled?: boolean;
}

function typesText(target: EventTarget | null): boolean {
	const element = target as HTMLElement | null;
	if (!element || typeof element.closest !== 'function') return false;
	return !!element.closest('input:not([type="file"]), textarea, select, [contenteditable]:not([contenteditable="false"])');
}

export function filesInput(node: HTMLElement, initial: FilesInputOptions) {
	let options = initial;

	const drop = fileDrop(node, {
		onFiles: (list) => {
			if (options.enabled !== false) options.onfiles(Array.from(list));
		},
		onActive: (active) => options.onactive?.(active && options.enabled !== false)
	});

	const paste = (event: ClipboardEvent) => {
		if (options.enabled === false || typesText(event.target)) return;
		const files = nameClipboardFiles(filesFromClipboard(event.clipboardData));
		if (files.length === 0) return;
		event.preventDefault();
		options.onfiles(files);
	};
	node.addEventListener('paste', paste);

	return {
		update(next: FilesInputOptions) {
			options = next;
		},
		destroy() {
			node.removeEventListener('paste', paste);
			drop.destroy();
		}
	};
}
