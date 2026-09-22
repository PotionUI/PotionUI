import { describe, it, expect, vi, afterEach } from 'vitest';
import { mount, unmount, flushSync } from 'svelte';
import type { RichSegment, SegmentTemplate } from '$lib/types/segments';

const { default: TemplateCard } = await import('../../src/routes/prompts/sections/TemplateCard.svelte');

function slot(name: string | null, overrides: Partial<RichSegment> = {}): RichSegment {
	return { type: 'content', content: '', chips: {}, enabled: true, name, ...overrides };
}

function slotRows(): HTMLElement[] {
	return Array.from(target.querySelectorAll<HTMLElement>('[data-template-slots] [data-template-slot]'));
}

function template(overrides: Partial<SegmentTemplate> = {}): SegmentTemplate {
	return {
		id: 'template-1',
		name: 'Portrait base',
		description: 'A three slot portrait layout',
		segments: [slot('Subject'), slot('Lighting'), slot(null)],
		tags: ['portrait', 'lighting', 'studio'],
		created_at: '2026-09-01T00:00:00.000Z',
		updated_at: '2026-09-20T00:00:00.000Z',
		...overrides
	};
}

let target: HTMLDivElement;
let component: ReturnType<typeof mount> | null = null;

function mountCard(overrides: Partial<Record<string, unknown>> = {}) {
	target = document.createElement('div');
	document.body.appendChild(target);
	component = mount(TemplateCard, {
		target,
		props: {
			template: template(),
			selected: false,
			onToggleSelect: vi.fn(),
			onOpen: vi.fn(),
			onApply: vi.fn(),
			onDuplicate: vi.fn(),
			onDelete: vi.fn(),
			...overrides
		}
	});
	flushSync();
}

afterEach(() => {
	if (component) {
		unmount(component);
		component = null;
	}
	target?.remove();
});

