import { describe, it, expect } from 'vitest';
import {
	isRunTerminal,
	shouldPollRun,
	canRetryRun,
	isConsentStatus,
	runBadgeVariant,
	runStatusLabel,
	stepBadgeVariant,
	stepStatusLabel,
	humanizeStepKey,
	groupStepAttempts,
	stepDuration,
	runProgressSummary,
	runNeedsConsent,
	decideRunDiscovery,
	manifestStepBadgeVariant,
	manifestStepStatusLabel,
	resolveStepGroups,
	runManifestProgressSummary,
	stepProgressLabel,
	stepProgressPercent,
	computeTransferStats,
	extractConsentRequest,
	extractGenerationHandoff,
	extractSmokeGeneration
} from './setupRunDisplay';
import type {
	SetupRun,
	SetupRunStatus,
	SetupRunStepView,
	SetupStepAttempt,
	SetupStepStatus
} from '$lib/services/api/setup';

const RUN_STATUSES: SetupRunStatus[] = [
	'pending',
	'running',
	'awaiting_consent',
	'paused',
	'completed',
	'failed',
	'cancelled'
];

const STEP_STATUSES: SetupStepStatus[] = [
	'running',
	'succeeded',
	'action_required',
	'awaiting_consent',
	'failed',
	'cancelled'
];

function attempt(overrides: Partial<SetupStepAttempt> = {}): SetupStepAttempt {
	return {
		step_key: 'download_model',
		attempt: 1,
		status: 'running',
		progress_current: null,
		progress_total: null,
		progress_unit: null,
		safe_output: null,
		error_code: null,
		safe_error_detail: null,
		safe_suggested_action: null,
		started_at: null,
		finished_at: null,
		...overrides
	};
}

function stepView(overrides: Partial<SetupRunStepView> = {}): SetupRunStepView {
	return {
		step_key: 'download_model',
		title: 'Download Model',
		kind: 'download_model',
		ordinal: 0,
		status: 'pending',
		attempts: [],
		...overrides
	};
}

function run(overrides: Partial<Pick<SetupRun, 'steps' | 'attempts'>> = {}): Pick<SetupRun, 'steps' | 'attempts'> {
	return {
		steps: [],
		attempts: [],
		...overrides
	};
}

describe('isRunTerminal / shouldPollRun', () => {
	it('completed/failed/cancelled are terminal, everything else is not', () => {
		for (const status of RUN_STATUSES) {
			const terminal = ['completed', 'failed', 'cancelled'].includes(status);
			expect(isRunTerminal(status)).toBe(terminal);
			expect(shouldPollRun(status)).toBe(!terminal);
		}
	});
});

describe('canRetryRun', () => {
	it('is only true for failed', () => {
		for (const status of RUN_STATUSES) {
			expect(canRetryRun(status)).toBe(status === 'failed');
		}
	});
});

describe('isConsentStatus / runNeedsConsent', () => {
	it('is true only for awaiting_consent, for both run and step statuses', () => {
		for (const status of RUN_STATUSES) {
			expect(isConsentStatus(status)).toBe(status === 'awaiting_consent');
		}
		for (const status of STEP_STATUSES) {
			expect(isConsentStatus(status)).toBe(status === 'awaiting_consent');
		}
		expect(runNeedsConsent({ status: 'awaiting_consent' })).toBe(true);
		expect(runNeedsConsent({ status: 'running' })).toBe(false);
	});
});

describe('runBadgeVariant / stepBadgeVariant', () => {
	it('gives every run status a variant', () => {
		for (const status of RUN_STATUSES) {
			expect(typeof runBadgeVariant(status)).toBe('string');
		}
		expect(runBadgeVariant('completed')).toBe('success');
		expect(runBadgeVariant('failed')).toBe('danger');
		expect(runBadgeVariant('awaiting_consent')).toBe('signal');
		expect(runBadgeVariant('cancelled')).toBe('neutral');
	});

	it('gives every step status a variant', () => {
		for (const status of STEP_STATUSES) {
			expect(typeof stepBadgeVariant(status)).toBe('string');
		}
		expect(stepBadgeVariant('succeeded')).toBe('success');
		expect(stepBadgeVariant('failed')).toBe('danger');
		expect(stepBadgeVariant('awaiting_consent')).toBe('signal');
		expect(stepBadgeVariant('action_required')).toBe('warning');
	});
});

