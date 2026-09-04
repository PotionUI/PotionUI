// @vitest-environment jsdom
//
// The segment composer's literal port (prompt-segments-concept.html) adds a
// `variant="segment-composer"` branch to the chips/pickers InlineChipEditor
// mounts, additively alongside their existing look — chat's ChatChipInput
// (and any other caller) never passes it, so its markup must be byte-for-byte
// what it always was. These prove both halves of that contract: the mock's
// own classes/anatomy show up only under the new variant, and the default
// variant is provably untouched.
import { describe, it, expect, afterEach, vi } from 'vitest';
import { mount, unmount, flushSync, createRawSnippet } from 'svelte';

const { default: InlinePopoverChip } = await import('$lib/components/InlinePopoverChip.svelte');
const { default: ChoiceGroupChip } = await import('$lib/components/ChoiceGroupChip.svelte');
const { default: VariableUsageChip } = await import('$lib/components/VariableUsageChip.svelte');

let target: HTMLDivElement;
let component: ReturnType<typeof mount> | undefined;

afterEach(() => {
	if (component) unmount(component);
	component = undefined;
	target?.remove();
	document.body.innerHTML = '';
	vi.clearAllMocks();
});

function mountEl<P extends Record<string, unknown>>(Component: unknown, props: P) {
	target = document.createElement('div');
	document.body.appendChild(target);
	component = mount(Component as never, { target, props });
	flushSync();
	return target;
}

describe('InlinePopoverChip variant', () => {
	const labelSnippet = createRawSnippet(() => ({ render: () => `<span>trigger label</span>` }));
	const popoverSnippet = createRawSnippet(() => ({ render: () => `<span>body</span>` }));

	it('default variant keeps its original trigger + standing remove button, no mock classes', () => {
		const root = mountEl(InlinePopoverChip, {
			popoverLabel: 'Test popover',
			label: labelSnippet,
			popover: popoverSnippet,
			onremove: vi.fn()
		});

		const chip = root.querySelector('.inline-popover-chip');
		expect(chip).not.toBeNull();
		expect(chip?.classList.contains('chip')).toBe(false);
		expect(root.querySelector('.chip-main')).toBeNull();
		expect(root.querySelector('.chip-config')).toBeNull();
	});

	it('segment-composer variant renders the mock\'s chip-main/chip-config anatomy, no standing remove button', () => {
		const root = mountEl(InlinePopoverChip, {
			variant: 'segment-composer',
			kind: 'choice',
			popoverLabel: 'Test popover',
			label: labelSnippet,
			popover: popoverSnippet,
			headerMark: '{ }',
			headerTitle: 'Choice group',
			onremove: vi.fn()
		});

		const chip = root.querySelector('.chip.choice-chip');
		expect(chip).not.toBeNull();
		expect(root.querySelector('.chip-main')).not.toBeNull();
		expect(root.querySelector('.chip-config')).not.toBeNull();
		// Removal moved into the popover footer — no bare close/remove button on the chip itself.
		expect(chip?.querySelectorAll(':scope > button').length).toBe(2);
	});

	it('segment-composer popover uses the mock\'s popover-head anatomy, not the bare panel', () => {
		const root = mountEl(InlinePopoverChip, {
			variant: 'segment-composer',
			kind: 'variable',
			popoverLabel: 'Test popover',
			label: labelSnippet,
			popover: popoverSnippet,
			headerMark: '$',
			headerTitle: 'surface'
		});

		target.querySelector<HTMLButtonElement>('.chip-main')?.dispatchEvent(
			new MouseEvent('mousedown', { bubbles: true, cancelable: true })
		);
		flushSync();

		const popover = document.querySelector('.variable-popover');
		expect(popover).not.toBeNull();
		expect(popover?.querySelector('.popover-head strong')?.textContent).toBe('surface');
		expect(popover?.querySelector('.popover-mark')?.textContent).toBe('$');
	});
});

describe('ChoiceGroupChip variant', () => {
	it('default variant has no mock choice-chip/choice-editor-list classes', () => {
		const root = mountEl(ChoiceGroupChip, { raw: '{a|b}' });
		expect(root.querySelector('.chip.choice-chip')).toBeNull();
		expect(root.querySelector('.choice-editor-list')).toBeNull();
	});

	it('segment-composer variant renders the mock\'s chip-mark + choice-editor-list', () => {
		const root = mountEl(ChoiceGroupChip, { raw: '{a|b}', variant: 'segment-composer' });
		const chip = root.querySelector('.chip.choice-chip');
		expect(chip).not.toBeNull();
		expect(chip?.querySelector('.chip-mark')?.textContent).toBe('{ }');

		chip?.querySelector<HTMLButtonElement>('.chip-main')?.dispatchEvent(
			new MouseEvent('mousedown', { bubbles: true, cancelable: true })
		);
		flushSync();

		const popover = document.querySelector('.choice-popover');
		expect(popover).not.toBeNull();
		expect(popover?.querySelectorAll('.choice-edit-row').length).toBe(2);
		expect(popover?.querySelector('.popover-actions .danger')?.textContent).toBe('Remove group');
	});
});

describe('VariableUsageChip variant', () => {
	it('default variant has no mock variable-chip classes', () => {
		const root = mountEl(VariableUsageChip, { name: 'style', definition: { type: 'text', value: 'oil painting' } });
		expect(root.querySelector('.chip.variable-chip')).toBeNull();
	});

	it('segment-composer variant puts "Remove usage" in the popover footer, not on the chip', () => {
		const onRemove = vi.fn();
		const root = mountEl(VariableUsageChip, {
			name: 'style',
			definition: { type: 'text', value: 'oil painting' },
			variant: 'segment-composer',
			onRemove
		});
		const chip = root.querySelector('.chip.variable-chip');
		expect(chip).not.toBeNull();
		// No standing remove button on the chip itself — only chip-main/chip-config.
		expect(chip?.querySelectorAll(':scope > button').length).toBe(2);
		expect(chip?.querySelector('button[title="Remove this usage"]')).toBeNull();

		chip?.querySelector<HTMLButtonElement>('.chip-main')?.dispatchEvent(
			new MouseEvent('mousedown', { bubbles: true, cancelable: true })
		);
		flushSync();

		const removeBtn = document.querySelector<HTMLButtonElement>(
			'.variable-popover button[title="Remove this usage"]'
		);
		expect(removeBtn).not.toBeNull();
		removeBtn?.click();
		expect(onRemove).toHaveBeenCalledTimes(1);
	});
});
