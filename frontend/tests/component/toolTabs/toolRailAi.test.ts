import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { mountComponent } from '../helpers/mount';
import { railContentHeight } from '$lib/editors/railFold';

vi.mock('$lib/services/api/index', () => ({ api: { getBaseURL: () => 'http://api.test' } }));
vi.mock('$lib/components/plugins/PluginDistHost.svelte', () => ({ default: () => null }));

const { default: ToolRail } = await import('$lib/components/tool-tabs/frame/ToolRail.svelte');
const { imageRail } = await import('$lib/tool-tabs/image/steps');
const { railTools } = await import('$lib/editors/railTools');
const { describeEditorEntries, emptyEditorSnapshot } = await import('$lib/editors/pluginEntries');
const { buildMenu } = await import('$lib/editors/menuItems');
const { menuContext } = await import('../helpers/menuContext');

let height = 5000;

beforeEach(() => {
	height = 5000;
	localStorage.clear();
	Object.defineProperty(HTMLElement.prototype, 'clientHeight', {
		configurable: true,
		get() {
			return (this as HTMLElement).dataset.toolRail === 'icons' ? height : 0;
		}
	});
	vi.stubGlobal('ResizeObserver', class { constructor(public cb: () => void) {} observe() {} unobserve() {} disconnect() {} });
});

afterEach(() => {
	vi.unstubAllGlobals();
	document.body.innerHTML = '';
	Reflect.deleteProperty(HTMLElement.prototype, 'clientHeight');
});

function rail(active: string | null, groups = imageRail()) {
	const view = mountComponent(ToolRail, { groups, active, variant: 'icons', memoryKey: null, onselect: () => {} });
	return { view, button: (id: string) => view.target.querySelector<HTMLElement>(`button[data-tool="${id}"]`)! };
}

describe('the AI ring on the rail', () => {
	it('puts the Generate group first', () => {
		expect(imageRail()[0].id).toBe('generate');
	});

	it('gives both Generate entries the ring at rest and the active variant when selected', () => {
		const rest = rail('crop');
		for (const id of ['edit_region', 'remove_object']) {
			expect(rest.button(id).className).toContain('ai-ring');
			expect(rest.button(id).className).not.toContain('ai-ring--on');
		}
		rest.view.destroy();
		const on = rail('edit_region');
		expect(on.button('edit_region').className).toContain('ai-ring--on');
		expect(on.button('edit_region').className).not.toContain('is-on');
		expect(on.button('edit_region').className).not.toContain('text-signal');
		expect(on.button('remove_object').className).not.toContain('ai-ring--on');
	});

	it('leaves non-AI entries alone', () => {
		const view = rail('crop');
		for (const id of ['crop', 'paint.brush', 'light', 'film_grain']) expect(view.button(id).className).not.toContain('ai-ring');
		expect(view.button('crop').className).toContain('is-on');
	});

	it('rings a plugin tool that declares ai in its manifest, and not one that does not', () => {
		const snapshot = {
			...emptyEditorSnapshot(),
			tools: [
				{ plugin_id: 'acme', id: 'acme.model', editor: 'image' as const, group: 'look', label: 'Model', ai: true },
				{ plugin_id: 'acme', id: 'acme.plain', editor: 'image' as const, group: 'look', label: 'Plain' }
			]
		};
		const descriptors = describeEditorEntries(snapshot, () => 'r1');
		descriptors.forEach((descriptor) => descriptor.register());
		try {
			const view = rail('crop');
			expect(view.button('acme.model').className).toContain('ai-ring');
			expect(view.button('acme.plain').className).not.toContain('ai-ring');
			expect(railTools.get('image', 'acme.model')?.ai).toBe(true);
		} finally {
			descriptors.forEach((descriptor) => descriptor.dispose());
		}
	});

	it('rings a folded group whose shown entry is an AI entry', () => {
		height = 120;
		const view = rail('edit_region');
		const generate = view.view.target.querySelector<HTMLElement>('button[data-group-button="generate"]')!;
		expect(generate).not.toBeNull();
		expect(generate.className).toContain('ai-ring--on');
		const idle = rail('crop');
		expect(idle.view.target.querySelector<HTMLElement>('button[data-group-button="generate"]')!.className).toMatch(/ai-ring(?!--)/);
		expect(idle.view.target.querySelector<HTMLElement>('button[data-group-button="shape"]')!.className).not.toContain('ai-ring');
	});

	it('does not change the fold measurement or the button size', () => {
		const groups = imageRail();
		const plain = groups.map((group) => ({ ...group, tools: group.tools.map(({ ai: _ai, ...tool }) => tool) }));
		const inputs = (list: typeof groups) => list.map((group) => ({ id: group.id, count: group.tools.length, fold: group.fold }));
		expect(railContentHeight(inputs(groups), new Set())).toBe(railContentHeight(inputs(plain), new Set()));
		const withRing = rail('crop', groups);
		const withoutRing = rail('crop', plain);
		const sizeClasses = (el: HTMLElement) => el.className.split(' ').filter((name) => /^(h|w)-/.test(name)).join(' ');
		expect(sizeClasses(withRing.button('edit_region'))).toBe(sizeClasses(withoutRing.button('edit_region')));
		expect(withRing.view.target.querySelectorAll('button[data-tool]').length).toBe(withoutRing.view.target.querySelectorAll('button[data-tool]').length);
	});

	it('marks the AI rows in the Tools menu and not the others', () => {
		const rows = buildMenu('tools', menuContext([], { rail: imageRail(), groupedTools: true })).groups.flat();
		const generate = rows.find((row) => row.id === 'toolgroup:generate')!;
		expect(generate.children!.every((child) => 'ai' in child && child.ai === true)).toBe(true);
		const shape = rows.find((row) => row.id === 'toolgroup:shape')!;
		expect(shape.children!.some((child) => 'ai' in child && child.ai)).toBe(false);
	});
});
