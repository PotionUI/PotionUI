import { describe, expect, it, vi } from 'vitest';
import { render } from 'svelte/server';
import CanvasZoomControl from './CanvasZoomControl.svelte';

const props = { percent: 83, canZoomIn: true, canZoomOut: true, fitted: true, actual: false, onzoomin: vi.fn(), onzoomout: vi.fn(), onfit: vi.fn(), onactual: vi.fn() };
const html = (extra: Record<string, unknown> = {}) => render(CanvasZoomControl, { props: { ...props, ...extra } }).body;

describe('CanvasZoomControl', () => {
	it('floats in the top-right corner, inset 12px, as a rounded-xl floating group', () => {
		const out = html();
		expect(out).toContain('data-zoom-corner="top-right"');
		expect(out).toContain('top-3');
		expect(out).toContain('right-3');
		expect(out).toContain('rounded-xl');
		expect(out).toContain('shadow-floating');
		expect(out).not.toMatch(/\bbottom-/);
	});

	it('keeps every control and shows the percentage in mono tabular figures', () => {
		const out = html();
		expect(out).toContain('aria-label="Zoom out"');
		expect(out).toContain('aria-label="Zoom in"');
		expect(out).toContain('data-canvas-fit');
		expect(out).toContain('data-canvas-actual');
		expect(out).toMatch(/font-mono[^>]*tabular-nums[^>]*data-canvas-zoom-value[^>]*>83%/);
	});

	it('disables the steps at the zoom limits', () => {
		const out = html({ canZoomIn: false });
		const zoomIn = out.match(/<button[^>]*aria-label="Zoom in"[^>]*>/)?.[0] ?? '';
		const zoomOut = out.match(/<button[^>]*aria-label="Zoom out"[^>]*>/)?.[0] ?? '';
		expect(zoomIn).toMatch(/\sdisabled(=|\s|>)/);
		expect(zoomOut).not.toMatch(/\sdisabled(=|\s|>)/);
	});
});
