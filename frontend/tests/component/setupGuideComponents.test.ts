import { describe, it, expect, afterEach, vi } from 'vitest';
import { mount, unmount, flushSync } from 'svelte';
import { machine, recipe, QWEN, SDXL, ZIMAGE } from './setupGuideFixtures';

vi.mock('$lib/services/adminWebsocket', () => ({
	adminWebSocket: {
		onIndexingStatus: () => () => {},
		onConnected: () => () => {},
		isConnected: () => true,
		connectAsync: async () => {}
	}
}));

const { api } = await import('$lib/services/api');
vi.spyOn(api, 'getModelRoots').mockResolvedValue({
	success: true,
	data: { roots: [], types: [], unplaced: [], indexing: { state: 'idle' }, server_os: 'Linux', path_style: 'posix' }
} as never);
vi.spyOn(api, 'getIndexingStatus').mockResolvedValue({ success: true, data: null } as never);
vi.spyOn(api, 'listModelLayouts').mockResolvedValue({ layouts: [] } as never);

const { default: ChoiceCard } = await import('../../src/lib/components/ui/ChoiceCard.svelte');
const { default: SetupStepper } = await import('../../src/lib/components/setup/SetupStepper.svelte');
const { default: StarterCard } = await import('../../src/lib/components/setup/StarterCard.svelte');
const { default: StarterStep } = await import('../../src/routes/setup/components/StarterStep.svelte');
const { default: MachineSummary } = await import('../../src/lib/components/setup/MachineSummary.svelte');
const { default: StepFacts } = await import('../../src/lib/components/setup/StepFacts.svelte');
const { default: NoCardOptions } = await import('../../src/lib/components/setup/NoCardOptions.svelte');
const { default: ReadyStep } = await import('../../src/routes/setup/components/ReadyStep.svelte');
const { default: WelcomeStep } = await import('../../src/routes/setup/components/WelcomeStep.svelte');
const { default: InstallStep } = await import('../../src/routes/setup/components/InstallStep.svelte');

let mounted: Record<string, any> | null = null;

function render(component: any, props: Record<string, unknown>) {
	const target = document.createElement('div');
	document.body.appendChild(target);
	mounted = mount(component, { target, props });
	flushSync();
	return target;
}

afterEach(() => {
	if (mounted) unmount(mounted);
	mounted = null;
	document.body.innerHTML = '';
});

describe('ChoiceCard', () => {
	function group(onSelect: (i: number) => void, checkedIndex = 0) {
		const target = document.createElement('div');
		target.setAttribute('role', 'radiogroup');
		document.body.appendChild(target);
		const cards = ['One', 'Two', 'Three'].map((title, i) => {
			const holder = document.createElement('div');
			target.appendChild(holder);
			return mount(ChoiceCard, {
				target: holder,
				props: { checked: i === checkedIndex, title, onselect: () => onSelect(i) }
			});
		});
		flushSync();
		return { target, cards };
	}

	it('renders a radio with checked state, roving tabindex and a badge', () => {
		const target = render(ChoiceCard, {
			checked: true,
			title: 'Start fresh',
			badge: 'Recommended',
			description: 'Own folder',
			onselect: () => {}
		});
		const radio = target.querySelector('[role="radio"]') as HTMLElement;
		expect(radio.getAttribute('aria-checked')).toBe('true');
		expect(radio.tabIndex).toBe(0);
		expect(target.textContent).toContain('Recommended');
		expect(target.textContent).toContain('Own folder');
	});

	it('is out of the tab order when not the active choice', () => {
		const target = render(ChoiceCard, { checked: false, title: 'X', onselect: () => {} });
		expect((target.querySelector('[role="radio"]') as HTMLElement).tabIndex).toBe(-1);
	});

	it('selects on click', () => {
		const onselect = vi.fn();
		const target = render(ChoiceCard, { checked: false, title: 'X', onselect });
		(target.querySelector('[role="radio"]') as HTMLElement).click();
		expect(onselect).toHaveBeenCalledTimes(1);
	});

	it('moves selection with arrow keys, wrapping around', () => {
		const picked: number[] = [];
		const { target, cards } = group((i) => picked.push(i));
		const radios = Array.from(target.querySelectorAll<HTMLElement>('[role="radio"]'));
		radios[0].dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowDown', bubbles: true }));
		radios[2].dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true }));
		radios[0].dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowUp', bubbles: true }));
		expect(picked).toEqual([1, 0, 2]);
		cards.forEach((c) => unmount(c));
	});
});

