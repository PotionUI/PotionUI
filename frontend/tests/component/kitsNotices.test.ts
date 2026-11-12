// @vitest-environment jsdom
import { describe, it, expect, afterEach, beforeEach, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import { makeItem, makeKit } from '$lib/kits/fixtures';

const { default: KitSkippedLine } = await import('$lib/components/kits/KitSkippedLine.svelte');
const { default: KitSessionNotice } = await import('$lib/components/kits/KitSessionNotice.svelte');
const { default: DirectorKitsSection } = await import('$lib/components/kits/DirectorKitsSection.svelte');
const { rememberKit, forgetKit, resetKitDirectory } = await import('$lib/stores/kits');
const { encodeKitKey, snapshotOf } = await import('$lib/kits/kitMarker');

const mara = makeKit('Mara', 'mara', [makeItem('image', 'face'), makeItem('audio', 'voice')]);
const imageOnly = [{ field: 'references', kind: 'image' }];
const everything = [
	{ field: 'references', kind: 'image' },
	{ field: 'references', kind: 'audio' }
];

let target: HTMLDivElement;
let mounted: ReturnType<typeof mount> | undefined;

function mountInto(component: unknown, props: Record<string, unknown>) {
	target = document.createElement('div');
	document.body.appendChild(target);
	mounted = mount(component as never, { target, props });
	flushSync();
}

beforeEach(() => resetKitDirectory());

afterEach(() => {
	if (mounted) unmount(mounted);
	mounted = undefined;
	document.body.innerHTML = '';
	resetKitDirectory();
});

describe('KitSkippedLine', () => {
	const cite = `@[references:${encodeKitKey(mara.id)}]`;

	it('says which items the model leaves out, by kind', () => {
		rememberKit(mara);
		mountInto(KitSkippedLine, { texts: [`Cast ${cite}`], specs: imageOnly });
		expect(target.querySelector('[data-kit-skipped-line]')?.textContent?.replace(/\s+/g, ' ').trim()).toBe(
			'This model does not take audio: voice is skipped.'
		);
	});

	it('is silent when the model takes everything', () => {
		rememberKit(mara);
		mountInto(KitSkippedLine, { texts: [`Cast ${cite}`], specs: everything });
		expect(target.querySelector('[data-kit-skipped-line]')).toBeNull();
	});
});

describe('KitSessionNotice', () => {
	const values = (rows: unknown[]) => ({ references: rows });

	it('names what changed since the session was saved, and "Got it" saves the new names', () => {
		const saved = snapshotOf(mara);
		const renamed = { ...mara, handle: 'mara_lind', items: mara.items.slice(0, 1) };
		rememberKit(renamed);
		const onChange = vi.fn();
		mountInto(KitSessionNotice, { formData: values([{ kit: mara.id, snapshot: saved }]), specs: everything, onChange });
		const text = target.textContent!;
		expect(text).toContain('Mara changed since this session was saved');
		expect(text).toContain('mara_lind');
		expect(text).toContain('voice');
		target.querySelectorAll('button')[0].click();
		const [field, rows] = onChange.mock.calls[0];
		expect(field).toBe('references');
		expect(rows).toEqual([{ kit: mara.id, snapshot: snapshotOf(renamed) }]);
	});

	it('says a deleted kit no longer exists and can drop it from the session', () => {
		forgetKit(mara.id);
		const onChange = vi.fn();
		mountInto(KitSessionNotice, { formData: values([{ kit: mara.id, snapshot: snapshotOf(mara) }]), specs: everything, onChange });
		expect(target.textContent).toContain('Mara no longer exists');
		const remove = Array.from(target.querySelectorAll('button')).find((button) => button.textContent?.includes('Remove from this session'));
		remove?.click();
		expect(onChange).toHaveBeenCalledWith('references', []);
	});

	it('is quiet when nothing changed', () => {
		rememberKit(mara);
		mountInto(KitSessionNotice, { formData: values([{ kit: mara.id, snapshot: snapshotOf(mara) }]), specs: everything, onChange: vi.fn() });
		expect(target.textContent?.trim()).toBe('');
	});
});

describe('DirectorKitsSection', () => {
	it('lists the loaded kits with a count and removes one without touching media', () => {
		rememberKit(mara);
		const media = { path: 'a.png', url: '/a.png' };
		const onChange = vi.fn();
		mountInto(DirectorKitsSection, { formData: { references: [media, { kit: mara.id }] }, specs: everything, onChange });
		expect(target.textContent).toContain('1 loaded');
		expect(target.textContent).toContain('The Director picks from these in every shot.');
		(target.querySelector('[aria-label="Remove Mara"]') as HTMLElement).click();
		expect(onChange).toHaveBeenCalledWith('references', [media]);
	});

	it('is absent when the mode has no field that takes kits', () => {
		mountInto(DirectorKitsSection, { formData: {}, specs: [], onChange: vi.fn() });
		expect(target.querySelector('[data-director-kits]')).toBeNull();
	});
});