describe('runStatusLabel / stepStatusLabel', () => {
	it('is jargon-free plain words for every status, no duplicates by accident', () => {
		const runLabels = new Set(RUN_STATUSES.map(runStatusLabel));
		expect(runLabels.size).toBe(RUN_STATUSES.length);
		const stepLabels = new Set(STEP_STATUSES.map(stepStatusLabel));
		expect(stepLabels.size).toBe(STEP_STATUSES.length);
		// No label leaks implementation jargon.
		for (const label of [...runLabels, ...stepLabels]) {
			expect(label.toLowerCase()).not.toMatch(/dto|executor|http|error_code/);
		}
	});
});

describe('humanizeStepKey', () => {
	it('turns snake_case into Title Case words', () => {
		expect(humanizeStepKey('download_model')).toBe('Download Model');
	});

	it('turns kebab-case into Title Case words', () => {
		expect(humanizeStepKey('test-backend')).toBe('Test Backend');
	});

	it('handles a single word', () => {
		expect(humanizeStepKey('smoke')).toBe('Smoke');
	});

	it('falls back to a generic label for an empty key', () => {
		expect(humanizeStepKey('')).toBe('Step');
	});

	it('never hardcodes a specific step name — any key humanizes generically', () => {
		expect(humanizeStepKey('acquire_totally_novel_asset')).toBe('Acquire Totally Novel Asset');
	});
});

describe('groupStepAttempts', () => {
	it('keeps the highest attempt number as latest for a retried step', () => {
		const groups = groupStepAttempts([
			attempt({ step_key: 'download_model', attempt: 1, status: 'failed' }),
			attempt({ step_key: 'download_model', attempt: 2, status: 'succeeded' })
		]);
		expect(groups).toHaveLength(1);
		expect(groups[0].latest.status).toBe('succeeded');
		expect(groups[0].attempts.map((a) => a.attempt)).toEqual([1, 2]);
	});

	it('orders steps by earliest started_at, not step_key alphabetically', () => {
		const groups = groupStepAttempts([
			attempt({ step_key: 'zzz_last_alphabetically', started_at: '2026-01-01T00:00:00Z' }),
			attempt({ step_key: 'aaa_first_alphabetically', started_at: '2026-01-01T00:05:00Z' })
		]);
		expect(groups.map((g) => g.stepKey)).toEqual([
			'zzz_last_alphabetically',
			'aaa_first_alphabetically'
		]);
	});

	it('sorts steps with no timestamp after ones that have started', () => {
		const groups = groupStepAttempts([
			attempt({ step_key: 'not_started_yet', started_at: null }),
			attempt({ step_key: 'already_running', started_at: '2026-01-01T00:00:00Z' })
		]);
		expect(groups.map((g) => g.stepKey)).toEqual(['already_running', 'not_started_yet']);
	});

	it('gives each group a humanized title', () => {
		const groups = groupStepAttempts([attempt({ step_key: 'download_model' })]);
		expect(groups[0].title).toBe('Download Model');
	});

	it('returns an empty list for no attempts', () => {
		expect(groupStepAttempts([])).toEqual([]);
	});
});

describe('stepDuration', () => {
	it('is null when the step has not started', () => {
		expect(stepDuration(attempt({ started_at: null }))).toBeNull();
	});

	it('formats elapsed time against now() while still running', () => {
		const result = stepDuration(
			attempt({ started_at: '2026-01-01T00:00:00.000Z', finished_at: null }),
			() => Date.parse('2026-01-01T00:00:05.000Z')
		);
		expect(result).toBe('5s');
	});

	it('formats elapsed time between start and finish once done', () => {
		const result = stepDuration(
			attempt({
				started_at: '2026-01-01T00:00:00.000Z',
				finished_at: '2026-01-01T00:01:02.000Z'
			})
		);
		expect(result).toBe('1m 2s');
	});

	it('is null for a malformed timestamp', () => {
		expect(stepDuration(attempt({ started_at: 'not-a-date' }))).toBeNull();
	});
});

