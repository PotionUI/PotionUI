import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { EFFECT_CATALOG } from '../../../../tests/component/fixtures/effectCatalog';
import { effectTools } from '$lib/effects/catalog';

const chains = vi.hoisted(() => [] as Array<{ disposed: boolean; uploads: number; dispose: () => void }>);

vi.mock('$lib/effects/ShaderChain', () => ({
	ShaderChain: {
		create: () => {
			const chain = {
				disposed: false,
				uploads: 0,
				isLost: false,
				setSource: () => undefined,
				hasLut: () => false,
				setLut: () => true,
				uploadPixels(this: { uploads: number }) {
					this.uploads += 1;
					return true;
				},
				readPixels: () => new Uint8Array([9, 8, 7, 255]),
				dispose(this: { disposed: boolean }) {
					this.disposed = true;
				}
			};
			chains.push(chain);
			return chain;
		}
	}
}));

const { StillShader } = await import('./shaderEffect');

const grain = effectTools(EFFECT_CATALOG, 'image').find((effect) => effect.id === 'film_grain')!;
const pixels = () => ({ width: 1, height: 1, data: new Uint8ClampedArray([1, 2, 3, 255]) });
const sources = { shader: vi.fn(async () => 'vec4 effect(vec4 color, vec2 uv) { return color; }'), table: vi.fn(async () => new Uint16Array(24)) };

describe('the still shader', () => {
	beforeEach(() => {
		chains.length = 0;
		vi.stubGlobal('document', { createElement: () => ({}) });
	});

	afterEach(() => {
		vi.unstubAllGlobals();
	});

	it('draws through one chain and keeps it between renders', async () => {
		const still = new StillShader(sources);

		const out = await still.render(pixels(), grain, { amount: 40 });
		await still.render(pixels(), grain, { amount: 60 });

		expect(Array.from(out.data)).toEqual([9, 8, 7, 255]);
		expect(chains).toHaveLength(1);
		expect(chains[0].uploads).toBe(2);
	});

	it('disposes the chain on dispose and opens a new one for the next render', async () => {
		const still = new StillShader(sources);
		await still.render(pixels(), grain, {});

		still.dispose();
		expect(chains[0].disposed).toBe(true);
		still.dispose();

		await still.render(pixels(), grain, {});
		expect(chains).toHaveLength(2);
		expect(chains[1].disposed).toBe(false);
	});

	it('rejects with an AbortError when the signal is aborted, before loading anything', async () => {
		const shader = vi.fn(async () => 'vec4 effect(vec4 color, vec2 uv) { return color; }');
		const still = new StillShader({ ...sources, shader });
		const controller = new AbortController();
		controller.abort();

		await expect(still.render(pixels(), grain, {}, controller.signal)).rejects.toMatchObject({ name: 'AbortError' });
		expect(shader).not.toHaveBeenCalled();
		expect(chains).toHaveLength(0);
	});

	it('rejects with an AbortError when the signal aborts while the shader loads', async () => {
		const controller = new AbortController();
		const slow = { ...sources, shader: vi.fn(async () => (controller.abort(), 'vec4 effect(vec4 color, vec2 uv) { return color; }')) };
		const still = new StillShader(slow);

		await expect(still.render(pixels(), grain, {}, controller.signal)).rejects.toMatchObject({ name: 'AbortError' });
		expect(chains).toHaveLength(0);
	});
});
