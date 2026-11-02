import { describe, expect, it, vi } from 'vitest';
import { showCanvas } from './displayCanvas';

class FakeDisplay extends EventTarget {
	width = 300;
	height = 150;
	draws: unknown[] = [];
	context = { drawImage: (source: unknown) => this.draws.push(source) };
	getContext() {
		return this.context;
	}
}

function picture(width: number, height: number) {
	return { width, height } as unknown as HTMLCanvasElement;
}

function mount(canvas: HTMLCanvasElement | null, onrestore = vi.fn()) {
	const node = new FakeDisplay();
	const action = showCanvas(node as unknown as HTMLCanvasElement, { canvas, onrestore });
	return { node, action, onrestore };
}

function loseAndRestore(node: FakeDisplay) {
	node.dispatchEvent(new Event('contextlost'));
	node.dispatchEvent(new Event('contextrestored'));
}

describe('showCanvas', () => {
	it('draws the rendered picture at its own size and again on every update', () => {
		const first = picture(40, 20);
		const { node, action } = mount(first);
		expect([node.width, node.height]).toEqual([40, 20]);
		expect(node.draws).toEqual([first]);
		const next = picture(10, 10);
		action.update({ canvas: next });
		expect([node.width, node.height]).toEqual([10, 10]);
		expect(node.draws).toEqual([first, next]);
	});

	it('repaints the latest picture and asks for a fresh render when a lost surface comes back', () => {
		const first = picture(40, 20);
		const onrestore = vi.fn();
		const { node, action } = mount(first, onrestore);
		const latest = picture(16, 16);
		action.update({ canvas: latest, onrestore });
		node.draws = [];

		node.dispatchEvent(new Event('contextlost'));
		expect(node.draws).toEqual([]);
		expect(onrestore).not.toHaveBeenCalled();

		node.dispatchEvent(new Event('contextrestored'));
		expect(node.draws).toEqual([latest]);
		expect([node.width, node.height]).toEqual([16, 16]);
		expect(onrestore).toHaveBeenCalledTimes(1);
	});

	it('still asks for a render when there is nothing to repaint yet, and stops listening once destroyed', () => {
		const { node, action, onrestore } = mount(null);
		loseAndRestore(node);
		expect(node.draws).toEqual([]);
		expect(onrestore).toHaveBeenCalledTimes(1);
		action.destroy();
		loseAndRestore(node);
		expect(onrestore).toHaveBeenCalledTimes(1);
	});
});
