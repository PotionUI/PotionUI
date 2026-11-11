import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { flushSync } from 'svelte';
import { settle } from './helpers/settle';

const hoisted = vi.hoisted(() => ({
	goto: vi.fn(),
	getSetupStatus: vi.fn()
}));

vi.mock('$app/navigation', () => ({ goto: hoisted.goto }));
vi.mock('$lib/services/api/index', () => ({
	api: { getSetupStatus: hoisted.getSetupStatus }
}));
vi.mock('$lib/stores/auth', () => ({
	authStore: {
		subscribe: (fn: (v: unknown) => void) => {
			fn({ isAuthenticated: true, loading: false, error: null, user: null, token: 't' });
			return () => {};
		}
	}
}));

const { default: RootPage } = await import('../../src/routes/+page.svelte');
const { createClassComponent } = await import('svelte/legacy');

let target: HTMLElement;
let instance: { $destroy: () => void };

function mountPage() {
	target = document.createElement('div');
	document.body.appendChild(target);
	instance = createClassComponent({ component: RootPage as never, target, props: {} });
}

async function advance(ms: number) {
	await vi.advanceTimersByTimeAsync(ms);
	flushSync();
}

const STARTING = 'Starting up. This can take a minute on the first run.';
const NOT_ANSWERING = "PotionUI's server is not answering yet";

beforeEach(() => {
	vi.useFakeTimers();
	hoisted.goto.mockReset();
	hoisted.getSetupStatus.mockReset();
});

afterEach(() => {
	instance?.$destroy();
	target?.remove();
	vi.useRealTimers();
});

describe('root page while the setup status is unknown', () => {
	it('never navigates over 70 seconds of failing fetches and shows the stages in order', async () => {
		hoisted.getSetupStatus.mockRejectedValue(new Error('Network Error'));
		mountPage();
		await settle();
		flushSync();

		expect(target.textContent).not.toContain('Starting up');
		expect(target.textContent).not.toContain(NOT_ANSWERING);

		await advance(6000);
		expect(target.textContent).toContain(STARTING);
		expect(target.textContent).not.toContain(NOT_ANSWERING);

		await advance(64000);
		expect(target.textContent).toContain(NOT_ANSWERING);
		expect(target.textContent).not.toContain(STARTING);
		expect(target.textContent).toContain('Checking again every few seconds');
		expect(target.textContent).toContain('Check now');
		expect(target.textContent).toContain('Network Error');
		expect(hoisted.goto).not.toHaveBeenCalled();
	});

	it('retries every 1.5 s for the first minute and every 5 s afterwards', async () => {
		hoisted.getSetupStatus.mockRejectedValue(new Error('Network Error'));
		mountPage();
		await settle();
		expect(hoisted.getSetupStatus).toHaveBeenCalledTimes(1);

		await advance(1500);
		expect(hoisted.getSetupStatus).toHaveBeenCalledTimes(2);

		await advance(58500);
		const atMinute = hoisted.getSetupStatus.mock.calls.length;
		expect(atMinute).toBeGreaterThanOrEqual(40);

		await advance(10000);
		const added = hoisted.getSetupStatus.mock.calls.length - atMinute;
		expect(added).toBeGreaterThanOrEqual(1);
		expect(added).toBeLessThanOrEqual(3);
	});

	it('navigates to the claim page once a later fetch succeeds', async () => {
		hoisted.getSetupStatus
			.mockRejectedValueOnce(new Error('Network Error'))
			.mockRejectedValueOnce(new Error('Network Error'))
			.mockResolvedValue({ needs_owner: true, registration_open: true, claim_requires_token: false });
		mountPage();
		await settle();
		await advance(5000);
		expect(hoisted.goto).toHaveBeenCalledTimes(1);
		expect(hoisted.goto).toHaveBeenCalledWith('/setup/claim');
	});

	it('Check now fetches at once', async () => {
		hoisted.getSetupStatus.mockRejectedValue(new Error('Network Error'));
		mountPage();
		await settle();
		await advance(70000);
		const before = hoisted.getSetupStatus.mock.calls.length;

		const button = [...target.querySelectorAll('button')].find((b) => b.textContent?.includes('Check now'));
		expect(button).toBeTruthy();
		button!.click();
		await settle();
		expect(hoisted.getSetupStatus.mock.calls.length).toBe(before + 1);
	});

	it('words an error status as an answer, not as starting up', async () => {
		hoisted.getSetupStatus.mockRejectedValue(
			Object.assign(new Error('Request failed with status code 500'), { response: { status: 500 } })
		);
		mountPage();
		await settle();
		await advance(6000);
		expect(target.textContent).toContain('The server answered with an error (500)');
		expect(target.textContent).not.toContain('Starting up');

		await advance(64000);
		expect(target.textContent).toContain('The server answered with an error (500)');
		expect(target.textContent).not.toContain(NOT_ANSWERING);
		expect(target.textContent).toContain('Request failed with status code 500');
		expect(hoisted.goto).not.toHaveBeenCalled();
	});
});
