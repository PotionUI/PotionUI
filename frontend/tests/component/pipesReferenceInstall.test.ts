// @vitest-environment jsdom
//
// A pipe reporting NOT_INSTALLED used to be a dead end - the reference showed
// the status and offered nowhere to go. What has to hold now is which of the
// two affordances a given pipe gets: an Install button only where an install
// can succeed, and the build commands where it cannot. An Install button on a
// pipe whose extensions are compiled from source is worse than none, so the
// case that matters most here is the one where the button must NOT appear.
import { describe, it, expect, vi, afterEach } from 'vitest';
import { writable } from 'svelte/store';

const installPipe = vi.fn().mockResolvedValue({ success: true, data: {} });
const getDocsLivePipes = vi.fn();

vi.mock('$lib/services/api/index', () => ({
	api: {
		get getDocsLivePipes() {
			return getDocsLivePipes;
		},
		get installPipe() {
			return installPipe;
		}
	}
}));

let pipeInstallCallback: ((status: unknown) => void) | null = null;

vi.mock('$lib/services/adminWebsocket', () => ({
	adminWebSocket: {
		isConnected: () => true,
		connectAsync: vi.fn().mockResolvedValue(undefined),
		onPipeInstallStatus: (cb: (status: unknown) => void) => {
			pipeInstallCallback = cb;
			return () => {
				pipeInstallCallback = null;
			};
		}
	}
}));

const authStore = writable<{ user: { account_type: string } | null }>({
	user: { account_type: 'ADMIN' }
});

vi.mock('$lib/stores/auth', () => ({
	get authStore() {
		return authStore;
	}
}));

const { default: PipesReference } = await import(
	'../../src/routes/docs/components/live/PipesReference.svelte'
);
const { createClassComponent } = await import('svelte/legacy');

const PIP_PIPE = {
	name: 'upscaler',
	description: 'Needs a pip package',
	status: 'not_installed',
	manual_install: null,
	requirements: { pip: ['realesrgan'], git: [], models: [] }
};

const SOURCE_BUILT_PIPE = {
	name: 'generator/trellis2',
	description: 'Needs six CUDA extensions',
	status: 'not_installed',
	manual_install: 'Build them from source:\n    . ./setup.sh --cumesh --o-voxel',
	requirements: { pip: ['cumesh'], git: [], models: [] }
};

async function mountReference(pipes: unknown[]) {
	getDocsLivePipes.mockResolvedValue({ success: true, data: { pipes } });

	const target = document.createElement('div');
	document.body.appendChild(target);
	const component = createClassComponent({ component: PipesReference as never, target, props: {} });

	await settle();
	// Everything install-related lives in the disclosure panel.
	for (const row of Array.from(target.querySelectorAll('button[aria-expanded]'))) {
		(row as HTMLButtonElement).click();
	}
	await settle();

	return {
		target,
		text: () => target.textContent ?? '',
		buttonLabelled: (label: string) =>
			Array.from(target.querySelectorAll('button')).find((b) =>
				(b.textContent ?? '').includes(label)
			),
		destroy: () => {
			component.$destroy();
			target.remove();
		}
	};
}

function settle() {
	return new Promise((resolve) => setTimeout(resolve, 0));
}

let mounted: Awaited<ReturnType<typeof mountReference>> | undefined;

afterEach(() => {
	mounted?.destroy();
	mounted = undefined;
	installPipe.mockClear();
	authStore.set({ user: { account_type: 'ADMIN' } });
});

describe('pipes reference install affordance', () => {
	it('offers an install for a pipe whose requirements pip can satisfy', async () => {
		mounted = await mountReference([PIP_PIPE]);

		const button = mounted.buttonLabelled('Install requirements');
		expect(button).toBeDefined();

		button!.click();
		await settle();

		expect(installPipe).toHaveBeenCalledWith('upscaler');
	});

	it('shows the build commands, and no install, for a pipe built from source', async () => {
		mounted = await mountReference([SOURCE_BUILT_PIPE]);

		expect(mounted.buttonLabelled('Install requirements')).toBeUndefined();
		expect(mounted.text()).toContain('. ./setup.sh --cumesh --o-voxel');
		expect(mounted.text()).toContain('manual setup');
	});

	it('never offers an install to a non-admin', async () => {
		authStore.set({ user: { account_type: 'USER' } });
		mounted = await mountReference([PIP_PIPE]);

		expect(mounted.buttonLabelled('Install requirements')).toBeUndefined();
		expect(mounted.text()).toContain('An administrator has to install');
	});

	it('repaints the row when the install reports failure over the admin socket', async () => {
		mounted = await mountReference([PIP_PIPE]);

		expect(mounted.text()).toContain('not installed');

		pipeInstallCallback?.({
			pipe: 'upscaler',
			status: 'error',
			message: "Pipe 'upscaler' failed to install: No matching distribution found for realesrgan"
		});
		await settle();

		expect(mounted.text()).toContain('install failed');
		expect(mounted.text()).toContain('No matching distribution found for realesrgan');
	});
});
