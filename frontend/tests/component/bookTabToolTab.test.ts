import { describe, it, expect, afterEach } from 'vitest';
import { flushSync } from 'svelte';

const { default: BookTab } = await import('$lib/components/BookTab.svelte');
const { createClassComponent } = await import('svelte/legacy');

let component: { $destroy: () => void } | undefined;

function render(props: Record<string, unknown>) {
	const target = document.createElement('div');
	document.body.appendChild(target);
	component = createClassComponent({
		component: BookTab as never,
		target,
		props: { id: 't1', name: 'observatory-dusk', isActive: false, canDelete: true, ...props }
	});
	flushSync();
	return target.querySelector<HTMLButtonElement>('button.book-tab')!;
}

afterEach(() => {
	component?.$destroy();
	component = undefined;
	document.body.innerHTML = '';
});

describe('BookTab for a tool tab', () => {
	it('leads with the 20px thumbnail and the tool badge instead of the colour dot', () => {
		const tab = render({ toolIcon: 'sliders-horizontal', toolLabel: 'Image editor', thumbnail: '/m/dusk.png', color: '#3b82f6' });
		const thumb = tab.querySelector('[data-testid="tab-tool-thumb"]')!;
		expect(thumb).not.toBeNull();
		expect(thumb.querySelector('img')?.getAttribute('src')).toBe('/m/dusk.png');
		expect(thumb.querySelector('.tool-badge svg')).not.toBeNull();
		expect(tab.querySelector('.color-dot')).toBeNull();
		expect(tab.textContent).toContain('Image editor:');
	});

	it('shows the tool glyph when nothing is loaded', () => {
		const tab = render({ toolIcon: 'video', toolLabel: 'Video editor', thumbnail: null });
		const thumb = tab.querySelector('[data-testid="tab-tool-thumb"]')!;
		expect(thumb.querySelector('img')).toBeNull();
		expect(thumb.querySelector('.tool-badge')).toBeNull();
		expect(thumb.querySelector('svg')).not.toBeNull();
	});

	it('marks unsaved steps with the warning dot and names the count', () => {
		const tab = render({ toolIcon: 'sliders-horizontal', unsaved: true, unsavedLabel: '2 unsaved edits' });
		expect(tab.querySelector('[data-testid="tab-unsaved"]')).not.toBeNull();
		expect(tab.textContent).toContain(', 2 unsaved edits');
	});

	it('shows the render percentage while a video saves', () => {
		const tab = render({ toolIcon: 'video', progress: 41.6 });
		const progress = tab.querySelector('[data-testid="tab-progress"]')!;
		expect(progress.textContent).toBe('42%');
		expect(progress.className).toContain('tabular-nums');
	});

	it('keeps the colour dot and no progress for a generation tab', () => {
		const tab = render({ color: '#ef4444' });
		expect(tab.querySelector('.color-dot')).not.toBeNull();
		expect(tab.querySelector('[data-testid="tab-tool-thumb"]')).toBeNull();
		expect(tab.querySelector('[data-testid="tab-progress"]')).toBeNull();
	});
});
