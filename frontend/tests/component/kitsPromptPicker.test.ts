// @vitest-environment jsdom
import { describe, it, expect, afterEach, beforeEach, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import type { PromptResourceSpec } from '$lib/utils/promptResources';
import { makeItem, makeKit } from '$lib/kits/fixtures';

const { default: InlineChipEditor } = await import('$lib/components/InlineChipEditor.svelte');
const { default: KitResourceChip } = await import('$lib/components/kits/KitResourceChip.svelte');
const { default: LoadedKitRow } = await import('$lib/components/kits/LoadedKitRow.svelte');
const { createClassComponent } = await import('svelte/legacy');
const { rememberKit, resetKitDirectory, forgetKit } = await import('$lib/stores/kits');
const { encodeKitKey } = await import('$lib/kits/kitMarker');
const { acceptedFrom } = await import('$lib/kits/kitCounts');

const specs: PromptResourceSpec[] = (['image', 'video', 'audio'] as const).map((kind) => ({
	field: 'references',
	kind,
	label: 'References',
	token: `<${kind} @>`
}));

const mara = makeKit('Mara', 'mara', [makeItem('image', 'face'), makeItem('audio', 'voice'), makeItem('text', 'look')], { label: 'Actor' });
const values = { references: [{ kit: mara.id }] };

let target: HTMLDivElement;
let mounted: ReturnType<typeof mount> | undefined;

beforeEach(() => {
	resetKitDirectory();
});

afterEach(() => {
	if (mounted) unmount(mounted);
	mounted = undefined;
	target?.remove();
	document.body.innerHTML = '';
	resetKitDirectory();
});

async function openPicker(resourceFieldValues: Record<string, unknown>) {
	target = document.createElement('div');
	document.body.appendChild(target);
	const onChange = vi.fn();
	const component = createClassComponent({
		component: InlineChipEditor as never,
		target,
		props: { value: '', chips: {}, variant: 'segment-composer', borderless: true, promptResources: specs, resourceFieldValues }
	});
	component.$on?.('change', onChange);
	await vi.waitFor(() => {
		flushSync();
		(component as unknown as { insertResourceTrigger: () => void }).insertResourceTrigger?.();
		flushSync();
		expect(document.querySelector('.resource-picker')).not.toBeNull();
	});
	return onChange;
}

function row(text: string) {
	return Array.from(document.querySelectorAll<HTMLElement>('.picker-row')).find((r) => r.textContent?.includes(text));
}

function click(el: Element | undefined) {
	el?.dispatchEvent(new MouseEvent('click', { bubbles: true }));
	flushSync();
}

describe('the @ picker with kits', () => {
	it('offers the Kits group even before a kit is loaded, saying none is', async () => {
		await openPicker({ references: [] });
		expect(row('Kits')?.textContent).toContain('No kits loaded yet');
	});

	it('lists loaded kits under a Kits group, one row per kit with its count and label', async () => {
		rememberKit(mara);
		await openPicker(values);
		expect(row('Kits')?.textContent).toContain('1 loaded');
		click(row('Kits'));
		const picker = document.querySelector('.resource-picker')!.textContent!;
		expect(picker).toContain('Mara');
		expect(picker).toContain('3 items · Actor');
	});

	it('opens a kit when its row is picked and cites nothing yet', async () => {
		rememberKit(mara);
		const onChange = await openPicker(values);
		click(row('Kits'));
		const before = onChange.mock.calls.length;
		click(row('Mara'));
		expect(onChange.mock.calls.length).toBe(before);
		expect(document.querySelector('.resource-picker')!.textContent).toContain('Everything this model can take');
	});

	it('cites the whole kit only from the first entry of an opened kit', async () => {
		rememberKit(mara);
		const onChange = await openPicker(values);
		click(row('Kits'));
		click(row('Mara'));
		expect(document.querySelectorAll('.picker-row')[0].textContent).toContain('Everything this model can take');
		click(row('Everything this model can take'));
		expect(onChange.mock.calls.at(-1)![0].detail.value).toBe(`@[references:${encodeKitKey(mara.id)}]`);
	});

	it('lists a kit items with kind and reason and cites one when picked', async () => {
		rememberKit(mara);
		const onChange = await openPicker(values);
		click(row('Kits'));
		click(row('Mara'));
		const picker = document.querySelector('.resource-picker')!.textContent!;
		expect(picker).toContain('face');
		expect(picker).toContain('Text · 6 characters');
		click(row('face'));
		expect(onChange.mock.calls.at(-1)![0].detail.value).toBe(`@[references:${encodeKitKey(mara.id, mara.items[0].id)}]`);
	});
});

const editorOf = () => target.querySelector<HTMLElement>('[contenteditable]')!;

function press(key: string) {
	editorOf().dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true }));
	flushSync();
}

