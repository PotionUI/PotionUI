import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { get } from 'svelte/store';

vi.mock('$lib/services/api', () => ({
	api: { getWorkspaces: vi.fn().mockResolvedValue({ success: true, data: [] }), getModeLabels: vi.fn().mockResolvedValue({ success: false }) }
}));

const { tabsStore } = await import('$lib/stores/tabs');
const { default: TabBar } = await import('../../src/routes/generate/components/TabBar.svelte');
const { ingestQueueSnapshot, resetElsewhereForTests } = await import('$lib/generation/elsewhere/store');
const { createClassComponent } = await import('svelte/legacy');
const { generationTabs } = await import('$lib/types/tabs');
const { settle } = await import('./helpers/settle');

let destroy: (() => void) | undefined;

function mountBar() {
	const target = document.createElement('div');
	document.body.appendChild(target);
	const component = createClassComponent({ component: TabBar as never, target });
	destroy = () => {
		component.$destroy();
		target.remove();
	};
}

beforeEach(() => {
	tabsStore.reset();
	resetElsewhereForTests();
});

afterEach(() => {
	destroy?.();
	destroy = undefined;
	tabsStore.reset();
	document.body.innerHTML = '';
});

describe('the tab strip', () => {
	it('has no running elsewhere button until something runs elsewhere', async () => {
		mountBar();
		await settle();
		expect(document.body.querySelector('[data-testid="running-elsewhere"]')).toBeNull();
	});

	it('puts the count beside the tabs when something runs elsewhere', async () => {
		ingestQueueSnapshot({
			running: [{ generation_id: 'g1', tab_id: null, has_tab: false, status: 'running', preset_name: 'SDXL', mode: 'txt2img', origin: { kind: 'mcp', key_id: 'k', key_name: 'claude desktop' } }],
			pending: []
		} as never);
		mountBar();
		await settle();
		expect(document.body.querySelector('[data-testid="running-elsewhere"]')!.textContent?.trim()).toBe('1 running over MCP');
	});

	it('marks a tab opened onto a generation from elsewhere, with its source in a tooltip', async () => {
		const first = generationTabs(get(tabsStore).tabs)[0];
		tabsStore.updateTab(first.id, { openedFrom: { generationId: 'g1', kind: 'mcp', label: 'Started over MCP · claude desktop' } });
		mountBar();
		await settle();
		const marks = document.body.querySelectorAll('[data-testid="tab-source"]');
		expect(marks).toHaveLength(1);
		expect(document.body.textContent).toContain('started over mcp · claude desktop');
	});

	it('does not mark an ordinary tab', async () => {
		mountBar();
		await settle();
		expect(document.body.querySelector('[data-testid="tab-source"]')).toBeNull();
	});
});
