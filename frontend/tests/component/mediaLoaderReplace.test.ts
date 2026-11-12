// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { CORE_TOOLS } from './fixtures/mediaToolCatalog';

vi.mock('$lib/services/api/mediaTools', () => ({
	listMediaTools: vi.fn().mockResolvedValue(CORE_TOOLS),
	startMediaToolRun: vi.fn(),
	getMediaToolRun: vi.fn(),
	cancelMediaToolRun: vi.fn(),
	previewMediaTool: vi.fn()
}));

vi.mock('$lib/services/api/index', () => ({
	api: {
		listGenerationMedia: vi.fn().mockResolvedValue({ success: false }),
		getUploadInfo: vi.fn().mockResolvedValue({ success: false })
	}
}));

vi.mock('$lib/utils/storage', () => ({
	storage: { get: vi.fn().mockReturnValue(null), set: vi.fn(), remove: vi.fn() }
}));

vi.mock('$lib/components/form-fields/mediaLoaderProbe', () => ({
	probeMediaFile: vi.fn().mockResolvedValue({})
}));

const postUpload = vi.fn();
vi.mock('$lib/components/form-fields/mediaLoaderUpload', async (importOriginal) => ({
	...(await importOriginal<typeof import('$lib/components/form-fields/mediaLoaderUpload')>()),
	postUpload: (...args: unknown[]) => postUpload(...args)
}));

vi.mock('$lib/components/modals/GenerationHistoryModal.svelte', async () => ({
	default: (await import('./fixtures/ReplaceHistoryStub.svelte')).default
}));
vi.mock('$lib/components/modals/UploadLibraryModal.svelte', async () => ({
	default: (await import('./fixtures/ReplaceLibraryStub.svelte')).default
}));

const { default: MediaLoaderField } = await import('$lib/components/form-fields/MediaLoaderField.svelte');
const { createClassComponent } = await import('svelte/legacy');
const { tick } = await import('svelte');

function mount(props: Record<string, unknown>) {
	const target = document.createElement('div');
	document.body.appendChild(target);
	const component = createClassComponent({ component: MediaLoaderField as any, target, props });
	return { target, component };
}

function historyItem(id: string, label?: string) {
	return {
		path: `generations/${id}/out.png`,
		relative_path: `generations/${id}/out.png`,
		url: `/api/media/generations/${id}/out.png`,
		name: 'out.png',
		type: 'image',
		metadata: { width: 512, height: 512 },
		...(label ? { label } : {})
	};
}

function uploadItem(id: string, label?: string) {
	return {
		path: `uploads/${id}.png`,
		relative_path: `uploads/${id}.png`,
		url: `/api/media/uploads/${id}.png`,
		name: `${id}.png`,
		type: 'image',
		metadata: { width: 512, height: 512 },
		...(label ? { label } : {})
	};
}

function replaceButton(root: ParentNode): HTMLButtonElement {
	return root.querySelector<HTMLButtonElement>('[data-media-inspector] [data-media-replace]')!;
}

async function openReplace(root: ParentNode): Promise<HTMLElement> {
	replaceButton(root).click();
	await tick();
	await tick();
	return document.body.querySelector<HTMLElement>('[data-source-menu="change"]')!;
}

async function choose(menu: HTMLElement, source: string) {
	menu.querySelector<HTMLButtonElement>(`[data-source="${source}"]`)!.click();
	await tick();
	await tick();
}

function stub(kind: 'history' | 'library'): HTMLElement | null {
	return document.body.querySelector<HTMLElement>(`[data-stub="${kind}"]`);
}

async function pickInStub(kind: 'history' | 'library') {
	stub(kind)!.querySelector<HTMLButtonElement>('[data-stub-pick]')!.click();
	await tick();
}

function tile(target: HTMLElement, index: number): HTMLElement {
	return target.querySelectorAll<HTMLElement>('[data-media-tile]')[index];
}

const singleConfig = { title: 'Reference image', accept: 'image/*' };
const multiConfig = { title: 'Reference images', accept: 'image/*', multiple: true, max_items: 3 };

beforeEach(() => {
	document.body.innerHTML = '';
	postUpload.mockReset();
});