const active = () => document.querySelector('.picker-row[aria-selected="true"]')?.textContent ?? '';
const pickerText = () => document.querySelector('.resource-picker')?.textContent ?? '';

describe('the @ picker with kits, by keyboard', () => {
	it('Enter on the Kits group opens it, like a click', async () => {
		rememberKit(mara);
		await openPicker(values);
		const rows = Array.from(document.querySelectorAll('.picker-row'));
		const index = rows.findIndex((r) => r.textContent?.includes('Kits'));
		for (let i = 0; i < index; i += 1) press('ArrowDown');
		expect(active()).toContain('Kits');
		press('Enter');
		expect(pickerText()).toContain('Mara');
		expect(pickerText()).toContain('3 items');
	});

	it('Enter on a kit opens it and cites nothing, ArrowRight does the same', async () => {
		rememberKit(mara);
		const onChange = await openPicker(values);
		click(row('Kits'));
		const before = onChange.mock.calls.length;
		press('Enter');
		expect(pickerText()).toContain('Everything this model can take');
		expect(onChange.mock.calls.length).toBe(before);
		press('ArrowLeft');
		expect(pickerText()).not.toContain('Everything this model can take');
		expect(pickerText()).toContain('Mara');
		press('ArrowRight');
		expect(pickerText()).toContain('Everything this model can take');
	});

	it('Enter on the first entry cites the whole kit, ArrowDown then Enter cites one item', async () => {
		rememberKit(mara);
		const onChange = await openPicker(values);
		click(row('Kits'));
		press('Enter');
		expect(active()).toContain('Everything this model can take');
		press('Enter');
		expect(onChange.mock.calls.at(-1)![0].detail.value).toBe(`@[references:${encodeKitKey(mara.id)}]`);
	});

	it('Enter on an item cites that item', async () => {
		rememberKit(mara);
		const onChange = await openPicker(values);
		click(row('Kits'));
		press('Enter');
		press('ArrowDown');
		expect(active()).toContain('face');
		press('Enter');
		expect(onChange.mock.calls.at(-1)![0].detail.value).toBe(`@[references:${encodeKitKey(mara.id, mara.items[0].id)}]`);
	});

	it('Escape closes the picker', async () => {
		rememberKit(mara);
		await openPicker(values);
		click(row('Kits'));
		press('Escape');
		expect(document.querySelector('.resource-picker')).toBeNull();
	});

	it('Tab opens the kit browser, not the generic one', async () => {
		rememberKit(mara);
		await openPicker(values);
		click(row('Kits'));
		press('Tab');
		expect(document.querySelector('.resource-picker')).toBeNull();
	});
});

describe('the @ picker with kits on an image-only model', () => {
	const imageOnly: PromptResourceSpec[] = [{ field: 'image', kind: 'image', label: 'Image', token: '<image @>' }];

	async function openImageOnly() {
		rememberKit(mara);
		target = document.createElement('div');
		document.body.appendChild(target);
		const onChange = vi.fn();
		const component = createClassComponent({
			component: InlineChipEditor as never,
			target,
			props: { value: '', chips: {}, variant: 'segment-composer', borderless: true, promptResources: imageOnly, resourceFieldValues: { image: [{ kit: mara.id }] } }
		});
		component.$on?.('change', onChange);
		await vi.waitFor(() => {
			flushSync();
			(component as unknown as { insertResourceTrigger: () => void }).insertResourceTrigger?.();
			flushSync();
			expect(document.querySelector('.resource-picker')).not.toBeNull();
		});
		return onChange;
	}

	it('descends into the kit on click instead of citing it', async () => {
		const onChange = await openImageOnly();
		click(row('Kits'));
		const before = onChange.mock.calls.length;
		click(row('Mara'));
		expect(onChange.mock.calls.length).toBe(before);
		expect(pickerText()).toContain('Everything this model can take · 2 of 3');
	});

	it('descends into the kit on Enter instead of citing it', async () => {
		const onChange = await openImageOnly();
		click(row('Kits'));
		const before = onChange.mock.calls.length;
		press('Enter');
		expect(onChange.mock.calls.length).toBe(before);
		expect(pickerText()).toContain('Everything this model can take · 2 of 3');
	});

	it('shows items the model cannot take disabled, with the reason, and ignores them', async () => {
		const onChange = await openImageOnly();
		click(row('Kits'));
		press('Enter');
		const voice = row('voice')!;
		expect(voice.getAttribute('aria-disabled')).toBe('true');
		expect(voice.textContent).toContain('Audio · not taken by this model');
		const before = onChange.mock.calls.length;
		click(voice);
		press('ArrowDown');
		press('ArrowDown');
		expect(active()).toContain('voice');
		press('Enter');
		expect(onChange.mock.calls.length).toBe(before);
		expect(row('face')?.getAttribute('aria-disabled')).toBeNull();
	});
});

