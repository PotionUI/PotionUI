import { describe, it, expect } from 'vitest';
import {
	aggregateDownloadBytes,
	buildGrantBody,
	chosenVariant,
	computeTotals,
	diskShortfall,
	downloadFileLabel,
	estimateSeconds,
	extractApprovedPlan,
	extractDownloadProgress,
	extractModesConsent,
	formatEta,
	freeHere,
	gatedNotice,
	initialInstallState,
	installBlocker,
	installLabel,
	isDerivedRecipeId,
	isModesRun,
	mergeReplan,
	selectedSlotIds,
	usedByLabel
} from './modeInstallModel';
import { modesRequest, slot, variant } from './modeInstallFixtures';

const GB = 1_000_000_000;

describe('initialInstallState', () => {
	it('ticks ready and locked modes, and every missing mode when nothing is preselected', () => {
		const state = initialInstallState(modesRequest());
		expect(state.modes).toEqual({ video: true, cinemagraph: true, motion: true });
		expect(state.targetId).toBe('native');
	});

	it('ticks only the preselected missing mode', () => {
		const state = initialInstallState(modesRequest(), ['cinemagraph']);
		expect(state.modes).toEqual({ video: true, cinemagraph: true, motion: false });
	});

	it('respects the payload selection when the server marked a missing mode selected', () => {
		const request = modesRequest();
		request.modes[2].selected = true;
		const state = initialInstallState(request);
		expect(state.modes).toEqual({ video: true, cinemagraph: false, motion: true });
	});
});

describe('variant selection', () => {
	it('keeps the installed variant preselected, not the GPU pick', () => {
		const request = modesRequest();
		const state = initialInstallState(request);
		const model = request.slots[0];
		expect(chosenVariant(model, state)?.id).toBe('int8');
		expect(model.best_fit_variant_id).toBe('nvfp4');
	});

	it('uses an admin pick over the recommendation and ignores unknown picks', () => {
		const request = modesRequest();
		const state = initialInstallState(request);
		state.variants = { model: 'nvfp4' };
		expect(chosenVariant(request.slots[0], state)?.id).toBe('nvfp4');
		state.variants = { model: 'ghost' };
		expect(chosenVariant(request.slots[0], state)?.id).toBe('int8');
	});
});

describe('computeTotals', () => {
	it('counts a shared slot once and skips installed files', () => {
		const request = modesRequest();
		const state = initialInstallState(request);
		const totals = computeTotals(request, state);
		expect(totals.files.map((file) => file.id).sort()).toEqual(['effect_lora@cinemagraph', 'patch@motion']);
		expect(totals.bytes).toBe(5 * GB);
		expect(totals.bytesByDisk).toEqual({ d0: 5 * GB });
	});

	it('moves the total when a different variant is picked', () => {
		const request = modesRequest();
		const state = initialInstallState(request);
		state.variants = { model: 'nvfp4' };
		expect(computeTotals(request, state).bytes).toBe(16 * GB);
	});

	it('drops the files of a mode once it is unticked', () => {
		const request = modesRequest();
		const state = initialInstallState(request, ['cinemagraph']);
		expect(computeTotals(request, state).bytes).toBe(2 * GB);
		state.modes.cinemagraph = false;
		expect(computeTotals(request, state).bytes).toBe(0);
	});

	it('adds ticked extras only', () => {
		const request = modesRequest();
		const state = initialInstallState(request, ['cinemagraph']);
		const before = computeTotals(request, state).bytes;
		state.extras = { preview_decoder: false };
		expect(computeTotals(request, state).bytes).toBe(before);
		state.extras = { preview_decoder: true };
		expect(computeTotals(request, state).bytes).toBe(before + GB / 10);
	});

	it('never counts manual or satisfied slots', () => {
		const request = modesRequest();
		request.slots[1] = slot('effect_lora@cinemagraph', { manual: true, used_by: ['cinemagraph'] });
		request.slots[2] = slot('patch@motion', { satisfied_by: 'admin_default', used_by: ['motion'] });
		expect(computeTotals(request, initialInstallState(request)).bytes).toBe(0);
	});

	it('downloads the first slot of an any_of group when none is present', () => {
		const request = modesRequest();
		request.slots.push(slot('alt_a', { variants: [variant('a', { size_bytes: GB })] }), slot('alt_b', { variants: [variant('b', { size_bytes: 9 * GB })] }));
		request.modes[1].any_of = [['alt_a', 'alt_b']];
		const ids = selectedSlotIds(request, initialInstallState(request, ['cinemagraph']));
		expect(ids.has('alt_a')).toBe(true);
		expect(ids.has('alt_b')).toBe(false);
	});

	it('skips an any_of group already satisfied by an installed slot', () => {
		const request = modesRequest();
		request.slots.push(slot('alt_a', { variants: [variant('a')] }), slot('alt_b', { variants: [variant('b', { installed: true })] }));
		request.modes[1].any_of = [['alt_a', 'alt_b']];
		expect(selectedSlotIds(request, initialInstallState(request, ['cinemagraph'])).has('alt_a')).toBe(false);
	});
});