describe('the Replace menu on a filled single loader', () => {
	it('opens the source menu with the same entries as the empty tile, headed by the kind', async () => {
		const { target } = mount({ name: 'ref', config: singleConfig, value: historyItem('g1'), onChange: vi.fn() });
		await tick();

		const menu = await openReplace(target);
		expect(menu).toBeTruthy();
		expect(menu.querySelector('[data-source-heading]')!.textContent).toBe('Replace image');
		const labels = Array.from(menu.querySelectorAll('[data-source]')).map((row) => row.querySelector('span')!.textContent!.trim());
		expect(labels).toEqual([
			'Upload file',
			'History',
			'Library',
			'Paste',
			'Draw'
		]);
	});

	it('checks the History row for a generation file and leaves the others plain', async () => {
		const { target } = mount({ name: 'ref', config: singleConfig, value: historyItem('g1'), onChange: vi.fn() });
		await tick();
		const menu = await openReplace(target);

		const current = Array.from(menu.querySelectorAll('[aria-current="true"]')).map((row) => row.getAttribute('data-source'));
		expect(current).toEqual(['history']);
		expect(menu.querySelector('[data-source="history"] svg')).toBeTruthy();
	});

	it('checks the Library row for an upload', async () => {
		const { target } = mount({ name: 'ref', config: singleConfig, value: uploadItem('u1'), onChange: vi.fn() });
		await tick();
		const menu = await openReplace(target);

		const current = Array.from(menu.querySelectorAll('[aria-current="true"]')).map((row) => row.getAttribute('data-source'));
		expect(current).toEqual(['library']);
	});

	it('replaces the item with a History pick, titled for the replacement', async () => {
		const onChange = vi.fn();
		const { target } = mount({ name: 'ref', config: singleConfig, value: uploadItem('u1'), onChange });
		await tick();

		await choose(await openReplace(target), 'history');
		expect(stub('history')!.dataset.title).toBe('Replace image');
		expect(stub('history')!.dataset.multiple).toBe('false');
		await pickInStub('history');

		expect(onChange).toHaveBeenCalledTimes(1);
		expect(onChange.mock.calls[0][0]).toBe('ref');
		expect(onChange.mock.calls[0][1]).toMatchObject({ url: '/api/media/generations/gen-new/new.png' });
	});

	it('replaces the item with a Library pick', async () => {
		const onChange = vi.fn();
		const { target } = mount({ name: 'ref', config: singleConfig, value: historyItem('g1'), onChange });
		await tick();

		await choose(await openReplace(target), 'library');
		expect(stub('library')!.dataset.title).toBe('Replace image');
		await pickInStub('library');

		expect(onChange).toHaveBeenCalledWith('ref', expect.objectContaining({ url: '/api/media/uploads/lib-new.png' }));
	});

	it('replaces the item with an uploaded file', async () => {
		const onChange = vi.fn();
		postUpload.mockResolvedValue({
			data: { path: 'uploads/new.png', relative_path: 'uploads/new.png', url: '/api/media/uploads/new.png', width: 10, height: 10, size: 4 }
		});
		const { target } = mount({ name: 'ref', config: singleConfig, value: historyItem('g1'), onChange });
		await tick();

		const input = target.querySelector<HTMLInputElement>('input[type="file"]')!;
		const clicked = vi.spyOn(input, 'click').mockImplementation(() => {});
		await choose(await openReplace(target), 'browse');
		expect(clicked).toHaveBeenCalled();

		Object.defineProperty(input, 'files', { value: [new File(['x'], 'new.png', { type: 'image/png' })], configurable: true });
		input.dispatchEvent(new Event('change', { bubbles: true }));
		await vi.waitFor(() => expect(onChange).toHaveBeenCalled());

		expect(onChange).toHaveBeenCalledWith('ref', expect.objectContaining({ url: '/api/media/uploads/new.png' }));
	});

	function pasteImage() {
		const blob = new Blob(['x'], { type: 'image/png' });
		const event = new Event('paste', { bubbles: true, cancelable: true });
		Object.defineProperty(event, 'clipboardData', {
			value: { items: [{ type: 'image/png', getAsFile: () => blob }] }
		});
		document.dispatchEvent(event);
	}

	it('pastes over the item once Paste is chosen from the menu', async () => {
		const onChange = vi.fn();
		postUpload.mockResolvedValue({
			data: { path: 'uploads/p.png', relative_path: 'uploads/p.png', url: '/api/media/uploads/p.png', width: 10, height: 10, size: 4 }
		});
		const { target } = mount({ name: 'ref', config: singleConfig, value: historyItem('g1'), onChange });
		await tick();

		await choose(await openReplace(target), 'paste');
		pasteImage();
		await vi.waitFor(() => expect(onChange).toHaveBeenCalled());

		expect(onChange).toHaveBeenCalledWith('ref', expect.objectContaining({ url: '/api/media/uploads/p.png' }));
	});

	it('still ignores a paste when no replacement was asked for', async () => {
		const onChange = vi.fn();
		const { target } = mount({ name: 'ref', config: singleConfig, value: historyItem('g1'), onChange, autoPaste: true });
		await tick();

		pasteImage();
		await tick();
		await tick();
		expect(postUpload).not.toHaveBeenCalled();
		expect(onChange).not.toHaveBeenCalled();
		expect(target).toBeTruthy();
	});
});

