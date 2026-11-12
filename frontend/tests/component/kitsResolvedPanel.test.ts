// @vitest-environment jsdom
import { describe, it, expect, afterEach, beforeEach, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import type { PromptResourceSpec } from '$lib/utils/promptResources';
import { makeItem, makeKit } from '$lib/kits/fixtures';

const resolveKitCitations = vi.fn();

vi.mock('$lib/services/api/index', async (importOriginal) => {
	const original = await importOriginal<typeof import('$lib/services/api/index')>();
	original.api.resolveKitCitations = ((request: unknown) => resolveKitCitations(request)) as never;
	return original;
});

const { default: ResolvedPromptPreview } = await import('$lib/components/ResolvedPromptPreview.svelte');
const { rememberKit, resetKitDirectory } = await import('$lib/stores/kits');
const { encodeKitKey } = await import('$lib/kits/kitMarker');

const specs: PromptResourceSpec[] = [{ field: 'refs', kind: 'image', token: '<Picture @>' }];
const mara = makeKit('Mara', 'mara', [makeItem('image', 'face')]);
const plain = { path: 'a.png', url: '/a.png' };
let mounted: ReturnType<typeof mount> | undefined;

function show(prompt: string, values: Record<string, unknown>) {
	const target = document.createElement('div');
	document.body.appendChild(target);
	mounted = mount(ResolvedPromptPreview, { target, props: { prompt, promptResources: specs, resourceFieldValues: values, presetId: 'p', mode: 'refs' } });
	flushSync();
	return target;
}

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

describe('Resolved prompt panel with kits', () => {
	it('shows the server text, numbered after the form media, once the server answers', async () => {
		resolveKitCitations.mockResolvedValue({ success: true, data: { field: 'refs', items: [], skipped: [], expanded: '', text: 'She is <Picture 2> here', takes: {}, limits: {} } });
		const target = show(`She is @[refs:${encodeKitKey(mara.id, mara.items[0].id)}] here`, { refs: [plain, { kit: mara.id }] });
		expect(resolveKitCitations).not.toHaveBeenCalled();
		await vi.advanceTimersByTimeAsync(500);
		flushSync();
		expect(resolveKitCitations.mock.calls[0][0].form_data).toEqual({ refs: [plain] });
		expect(target.textContent).toContain('She is <Picture 2> here');
	});

	it('is unchanged, with no server call, for a form without kits', async () => {
		const target = show('A cat @[refs:a.png]', { refs: [plain] });
		await vi.advanceTimersByTimeAsync(600);
		flushSync();
		expect(resolveKitCitations).not.toHaveBeenCalled();
		expect(target.textContent).toContain('A cat <Picture 1>');
	});
});
