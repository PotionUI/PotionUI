// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

vi.mock('$lib/services/api/index', () => ({
	api: { removeFromCollection: vi.fn(), listCollections: vi.fn(), getGenerationHistory: vi.fn() }
}));

const { api } = await import('$lib/services/api/index');
const { historyStore } = await import('$lib/stores/history');
const { default: HistorySelectionToolbar } = await import('../../src/routes/history/components/HistorySelectionToolbar.svelte');
const { createClassComponent } = await import('svelte/legacy');

const widthOf = (el: HTMLElement): number => {
	if (el.hasAttribute('data-bar-icon')) return 40;
	if (el.classList.contains('sel-actions')) return Array.from(el.children).reduce((sum, child) => sum + widthOf(child as HTMLElement), 0);
	if (el.getAttribute('role') === 'toolbar') return 300;
	return 100;
};

const original = {
	offsetWidth: Object.getOwnPropertyDescriptor(HTMLElement.prototype, 'offsetWidth'),
	scrollWidth: Object.getOwnPropertyDescriptor(Element.prototype, 'scrollWidth'),
	clientWidth: Object.getOwnPropertyDescriptor(Element.prototype, 'clientWidth')
};

function mountAt(width: number) {
	Object.defineProperty(window, 'innerWidth', { configurable: true, value: width });
	const target = document.createElement('div');
	document.body.appendChild(target);
	const component = createClassComponent({
		component: HistorySelectionToolbar as never,
		target,
		props: { onBulkDeleteClick: () => {}, onToolSelect: () => {} }
	});
	return { target, destroy: () => (component.$destroy(), target.remove()) };
}

let mounted: ReturnType<typeof mountAt> | undefined;

beforeEach(() => {
	Object.defineProperty(HTMLElement.prototype, 'offsetWidth', { configurable: true, get() { return widthOf(this); } });
	Object.defineProperty(Element.prototype, 'clientWidth', { configurable: true, get() { return widthOf(this as HTMLElement); } });
	Object.defineProperty(Element.prototype, 'scrollWidth', {
		configurable: true,
		get() {
			const el = this as HTMLElement;
			return el.getAttribute('role') === 'toolbar' ? 200 + widthOf(el.querySelector('.sel-actions') as HTMLElement) : widthOf(el);
		}
	});
	vi.mocked(api.listCollections).mockResolvedValue({ success: true, data: { collections: [], total: 0 } } as never);
	vi.mocked(api.getGenerationHistory).mockResolvedValue({ success: true, data: { generations: [], total: 0 } } as never);
	historyStore.setFilter('collectionId', undefined);
	historyStore.toggleSelect('g1');
});

afterEach(() => {
	mounted?.destroy();
	mounted = undefined;
	historyStore.clearSelection();
	for (const [key, descriptor] of Object.entries(original)) {
		const proto = key === 'offsetWidth' ? HTMLElement.prototype : Element.prototype;
		if (descriptor) Object.defineProperty(proto, key, descriptor);
	}
});

const actions = (target: HTMLElement) => target.querySelector<HTMLElement>('.sel-actions')!;
const more = (target: HTMLElement) => target.querySelector<HTMLButtonElement>('[data-bar-more] button')!;
const moreWrap = (target: HTMLElement) => target.querySelector<HTMLElement>('[data-bar-more]')!;
const names = (root: HTMLElement) => Array.from(root.querySelectorAll('button')).map((b) => (b.getAttribute('aria-label') ?? b.textContent ?? '').replace(/\s+/g, ' ').trim());

describe('selection bar responsive layout', () => {
	it('keeps labels and hides More while the bar fits', async () => {
		mounted = mountAt(1600);
		await Promise.resolve();
		expect(actions(mounted.target).dataset.mode).toBe('inline');
		expect(moreWrap(mounted.target).style.display).toBe('none');
		expect(actions(mounted.target).querySelector('[data-bar-icon]')).toBeNull();
	});

	it('collapses to icon-only buttons that keep an accessible name', async () => {
		mounted = mountAt(560);
		await Promise.resolve();
		const bar = actions(mounted.target);
		expect(bar.dataset.mode).toBe('inline');
		const collapsed = Array.from(bar.querySelectorAll<HTMLElement>('[data-bar-icon]'));
		expect(collapsed.length).toBeGreaterThan(0);
		for (const item of collapsed) {
			const button = item.tagName === 'BUTTON' ? item : item.querySelector('button')!;
			expect(button.getAttribute('aria-label')).toBeTruthy();
		}
	});

	it('moves every action into one More menu when icons still do not fit, and keeps Delete inline', async () => {
		mounted = mountAt(300);
		await Promise.resolve();
		const bar = actions(mounted.target);
		expect(bar.dataset.mode).toBe('overflow');
		expect(moreWrap(mounted.target).style.display).toBe('');
		expect(bar.dataset.open).toBe('false');
		expect(names(bar)).toEqual(expect.arrayContaining(['Copy to Library', 'Add to kit', 'Tools']));
		expect(names(bar)).not.toContain('Delete');
		expect(mounted.target.querySelector('[role="toolbar"]')!.textContent).toContain('Delete');

		more(mounted.target).click();
		await Promise.resolve();
		await Promise.resolve();
		expect(bar.dataset.open).toBe('true');
	});

	it('closes the More menu after a plain action is chosen', async () => {
		mounted = mountAt(300);
		await Promise.resolve();
		more(mounted.target).click();
		await Promise.resolve();
		await Promise.resolve();
		const kit = Array.from(actions(mounted.target).querySelectorAll('button')).find((b) => b.textContent?.includes('Add to kit'))!;
		kit.click();
		await Promise.resolve();
		await Promise.resolve();
		expect(actions(mounted.target).dataset.open).toBe('false');
	});
});
