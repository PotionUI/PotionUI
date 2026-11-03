import { beforeEach, describe, expect, it, vi } from 'vitest';
import { get } from 'svelte/store';

const getPromptResourceMembers = vi.fn();
const getModelById = vi.fn();

vi.mock('$lib/services/api', () => ({ api: { getPromptResourceMembers, getModelById } }));

const { bundleDirectories, loadBundles, resetBundleDirectories } = await import('./bundleDirectory');

async function settle() {
	await new Promise((resolve) => setTimeout(resolve, 0));
}

describe('bundleDirectory', () => {
	beforeEach(() => {
		resetBundleDirectories();
		getPromptResourceMembers.mockReset();
		getModelById.mockReset();
		getModelById.mockResolvedValue({ success: true, data: { model: { name: 'Face' } } });
	});

	it('loads members per source and item through the generic source route', async () => {
		getPromptResourceMembers.mockImplementation(async (source: string) => ({
			success: true,
			data: { members: [{ kind: source === 'stack' ? 'image' : 'audio', name: null }] }
		}));

		loadBundles([
			{ source: 'stack', itemKey: 'model:01X' },
			{ source: 'cast', itemKey: 'model:01X' }
		]);
		await settle();

		expect(getPromptResourceMembers.mock.calls).toEqual([
			['stack', 'model:01X'],
			['cast', 'model:01X']
		]);
		expect(getModelById.mock.calls).toEqual([['01X'], ['01X']]);
		const directories = get(bundleDirectories);
		expect(directories.stack['model:01X'].members).toEqual([{ kind: 'image', name: null }]);
		expect(directories.cast['model:01X'].members).toEqual([{ kind: 'audio', name: null }]);
		expect(directories.stack['model:01X'].name).toBe('Face');
	});

	it('asks for each source item once and records a failed read as no members', async () => {
		getPromptResourceMembers.mockRejectedValue(new Error('422'));

		loadBundles([{ source: 'stack', itemKey: 'model:01X' }]);
		loadBundles([{ source: 'stack', itemKey: 'model:01X' }]);
		await settle();

		expect(getPromptResourceMembers).toHaveBeenCalledTimes(1);
		expect(get(bundleDirectories).stack['model:01X'].members).toEqual([]);
	});

	it('does not look up a name for an item that is not a model', async () => {
		getPromptResourceMembers.mockResolvedValue({ success: true, data: { members: [] } });

		loadBundles([{ source: 'stack', itemKey: 'local-item' }]);
		await settle();

		expect(getModelById).not.toHaveBeenCalled();
	});
});
