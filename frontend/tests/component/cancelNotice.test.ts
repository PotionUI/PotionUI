// @vitest-environment jsdom
import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import { get } from 'svelte/store';

const { tabsStore } = await import('$lib/stores/tabs');
const { default: CancelNotice } = await import('$lib/components/generation/CancelNotice.svelte');
const { createClassComponent } = await import('svelte/legacy');

const NOTICE = 'Stopped waiting. The provider may still finish this job and bill it.';

function currentTab() {
	return get(tabsStore).tabs[0];
}

function mountNotice() {
	const target = document.createElement('div');
	document.body.appendChild(target);
	const component = createClassComponent({ component: CancelNotice as never, target, props: { tab: currentTab() } });
	return {
		target,
		update: () => component.$set({ tab: currentTab() }),
		destroy: () => {
			component.$destroy();
			target.remove();
		}
	};
}

async function settle() {
	for (let i = 0; i < 6; i++) await new Promise((resolve) => setTimeout(resolve, 0));
}

let mounted: ReturnType<typeof mountNotice> | undefined;

beforeEach(() => {
	tabsStore.reset();
});

afterEach(() => {
	mounted?.destroy();
	mounted = undefined;
});

describe('Cancel notice', () => {
	it('shows nothing when the tab has no notice', async () => {
		mounted = mountNotice();
		await settle();
		expect(mounted.target.querySelector('[data-testid="cancel-notice"]')).toBeNull();
	});

	it('shows the notice calmly, as a status and not an error', async () => {
		const tab = currentTab();
		tabsStore.updateTab(tab.id, { generation: { ...tab.generation, cancelNotice: NOTICE } });
		mounted = mountNotice();
		await settle();

		const box = mounted.target.querySelector('[data-testid="cancel-notice"]')!;
		expect(box.textContent).toContain(NOTICE);
		expect(box.querySelector('[role="status"]')).toBeTruthy();
		expect(box.querySelector('[role="alert"]')).toBeNull();
		expect(box.innerHTML).toContain('bg-surface-2');
		expect(box.innerHTML).not.toContain('text-danger');
	});

	it('goes away when dismissed', async () => {
		const tab = currentTab();
		tabsStore.updateTab(tab.id, { generation: { ...tab.generation, cancelNotice: NOTICE } });
		mounted = mountNotice();
		await settle();

		Array.from(mounted.target.querySelectorAll('button')).find((b) => b.textContent?.includes('Dismiss'))!.click();
		await settle();
		mounted.update();
		await settle();

		expect(currentTab().generation.cancelNotice).toBeNull();
		expect(mounted.target.querySelector('[data-testid="cancel-notice"]')).toBeNull();
	});
});