describe('disk state', () => {
	it('is fine when the selection fits below free minus the kept spare', () => {
		const request = modesRequest();
		const totals = computeTotals(request, initialInstallState(request));
		expect(diskShortfall(request, totals)).toBeNull();
		expect(installBlocker(request, initialInstallState(request))).toBeNull();
	});

	it('reports how much to deselect when the disk is too small', () => {
		const request = modesRequest();
		request.disk = [{ id: 'd0', target: 'pod', label: 'Models folder', free_bytes: 3.1 * GB, reserve_bytes: 2 * GB }];
		const state = initialInstallState(request);
		const short = diskShortfall(request, computeTotals(request, state));
		expect(short?.usable).toBeCloseTo(1.1 * GB);
		expect(short?.short).toBeCloseTo(3.9 * GB);
		expect(installBlocker(request, state)).toBe('Deselect 3.6 GB to continue');
	});

	it('does not block on a disk whose free space is unknown', () => {
		const request = modesRequest();
		request.disk = [{ id: 'd0', target: 'pod', label: 'Models folder', free_bytes: null, reserve_bytes: 2 * GB }];
		expect(installBlocker(request, initialInstallState(request))).toBeNull();
		expect(freeHere(request)).toBe(request.target.free_bytes);
	});

	it('blocks when nothing is picked', () => {
		const request = modesRequest();
		const state = initialInstallState(request, ['cinemagraph']);
		state.modes.cinemagraph = false;
		expect(installBlocker(request, state)).toBe('Pick a mode to install');
	});

	it('blocks installs on a server that manages its own files', () => {
		const request = modesRequest();
		request.target.kind = 'external';
		expect(installBlocker(request, initialInstallState(request))).toMatch(/cannot download/);
	});
});

describe('time estimate', () => {
	it('is null when the API sent no speed, so the cell stays hidden', () => {
		const request = modesRequest({ estimate: null });
		expect(estimateSeconds(request, 5 * GB)).toBeNull();
	});

	it('divides the selection by the recent speed', () => {
		const request = modesRequest();
		expect(estimateSeconds(request, 31 * GB)).toBeCloseTo(1000);
		expect(estimateSeconds(request, 0)).toBeNull();
	});

	it('formats minutes and hours', () => {
		expect(formatEta(20)).toBe('About 1 min');
		expect(formatEta(180)).toBe('About 3 min');
		expect(formatEta(3900)).toBe('About 1 h 5 min');
	});
});

