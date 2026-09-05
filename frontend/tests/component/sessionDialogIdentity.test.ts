// @vitest-environment jsdom
//
// Cancel is enabled while a save is still in flight, so the user can dismiss
// the dialog and open a fresh one without ever submitting a second time. The
// controller's command handle and its tab/preset/mode context are identical
// across that cancel/reopen, so nothing but an explicit dialog identity can
// tell the two openings apart: without one, the old completion's "you may
// close" answer closes the dialog the user just opened, and an old failure
// writes its message into it. Driven through the real presenter so the
// close/reopen goes through the same handlers the user's clicks reach.
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

vi.mock('$lib/services/api/index', () => ({
	api: {
		getSessionsForPreset: vi.fn(),
		getSessionById: vi.fn(),
		saveSession: vi.fn()
	}
}));

const { api } = await import('$lib/services/api/index');
const { tabsStore } = await import('$lib/stores/tabs');
const { default: SessionPill } = await import('$lib/components/session/SessionPill.svelte');
const { createClassComponent } = await import('svelte/legacy');

const PRESET_ID = 'preset-dialog-identity';
const CURRENT_MODE = 'image';

function makeSession(id: string, name = `Session ${id}`) {
	return {
		id,
		preset_id: PRESET_ID,
		name,
		data: { [CURRENT_MODE]: {} },
		created_at: '2026-01-01T00:00:00Z',
		updated_at: '2026-01-01T00:00:00Z'
	};
}

function deferred<T>() {
	let resolve!: (value: T) => void;
	let reject!: (reason?: unknown) => void;
	const promise = new Promise<T>((res, rej) => {
		resolve = res;
		reject = rej;
	});
	promise.catch(() => {});
	return { promise, resolve, reject };
}

async function settle() {
	for (let i = 0; i < 10; i++) await new Promise((resolve) => setTimeout(resolve, 0));
}

function mountPill(tabId: string) {
	const target = document.createElement('div');
	document.body.appendChild(target);
	const component = createClassComponent({
		component: SessionPill as never,
		target,
		props: { presetId: PRESET_ID, currentMode: CURRENT_MODE, tabId, availableModes: [] }
	});
	// BaseModal portals its dialog to document.body, so modal content is not
	// under the mount point, and the pill's own "Save" button would otherwise
	// shadow the dialog's.
	const dialog = () => document.querySelector<HTMLElement>('[role="dialog"],[role="alertdialog"]');
	const clickIn = (root: ParentNode, text: string) =>
		Array.from(root.querySelectorAll<HTMLButtonElement>('button'))
			.find((el) => el.textContent?.trim().startsWith(text))!
			.click();
	return {
		target,
		component,
		saveDialogOpen: () => !!document.querySelector('#session-save-name'),
		nameError: () => dialog()?.querySelector('.text-danger')?.textContent ?? '',
		openSaveAs: () => {
			target.querySelector<HTMLButtonElement>('button[aria-label="Save as a new session"]')!.click();
		},
		clickDialog: (text: string) => clickIn(dialog()!, text),
		type: (value: string) => {
			const input = document.querySelector<HTMLInputElement>('#session-save-name')!;
			input.value = value;
			input.dispatchEvent(new Event('input', { bubbles: true }));
		},
		destroy: () => {
			component.$destroy();
			target.remove();
		}
	};
}

let mounted: ReturnType<typeof mountPill> | undefined;
let tabId: string;

afterEach(() => {
	mounted?.destroy();
	mounted = undefined;
	tabsStore.removeTab(tabId);
	vi.clearAllMocks();
});

describe('SessionPill save dialog identity', () => {
	beforeEach(() => {
		tabId = tabsStore.addTabWithData('Dialog identity tab', {
			selectedPreset: PRESET_ID,
			selectedMode: CURRENT_MODE
		});
		vi.mocked(api.getSessionsForPreset).mockResolvedValue({ success: true, data: [] } as never);
	});

	it('does not close a save dialog reopened after the previous attempt was cancelled', async () => {
		const pending = deferred<unknown>();
		vi.mocked(api.saveSession).mockReturnValue(pending.promise as never);

		mounted = mountPill(tabId);
		await settle();

		mounted.openSaveAs();
		await settle();
		mounted.type('First attempt');
		await settle();
		mounted.clickDialog('Save');
		await settle();

		mounted.clickDialog('Cancel');
		await settle();
		expect(mounted.saveDialogOpen()).toBe(false);

		// Reopened, with nothing submitted from it.
		mounted.openSaveAs();
		await settle();
		expect(mounted.saveDialogOpen()).toBe(true);

		pending.resolve({ success: true, data: makeSession('created', 'First attempt') });
		await settle();

		expect(mounted.saveDialogOpen()).toBe(true);
	});

	it('does not write a cancelled attempt failure into the reopened save dialog', async () => {
		const pending = deferred<unknown>();
		vi.mocked(api.saveSession).mockReturnValue(pending.promise as never);

		mounted = mountPill(tabId);
		await settle();

		mounted.openSaveAs();
		await settle();
		mounted.type('First attempt');
		await settle();
		mounted.clickDialog('Save');
		await settle();

		mounted.clickDialog('Cancel');
		await settle();
		mounted.openSaveAs();
		await settle();

		pending.reject(new Error('server refused'));
		await settle();

		expect(mounted.saveDialogOpen()).toBe(true);
		expect(mounted.nameError()).not.toContain('server refused');
	});
});