describe('SetupStepper', () => {
	it('marks the current step and makes earlier steps clickable', () => {
		const onnavigate = vi.fn();
		const target = render(SetupStepper, { current: 'starter', ownerExists: true, onnavigate });
		const current = target.querySelector('[aria-current="step"]') as HTMLElement;
		expect(current.textContent).toContain('Starter');
		const back = target.querySelector('[aria-label="Back to Computer"]') as HTMLButtonElement;
		expect(back.disabled).toBe(false);
		back.click();
		expect(onnavigate).toHaveBeenCalledWith('computer');
		expect(target.querySelector('[aria-label="Back to Models"]')).toBeNull();
	});

	it('locks Welcome and Account once the owner exists and future steps always', () => {
		const target = render(SetupStepper, { current: 'starter', ownerExists: true, onnavigate: () => {} });
		expect(target.querySelector('[aria-label="Back to Welcome"]')).toBeNull();
		expect(target.querySelector('[aria-label="Back to Account"]')).toBeNull();
		const buttons = Array.from(target.querySelectorAll<HTMLButtonElement>('ol button'));
		const install = buttons.find((b) => b.textContent?.includes('Install'))!;
		expect(install.disabled).toBe(true);
	});

	it('lets the focus shell go back to Welcome before an owner exists', () => {
		const target = render(SetupStepper, { current: 'account', ownerExists: false, onnavigate: () => {} });
		expect(target.querySelector('[aria-label="Back to Welcome"]')).not.toBeNull();
	});

	it('shows the compact step line for narrow screens', () => {
		const target = render(SetupStepper, { current: 'computer', ownerExists: true });
		expect(target.textContent).toContain('Step 3 of 6');
	});
});

describe('StarterCard', () => {
	it('leads with the plain line, then the model, size and a fit sentence', () => {
		const target = render(StarterCard, {
			recipe: recipe({ ...ZIMAGE, blurb: 'Makes sharp, detailed pictures quickly.' }),
			machine: machine(24),
			checked: true,
			tabbable: true,
			recommended: true,
			onselect: () => {}
		});
		const radio = target.querySelector('[role="radio"]') as HTMLElement;
		expect(radio.textContent).toContain('Makes sharp, detailed pictures quickly.');
		expect(target.querySelector('[data-spec]')?.textContent).toContain('Z-Image');
		expect(target.querySelector('[data-spec-download]')?.textContent?.trim()).toBe('19.27 GB');
		expect(target.querySelector('[data-spec-fit]')?.textContent?.trim()).toBe('Fits your card');
		expect(target.textContent).toContain('Recommended for your card');
	});

	it('no longer shows the source host or the two graphics-memory columns', () => {
		const target = render(StarterCard, {
			recipe: ZIMAGE,
			machine: machine(24),
			checked: false,
			tabbable: false,
			onselect: () => {}
		});
		expect(target.querySelector('[data-spec-source]')).toBeNull();
		expect(target.querySelector('[data-spec-vram]')).toBeNull();
		expect(target.textContent).not.toContain('Graphics memory');
		expect(target.textContent).not.toContain('Your card');
		expect(target.textContent).not.toContain('huggingface.co');
	});

	it('says the shortfall as one sentence when the card falls short', () => {
		const target = render(StarterCard, {
			recipe: QWEN,
			machine: machine(8),
			checked: false,
			tabbable: false,
			onselect: () => {}
		});
		expect(target.querySelector('[data-spec-fit]')?.textContent?.trim()).toBe(
			'Not tested below 16 GB. Yours has 8 GB.'
		);
		expect(target.textContent).not.toContain('Recommended for your card');
	});
});

