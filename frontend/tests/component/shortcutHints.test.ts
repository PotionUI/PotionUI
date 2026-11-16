// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import { settle } from './helpers/settle';

vi.mock('$lib/tools/pluginTools', async (importOriginal) => ({
	...(await importOriginal<typeof import('$lib/tools/pluginTools')>()),
	retryMediaToolCatalog: vi.fn()
}));

import ToolsMenu from '$lib/components/tools/ToolsMenu.svelte';
import { listTools, type MediaTool } from '$lib/tools/tools';
import { registerEntryTools } from '$lib/tools/entryTools';
import { registerCoreHistoryTools } from '../../src/routes/history/tools/coreTools';

registerEntryTools();
registerCoreHistoryTools();

let cleanup: (() => void) | undefined;

afterEach(() => {
	cleanup?.();
	cleanup = undefined;
	document.body.innerHTML = '';
});

const hinted = listTools().filter((entry) => entry.shortcut);

describe('every shortcut hint in the Tools menu and the card menu', () => {
	it('exists on at least the four documented tools', () => {
		expect(hinted.map((entry) => entry.id).sort()).toEqual(['continue-edit', 'delete', 'download', 'edit-image', 'favorite']);
	});

	for (const entry of hinted) {
		it(`works: ${entry.shortcut} runs ${entry.id} in an open menu`, async () => {
			const onPick = vi.fn();
			const target = document.createElement('div');
			document.body.appendChild(target);
			const instance = mount(ToolsMenu, {
				target,
				props: {
					open: true,
					scope: 'library',
					groups: [
						{
							category: { id: entry.category, label: entry.category, order: 0 },
							tools: [{ tool: entry as MediaTool, availability: { enabled: true } }]
						}
					],
					onToggle: vi.fn(),
					onClose: vi.fn(),
					onPick
				}
			});
			cleanup = () => {
				unmount(instance);
				target.remove();
			};
			flushSync();
			await settle(2);
			expect(document.body.querySelector('[data-tools-menu] kbd')).not.toBeNull();

			window.dispatchEvent(new KeyboardEvent('keydown', { key: entry.shortcut!, bubbles: true, cancelable: true }));
			flushSync();

			expect(onPick).toHaveBeenCalledTimes(1);
			expect((onPick.mock.calls[0][0] as MediaTool).id).toBe(entry.id);
		});
	}
});