describe('the Replace menu on a multi loader', () => {
	function items() {
		return [historyItem('g1', 'first'), uploadItem('u2', 'second'), uploadItem('u3', 'third')];
	}

	async function selectTile(target: HTMLElement, index: number) {
		tile(target, index).click();
		await tick();
	}

	it('replaces the selected item in place with a History pick and keeps its label', async () => {
		const onChange = vi.fn();
		const { target } = mount({ name: 'refs', config: multiConfig, value: items(), onChange });
		await tick();
		await selectTile(target, 1);

		await choose(await openReplace(target), 'history');
		expect(stub('history')!.dataset.multiple).toBe('false');
		await pickInStub('history');

		const next = onChange.mock.calls.at(-1)![1] as Record<string, unknown>[];
		expect(next).toHaveLength(3);
		expect(next[0]).toMatchObject({ label: 'first', url: '/api/media/generations/g1/out.png' });
		expect(next[1]).toMatchObject({ label: 'second', url: '/api/media/generations/gen-new/new.png' });
		expect(next[2]).toMatchObject({ label: 'third' });
	});

	it('replaces in place with a Library pick even when the field is full', async () => {
		const onChange = vi.fn();
		const { target } = mount({ name: 'refs', config: multiConfig, value: items(), onChange });
		await tick();
		await selectTile(target, 2);

		await choose(await openReplace(target), 'library');
		await pickInStub('library');

		const next = onChange.mock.calls.at(-1)![1] as Record<string, unknown>[];
		expect(next).toHaveLength(3);
		expect(next[2]).toMatchObject({ url: '/api/media/uploads/lib-new.png' });
		expect(next[0]).toMatchObject({ label: 'first' });
	});

	it('checks the source of the selected item, not of the first one', async () => {
		const { target } = mount({ name: 'refs', config: multiConfig, value: items(), onChange: vi.fn() });
		await tick();
		await selectTile(target, 1);
		const menu = await openReplace(target);
		expect(menu.querySelector('[aria-current="true"]')!.getAttribute('data-source')).toBe('library');
	});

	it('pastes over the selected item instead of adding one', async () => {
		const onChange = vi.fn();
		postUpload.mockResolvedValue({
			data: { path: 'uploads/p.png', relative_path: 'uploads/p.png', url: '/api/media/uploads/p.png', width: 10, height: 10, size: 4 }
		});
		const { target } = mount({ name: 'refs', config: multiConfig, value: items(), onChange });
		await tick();
		await selectTile(target, 1);

		await choose(await openReplace(target), 'paste');
		const blob = new Blob(['x'], { type: 'image/png' });
		const event = new Event('paste', { bubbles: true, cancelable: true });
		Object.defineProperty(event, 'clipboardData', { value: { items: [{ type: 'image/png', getAsFile: () => blob }] } });
		document.dispatchEvent(event);
		await vi.waitFor(() => expect(onChange).toHaveBeenCalled());

		const next = onChange.mock.calls.at(-1)![1] as Record<string, unknown>[];
		expect(next).toHaveLength(3);
		expect(next[1]).toMatchObject({ url: '/api/media/uploads/p.png', label: 'second' });
	});
});

describe('the Replace menu on a row', () => {
	it('opens from the row and replaces that row only', async () => {
		const onChange = vi.fn();
		const { target } = mount({
			name: 'refs',
			config: multiConfig,
			value: [historyItem('g1', 'first'), uploadItem('u2', 'second')],
			onChange,
			compact: true
		});
		await tick();

		const rows = target.querySelectorAll<HTMLButtonElement>('[data-media-replace]');
		expect(rows).toHaveLength(2);
		rows[1].click();
		await tick();
		await tick();
		const menu = document.body.querySelector<HTMLElement>('[data-source-menu="change"]')!;
		expect(menu.querySelector('[aria-current="true"]')!.getAttribute('data-source')).toBe('library');
		await choose(menu, 'history');
		await pickInStub('history');

		const next = onChange.mock.calls.at(-1)![1] as Record<string, unknown>[];
		expect(next).toHaveLength(2);
		expect(next[0]).toMatchObject({ label: 'first', url: '/api/media/generations/g1/out.png' });
		expect(next[1]).toMatchObject({ label: 'second', url: '/api/media/generations/gen-new/new.png' });
	});

	it('no longer lists a Replace file entry in the row tools menu', async () => {
		const { target } = mount({ name: 'ref', config: singleConfig, value: historyItem('g1'), onChange: vi.fn(), compact: true });
		await tick();
		target.querySelector<HTMLButtonElement>('[data-tools-trigger]')!.click();
		await tick();
		await tick();
		const menu = document.body.querySelector<HTMLElement>('[data-tools-menu]')!;
		expect(menu.querySelector('[data-tool="replace"]')).toBeNull();
	});
});

describe('the empty tile', () => {
	it('names the same sources the Replace menu names', async () => {
		const { target } = mount({ name: 'ref', config: singleConfig, value: null, onChange: vi.fn() });
		await tick();
		const labels = Array.from(target.querySelectorAll('[data-media-dropzone] button')).map((b) => b.textContent!.trim());
		expect(labels).toEqual(['Upload file', 'History', 'Library', 'Paste', 'Draw']);
	});
});
