import { afterEach, describe, expect, it, vi } from 'vitest';
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

type Chunk = { file: string; imports?: string[]; dynamicImports?: string[]; css?: string[] };
type Manifest = Record<string, Chunk>;

const budget = await import(new URL('../../../scripts/check-bundle-budget.mjs', import.meta.url).href);

const manifest: Manifest = {
	'x/.svelte-kit/generated/client-optimized/nodes/0.js': { file: 'n0.js', imports: ['shared.js'], dynamicImports: ['lazy.js'], css: ['n0.css'] },
	'x/.svelte-kit/generated/client-optimized/app.js': { file: 'app.js', imports: ['shared.js'] },
	'node_modules/@sveltejs/kit/src/runtime/client/entry.js': { file: 'start.js', imports: ['shared.js', 'other.js'] },
	'shared.js': { file: 'shared.js', css: ['n0.css', 'shared.css'] },
	'other.js': { file: 'other.js' },
	'lazy.js': { file: 'lazy.js', css: ['lazy.css'] }
};

const sizes: Record<string, number> = {
	'n0.js': 100,
	'app.js': 10,
	'start.js': 20,
	'shared.js': 1000,
	'other.js': 5,
	'lazy.js': 99999,
	'n0.css': 300,
	'shared.css': 50,
	'lazy.css': 77777
};
const sizeOf = (file: string) => sizes[file];

describe('bundle budget closure', () => {
	it('sums the static closure once per chunk and skips dynamic imports', () => {
		const result = budget.measure(manifest, sizeOf);
		expect(result.js_raw).toBe(100 + 10 + 20 + 1000 + 5);
		expect(result.chunks).toBe(5);
	});

	it('counts shared css once', () => {
		const result = budget.measure(manifest, sizeOf);
		expect(result.css_raw).toBe(350);
		expect(result.css).toHaveLength(2);
	});

	it('fails loudly when a root disappears from the manifest', () => {
		const broken = { ...manifest };
		delete broken['node_modules/@sveltejs/kit/src/runtime/client/entry.js'];
		expect(() => budget.measure(broken, sizeOf)).toThrow(/entry\.js/);
	});

	it('finds roots whatever the outDir prefix is', () => {
		const moved: Manifest = {};
		for (const [key, value] of Object.entries(manifest)) moved[key.replace('x/.svelte-kit', 'C:/other/.svelte-kit')] = value;
		expect(budget.measure(moved, sizeOf).js_raw).toBe(1135);
	});
});

describe('bundle budget verdict', () => {
	const result = { js_raw: 1000, css_raw: 500 };

	it('passes at or under the limit', () => {
		expect(budget.evaluate(result, { js_raw: 1000, css_raw: 500 }).failures).toEqual([]);
	});

	it('names every metric over its limit', () => {
		expect(budget.evaluate(result, { js_raw: 999, css_raw: 499 }).failures).toEqual(['js_raw', 'css_raw']);
	});

	it('nudges a tighter budget when more than 10 percent under', () => {
		expect(budget.evaluate(result, { js_raw: 2000, css_raw: 510 }).notes).toEqual(['js_raw']);
	});

	it('writes measured plus 3 percent rounded up to the next KB and keeps the target', () => {
		const next = budget.nextBudget({ js_raw: 100000, css_raw: 2000 }, { target: { js_raw: 5 } });
		expect(next.js_raw).toBe(Math.ceil((100000 * 1.03) / 1024) * 1024);
		expect(next.css_raw).toBe(3072);
		expect(next.target).toEqual({ js_raw: 5 });
	});
});

