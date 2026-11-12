import tooltip from '$lib/actions/tooltip';
import { planSelectionBar } from './selectionBarLayout';

export interface ResponsiveBarOptions {
	onMode: (mode: 'inline' | 'overflow') => void;
}

const VIEWPORT_MARGIN = 32;

function primaryButton(item: HTMLElement): HTMLElement | null {
	return item.tagName === 'BUTTON' ? item : item.querySelector<HTMLElement>('button');
}

function labelOf(button: HTMLElement): string {
	return (button.getAttribute('aria-label') ?? button.textContent ?? '').replace(/\s+/g, ' ').trim();
}

export default function responsiveBar(container: HTMLElement, options: ResponsiveBarOptions) {
	const bar = container.closest<HTMLElement>('[role="toolbar"]');
	const more = bar?.querySelector<HTMLElement>('[data-bar-more]') ?? null;
	let tips: { destroy(): void }[] = [];
	let labelled: HTMLElement[] = [];
	let applying = false;

	function items(): HTMLElement[] {
		return Array.from(container.children) as HTMLElement[];
	}

	function reset() {
		for (const tip of tips) tip.destroy();
		tips = [];
		for (const button of labelled) button.removeAttribute('aria-label');
		labelled = [];
		container.dataset.mode = 'inline';
		if (more) more.style.display = 'none';
		for (const item of items()) item.removeAttribute('data-bar-icon');
	}

	function applyIcons(indexes: number[], list: HTMLElement[]) {
		for (const index of indexes) {
			const item = list[index];
			item.setAttribute('data-bar-icon', '');
			const button = primaryButton(item);
			if (!button) continue;
			const label = labelOf(button);
			if (!label) continue;
			if (!button.hasAttribute('aria-label')) {
				button.setAttribute('aria-label', label);
				labelled.push(button);
			}
			if (!button.hasAttribute('data-has-tooltip')) {
				tips.push(tooltip(button, { text: label, position: 'top' }));
			}
		}
	}

	function apply() {
		if (!bar || applying) return;
		applying = true;
		reset();
		if (bar.offsetWidth === 0) {
			options.onMode('inline');
			applying = false;
			return;
		}
		const list = items();
		const full = list.map((item) => item.offsetWidth);
		for (const item of list) item.setAttribute('data-bar-icon', '');
		const icon = list.map((item) => item.offsetWidth);
		for (const item of list) item.removeAttribute('data-bar-icon');
		const gap = parseFloat(getComputedStyle(container).columnGap) || 0;
		const fixed = bar.scrollWidth - container.offsetWidth;
		const available = window.innerWidth - VIEWPORT_MARGIN - (bar.offsetWidth - bar.clientWidth);
		const plan = planSelectionBar({
			available,
			fixed,
			gap,
			items: list.map((_, index) => ({ full: full[index], icon: icon[index] }))
		});
		if (plan.mode === 'overflow') {
			container.dataset.mode = 'overflow';
			if (more) more.style.display = '';
		} else applyIcons(plan.iconOnly, list);
		options.onMode(plan.mode);
		applying = false;
	}

	const observer = new MutationObserver(apply);
	if (bar) observer.observe(bar, { childList: true, characterData: true, subtree: true });
	window.addEventListener('resize', apply);
	void document.fonts?.ready.then(apply);
	apply();

	return {
		destroy() {
			observer.disconnect();
			window.removeEventListener('resize', apply);
			for (const tip of tips) tip.destroy();
		}
	};
}
