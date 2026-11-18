import { describe, expect, it } from 'vitest';
import { render } from 'svelte/server';
import PaintEditsGroup from './PaintEditsGroup.svelte';

function layer(id: number) {
	return { id, name: `Layer ${id}`, visible: true, opacity: 1, width: 4, height: 4, x: 0, y: 0, canvas: null };
}

function stateLine(count: number): string {
	const snapshot = { layers: Array.from({ length: count }, (_, i) => layer(i + 1)), activeIndex: 0, floatingId: null };
	const body = render(PaintEditsGroup, { props: { session: {} as never, state: snapshot as never } }).body;
	const line = body.match(/<span[^>]*data-stack-state="in-progress"[^>]*>([\s\S]*?)<\/span><\/span>\s*<\/span>/)?.[0] ?? body.match(/<span[^>]*data-stack-state="in-progress"[\s\S]*?data-layer-count[\s\S]*?<\/span>[\s\S]*?<\/span>/)?.[0] ?? '';
	return line.replace(/<!--[\s\S]*?-->/g, '').replace(/<[^>]+>/g, '');
}

describe('PaintEditsGroup row text', () => {
	it('separates the state from the layer count with spaces', () => {
		expect(stateLine(2)).toBe('In progress · 2 layers');
	});

	it('uses the singular for one layer', () => {
		expect(stateLine(1)).toBe('In progress · 1 layer');
	});
});