describe('bundle budget route closure', () => {
	const routed: Manifest = {
		...manifest,
		'x/.svelte-kit/generated/client-optimized/nodes/7.js': { file: 'n7.js', imports: ['shared.js', 'route.js'], css: ['route.css', 'shared.css'] },
		'route.js': { file: 'route.js', imports: ['other.js'] }
	};
	const routeSizes: Record<string, number> = { ...sizes, 'n7.js': 40, 'route.js': 60, 'route.css': 9 };
	const routeSizeOf = (file: string) => routeSizes[file];
	const appSource = 'ht={"/":[3],"/generate":[7,[2]],"/history":[8]}';

	it('finds the node of a route in the app entry', () => {
		expect(budget.routeNodeKey(routed, appSource, '/generate')).toMatch(/nodes\/7\.js$/);
		expect(() => budget.routeNodeKey(routed, appSource, '/missing')).toThrow(/\/missing/);
	});

	it('counts only what the route adds beyond the shell', () => {
		const shell = budget.measure(routed, routeSizeOf);
		const route = budget.measureRoute(routed, routeSizeOf, appSource, '/generate', shell);
		expect(route.js_raw).toBe(40 + 60);
		expect(route.css_raw).toBe(9);
	});

	it('fails the verdict for a route over its own budget and names it', () => {
		const verdict = budget.evaluate({ js_raw: 1, css_raw: 1 }, { js_raw: 10, css_raw: 10, routes: { '/generate': { js_raw: 5, css_raw: 5 } } }, { '/generate': { js_raw: 6, css_raw: 1 } });
		expect(verdict.failures).toEqual(['/generate js_raw']);
	});
});

describe('bundle budget guards', () => {
	it('throws on an imported chunk missing from the manifest', () => {
		const broken: Manifest = { ...manifest, 'shared.js': { file: 'shared.js', imports: ['gone.js'] } };
		expect(() => budget.measure(broken, sizeOf)).toThrow(/gone\.js/);
	});

	it('throws when the budget lacks a key', () => {
		expect(() => budget.evaluate({ js_raw: 1, css_raw: 1 }, { js_raw: 10 })).toThrow(/css_raw/);
		expect(() => budget.evaluate({ js_raw: 1, css_raw: 1 }, { js_raw: 10, css_raw: 10 }, { '/generate': { js_raw: 1, css_raw: 1 } })).toThrow(/\/generate js_raw/);
	});
});

describe('bundle budget exit codes', () => {
	const dirs: string[] = [];
	afterEach(() => {
		vi.restoreAllMocks();
		for (const dir of dirs.splice(0)) rmSync(dir, { recursive: true, force: true });
	});

	function fixture(budgetValue: unknown): { dir: string; budget: string } {
		const root = mkdtempSync(join(tmpdir(), 'potionui-budget-'));
		dirs.push(root);
		const client = join(root, 'client');
		mkdirSync(join(client, '.vite'), { recursive: true });
		const files: Record<string, string> = { 'n0.js': 'a'.repeat(100), 'app.js': 'ht={"/generate":[7]}', 'start.js': 'b'.repeat(10), 'n7.js': 'c'.repeat(50) };
		for (const [name, body] of Object.entries(files)) writeFileSync(join(client, name), body);
		const entries: Manifest = {
			'x/client-optimized/nodes/0.js': { file: 'n0.js' },
			'x/client-optimized/app.js': { file: 'app.js' },
			'node_modules/k/src/runtime/client/entry.js': { file: 'start.js' },
			'x/client-optimized/nodes/7.js': { file: 'n7.js' }
		};
		writeFileSync(join(client, '.vite', 'manifest.json'), JSON.stringify(entries));
		const budgetPath = join(root, 'budget.json');
		if (budgetValue !== null) writeFileSync(budgetPath, JSON.stringify(budgetValue));
		return { dir: client, budget: budgetPath };
	}

	const quiet = () => {
		vi.spyOn(console, 'log').mockImplementation(() => undefined);
		vi.spyOn(console, 'error').mockImplementation(() => undefined);
	};
	const limits = (js: number) => ({ js_raw: js, css_raw: 10, routes: { '/generate': { js_raw: js, css_raw: 10 } } });

	it('exits 0 under budget and 1 over it', () => {
		quiet();
		const under = fixture(limits(1000));
		expect(budget.main(['--dir', under.dir, '--budget', under.budget])).toBe(0);
		const over = fixture(limits(20));
		expect(budget.main(['--dir', over.dir, '--budget', over.budget])).toBe(1);
	});

	it('exits 2 for a missing manifest, a missing budget file and a missing budget key', () => {
		quiet();
		expect(budget.main(['--dir', join(tmpdir(), 'potionui-budget-none'), '--budget', 'x'])).toBe(2);
		const none = fixture(null);
		expect(budget.main(['--dir', none.dir, '--budget', none.budget])).toBe(2);
		const partial = fixture({ js_raw: 1000 });
		expect(budget.main(['--dir', partial.dir, '--budget', partial.budget])).toBe(2);
	});
});