describe('runProgressSummary', () => {
	it('says nothing has started when there are no steps', () => {
		expect(runProgressSummary([])).toBe('No steps have started yet.');
	});

	it('never claims a total step count, only observed counts', () => {
		const groups = groupStepAttempts([
			attempt({ step_key: 'a', status: 'succeeded' }),
			attempt({ step_key: 'b', status: 'running' })
		]);
		const summary = runProgressSummary(groups);
		expect(summary).toBe('1 done, 1 in progress');
		expect(summary).not.toMatch(/of \d+/);
	});

	it('surfaces consent-waiting and failed counts distinctly', () => {
		const groups = groupStepAttempts([
			attempt({ step_key: 'a', status: 'awaiting_consent' }),
			attempt({ step_key: 'b', status: 'failed' })
		]);
		expect(runProgressSummary(groups)).toBe('1 waiting on you, 1 couldn\'t finish');
	});

	it('falls back to a queued count when nothing has a countable status yet', () => {
		// action_required is the only status left uncounted by name in the
		// "done/in progress/waiting/failed" phrasing above it — still surfaced.
		const groups = groupStepAttempts([attempt({ step_key: 'a', status: 'action_required' })]);
		expect(runProgressSummary(groups)).toBe('1 need attention');
	});
});

describe('decideRunDiscovery', () => {
	it('always shows the active run when the active check found one, regardless of what was stored', () => {
		const storedOptions: (Pick<SetupRun, 'status'> | null)[] = [
			null,
			{ status: 'failed' },
			{ status: 'completed' },
			{ status: 'cancelled' },
			{ status: 'running' }
		];
		for (const stored of storedOptions) {
			expect(decideRunDiscovery('found', stored)).toEqual({ show: 'active' });
		}
	});

	it('keeps showing a stored run that is specifically failed when nothing is active', () => {
		expect(decideRunDiscovery('not_found', { status: 'failed' })).toEqual({ show: 'stored-failed' });
	});

	it('gives up on a stored run that finished terminally in a non-failed way', () => {
		expect(decideRunDiscovery('not_found', { status: 'completed' })).toEqual({ show: 'none' });
		expect(decideRunDiscovery('not_found', { status: 'cancelled' })).toEqual({ show: 'none' });
	});

	it('gives up on a stored run in a non-terminal status the active check somehow missed', () => {
		// Shouldn't happen in practice (a non-terminal run should always be
		// "active"), but a stale race is possible — only "failed" gets the
		// special-cased second look, everything else defers to the
		// authoritative active-check result.
		expect(decideRunDiscovery('not_found', { status: 'running' })).toEqual({ show: 'none' });
		expect(decideRunDiscovery('not_found', { status: 'awaiting_consent' })).toEqual({ show: 'none' });
	});

	it('gives up when there was no stored run at all', () => {
		expect(decideRunDiscovery('not_found', null)).toEqual({ show: 'none' });
	});
});

describe('manifestStepBadgeVariant / manifestStepStatusLabel', () => {
	it('gives pending a neutral, jargon-free treatment distinct from real step statuses', () => {
		expect(manifestStepBadgeVariant('pending')).toBe('neutral');
		expect(manifestStepStatusLabel('pending')).toBe('Not started yet');
	});

	it('defers to the regular step helpers for real statuses', () => {
		for (const status of STEP_STATUSES) {
			expect(manifestStepBadgeVariant(status)).toBe(stepBadgeVariant(status));
			expect(manifestStepStatusLabel(status)).toBe(stepStatusLabel(status));
		}
	});
});

describe('resolveStepGroups', () => {
	it('uses the manifest, in ordinal order, when steps are present', () => {
		const resolved = resolveStepGroups(
			run({
				steps: [
					stepView({ step_key: 'b', ordinal: 1, status: 'pending' }),
					stepView({ step_key: 'a', ordinal: 0, status: 'succeeded', attempts: [attempt({ step_key: 'a', status: 'succeeded' })] })
				],
				attempts: [attempt({ step_key: 'a', status: 'succeeded' })]
			})
		);
		expect(resolved.hasManifest).toBe(true);
		expect(resolved.groups.map((g) => g.stepKey)).toEqual(['a', 'b']);
	});

	it('gives a pending step a null latest attempt', () => {
		const resolved = resolveStepGroups(run({ steps: [stepView({ status: 'pending', attempts: [] })] }));
		expect(resolved.groups[0].status).toBe('pending');
		expect(resolved.groups[0].latest).toBeNull();
	});

	it('exposes the newest attempt as latest for a step with retries', () => {
		const resolved = resolveStepGroups(
			run({
				steps: [
					stepView({
						status: 'succeeded',
						attempts: [
							attempt({ attempt: 1, status: 'failed' }),
							attempt({ attempt: 2, status: 'succeeded' })
						]
					})
				]
			})
		);
		expect(resolved.groups[0].latest?.attempt).toBe(2);
	});

	it('falls back to grouping flat attempts when the manifest is empty', () => {
		const resolved = resolveStepGroups(
			run({ steps: [], attempts: [attempt({ step_key: 'download_model', status: 'running' })] })
		);
		expect(resolved.hasManifest).toBe(false);
		expect(resolved.groups).toHaveLength(1);
		expect(resolved.groups[0].stepKey).toBe('download_model');
		expect(resolved.groups[0].latest?.status).toBe('running');
	});

	it('returns no groups for a run with neither a manifest nor attempts', () => {
		expect(resolveStepGroups(run()).groups).toEqual([]);
	});
});

