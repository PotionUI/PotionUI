// @vitest-environment jsdom
//
// Maintainer bug report: the generation panel's icon-button/mode-button
// tooltips (the mock's own CSS `::after`, `content: attr(data-tooltip)`)
// rendered in the wrong place once the panel became a `position: fixed`
// docked strip with its own stacking context. GenerationPanel.svelte now
// wraps every such control in the app's real Tooltip component instead
// (position="top", computing its own `position: fixed` rect from the
// trigger's live bounding box) — this drives that component for real and
// checks the rendered tooltip carries the trigger's text and the "top"
// arrow orientation, and that a Save-style dynamic label reaches an
// accessible description on the trigger itself.
import { describe, it, expect, afterEach } from 'vitest';

const { createClassComponent } = await import('svelte/legacy');
const { default: TooltipIconButtonFixture } = await import('./fixtures/TooltipIconButtonFixture.svelte');

let mounted: { target: HTMLElement; destroy: () => void } | undefined;

function mount(text: string) {
	const target = document.createElement('div');
	document.body.appendChild(target);
	const component = createClassComponent({
		component: TooltipIconButtonFixture as never,
		target,
		props: { text }
	});
	return {
		target,
		destroy: () => {
			component.$destroy();
			target.remove();
		}
	};
}

async function settle() {
	for (let i = 0; i < 5; i++) await new Promise((resolve) => setTimeout(resolve, 0));
}

afterEach(() => {
	mounted?.destroy();
	mounted = undefined;
});

describe('generation panel tooltips', () => {
	it('renders the real Tooltip (not the neutralized CSS one) above the trigger on hover', async () => {
		mounted = mount('Generation settings');

		const trigger = mounted.target.querySelector<HTMLButtonElement>('.icon-button')!;
		expect(trigger).toBeTruthy();
		// The accessible name is real regardless of hover — the icon-only
		// button never depends on the tooltip alone to be usable.
		expect(trigger.getAttribute('aria-label')).toBe('Generation settings');
		// The neutralized mock CSS tooltip: no `data-tooltip` markup left for
		// `.icon-button::after { content: attr(data-tooltip) }` to render.
		expect(trigger.hasAttribute('data-tooltip')).toBe(false);

		expect(document.body.textContent).not.toContain('Generation settings');

		// `mouseenter` doesn't bubble, and Tooltip's own listener sits on its
		// wrapping trigger `<div>`, not the slotted button — dispatch there
		// (`bubbles: true` also lets jsdom's dispatch algorithm walk up
		// regardless, matching how a real pointer-driven mouseenter behaves).
		trigger.parentElement!.dispatchEvent(new MouseEvent('mouseenter', { bubbles: true }));
		await settle();

		const tooltip = Array.from(document.body.querySelectorAll('div')).find(
			(el) => el.textContent?.trim() === 'Generation settings' && el.className.includes('fixed')
		);
		expect(tooltip, 'Tooltip did not render into the DOM on hover').toBeTruthy();
		// position="top": the component's own arrow class for that orientation.
		const arrow = tooltip!.querySelector('div');
		expect(arrow?.className).toContain('bottom-[-4px]');

		trigger.parentElement!.dispatchEvent(new MouseEvent('mouseleave', { bubbles: true }));
		await settle();
		expect(document.body.textContent).not.toContain('Generation settings');
	});

	it('carries a dynamic Save-style label as an accessible description', async () => {
		mounted = mount('Save session');

		const trigger = mounted.target.querySelector<HTMLButtonElement>('.icon-button')!;
		expect(trigger.getAttribute('aria-label')).toBe('Save session');

		// `mouseenter` doesn't bubble, and Tooltip's own listener sits on its
		// wrapping trigger `<div>`, not the slotted button — dispatch there
		// (`bubbles: true` also lets jsdom's dispatch algorithm walk up
		// regardless, matching how a real pointer-driven mouseenter behaves).
		trigger.parentElement!.dispatchEvent(new MouseEvent('mouseenter', { bubbles: true }));
		await settle();

		expect(
			Array.from(document.body.querySelectorAll('div')).some((el) => el.textContent?.trim() === 'Save session' && el.className.includes('fixed'))
		).toBe(true);
	});
});
