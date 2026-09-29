// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach } from 'vitest';

vi.mock('$lib/services/admin-api', () => ({
	getContentSafetyStatus: vi.fn()
}));

const fetchModel = vi.fn();
let taggerState: Record<string, unknown> = {};

vi.mock('../../src/routes/admin/components/settings/modelFetch.svelte', () => ({
	createModelFetchController: () => ({
		state: {
			get media_tagger() {
				return taggerState;
			}
		},
		fetchModel
	}),
	modelNameLookup: () => () => 'tagger',
	isFetchDisabled: (state: { status: string }) =>
		state.status === 'checking' || state.status === 'queued' || state.status === 'downloading'
}));

const adminApi = await import('$lib/services/admin-api');
const { default: ContentSafetyPanel } = await import(
	'../../src/routes/admin/components/settings/ContentSafetyPanel.svelte'
);
const { createClassComponent } = await import('svelte/legacy');

function idleState(status = 'idle') {
	return { status, progress: 0, error: null, path: null, size: null, downloadId: null, loaded: false };
}

function statusPayload(overrides: Record<string, unknown> = {}) {
	return {
		success: true,
		data: {
			policy: 'blur',
			tagger: { present: false, device: 'cpu', downloading: false },
			backfill: { total: 0, rated: 0, running: false },
			...overrides
		}
	};
}

async function settle() {
	for (let i = 0; i < 8; i++) await new Promise((resolve) => setTimeout(resolve, 0));
}

async function mountPanel(settings: Record<string, unknown>) {
	const target = document.createElement('div');
	document.body.appendChild(target);
	const onSettingChange = vi.fn();
	const component = createClassComponent({
		component: ContentSafetyPanel as never,
		target,
		props: { settings, onSettingChange }
	});
	await settle();
	return {
		target,
		onSettingChange,
		button: (text: string) =>
			Array.from(target.querySelectorAll<HTMLButtonElement>('button')).find((b) => b.textContent?.includes(text)),
		destroy: () => {
			component.$destroy();
			target.remove();
		}
	};
}

afterEach(() => {
	vi.clearAllMocks();
	taggerState = idleState();
});

describe('ContentSafetyPanel', () => {
	it('renders the three policy segments and writes the chosen policy', async () => {
		taggerState = idleState('ready');
		vi.mocked(adminApi.getContentSafetyStatus).mockResolvedValue(
			statusPayload({ tagger: { present: true, device: 'cpu', downloading: false } }) as never
		);
		const panel = await mountPanel({ content_policy_nsfw: 'allowed' });

		expect(panel.button('Allowed')?.getAttribute('aria-pressed')).toBe('true');
		panel.button('Blocked')!.click();
		expect(panel.onSettingChange).toHaveBeenCalledWith('content_policy_nsfw', 'blocked');
		panel.destroy();
	});

	it('shows the missing-tagger state with a download action and a Blocked warning', async () => {
		taggerState = idleState();
		vi.mocked(adminApi.getContentSafetyStatus).mockResolvedValue(statusPayload() as never);
		const panel = await mountPanel({ content_policy_nsfw: 'blocked' });

		expect(panel.target.querySelector('[data-tagger-status]')?.textContent).toContain('Not downloaded');
		expect(panel.target.textContent).toContain('Blocked refuses every generation');

		panel.button('Download and enable')!.click();
		await settle();
		expect(panel.onSettingChange).toHaveBeenCalledWith('media_tagger_auto_download', true);
		expect(fetchModel).toHaveBeenCalledWith('media_tagger');
		panel.destroy();
	});

	it('shows a ready tagger without a download action or warning', async () => {
		taggerState = idleState('ready');
		vi.mocked(adminApi.getContentSafetyStatus).mockResolvedValue(
			statusPayload({ tagger: { present: true, device: 'cuda', downloading: false } }) as never
		);
		const panel = await mountPanel({ content_policy_nsfw: 'blocked' });

		expect(panel.target.querySelector('[data-tagger-status]')?.textContent).toContain('Ready');
		expect(panel.button('Download and enable')).toBeUndefined();
		expect(panel.target.textContent).not.toContain('refuses every generation');
		panel.destroy();
	});

	it('does not warn about the tagger when the policy is Allowed', async () => {
		vi.mocked(adminApi.getContentSafetyStatus).mockResolvedValue(statusPayload() as never);
		const panel = await mountPanel({ content_policy_nsfw: 'allowed' });
		expect(panel.target.querySelector('[role="status"]')).toBeNull();
		panel.destroy();
	});

	it('writes banned words as a parsed list and tests prompts against it', async () => {
		vi.mocked(adminApi.getContentSafetyStatus).mockResolvedValue(statusPayload() as never);
		const panel = await mountPanel({ content_policy_nsfw: 'blur', content_banned_words: [] });

		const words = panel.target.querySelector<HTMLTextAreaElement>('#content-banned-words')!;
		words.value = 'Nud*\n\n  red  flag \nnud*';
		words.dispatchEvent(new Event('input', { bubbles: true }));
		expect(panel.onSettingChange).toHaveBeenCalledWith('content_banned_words', ['nud*', 'red flag']);

		const test = panel.target.querySelector<HTMLInputElement>('#content-banned-test')!;
		test.value = 'a portrait, NUDITY study';
		test.dispatchEvent(new Event('input', { bubbles: true }));
		await settle();
		expect(panel.target.querySelector('[data-banned-test-result]')?.textContent).toContain('Refused');

		test.value = 'a nudist';
		test.dispatchEvent(new Event('input', { bubbles: true }));
		await settle();
		expect(panel.target.querySelector('[data-banned-test-result]')?.textContent).toContain('Refused');

		test.value = 'a portrait of a lady';
		test.dispatchEvent(new Event('input', { bubbles: true }));
		await settle();
		expect(panel.target.querySelector('[data-banned-test-result]')?.textContent).toContain('Would pass');
		panel.destroy();
	});

	it('renders backfill progress only when there is media to rate', async () => {
		vi.mocked(adminApi.getContentSafetyStatus).mockResolvedValue(
			statusPayload({ backfill: { total: 200, rated: 50, running: true } }) as never
		);
		const panel = await mountPanel({ content_policy_nsfw: 'blur' });
		const block = panel.target.querySelector('[data-backfill]');
		expect(block?.textContent).toContain('50 / 200');
		expect(block?.querySelector('[role="progressbar"]')?.getAttribute('aria-valuenow')).toBe('25');
		panel.destroy();
	});

	it('never mentions repo or developer internals in its copy', async () => {
		vi.mocked(adminApi.getContentSafetyStatus).mockResolvedValue(statusPayload() as never);
		const panel = await mountPanel({ content_policy_nsfw: 'blur' });
		const text = panel.target.textContent ?? '';
		expect(text).not.toMatch(/repo|ships|migration|backend/i);
		panel.destroy();
	});
});
