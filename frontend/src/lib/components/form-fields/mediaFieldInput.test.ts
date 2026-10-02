// @vitest-environment jsdom
import { describe, it, expect, vi } from 'vitest';
import { clickIsOutside, fileDrop, readPastedImage } from './mediaFieldInput';

function dragEvent(type: string, types: string[], files: File[] = []): DragEvent {
	const event = new Event(type, { bubbles: true, cancelable: true }) as DragEvent;
	Object.defineProperty(event, 'dataTransfer', { value: { types, files } });
	return event;
}

describe('fileDrop', () => {
	function setup() {
		const node = document.createElement('div');
		document.body.appendChild(node);
		const onFiles = vi.fn();
		const onActive = vi.fn();
		const action = fileDrop(node, { onFiles, onActive });
		return { node, onFiles, onActive, action };
	}

	it('stays active until the pointer has left every nested element it entered', () => {
		const { node, onActive } = setup();
		node.dispatchEvent(dragEvent('dragenter', ['Files']));
		node.dispatchEvent(dragEvent('dragenter', ['Files']));
		node.dispatchEvent(dragEvent('dragleave', ['Files']));
		expect(onActive).toHaveBeenLastCalledWith(true);
		node.dispatchEvent(dragEvent('dragleave', ['Files']));
		expect(onActive).toHaveBeenLastCalledWith(false);
	});

	it('hands the dropped files over and goes inactive', () => {
		const { node, onFiles, onActive } = setup();
		const file = new File(['x'], 'a.png', { type: 'image/png' });
		node.dispatchEvent(dragEvent('dragenter', ['Files']));
		node.dispatchEvent(dragEvent('drop', ['Files'], [file]));
		expect(onFiles).toHaveBeenCalledTimes(1);
		expect((onFiles.mock.calls[0][0] as unknown as File[])[0]).toBe(file);
		expect(onActive).toHaveBeenLastCalledWith(false);
	});

	it('ignores a drag that carries no files, such as a tile being reordered', () => {
		const { node, onFiles, onActive } = setup();
		node.dispatchEvent(dragEvent('dragenter', ['text/plain']));
		const drop = dragEvent('drop', ['text/plain']);
		node.dispatchEvent(drop);
		expect(onActive).not.toHaveBeenCalled();
		expect(onFiles).not.toHaveBeenCalled();
		expect(drop.defaultPrevented).toBe(false);
	});

	it('stops listening once destroyed', () => {
		const { node, onActive, action } = setup();
		action.destroy();
		node.dispatchEvent(dragEvent('dragenter', ['Files']));
		expect(onActive).not.toHaveBeenCalled();
	});

	it('uses the handlers it was last given', () => {
		const { node, onFiles, action } = setup();
		const next = vi.fn();
		action.update({ onFiles: next, onActive: vi.fn() });
		node.dispatchEvent(dragEvent('drop', ['Files'], [new File(['x'], 'a.png')]));
		expect(next).toHaveBeenCalledTimes(1);
		expect(onFiles).not.toHaveBeenCalled();
	});
});

describe('readPastedImage', () => {
	function items(entries: Array<{ type: string; blob: Blob | null }>): DataTransferItemList {
		return Object.assign(
			entries.map((entry) => ({ type: entry.type, getAsFile: () => entry.blob })),
			{}
		) as unknown as DataTransferItemList;
	}

	it('wraps the first image on the clipboard in a named file', () => {
		const outcome = readPastedImage(items([{ type: 'image/png', blob: new Blob(['x'], { type: 'image/png' }) }]));
		expect('file' in outcome && outcome.file.name).toMatch(/^pasted-image-\d+\.png$/);
	});

	it('explains a clipboard with nothing to paste', () => {
		expect(readPastedImage(items([{ type: 'text/plain', blob: null }]))).toEqual({
			error: 'Nothing pasteable in the clipboard',
			detail: 'Copy an image first'
		});
	});

	it('reports an unreadable clipboard', () => {
		expect(readPastedImage(null)).toEqual({ error: 'Unable to read the clipboard', detail: null });
	});
});

describe('clickIsOutside', () => {
	it('is outside for a click beyond the field and inside for one within it', () => {
		const root = document.createElement('div');
		const inner = document.createElement('span');
		root.appendChild(inner);
		const other = document.createElement('div');
		document.body.append(root, other);
		expect(clickIsOutside(root, inner)).toBe(false);
		expect(clickIsOutside(root, other)).toBe(true);
	});

	it('treats a click inside the same dialog layer as inside', () => {
		const layer = document.createElement('div');
		layer.className = 'z-50';
		const root = document.createElement('div');
		const sibling = document.createElement('div');
		layer.append(root, sibling);
		document.body.appendChild(layer);
		expect(clickIsOutside(root, sibling)).toBe(false);
	});
});
