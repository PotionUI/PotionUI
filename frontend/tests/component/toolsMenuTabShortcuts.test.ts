// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import { settle } from './helpers/settle';

vi.mock('$lib/tools/pluginTools', () => ({ retryMediaToolCatalog: vi.fn() }));

import ToolsMenu from '$lib/components/tools/ToolsMenu.svelte';
import { buildLibraryToolContext, type MediaTool, type MediaToolGroup } from '$lib/tools/tools';

function tool(id: string, extra: Partial<MediaTool> = {}): MediaTool {
	return {
		id,
		label: id.toUpperCase(),
		icon: 'extension',
		category: 'edit',
		source: 'core',
		scopes: ['history', 'library'],
		applies: () => ({ enabled: true }),
		...extra
	};
}

const entry = (id: string, extra: Partial<MediaTool> = {}) => ({ tool: tool(id, extra), availability: { enabled: true } });

const groups = (): MediaToolGroup[] => [
	{
		category: { id: 'edit', label: 'Edit', order: -20 },
		tools: [
			entry('crop'),
			entry('resize'),
			entry('trim', { note: 'Saves one clip', catalogToolId: 'trim' }),
			entry('boomerang.loop', { source: 'plugin', note: 'Boomerang' })
		]
	},
	{ category: { id: 'effects', label: 'Effects', order: -10 }, tools: [entry('apply-look', { category: 'effects' }), entry('look:noir', { category: 'effects', note: 'Black and white' })] },
	{
		category: { id: 'compose', label: 'Compose', order: 20 },
		tools: [entry('stitch', { category: 'compose' }), entry('edit-image', { category: 'compose', shortcut: 'E' })]
	}
];

const media = (kind: string, id: string) =>
	({ id, filename: `${id}.bin`, media_type: kind, url: `/u/${id}`, tags: [] }) as never;

let cleanup: (() => void) | undefined;

function render(items: never[], props: Record<string, unknown> = {}) {
	const target = document.createElement('div');
	document.body.appendChild(target);
	const instance = mount(ToolsMenu, {
		target,
		props: {
			open: true,
			scope: 'library',
			groups: groups(),
			ctx: buildLibraryToolContext(items, null),
			onToggle: vi.fn(),
			onClose: vi.fn(),
			onPick: vi.fn(),
			...props
		}
	});
	flushSync();
	cleanup = () => {
		unmount(instance);
		target.remove();
	};
}

afterEach(() => {
	cleanup?.();
	cleanup = undefined;
	document.body.innerHTML = '';
});

const menu = () => document.body.querySelector<HTMLElement>('[data-tools-menu]')!;
const tool$ = (id: string) => menu().querySelector<HTMLElement>(`[data-tool="${id}"]`)!;
const glyphs = () =>
	Array.from(menu().querySelectorAll('[data-opens-elsewhere]')).map((node) => node.closest('[data-tool]')?.getAttribute('data-tool'));
const footer = () => menu().querySelector<HTMLElement>('[data-tab-footer]');

async function openSubmenu(id: 'edit' | 'effects') {
	menu().querySelector<HTMLButtonElement>(`[data-submenu-row="${id}"]`)!.click();
	flushSync();
	await settle(3);
	await new Promise((resolve) => setTimeout(resolve, 40));
	flushSync();
}

describe('tools that open an editor tab', () => {
	it('marks the Edit rows an image opens in the Image editor, and not the plugin row', async () => {
		render([media('image', 'a')]);
		await openSubmenu('edit');

		expect(glyphs()).toEqual(['crop', 'resize']);
		expect(tool$('trim').querySelector('[data-opens-elsewhere]')).toBeNull();
		expect(tool$('boomerang.loop').querySelector('[data-opens-elsewhere]')).toBeNull();
	});

	it('names the Image editor tab in the footer of the Edit submenu', async () => {
		render([media('image', 'a')]);
		await openSubmenu('edit');

		expect(footer()!.textContent).toContain('Image editor tab');
		expect(footer()!.textContent).toContain('this picture');
	});

	it('names the Video editor for a clip and marks Trim instead of nothing', async () => {
		render([media('video', 'v')]);
		await openSubmenu('edit');

		expect(glyphs()).toEqual(['crop', 'resize', 'trim']);
		expect(footer()!.textContent).toContain('Video editor tab');
		expect(footer()!.textContent).toContain('this clip');
	});

	it('drops the save note of a row that opens a tab, and keeps it for a row that does not', async () => {
		render([media('video', 'v')]);
		await openSubmenu('edit');

		expect(tool$('trim').querySelector('[data-tool-sub]')).toBeNull();
		expect(tool$('boomerang.loop').querySelector('[data-tool-sub]')!.textContent).toBe('Boomerang');
	});

	it('keeps the group note of a recent look', async () => {
		render([media('image', 'a')]);
		await openSubmenu('effects');

		expect(tool$('look:noir').querySelector('[data-tool-sub]')!.textContent).toBe('Black and white');
	});

	it('says one tab per item for a selection of several', async () => {
		render([media('image', 'a'), media('image', 'b'), media('image', 'c')]);
		await openSubmenu('edit');

		expect(footer()!.textContent).toContain('one Image editor tab per picture');
	});

	it('marks Apply a look in the Effects submenu and names the Image editor', async () => {
		render([media('image', 'a')]);
		await openSubmenu('effects');

		expect(glyphs()).toEqual(['apply-look', 'look:noir']);
		expect(footer()!.textContent).toContain('Image editor tab');
	});

	it('shows the E hint instead of the glyph on Open in editor, and a footer on the top level', () => {
		render([media('image', 'a')]);

		expect(tool$('edit-image').querySelector('kbd')).not.toBeNull();
		expect(tool$('edit-image').querySelector('[data-opens-elsewhere]')).toBeNull();
		expect(tool$('stitch').querySelector('[data-opens-elsewhere]')).toBeNull();
		expect(footer()!.textContent).toContain('Image editor tab');
	});

	it('shows no glyph and no footer for audio, which keeps its dialogs', async () => {
		render([media('audio', 's')]);
		await openSubmenu('edit');

		expect(glyphs()).toEqual([]);
		expect(footer()).toBeNull();
	});

	it('shows none of it in a field menu', async () => {
		render([media('image', 'a')], { scope: 'field' });

		expect(glyphs()).toEqual([]);
		expect(footer()).toBeNull();
	});

	it('shows none of it without a context to resolve the media from', async () => {
		render([media('image', 'a')], { ctx: null });
		await openSubmenu('edit');

		expect(glyphs()).toEqual([]);
		expect(footer()).toBeNull();
	});
});
