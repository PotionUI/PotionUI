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

	it('cites a whole kit by its stable id when its row is picked', async () => {
		rememberKit(mara);
		const onChange = await openPicker(values);
		click(row('Kits'));
		click(row('Mara'));
		expect(onChange.mock.calls.at(-1)![0].detail.value).toBe(`@[references:${encodeKitKey(mara.id)}]`);
	});

	it('opens a kit with its chevron and cites one item, telling what the model takes', async () => {
		rememberKit(mara);
		const onChange = await openPicker(values);
		click(row('Kits'));
		click(row('Mara')?.querySelector('.row-drill') ?? undefined);
		const picker = document.querySelector('.resource-picker')!.textContent!;
		expect(picker).toContain('Everything this model can take');
		expect(picker).toContain('face');
		expect(picker).toContain('Text · 6 characters');
		click(row('face'));
		expect(onChange.mock.calls.at(-1)![0].detail.value).toBe(`@[references:${encodeKitKey(mara.id, mara.items[0].id)}]`);
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
