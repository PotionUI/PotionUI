// @vitest-environment jsdom
import { describe, it, expect, afterEach, beforeEach, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import { makeItem, makeKit } from '$lib/kits/fixtures';

const resolveKitCitations = vi.fn();

vi.mock('$lib/services/api/index', async (importOriginal) => {
	const original = await importOriginal<typeof import('$lib/services/api/index')>();
	original.api.resolveKitCitations = ((request: unknown) => resolveKitCitations(request)) as never;
	return original;
});

const { default: KitSkippedLine } = await import('$lib/components/kits/KitSkippedLine.svelte');
const { rememberKit, resetKitDirectory } = await import('$lib/stores/kits');
const { kitNotices } = await import('$lib/stores/kitNotices');
const { encodeKitKey } = await import('$lib/kits/kitMarker');
const { get } = await import('svelte/store');

const mara = makeKit('Mara', 'mara', [makeItem('image', 'face'), makeItem('audio', 'voice')]);
let mounted: ReturnType<typeof mount> | undefined;

beforeEach(() => {
	vi.useFakeTimers();
	resolveKitCitations.mockReset();
	resetKitDirectory();
	rememberKit(mara);
});

afterEach(() => {
	if (mounted) unmount(mounted);
	mounted = undefined;
	document.body.innerHTML = '';
	vi.useRealTimers();
});

describe('KitSkippedLine asks the server', () => {
	it('shows the server notices as plain lines and publishes them for the deck', async () => {
		const notice = { code: 'unsupported_kind', message: 'This model does not take audio: `voice` is skipped.', kit: 'mara', item: 'voice', kind: 'audio', field: 'refs' };
		resolveKitCitations.mockResolvedValue({ success: true, data: { field: 'refs', items: [], skipped: [notice], expanded: '', text: '', takes: {}, limits: {} } });
		const target = document.createElement('div');
		document.body.appendChild(target);
		mounted = mount(KitSkippedLine, {
			target,
			props: {
				texts: [`@[refs:${encodeKitKey(mara.id)}]`],
				specs: [{ field: 'refs', kind: 'image' }],
				formData: { refs: [{ kit: mara.id }] },
				presetId: 'minimax',
				mode: 'refs'
			}
		});
		flushSync();
		await vi.advanceTimersByTimeAsync(500);
		flushSync();
		expect(resolveKitCitations).toHaveBeenCalledTimes(1);
		expect(resolveKitCitations.mock.calls[0][0]).toMatchObject({ loaded: [mara.id], field: 'refs', preset_id: 'minimax', mode: 'refs' });
		expect(target.querySelector('[data-kit-skipped-line]')?.textContent?.trim()).toBe('This model does not take audio: voice is skipped.');
		expect(get(kitNotices)).toEqual([notice]);
	});

	it('does not call the server for a prompt that cites no kit', async () => {
		const target = document.createElement('div');
		document.body.appendChild(target);
		mounted = mount(KitSkippedLine, { target, props: { texts: ['plain'], specs: [], formData: {} } });
		flushSync();
		await vi.advanceTimersByTimeAsync(600);
		expect(resolveKitCitations).not.toHaveBeenCalled();
	});
});
