// @vitest-environment jsdom
//
// Repro for the maintainer's report (2026-09-08): a segment contains a
// phrasebook chip (`#category.path` marker + a `segment.chips` entry). The
// user applies an LLM chat proposal for that segment — `applySegmentUpdate`
// (promptSegments.ts) re-parses the proposed content with
// `shuffleCategoryChips: true`, minting a BRAND NEW chip id for the marker
// (mergeChipSelections keeps only the new id, carrying the old choice's
// value/label/shuffle across by category path) — while the surrounding
// prose usually changes too, so both `value` and `chips` change together.
//
// That fires two independent InlineChipEditor.svelte reactive paths in the
// same flush: the `value`-watcher's `syncDOMWithValue()` (full DOM rebuild)
// and the `chips`-hash watcher's `remountChip()` (finds-and-mounts the new
// id's container directly). Both eventually call `mountChips()`, whose
// "already mounted, skip" guard checked `container.querySelector('.inline-chip')`
// — but InlineChip.svelte's own root carries `.phrase-chip`, never
// `.inline-chip` (that class belongs to the *container* span mountChips is
// walking). The guard therefore never matched, so any mountChips() pass that
// landed on a container remountChip() had already populated stacked a SECOND
// InlineChip on top of the first — two rendered chips in one container,
// exactly the reported symptom.
import { describe, it, expect, afterEach } from 'vitest';
import { flushSync } from 'svelte';
import type { ChipData } from '$lib/types/segments';

const { default: InlineChipEditor } = await import('$lib/components/InlineChipEditor.svelte');
const { createClassComponent } = await import('svelte/legacy');

let target: HTMLDivElement;
let component: ReturnType<typeof createClassComponent> | undefined;

function makeChip(id: string): ChipData {
	return {
		id,
		categoryPath: 'style.painterly',
		valueId: 'v1',
		label: 'Painterly',
		value: 'painterly',
		allValues: [{ id: 'v1', label: 'Painterly', value: 'painterly' }],
		shuffle: false,
		autoRegen: false
	};
}

afterEach(() => {
	target?.remove();
	document.body.innerHTML = '';
});

describe('InlineChipEditor apply-from-chat chip swap', () => {
	it('renders exactly one chip after value AND chip id change together, as an apply does', async () => {
		const value = 'a portrait, #style.painterly, soft light';
		target = document.createElement('div');
		document.body.appendChild(target);

		component = createClassComponent({
			component: InlineChipEditor as never,
			target,
			props: { value, chips: { 'chip-old': makeChip('chip-old') }, variant: 'segment-composer', borderless: true }
		});
		flushSync();
		// Let the mount's own pending tick()-scheduled work (syncDOMWithValue
		// on the initial `value`) fully settle before mutating props below —
		// otherwise a still-pending tick() from mount can run AFTER the
		// mutation and coincidentally read the already-mutated props,
		// masking a real bug under test.
		await new Promise((r) => setTimeout(r, 0));
		await new Promise((r) => setTimeout(r, 0));
		flushSync();
		expect(target.querySelectorAll('.inline-chip-container').length).toBe(1);
		expect(target.querySelectorAll('.phrase-chip').length).toBe(1);

		// Simulate an apply: the LLM's revised content keeps the marker but
		// changes the surrounding prose, and the re-parse mints a new chip id.
		const newValue = 'a stunning portrait, #style.painterly, warm soft light';
		component.$set({ value: newValue, chips: { 'chip-new': makeChip('chip-new') } });
		flushSync();
		await new Promise((r) => setTimeout(r, 0));
		await new Promise((r) => setTimeout(r, 0));
		await new Promise((r) => setTimeout(r, 0));
		flushSync();

		const containers = target.querySelectorAll('.inline-chip-container');
		expect(containers.length).toBe(1);
		expect((containers[0] as HTMLElement).dataset.chipId).toBe('chip-new');
		expect(target.querySelectorAll('.phrase-chip').length).toBe(1);
	});

	it('rebuilds the chip when the chip id swaps but the marker text (and so `value`) is byte-identical', async () => {
		// The narrower case: the LLM's revised content reproduces the exact
		// same marker with no other change to the segment's prose, so `value`
		// never changes — only `chips` does. The `value`-watcher never fires,
		// so this exercises the `chips`-hash watcher's own id-mismatch
		// detection in isolation. Before the fix, the DOM kept the OLD
		// container (data-chip-id="chip-old") forever, since `remountChip`
		// can only find a container for the id it's given, and nothing ever
		// rebuilt the DOM to produce one for "chip-new".
		const value = 'a portrait, #style.painterly, soft light';
		target = document.createElement('div');
		document.body.appendChild(target);

		component = createClassComponent({
			component: InlineChipEditor as never,
			target,
			props: { value, chips: { 'chip-old': makeChip('chip-old') }, variant: 'segment-composer', borderless: true }
		});
		flushSync();
		// Let the mount's own pending tick()-scheduled work (syncDOMWithValue
		// on the initial `value`) fully settle before mutating props below —
		// otherwise a still-pending tick() from mount can run AFTER the
		// mutation and coincidentally read the already-mutated props,
		// masking a real bug under test.
		await new Promise((r) => setTimeout(r, 0));
		await new Promise((r) => setTimeout(r, 0));
		flushSync();
		expect(target.querySelectorAll('.inline-chip-container').length).toBe(1);
		expect(target.querySelectorAll('.phrase-chip').length).toBe(1);

		component.$set({ value, chips: { 'chip-new': makeChip('chip-new') } });
		flushSync();
		await new Promise((r) => setTimeout(r, 0));
		await new Promise((r) => setTimeout(r, 0));
		await new Promise((r) => setTimeout(r, 0));
		flushSync();

		const containers = target.querySelectorAll('.inline-chip-container');
		expect(containers.length).toBe(1);
		expect((containers[0] as HTMLElement).dataset.chipId).toBe('chip-new');
		expect(target.querySelectorAll('.phrase-chip').length).toBe(1);
	});
});