describe('runManifestProgressSummary', () => {
	it('states the real total once the manifest is known', () => {
		const resolved = resolveStepGroups(
			run({
				steps: [
					stepView({ step_key: 'a', ordinal: 0, status: 'succeeded', attempts: [attempt({ status: 'succeeded' })] }),
					stepView({ step_key: 'b', ordinal: 1, status: 'pending' }),
					stepView({ step_key: 'c', ordinal: 2, status: 'pending' })
				]
			})
		);
		expect(runManifestProgressSummary(resolved)).toBe('1 of 3 steps done');
	});

	it('appends notable states alongside the total', () => {
		const resolved = resolveStepGroups(
			run({
				steps: [
					stepView({ step_key: 'a', ordinal: 0, status: 'succeeded', attempts: [attempt({ status: 'succeeded' })] }),
					stepView({ step_key: 'b', ordinal: 1, status: 'awaiting_consent', attempts: [attempt({ status: 'awaiting_consent' })] })
				]
			})
		);
		expect(runManifestProgressSummary(resolved)).toBe('1 of 2 steps done, 1 waiting on you');
	});

	it('falls back to observed-only wording without a manifest', () => {
		const resolved = resolveStepGroups(
			run({ attempts: [attempt({ step_key: 'a', status: 'succeeded' }), attempt({ step_key: 'b', status: 'running' })] })
		);
		expect(runManifestProgressSummary(resolved)).toBe('1 done, 1 in progress');
		expect(runManifestProgressSummary(resolved)).not.toMatch(/of \d+/);
	});

	it('says nothing has started for an empty run', () => {
		expect(runManifestProgressSummary(resolveStepGroups(run()))).toBe('No steps have started yet.');
	});
});

describe('stepProgressLabel', () => {
	it('is null with no progress fields', () => {
		expect(stepProgressLabel(attempt())).toBeNull();
	});

	it('is null for a missing attempt', () => {
		expect(stepProgressLabel(null)).toBeNull();
		expect(stepProgressLabel(undefined)).toBeNull();
	});

	it('formats a bytes unit with formatBytes', () => {
		const label = stepProgressLabel(
			attempt({ progress_current: 512000, progress_total: 6500000000, progress_unit: 'bytes' })
		);
		expect(label).toMatch(/^[\d.]+ (KB|MB) of [\d.]+ GB$/);
	});

	it('formats a non-bytes unit as a plain count with suffix', () => {
		const label = stepProgressLabel(
			attempt({ progress_current: 3, progress_total: 12, progress_unit: 'files' })
		);
		expect(label).toBe('3 of 12 files');
	});

	it('handles a current-only progress (no total yet)', () => {
		const label = stepProgressLabel(attempt({ progress_current: 3, progress_total: null, progress_unit: 'files' }));
		expect(label).toBe('3 files');
	});
});

describe('stepProgressPercent', () => {
	it('is null with no progress fields', () => {
		expect(stepProgressPercent(attempt())).toBeNull();
	});

	it('is null with a zero or missing total', () => {
		expect(stepProgressPercent(attempt({ progress_current: 5, progress_total: 0 }))).toBeNull();
		expect(stepProgressPercent(attempt({ progress_current: 5, progress_total: null }))).toBeNull();
	});

	it('rounds current/total to a whole percent', () => {
		expect(stepProgressPercent(attempt({ progress_current: 3_200_000_000, progress_total: 6_900_000_000 }))).toBe(
			46
		);
	});

	it('clamps to 100 even if current somehow exceeds total', () => {
		expect(stepProgressPercent(attempt({ progress_current: 11, progress_total: 10 }))).toBe(100);
	});
});

