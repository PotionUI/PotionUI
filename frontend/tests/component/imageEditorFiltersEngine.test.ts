import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { installFakeCanvas } from './helpers/fakeCanvas';

const listFilters = vi.fn();

vi.mock('$lib/services/api/index', () => ({
	api: {
		listFilters: (...args: unknown[]) => listFilters(...args),
		getFilterLut: vi.fn(),
		createMineFilter: vi.fn(),
		updateMineFilter: vi.fn(),
		deleteMineFilter: vi.fn()
	}
}));

const { default: FiltersPanel } = await import('$lib/components/imageEditor/FiltersPanel.svelte');
const { PaintSession } = await import('$lib/components/imageEditor/session');
const { resetFilterCatalog } = await import('$lib/filters/catalog');
const { createClassComponent } = await import('svelte/legacy');
const { tick } = await import('svelte');

interface GoldenCase {
	name: string;
	steps: Array<{ op: string; [key: string]: unknown }>;
	intensity: number;
	expected: number[];
}

const golden = JSON.parse(
	readFileSync(
		resolve(process.cwd(), '../tests/fixtures/filters/golden/filters.json'),
		'utf-8'
	)
) as {
	tolerance: number;
	image: { width: number; height: number; pixels: number[] };
	cases: GoldenCase[];
};

const EMBER_50 = golden.cases.find((entry) => entry.name === 'ember-50')!;

let restoreCanvas: () => void;
let session: InstanceType<typeof PaintSession>;
let host: HTMLElement;
let instance: { $destroy: () => void; $set: (props: Record<string, unknown>) => void };
let unsubscribe: () => void;

async function settle() {
	for (let i = 0; i < 8; i += 1) {
		await tick();
		await new Promise((resolve) => setTimeout(resolve, 0));
	}
}

function input(el: Element, value: string) {
	(el as HTMLInputElement).value = value;
	el.dispatchEvent(new Event('input', { bubbles: true }));
}

function paintGoldenImage(canvas: HTMLCanvasElement) {
	const { width, height, pixels } = golden.image;
	const data = new Uint8ClampedArray(width * height * 4);
	for (let i = 0; i < width * height; i++) {
		data[i * 4] = pixels[i * 3];
		data[i * 4 + 1] = pixels[i * 3 + 1];
		data[i * 4 + 2] = pixels[i * 3 + 2];
		data[i * 4 + 3] = 255;
	}
	canvas.getContext('2d')!.putImageData(new ImageData(data, width, height), 0, 0);
}

function layerRgb(canvas: HTMLCanvasElement): number[] {
	const data = canvas.getContext('2d')!.getImageData(0, 0, canvas.width, canvas.height).data;
	const rgb: number[] = [];
	for (let i = 0; i < data.length; i += 4) rgb.push(data[i], data[i + 1], data[i + 2]);
	return rgb;
}

beforeEach(() => {
	restoreCanvas = installFakeCanvas();
	host = document.createElement('div');
	document.body.appendChild(host);
	resetFilterCatalog();
	listFilters.mockReset().mockResolvedValue({
		filters: [
			{
				id: 'ember',
				name: 'Ember',
				description: '',
				group: 'Colour',
				order: 20,
				intensity: 100,
				source: 'builtin',
				plugin_id: null,
				owned: false,
				has_lut: false,
				lut_size: null,
				lut_url: null,
				steps: EMBER_50.steps,
				unavailable_ops: [],
				needs_plugin: null,
				backend_ok: true,
				revision: 'r1'
			}
		],
		ops: [],
		groups: ['Colour']
	});
	session = new PaintSession();
	session.openBlank({ width: golden.image.width, height: golden.image.height, background: 'white', pen: null });
	session.setTool('filters');
	instance = createClassComponent({
		component: FiltersPanel as never,
		target: host,
		props: { session, state: session.snapshot(), phone: true }
	}) as typeof instance;
	unsubscribe = session.subscribe(() => instance.$set({ state: session.snapshot() }));
});

afterEach(() => {
	unsubscribe();
	instance.$destroy();
	session.detach();
	host.remove();
	document.body.innerHTML = '';
	restoreCanvas();
});

describe('Filters tool on the real engine', () => {
	it('applies Ember at 50% to the layer exactly as the golden fixture', async () => {
		await settle();
		const canvas = session.activeLayer!.canvas;
		paintGoldenImage(canvas);
		const before = layerRgb(canvas);

		(host.querySelector('[aria-label="Ember"]') as HTMLElement).click();
		await settle();
		input(host.querySelector('#filter-intensity')!, '50');
		await settle();
		expect(session.snapshot().filter.intensity).toBe(50);

		const apply = Array.from(host.querySelectorAll('button')).find(
			(button) => (button.textContent || '').trim() === 'Apply'
		)!;
		apply.click();
		await settle();

		const after = layerRgb(canvas);
		let worst = 0;
		for (let i = 0; i < after.length; i++) worst = Math.max(worst, Math.abs(after[i] - EMBER_50.expected[i]));
		expect(worst).toBeLessThanOrEqual(golden.tolerance);
		expect(after).not.toEqual(before);
		expect(session.snapshot().undoLabel).toBe('Filter: Ember 50%');
	});
});