describe('labels and grant body', () => {
	it('names the install button after the modes and a single picked variant', () => {
		const request = modesRequest();
		const state = initialInstallState(request);
		expect(installLabel(request, state)).toBe('Install 2 modes');
		state.variants = { model: 'nvfp4' };
		expect(installLabel(request, state)).toBe('Install 2 modes and nvfp4');
	});

	it('sends only explicit picks, ticked modes, ticked extras and the target', () => {
		const request = modesRequest();
		const state = initialInstallState(request, ['cinemagraph']);
		state.variants = { model: 'nvfp4', 'patch@motion': 'motion' };
		state.extras = { preview_decoder: true };
		state.targetId = 'native';
		expect(buildGrantBody(request, state)).toEqual({
			selections: { model: 'nvfp4' },
			modes: ['video', 'cinemagraph'],
			extras: ['preview_decoder'],
			target: 'native'
		});
	});

	it('lists the gated files of the selection with the first licence link', () => {
		const request = modesRequest();
		const state = initialInstallState(request);
		expect(gatedNotice(request, state)).toEqual({ labels: ['Motion transfer'], licenseUrl: 'https://example.test/licence' });
		state.modes.motion = false;
		expect(gatedNotice(request, state)).toBeNull();
	});

	it('describes who uses a slot', () => {
		const request = modesRequest();
		expect(usedByLabel(request, request.slots[0])).toBe('Used by every mode');
		expect(usedByLabel(request, request.slots[1])).toBe('Cinemagraph');
	});
});

describe('payload parsing', () => {
	const attempt = (output: Record<string, unknown> | null) => ({ safe_output: output });

	it('only accepts a modes consent request', () => {
		expect(extractModesConsent(attempt({ consent_request: { artifacts: [] } }))).toBeNull();
		const parsed = extractModesConsent(attempt({ consent_request: modesRequest() as never }));
		expect(parsed?.modes).toHaveLength(3);
		expect(parsed?.estimate).not.toBeNull();
	});

	it('defaults the optional lists of a sparse payload', () => {
		const sparse = { ...modesRequest() } as Record<string, unknown>;
		delete sparse.disk;
		delete sparse.providers;
		delete sparse.estimate;
		const parsed = extractModesConsent(attempt({ consent_request: sparse }));
		expect(parsed?.disk).toEqual([]);
		expect(parsed?.providers).toEqual([]);
		expect(parsed?.estimate).toBeNull();
	});

	it('merges a re-plan for another target into the open request', () => {
		const current = modesRequest();
		const next = modesRequest({ disk: [{ id: 'd0', target: 'pod', label: 'Models folder', free_bytes: GB, reserve_bytes: 0 }] });
		next.target.backend_id = 'pod';
		const merged = mergeReplan(current, next);
		expect(merged.target.backend_id).toBe('pod');
		expect(merged.disk[0].free_bytes).toBe(GB);
		expect(merged.preset_name).toBe('LTX-2.5');
	});

	it('reads download progress and aggregates bytes', () => {
		const progress = extractDownloadProgress(
			attempt({
				files: [
					{ slot_id: 'a', display_name: 'A', filename: 'a', size_bytes: 200, downloaded_bytes: 200, status: 'completed' },
					{ slot_id: 'b', display_name: 'B', filename: 'b', size_bytes: 400, downloaded_bytes: 100, status: 'downloading' },
					{ slot_id: 'c', display_name: 'C', filename: 'c', size_bytes: 400, downloaded_bytes: 0, status: 'pending' }
				],
				files_done: 1,
				files_total: 3,
				speed_bytes_per_sec: null,
				eta_seconds: null
			})
		);
		expect(progress?.files_total).toBe(3);
		expect(aggregateDownloadBytes(progress!)).toEqual({ done: 300, total: 1000 });
		expect(progress!.files.map(downloadFileLabel)).toEqual(['Done', '25%', 'Waiting']);
		expect(extractDownloadProgress(attempt({ fetched: [] }))).toBeNull();
	});

	it('finds the approved plan on the latest attempt that has one', () => {
		const run = {
			steps: [
				{
					kind: 'modes.plan',
					attempts: [
						attempt(null),
						attempt({ plan: { preset_id: 'p1', modes: ['video', 'cinemagraph'], bytes: 5, files: [1, 2] } })
					]
				}
			]
		} as never;
		expect(extractApprovedPlan(run)).toEqual({ presetId: 'p1', modes: ['video', 'cinemagraph'], bytes: 5, files: 2 });
		expect(isModesRun(run)).toBe(true);
		expect(isModesRun({ steps: [{ kind: 'artifacts.plan' }] } as never)).toBe(false);
	});

	it('recognises derived recipe ids', () => {
		expect(isDerivedRecipeId('modes-ltx-2-5')).toBe(true);
		expect(isDerivedRecipeId('ltx25-starter')).toBe(false);
	});
});