describe('computeTransferStats', () => {
	it('is all-null with no prior sample', () => {
		expect(computeTransferStats(null, { bytes: 1000, at: 1000 }, 10_000)).toEqual({
			bytesPerSecond: null,
			etaMs: null
		});
	});

	it('derives bytes/sec and a remaining-time ETA from two samples', () => {
		const stats = computeTransferStats({ bytes: 1_000_000, at: 0 }, { bytes: 3_000_000, at: 1000 }, 11_000_000);
		expect(stats.bytesPerSecond).toBe(2_000_000);
		expect(stats.etaMs).toBe(4000);
	});

	it('is null when the clock did not move forward', () => {
		expect(computeTransferStats({ bytes: 1000, at: 1000 }, { bytes: 2000, at: 1000 }, null)).toEqual({
			bytesPerSecond: null,
			etaMs: null
		});
	});

	it('is null when bytes went backwards (a retried/reset step)', () => {
		expect(computeTransferStats({ bytes: 5000, at: 0 }, { bytes: 1000, at: 1000 }, null)).toEqual({
			bytesPerSecond: null,
			etaMs: null
		});
	});

	it('reports a rate with a null ETA when the total is unknown', () => {
		const stats = computeTransferStats({ bytes: 1000, at: 0 }, { bytes: 2000, at: 1000 }, null);
		expect(stats.bytesPerSecond).toBe(1000);
		expect(stats.etaMs).toBeNull();
	});

	it('reports a zero ETA once the total is already reached', () => {
		const stats = computeTransferStats({ bytes: 9000, at: 0 }, { bytes: 10_000, at: 1000 }, 10_000);
		expect(stats.etaMs).toBe(0);
	});
});

describe('extractConsentRequest', () => {
	it('parses a well-formed consent_request out of safe_output', () => {
		const result = extractConsentRequest(
			attempt({
				safe_output: {
					consent_request: {
						artifacts: [{ id: 'a1', display_name: 'SDXL checkpoint', size_bytes: 123, kind: 'checkpoint' }],
						total_bytes: 123
					}
				}
			})
		);
		expect(result).toEqual({
			artifacts: [{ id: 'a1', display_name: 'SDXL checkpoint', size_bytes: 123, kind: 'checkpoint' }],
			total_bytes: 123
		});
	});

	it('is null when there is no consent_request', () => {
		expect(extractConsentRequest(attempt({ safe_output: {} }))).toBeNull();
		expect(extractConsentRequest(attempt({ safe_output: null }))).toBeNull();
		expect(extractConsentRequest(null)).toBeNull();
		expect(extractConsentRequest(undefined)).toBeNull();
	});

	it('is null when artifacts is missing or malformed', () => {
		expect(
			extractConsentRequest(attempt({ safe_output: { consent_request: { total_bytes: 1 } } }))
		).toBeNull();
		expect(
			extractConsentRequest(attempt({ safe_output: { consent_request: { artifacts: 'nope' } } }))
		).toBeNull();
	});

	it('drops malformed artifact entries but keeps well-formed ones', () => {
		const result = extractConsentRequest(
			attempt({
				safe_output: {
					consent_request: {
						artifacts: [{ id: 'ok', display_name: 'Fine' }, { no_id: true }, null],
						total_bytes: null
					}
				}
			})
		);
		expect(result?.artifacts).toEqual([{ id: 'ok', display_name: 'Fine', size_bytes: null, kind: '' }]);
	});

	it('falls back to id as display_name when display_name is missing', () => {
		const result = extractConsentRequest(
			attempt({ safe_output: { consent_request: { artifacts: [{ id: 'a1' }], total_bytes: null } } })
		);
		expect(result?.artifacts[0].display_name).toBe('a1');
	});

	it('parses an unconfigured-provider prompt when the plan step includes one', () => {
		const result = extractConsentRequest(
			attempt({
				safe_output: {
					consent_request: {
						artifacts: [{ id: 'a1', display_name: 'Checkpoint' }],
						total_bytes: null,
						providers: [
							{ id: 'civitai', name: 'CivitAI', website: 'https://civitai.com', field_name: 'api_key', configured: false }
						]
					}
				}
			})
		);
		expect(result?.providers).toEqual([
			{ id: 'civitai', name: 'CivitAI', website: 'https://civitai.com', field_name: 'api_key', configured: false }
		]);
	});

	it('omits providers entirely when the plan step did not send any', () => {
		const result = extractConsentRequest(
			attempt({
				safe_output: { consent_request: { artifacts: [{ id: 'a1' }], total_bytes: null } }
			})
		);
		expect(result?.providers).toBeUndefined();
	});
});