describe('StarterStep', () => {
	const baseProps = {
		loading: false,
		error: '',
		modelsPath: '/data/models',
		starting: false,
		startError: '',
		onStart: () => {},
		onSkip: () => {},
		onRetry: () => {}
	};

	it('preselects the recommended starter first and states its size on the button', () => {
		const target = render(StarterStep, {
			...baseProps,
			recipes: [QWEN, SDXL, ZIMAGE, recipe({ id: 'klein', name: 'Klein', first_run: false })],
			machine: machine(24)
		});
		const radios = Array.from(target.querySelectorAll<HTMLElement>('[role="radio"]'));
		expect(radios.map((r) => r.textContent?.includes('Z-Image'))).toEqual([true, false, false]);
		expect(radios[0].getAttribute('aria-checked')).toBe('true');
		expect(target.textContent).not.toContain('Klein');
		expect(target.textContent).toContain('Review 19.27 GB download');
	});

	it('recommends SDXL on a 12 GB card and follows a different pick', () => {
		const onStart = vi.fn();
		const target = render(StarterStep, {
			...baseProps,
			onStart,
			recipes: [QWEN, SDXL, ZIMAGE],
			machine: machine(12)
		});
		const radios = Array.from(target.querySelectorAll<HTMLElement>('[role="radio"]'));
		expect(radios[0].textContent).toContain('SDXL');
		expect(radios[0].getAttribute('aria-checked')).toBe('true');
		radios[1].click();
		flushSync();
		const review = Array.from(target.querySelectorAll('button')).find((b) => b.textContent?.includes('Review'))!;
		review.click();
		expect(onStart).toHaveBeenCalledWith(ZIMAGE);
	});

	it('preselects nothing and offers to continue without a starter when there is no card', () => {
		const onSkip = vi.fn();
		const target = render(StarterStep, {
			...baseProps,
			onSkip,
			recipes: [QWEN, SDXL, ZIMAGE],
			machine: machine(null)
		});
		expect(target.querySelector('[role="radio"][aria-checked="true"]')).toBeNull();
		const buttons = Array.from(target.querySelectorAll('button'));
		expect(buttons.some((b) => b.textContent?.includes('Review'))).toBe(false);
		expect(target.textContent).toContain('No NVIDIA graphics card was found.');
		expect(target.textContent).toContain(
			'This computer has no NVIDIA graphics card, so pictures are made somewhere else.'
		);
		buttons.find((b) => b.textContent?.includes('Continue without a starter'))!.click();
		expect(onSkip).toHaveBeenCalled();
	});

	it('lists the three ways to generate without a card, each with its links', () => {
		const target = render(StarterStep, {
			...baseProps,
			recipes: [QWEN, SDXL, ZIMAGE],
			machine: machine(null)
		});
		const rows = Array.from(target.querySelectorAll('[data-no-card-option]'));
		expect(rows.map((r) => r.getAttribute('data-no-card-option'))).toEqual(['computer', 'comfyui', 'online']);
		const hrefs = rows.map((r) => Array.from(r.querySelectorAll('a')).map((a) => a.getAttribute('href')));
		expect(hrefs).toEqual([
			['/admin?tab=backends', '/docs?doc=remote-native'],
			['/admin?tab=backends', '/docs?doc=user/comfyui-presets'],
			['/admin?tab=plugins', '/docs?doc=cloud-models']
		]);
	});

	it('shows no extra options and keeps the review button when the card is fine', () => {
		const target = render(StarterStep, {
			...baseProps,
			recipes: [QWEN, SDXL, ZIMAGE],
			machine: machine(24)
		});
		expect(target.querySelector('[data-no-card-option]')).toBeNull();
		expect(target.textContent).not.toContain('Continue without a starter');
		expect(target.textContent).toContain('Skip for now');
	});

	it('puts the models folder inside the step instead of asking for it first', () => {
		const target = render(StarterStep, {
			...baseProps,
			recipes: [QWEN, SDXL, ZIMAGE],
			machine: machine(24)
		});
		const row = target.querySelector('[data-models-row]') as HTMLElement;
		expect(row.textContent).toContain('Saved to');
		expect(row.textContent).toContain('/data/models');
		const labels = Array.from(row.querySelectorAll('button')).map((b) => b.textContent?.trim());
		expect(labels).toEqual(['Change', 'Use a folder you already have']);
	});

	describe('a card below the starter minimum', () => {
		function pickZImageOnAn8GbCard(onStart: (...args: any[]) => void) {
			const target = render(StarterStep, {
				...baseProps,
				onStart,
				recipes: [QWEN, SDXL, ZIMAGE],
				machine: machine(8)
			});
			const radios = Array.from(target.querySelectorAll<HTMLElement>('[role="radio"]'));
			radios.find((r) => r.textContent?.includes('Z-Image'))!.click();
			flushSync();
			Array.from(target.querySelectorAll('button'))
				.find((b) => b.textContent?.includes('Review'))!
				.click();
			flushSync();
			return target;
		}

		const dialogButtons = () => Array.from(document.body.querySelectorAll('button'));

		it('asks first, names the need, the card, the size and the starter that fits', () => {
			const onStart = vi.fn();
			pickZImageOnAn8GbCard(onStart);
			const text = document.body.textContent ?? '';
			expect(text).toContain('This starter needs a bigger graphics card');
			expect(text).toContain('Needs a 12 GB graphics card. Yours has 8 GB.');
			expect(text).toContain('19.27 GB download');
			expect(text).toContain('It will probably not run here.');
			expect(text).toContain('SDXL fits your card.');
			expect(onStart).not.toHaveBeenCalled();
		});

		it('Pick another starts nothing', async () => {
			const onStart = vi.fn();
			pickZImageOnAn8GbCard(onStart);
			dialogButtons().find((b) => b.textContent?.includes('Pick another'))!.click();
			await new Promise((resolve) => setTimeout(resolve, 600));
			flushSync();
			expect(onStart).not.toHaveBeenCalled();
			expect(document.body.textContent).not.toContain('This starter needs a bigger graphics card');
		});

		it('Install anyway starts the run with the acknowledgement', () => {
			const onStart = vi.fn();
			pickZImageOnAn8GbCard(onStart);
			dialogButtons().find((b) => b.textContent?.includes('Install anyway'))!.click();
			flushSync();
			expect(onStart).toHaveBeenCalledTimes(1);
			expect(onStart).toHaveBeenCalledWith(ZIMAGE, {
				safeInput: { card_memory_gb: 8, needed_memory_gb: 12, below_graphics_memory: true }
			});
		});

		it('does not ask when the card meets the minimum', () => {
			const onStart = vi.fn();
			const target = render(StarterStep, {
				...baseProps,
				onStart,
				recipes: [QWEN, SDXL, ZIMAGE],
				machine: machine(12)
			});
			Array.from(target.querySelectorAll<HTMLElement>('[role="radio"]'))
				.find((r) => r.textContent?.includes('Z-Image'))!
				.click();
			flushSync();
			Array.from(target.querySelectorAll('button')).find((b) => b.textContent?.includes('Review'))!.click();
			expect(onStart).toHaveBeenCalledWith(ZIMAGE);
			expect(document.body.textContent).not.toContain('This starter needs a bigger graphics card');
		});

		it('does not ask when the memory is unknown', () => {
			const onStart = vi.fn();
			const unreadable = machine(8);
			unreadable.gpu!.vram_bytes = 0;
			const target = render(StarterStep, {
				...baseProps,
				onStart,
				recipes: [QWEN, SDXL, ZIMAGE],
				machine: unreadable
			});
			Array.from(target.querySelectorAll<HTMLElement>('[role="radio"]'))
				.find((r) => r.textContent?.includes('Z-Image'))!
				.click();
			flushSync();
			Array.from(target.querySelectorAll('button')).find((b) => b.textContent?.includes('Review'))!.click();
			expect(onStart).toHaveBeenCalledWith(ZIMAGE);
		});
	});

	it('shows an error with a retry', () => {
		const onRetry = vi.fn();
		const target = render(StarterStep, {
			...baseProps,
			onRetry,
			recipes: null,
			error: 'boom',
			machine: null
		});
		expect(target.textContent).toContain('boom');
		Array.from(target.querySelectorAll('button')).find((b) => b.textContent?.includes('Try again'))!.click();
		expect(onRetry).toHaveBeenCalled();
	});
});