describe('KitResourceChip', () => {
	function mountChip(key: string, accepted = acceptedFrom(['image', 'video', 'audio'])) {
		target = document.createElement('div');
		document.body.appendChild(target);
		mounted = mount(KitResourceChip, { target, props: { itemKey: key, accepted, modelName: 'LTX 2.5', onRemove: vi.fn() } });
		flushSync();
		return target.querySelector<HTMLElement>('[data-kit-chip]');
	}

	it('shows the current path and follows a rename', () => {
		rememberKit(mara);
		const chip = mountChip(encodeKitKey(mara.id, mara.items[0].id));
		expect(chip?.dataset.kitChip).toBe('ok');
		expect(chip?.textContent).toContain('mara.face');
		rememberKit({ ...mara, handle: 'mara_lind', items: mara.items.map((item) => (item.id === mara.items[0].id ? { ...item, name: 'portrait' } : item)) });
		flushSync();
		expect(chip?.textContent).toContain('mara_lind.portrait');
	});

	it('is skipped, with the plain line in a popover, when the model does not take the kind', () => {
		rememberKit(mara);
		const chip = mountChip(encodeKitKey(mara.id, mara.items[1].id), acceptedFrom(['image', 'video']));
		expect(chip?.dataset.kitChip).toBe('skipped');
		chip?.click();
		flushSync();
		const popover = document.querySelector('[data-testid="kit-chip-popover"]');
		expect(popover?.textContent).toContain('voice is skipped');
		expect(popover?.textContent).toContain('LTX 2.5 does not take audio');
		expect(popover?.textContent).toContain('Remove from prompt');
	});

	it('is drawn as deleted, struck through, when the kit is gone', () => {
		forgetKit(mara.id);
		const chip = mountChip(encodeKitKey(mara.id, mara.items[0].id));
		expect(chip?.dataset.kitChip).toBe('kit-deleted');
		expect(chip?.querySelector('.line-through')).not.toBeNull();
	});

	it('shows a bare kit with the count it hands the model', () => {
		rememberKit(mara);
		const chip = mountChip(encodeKitKey(mara.id), acceptedFrom(['image']));
		expect(chip?.textContent).toContain('mara');
		expect(chip?.querySelector('[data-kit-chip-count]')?.textContent).toBe('2');
	});
});

describe('LoadedKitRow', () => {
	it('says how many items the model leaves out and marks them', () => {
		target = document.createElement('div');
		document.body.appendChild(target);
		mounted = mount(LoadedKitRow, { target, props: { kit: mara, accepted: acceptedFrom(['image']) } });
		flushSync();
		expect(target.querySelector('[data-kit-usage-note]')?.textContent).toContain('1 skipped');
		expect(target.querySelector('[data-kit-item-chip="voice"]')?.hasAttribute('data-skipped')).toBe(true);
		expect(target.querySelector('[data-kit-item-chip="face"]')?.hasAttribute('data-skipped')).toBe(false);
	});

	it('draws an item removed since the session was saved as a struck chip', () => {
		target = document.createElement('div');
		document.body.appendChild(target);
		const snapshot = { name: 'Mara', handle: 'mara', items: [...mara.items.map((item) => ({ id: item.id, name: item.name })), { id: 'it_deadbeef', name: 'hat' }] };
		mounted = mount(LoadedKitRow, { target, props: { kit: mara, row: { kit: mara.id, snapshot }, accepted: null } });
		flushSync();
		expect(target.querySelector('[data-kit-item-gone="hat"]')).not.toBeNull();
		expect(target.textContent).toContain('1 removed');
	});
});