describe('extractGenerationHandoff', () => {
	it('reads preset_id and mode from the succeeded preset.ensure/pipeline.render steps', () => {
		const resolved = run({
			steps: [
				stepView({
					step_key: 'preset.ensure',
					kind: 'preset.ensure',
					status: 'succeeded',
					attempts: [attempt({ step_key: 'preset.ensure', status: 'succeeded', safe_output: { preset_id: 'p1', assigned_to: 'u1' } })]
				}),
				stepView({
					step_key: 'pipeline.render',
					kind: 'pipeline.render',
					status: 'succeeded',
					attempts: [attempt({ step_key: 'pipeline.render', status: 'succeeded', safe_output: { preset_id: 'p1', mode: 'img2img', pipe_count: 4 } })]
				})
			]
		});
		expect(extractGenerationHandoff(resolved)).toEqual({ presetId: 'p1', mode: 'img2img' });
	});

	it('defaults mode to txt2img when the pipeline.render step is missing', () => {
		const resolved = run({
			steps: [
				stepView({
					step_key: 'preset.ensure',
					kind: 'preset.ensure',
					status: 'succeeded',
					attempts: [attempt({ step_key: 'preset.ensure', status: 'succeeded', safe_output: { preset_id: 'p1' } })]
				})
			]
		});
		expect(extractGenerationHandoff(resolved)).toEqual({ presetId: 'p1', mode: 'txt2img' });
	});

	it('is null when the preset step has not succeeded', () => {
		const resolved = run({
			steps: [
				stepView({
					step_key: 'preset.ensure',
					kind: 'preset.ensure',
					status: 'failed',
					attempts: [attempt({ step_key: 'preset.ensure', status: 'failed' })]
				})
			]
		});
		expect(extractGenerationHandoff(resolved)).toBeNull();
	});

	it('falls back to flat attempts by step_key when the manifest is empty', () => {
		const resolved = run({
			steps: [],
			attempts: [attempt({ step_key: 'preset.ensure', status: 'succeeded', safe_output: { preset_id: 'p1' } })]
		});
		expect(extractGenerationHandoff(resolved)).toEqual({ presetId: 'p1', mode: 'txt2img' });
	});
});

describe('extractSmokeGeneration', () => {
	it('reads a generation_id and filename from the succeeded smoke step', () => {
		const resolved = run({
			steps: [
				stepView({
					step_key: 'generation.smoke',
					kind: 'generation.smoke',
					status: 'succeeded',
					attempts: [
						attempt({
							step_key: 'generation.smoke',
							status: 'succeeded',
							safe_output: { generation_id: 'gen-1', filename: 'output-01.png' }
						})
					]
				})
			]
		});
		expect(extractSmokeGeneration(resolved)).toEqual({ generationId: 'gen-1', filename: 'output-01.png' });
	});

	it('derives the filename from a file_path when filename is absent', () => {
		const resolved = run({
			steps: [
				stepView({
					step_key: 'generation.smoke',
					kind: 'generation.smoke',
					status: 'succeeded',
					attempts: [
						attempt({
							step_key: 'generation.smoke',
							status: 'succeeded',
							safe_output: { generation_id: 'gen-1', file_path: '/data/generations/gen-1/output-01.png' }
						})
					]
				})
			]
		});
		expect(extractSmokeGeneration(resolved)?.filename).toBe('output-01.png');
	});

	it('is null without a generation_id, even if the step succeeded', () => {
		const resolved = run({
			steps: [
				stepView({
					step_key: 'generation.smoke',
					kind: 'generation.smoke',
					status: 'succeeded',
					attempts: [attempt({ step_key: 'generation.smoke', status: 'succeeded', safe_output: {} })]
				})
			]
		});
		expect(extractSmokeGeneration(resolved)).toBeNull();
	});

	it('is null when the smoke step has not succeeded yet', () => {
		const resolved = run({
			steps: [stepView({ step_key: 'generation.smoke', kind: 'generation.smoke', status: 'running' })]
		});
		expect(extractSmokeGeneration(resolved)).toBeNull();
	});

	it('is null when there is no smoke step at all', () => {
		expect(extractSmokeGeneration(run())).toBeNull();
	});
});