describe('MachineSummary', () => {
	it('renders detected values and a verdict', () => {
		const target = render(MachineSummary, { machine: machine(31.4), recipes: [QWEN, SDXL, ZIMAGE] });
		expect(target.textContent).toContain('NVIDIA GeForce RTX');
		expect(target.textContent).toContain('31 GB');
		expect(target.textContent).toContain('64 GB');
		expect(target.textContent).toContain('412 GB');
		expect(target.querySelector('[data-verdict="good"]')).not.toBeNull();
	});

	it('explains a missing GPU', () => {
		const target = render(MachineSummary, { machine: machine(null), recipes: [] });
		expect(target.textContent).toContain('No NVIDIA graphics card found');
		expect(target.querySelector('[data-verdict="no-gpu"]')).not.toBeNull();
	});

	it('degrades gracefully when nothing could be detected', () => {
		const target = render(MachineSummary, { machine: null, recipes: [] });
		expect(target.textContent?.match(/Couldn't detect/g)?.length).toBe(3);
		expect(target.querySelector('[data-verdict="unknown"]')).not.toBeNull();
	});

	it('copes with partial data', () => {
		const target = render(MachineSummary, {
			machine: machine(16, { ram_total_bytes: null, models_disk: null }),
			recipes: []
		});
		expect(target.textContent?.match(/Couldn't detect/g)?.length).toBe(2);
	});
});

describe('StepFacts', () => {
	it('renders plain and monospaced parts', () => {
		const target = render(StepFacts, { facts: [{ label: 'Stored in', body: [{ mono: 'storage/db.sqlite' }, ' inside it.'] }] });
		expect(target.textContent).toContain('Stored in');
		expect(target.querySelector('.font-mono.tabular-nums')?.textContent).toBe('storage/db.sqlite');
	});

	it('offers line breaks only after path separators', () => {
		const target = render(StepFacts, { facts: [{ label: 'Saved to', body: [{ mono: '/workspace/potionui/models' }] }] });
		const mono = target.querySelector('.font-mono.tabular-nums')!;
		expect(mono.textContent).toBe('/workspace/potionui/models');
		expect(mono.querySelectorAll('wbr')).toHaveLength(3);
		expect(mono.className).not.toContain('anywhere');
		const windows = render(StepFacts, { facts: [{ label: 'Saved to', body: [{ mono: 'C:\\models\\flux' }] }] });
		expect(windows.querySelectorAll('wbr')).toHaveLength(2);
	});
});

describe('WelcomeStep', () => {
	it('keeps the title, the three facts and the button, and drops the plan and trust cards', () => {
		const onStart = vi.fn();
		const target = render(WelcomeStep, { onStart });
		expect(target.querySelector('h2')?.textContent).toBe('A local studio for AI images and video');
		expect(target.textContent).toContain('You will need');
		expect(target.textContent).toContain('Disk space');
		expect(target.textContent).toContain('Internet');
		expect(target.textContent).not.toContain('What setup will do');
		expect(target.textContent).not.toContain('Runs on your computer');
		const start = Array.from(target.querySelectorAll('button')).find((b) => b.textContent?.includes('Get started'))!;
		start.click();
		expect(onStart).toHaveBeenCalled();
	});
});

describe('ReadyStep', () => {
	const base = { installedName: 'Z-Image', thumbnailUrl: null, modelsPath: '/data/models', onCreate: () => {} };

	it('success: says a test image was made and offers the first image', () => {
		const target = render(ReadyStep, { ...base });
		expect(target.querySelector('[data-ready-state="installed"]')).not.toBeNull();
		expect(target.textContent).toContain('Z-Image is installed and a test image was made on this computer.');
		expect(target.textContent).toContain('Create your first image');
		expect(target.textContent).toContain('Add another models folder');
		expect(target.textContent).toContain('Install more starters');
		expect(target.textContent).not.toContain('Connect ComfyUI');
		expect(target.textContent).not.toContain('Invite people');
	});

	it('test failed: warns, names the sentence and the next action, and keeps the details behind a disclosure', () => {
		const onCreate = vi.fn();
		const target = render(ReadyStep, {
			...base,
			onCreate,
			smokeFailure: {
				reason: "The test didn't finish: it took longer than 5 minutes.",
				action: 'Make a picture in Generate to try again.',
				technical: 'timed out waiting for completion after 300s'
			}
		});
		expect(target.querySelector('[data-ready-state="test-failed"]')).not.toBeNull();
		expect(target.textContent).toContain('Z-Image is installed');
		expect(target.textContent).toContain('Test failed');
		expect(target.querySelector('[data-smoke-reason]')?.textContent).toBe(
			"The test didn't finish: it took longer than 5 minutes."
		);
		expect(target.textContent).toContain('Make a picture in Generate to try again.');
		expect(target.textContent).not.toContain('a test image was made');
		expect(target.textContent).not.toContain('Create your first image');
		const details = target.querySelector('details') as HTMLDetailsElement;
		expect(details.textContent).toContain('timed out waiting for completion after 300s');
		expect(details.open).toBe(false);
		const again = Array.from(target.querySelectorAll('a')).find((a) => a.textContent?.includes('Run the check again'))!;
		expect(again.getAttribute('href')).toBe('/admin?tab=recipes');
		Array.from(target.querySelectorAll('button')).find((b) => b.textContent?.includes('Try it in Generate'))!.click();
		expect(onCreate).toHaveBeenCalled();
	});

	it('test failed on a card below the starter says so', () => {
		const withNote = render(ReadyStep, {
			...base,
			belowCard: true,
			smokeFailure: { reason: "The test didn't finish.", action: null, technical: null }
		});
		expect(withNote.querySelector('[data-below-card]')?.textContent).toBe(
			'This card is below what Z-Image needs.'
		);
		document.body.innerHTML = '';
		const without = render(ReadyStep, {
			...base,
			smokeFailure: { reason: "The test didn't finish.", action: null, technical: null }
		});
		expect(without.querySelector('[data-below-card]')).toBeNull();
		expect(without.querySelector('details')).toBeNull();
	});

	it('no card and nothing installed: shows the three options and hides the first-image button', () => {
		const target = render(ReadyStep, { ...base, installedName: null, noCard: true });
		expect(target.querySelector('[data-ready-state="no-card"]')).not.toBeNull();
		expect(target.textContent).toContain(
			'This computer has no NVIDIA graphics card, so pictures are made somewhere else.'
		);
		expect(target.querySelectorAll('[data-no-card-option]')).toHaveLength(3);
		expect(target.textContent).not.toContain('Create your first image');
		const connect = Array.from(target.querySelectorAll('a')).find((a) => a.textContent?.includes('Connect a backend'))!;
		expect(connect.getAttribute('href')).toBe('/admin?tab=backends');
	});

	it('with a card and nothing installed nothing changes', () => {
		const target = render(ReadyStep, { ...base, installedName: null });
		expect(target.querySelector('[data-ready-state="empty"]')).not.toBeNull();
		expect(target.textContent).toContain('Install a starter model any time from Setup');
		expect(target.textContent).toContain('Create your first image');
		expect(target.querySelector('[data-no-card-option]')).toBeNull();
	});
});

describe('InstallStep', () => {
	const consentSteps = [
		{
			step_key: 'artifacts.plan',
			title: 'Plan the download',
			kind: 'artifacts.plan',
			ordinal: 1,
			status: 'awaiting_consent',
			attempts: [
				{
					step_key: 'artifacts.plan',
					attempt: 1,
					status: 'awaiting_consent',
					progress_current: null,
					progress_total: null,
					progress_unit: null,
					safe_output: {
						consent_request: {
							artifacts: [
								{ id: 'a', display_name: 'unet.safetensors', size_bytes: 1024, kind: 'checkpoint', gated: false, license_url: null }
							],
							total_bytes: 1024
						}
					},
					error_code: null,
					safe_error_detail: null,
					safe_suggested_action: null,
					started_at: null,
					finished_at: null
				}
			]
		}
	];
	const run = (overrides: Record<string, unknown> = {}) =>
		({
			id: 'r1',
			recipe_id: 'zimage-starter',
			recipe_version: 2,
			scope: 'instance',
			mode: 'onboarding',
			status: 'awaiting_consent',
			current_step: 'artifacts.plan',
			safe_input: null,
			safe_output: null,
			error_code: null,
			safe_error_detail: null,
			created_at: null,
			updated_at: null,
			completed_at: null,
			steps: consentSteps,
			attempts: [],
			...overrides
		}) as any;
	const props = { fetchError: '', modelsPath: '/data/models', onRunUpdated: () => {}, onStartOver: () => {} };

	it('warns above the file list when the card is below the starter minimum', () => {
		const target = render(InstallStep, { ...props, run: run(), recipe: ZIMAGE, machine: machine(8) });
		const note = target.querySelector('[data-shortfall-note]');
		expect(note?.textContent).toContain('Needs a 12 GB graphics card. Yours has 8 GB.');
		expect(note?.textContent).toContain('It will probably not run here.');
	});

	it('shows no warning when the card is enough or the machine is unknown', () => {
		const fine = render(InstallStep, { ...props, run: run(), recipe: ZIMAGE, machine: machine(24) });
		expect(fine.querySelector('[data-shortfall-note]')).toBeNull();
		document.body.innerHTML = '';
		const unknown = render(InstallStep, { ...props, run: run(), recipe: ZIMAGE, machine: null });
		expect(unknown.querySelector('[data-shortfall-note]')).toBeNull();
	});

	it('keeps the opening the spec asserts and drops the word plugin', () => {
		const target = render(InstallStep, { ...props, run: run(), recipe: ZIMAGE, machine: machine(24) });
		const info = target.querySelector('[data-hf-info]')!.textContent ?? '';
		expect(info).toContain('Downloads come from Hugging Face');
		expect(info.toLowerCase()).not.toContain('plugin');
	});

	it('says an interrupted run was interrupted and offers to continue', () => {
		const target = render(InstallStep, {
			...props,
			run: run({
				status: 'failed',
				error_code: 'RUN_INTERRUPTED',
				safe_error_detail: 'This step was interrupted by a restart. Retry it to pick up where it left off.'
			}),
			recipe: ZIMAGE,
			machine: machine(24)
		});
		expect(target.textContent).toContain('Setup was interrupted');
		expect(target.textContent).toContain('Files already downloaded are kept.');
		expect(target.textContent).not.toContain("Setup couldn't finish");
		const labels = Array.from(target.querySelectorAll('button')).map((b) => b.textContent?.trim());
		expect(labels).toContain('Continue');
		expect(labels).not.toContain('Try again');
	});

	it('keeps Try again for an ordinary failure', () => {
		const target = render(InstallStep, {
			...props,
			run: run({ status: 'failed', error_code: 'ARTIFACT_DOWNLOAD_FAILED', safe_error_detail: "Downloading 'A' failed." }),
			recipe: ZIMAGE,
			machine: machine(24)
		});
		expect(target.textContent).toContain("Setup couldn't finish");
		expect(Array.from(target.querySelectorAll('button')).map((b) => b.textContent?.trim())).toContain('Try again');
	});
});

describe('NoCardOptions', () => {
	it('renders three rows in plain words with what is needed outside this computer', () => {
		const target = render(NoCardOptions, {});
		const text = target.textContent ?? '';
		expect(text).toContain('Another computer with an NVIDIA card');
		expect(text).toContain('A ComfyUI server');
		expect(text).toContain('An online service');
		expect(text).toContain('You need: A second computer with an NVIDIA graphics card.');
		expect(text).toContain('You need: A key from the service you choose.');
	});
});