describe('TemplateCard', () => {
	it('renders the template name', () => {
		mountCard();
		expect(target.textContent).toContain('Portrait base');
	});

	it('renders the hover actions inside the footer row', () => {
		mountCard({ template: template({ name: 'Portrait base with three named slots' }) });
		target.style.width = '300px';
		flushSync();
		const card = target.querySelector('[data-template-card]') as HTMLElement;
		const title = card.querySelector('h3') as HTMLElement;
		const footer = card.querySelector('[data-card-footer]') as HTMLElement;
		const actions = card.querySelector('[data-card-actions]') as HTMLElement;
		expect(title.textContent).toBe('Portrait base with three named slots');
		expect(footer.contains(actions)).toBe(true);
		expect(actions.classList.contains('absolute')).toBe(false);
		expect(title.parentElement!.contains(actions)).toBe(false);
	});

	it('renders one row per slot in order and names an unnamed slot by its position', () => {
		mountCard({
			template: template({
				segments: [
					slot('Subject', { content: 'a portrait of a woman' }),
					slot('Lighting', { content: 'soft window light' }),
					slot(null, { content: 'film grain' })
				]
			})
		});
		const rows = slotRows();
		expect(rows.map((row) => row.querySelector('.font-medium')?.textContent?.trim())).toEqual([
			'Subject',
			'Lighting',
			'Slot 3'
		]);
		expect(rows.map((row) => row.textContent?.replace(/\s+/g, ' ').trim())).toEqual([
			'Subject a portrait of a woman',
			'Lighting soft window light',
			'Slot 3 film grain'
		]);
	});

	it('renders an empty slot as a dashed box that says so', () => {
		mountCard({ template: template({ segments: [slot('Subject')] }) });
		const [row] = slotRows();
		expect(row.dataset.templateSlot).toBe('empty');
		expect(row.classList.contains('border-dashed')).toBe(true);
		expect(row.textContent).toContain('Empty slot');
	});

	it('does not dash a slot that has starter text', () => {
		mountCard({ template: template({ segments: [slot('Subject', { content: 'a cat' })] }) });
		const [row] = slotRows();
		expect(row.dataset.templateSlot).toBe('content');
		expect(row.classList.contains('border-dashed')).toBe(false);
		expect(row.textContent).not.toContain('Empty slot');
	});

	it('renders a break slot as the prompt break divider', () => {
		mountCard({
			template: template({
				segments: [slot('Subject', { content: 'a cat' }), slot(null, { type: 'break' }), slot('Style')]
			})
		});
		const rows = slotRows();
		expect(rows[1].dataset.templateSlot).toBe('break');
		expect(rows[1].textContent?.trim()).toBe('Prompt break');
		expect(rows[1].querySelectorAll('.h-px').length).toBe(2);
	});

	it('marks a disabled slot Off and dims it', () => {
		mountCard({
			template: template({
				segments: [slot('Subject', { content: 'a cat' }), slot('Style', { content: 'oil', enabled: false })]
			})
		});
		const [on, off] = slotRows();
		expect(on.querySelector('[data-template-slot-off]')).toBeNull();
		expect(on.classList.contains('opacity-60')).toBe(false);
		expect(off.querySelector('[data-template-slot-off]')?.textContent?.trim()).toBe('Off');
		expect(off.classList.contains('opacity-60')).toBe(true);
	});

	it('shows prefix and suffix marks around the starter text', () => {
		mountCard({
			template: template({
				segments: [
					slot('Subject', { content: 'a cat', prefix: '(', suffix: ':1.2)' }),
					slot('Style', { content: '', prefix: '(' })
				]
			})
		});
		const [filled, empty] = slotRows();
		expect(filled.querySelector('[data-template-slot-prefix]')?.textContent?.trim()).toBe('(');
		expect(filled.querySelector('[data-template-slot-suffix]')?.textContent?.trim()).toBe(':1.2)');
		expect(empty.querySelector('[data-template-slot-prefix]')).toBeNull();
	});

	it('truncates a long affix mark', () => {
		mountCard({
			template: template({ segments: [slot('Subject', { content: 'a cat', prefix: 'abcdefghijklmnop' })] })
		});
		expect(slotRows()[0].querySelector('[data-template-slot-prefix]')?.textContent?.trim()).toBe('abcdefghijkl…');
	});

	it('renders chip tokens in starter text as their plain value', () => {
		mountCard({
			template: template({
				segments: [
					slot('Subject', {
						content: 'a #style portrait',
						chips: {
							'chip-1': {
								id: 'chip-1',
								categoryPath: 'style',
								valueId: 'v1',
								label: 'Noir',
								value: 'film noir',
								allValues: [],
								shuffle: false,
								autoRegen: false
							}
						}
					})
				]
			})
		});
		const text = slotRows()[0].textContent?.replace(/\s+/g, ' ') ?? '';
		expect(text).toContain('film noir');
		expect(text).not.toContain('#style');
	});

	it('caps the slot rows at eight and counts the rest', () => {
		mountCard({
			template: template({ segments: Array.from({ length: 10 }, (unused, index) => slot(`S${index + 1}`)) })
		});
		expect(slotRows().map((row) => row.querySelector('.font-medium')?.textContent?.trim())).toEqual([
			'S1',
			'S2',
			'S3',
			'S4',
			'S5',
			'S6',
			'S7',
			'S8'
		]);
		const more = target.querySelector('[data-template-slots-more]') as HTMLElement;
		expect(more.textContent?.replace(/\s+/g, ' ').trim()).toBe('+2 more slots');
	});

	it('does not render the more line when every slot fits', () => {
		mountCard({
			template: template({ segments: Array.from({ length: 8 }, (unused, index) => slot(`S${index + 1}`)) })
		});
		expect(slotRows().length).toBe(8);
		expect(target.querySelector('[data-template-slots-more]')).toBeNull();
	});

	it('shows the slot-count badge and at most two tags', () => {
		mountCard();
		const text = target.textContent ?? '';
		expect(text).toContain('slots');
		expect(text).toContain('portrait');
		expect(text).toContain('lighting');
		expect(text).not.toContain('studio');
	});

	it('singularises the slot-count badge for a one-slot template', () => {
		mountCard({ template: template({ segments: [slot('Only')] }) });
		expect(target.textContent).toContain('slot');
		expect(target.textContent).not.toContain('slots');
	});

	it('opens the template on Enter and toggles selection on Space', () => {
		const onOpen = vi.fn();
		const onToggleSelect = vi.fn();
		mountCard({ onOpen, onToggleSelect });
		const card = target.querySelector('[data-template-card]') as HTMLElement;
		expect(card).toBeTruthy();

		card.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
		expect(onOpen).toHaveBeenCalledTimes(1);
		expect(onOpen.mock.calls[0][0].id).toBe('template-1');
		expect(onToggleSelect).not.toHaveBeenCalled();

		card.dispatchEvent(new KeyboardEvent('keydown', { key: ' ', bubbles: true }));
		expect(onToggleSelect).toHaveBeenCalledTimes(1);
		expect(onToggleSelect.mock.calls[0][0].id).toBe('template-1');
		expect(onOpen).toHaveBeenCalledTimes(1);
	});

	it('opens the template without applying it when Edit is clicked', () => {
		const onApply = vi.fn();
		const onOpen = vi.fn();
		mountCard({ onApply, onOpen });
		const edit = Array.from(target.querySelectorAll('button')).find((b) => b.textContent?.trim() === 'Edit');
		expect(edit).toBeTruthy();
		edit!.click();
		expect(onOpen).toHaveBeenCalledTimes(1);
		expect(onApply).not.toHaveBeenCalled();
	});

	it('applies the template without opening it when Apply is clicked', () => {
		const onApply = vi.fn();
		const onOpen = vi.fn();
		mountCard({ onApply, onOpen });
		const apply = Array.from(target.querySelectorAll('button')).find((b) => b.textContent?.trim() === 'Apply');
		expect(apply).toBeTruthy();
		apply!.click();
		expect(onApply).toHaveBeenCalledTimes(1);
		expect(onOpen).not.toHaveBeenCalled();
	});
});
