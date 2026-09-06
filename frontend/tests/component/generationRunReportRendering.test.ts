// @vitest-environment jsdom
//
// Mounts the real GenerationRunReport - the composed admin detail page -
// against a fixture generation + RunReport, proving the wiring between
// `runReport.ts`'s pure grouping/timeline math and the markup: the pipe
// timeline gantt, a pipe's grouped status entries (behind the collapsed
// status log), its resolved seed artifact, and a plugin_outputs entry (which
// has no persisted `asset`, so it must fall back to a generic block rather
// than trying to mount a live plugin renderer).
import { describe, it, expect, afterEach } from 'vitest';
import type { AdminGenerationListItem, RunReport } from '$lib/services/admin-api';

const { default: GenerationRunReport } = await import(
	'../../src/routes/admin/components/GenerationRunReport.svelte'
);
const { createClassComponent } = await import('svelte/legacy');

function mount(generation: AdminGenerationListItem, report: RunReport | null) {
	const target = document.createElement('div');
	document.body.appendChild(target);
	const component = createClassComponent({
		component: GenerationRunReport as never,
		target,
		props: { generation, report, username: 'alice' }
	});
	return {
		target,
		text: () => target.textContent ?? '',
		destroy: () => {
			component.$destroy();
			target.remove();
		}
	};
}

async function settle() {
	for (let i = 0; i < 8; i++) await new Promise((resolve) => setTimeout(resolve, 0));
}

let mounted: ReturnType<typeof mount> | undefined;

afterEach(() => {
	mounted?.destroy();
	mounted = undefined;
});

function baseGeneration(overrides: Partial<AdminGenerationListItem> = {}): AdminGenerationListItem {
	return {
		id: 'gen-1',
		form_data: {},
		status: 'completed',
		progress: 1,
		created_at: '2026-08-14T00:00:00Z',
		completed_at: '2026-08-14T00:00:05Z',
		updated_at: '2026-08-14T00:00:05Z',
		files: [],
		rating: 0,
		is_favorite: false,
		user_id: 'user-1',
		has_run_report: true,
		...overrides
	};
}

function baseReport(overrides: Partial<RunReport> = {}): RunReport {
	return {
		schema_version: 1,
		status_history: [],
		pipe_timers: {},
		artifacts: [],
		plugin_outputs: {},
		prompt_template: null,
		artifacts_omitted: 0,
		plugin_outputs_omitted: 0,
		artifacts_bytes: 0,
		stored_bytes: 0,
		...overrides
	};
}

describe('GenerationRunReport', () => {
	it('renders the pipe timeline, resolves a seed artifact, and expands the status log', async () => {
		mounted = mount(
			baseGeneration({ created_at: '2026-08-14T00:00:00Z', completed_at: '2026-08-14T00:00:05Z' }),
			baseReport({
				status_history: [
					{ at: '2026-08-14T00:00:00Z', pipe_id: 7, step: '<<PIPE:generator:bolt>> Sampling', message: null, progress: 0.1 },
					{ at: '2026-08-14T00:00:01Z', pipe_id: 7, step: '<<PIPE:generator:bolt>> Sampling', message: null, progress: 0.9 }
				],
				pipe_timers: { '7': { started_at: '2026-08-14T00:00:00Z', ended_at: '2026-08-14T00:00:05Z' } },
				artifacts: [{ at: '2026-08-14T00:00:05Z', pipe_id: 7, artifact_type: 'seed', artifact_data: { seed: 4242 } }]
			})
		);
		await settle();

		let text = mounted.text();
		expect(text).toContain('generator'); // pipe label on the timeline row
		expect(text).toContain('5.0s'); // pipe_timers wall-clock gap, labeled on the bar
		expect(text).toContain('4242'); // resolved SeedArtifact - artifacts grid is always visible now

		// The status log itself starts collapsed - its grouped progress text
		// only shows once expanded.
		expect(text).not.toContain('10%');
		const statusLogToggle = Array.from(mounted.target.querySelectorAll('button')).find((b) =>
			b.textContent?.includes('Status log')
		);
		statusLogToggle?.click();
		await settle();

		text = mounted.text();
		expect(text).toContain('10%');
		expect(text).toContain('90%');
	});

	it('falls back to a generic plugin_outputs block since the persisted shape carries no asset', async () => {
		mounted = mount(
			baseGeneration(),
			baseReport({
				plugin_outputs: {
					'my_plugin.summary': { plugin_id: 'my-plugin', message: { note: 'hello' }, at: '2026-08-14T00:00:00Z' }
				}
			})
		);
		await settle();

		const text = mounted.text();
		expect(text).toContain('my-plugin');
		expect(text).toContain('my_plugin.summary');
		expect(text).toContain('hello');
	});

	it('renders a saved comparison artifact from its stored reference, not a payload', async () => {
		mounted = mount(
			baseGeneration(),
			baseReport({
				schema_version: 2,
				pipe_timers: { '7': { started_at: '2026-08-14T00:00:00Z', ended_at: '2026-08-14T00:00:05Z' } },
				status_history: [
					{ at: '2026-08-14T00:00:00Z', pipe_id: 7, step: 'Sampling', message: null, progress: 0.1 }
				],
				artifacts: [
					{
						at: '2026-08-14T00:00:05Z',
						pipe_id: 7,
						artifact_type: 'compare_images',
						artifact_data: {
							compare_label: 'Before',
							to_label: 'After',
							to_image: {
								$media: 'run_report_artifact',
								name: 'runreport_01H.jpg',
								url: '/api/generations/gen-1/run-report/artifacts/runreport_01H.jpg',
								path: 'generations/2026-08-14/gen-1/runreport_01H.jpg',
								bytes: 4096,
								mime: 'image/jpeg'
							}
						}
					}
				]
			})
		);
		await settle();

		const image = mounted.target.querySelector('img[alt="After"]') as HTMLImageElement | null;
		expect(image?.getAttribute('src')).toContain(
			'/api/generations/gen-1/run-report/artifacts/runreport_01H.jpg'
		);
	});

	it('says a payload was not recorded instead of rendering an empty artifact', async () => {
		mounted = mount(
			baseGeneration(),
			baseReport({
				schema_version: 2,
				pipe_timers: { '7': { started_at: '2026-08-14T00:00:00Z', ended_at: '2026-08-14T00:00:05Z' } },
				status_history: [
					{ at: '2026-08-14T00:00:00Z', pipe_id: 7, step: 'Sampling', message: null, progress: 0.1 }
				],
				artifacts: [
					{
						at: '2026-08-14T00:00:05Z',
						pipe_id: 7,
						artifact_type: 'workflow',
						artifact_data: null,
						omitted: { reason: 'item_bytes', bytes: 132000 }
					}
				],
				artifacts_omitted: 1
			})
		);
		await settle();

		const text = mounted.text();
		expect(text).toContain('Payload not recorded');
		expect(text).toContain('132000');
		expect(text).toContain('item bytes');
	});

	it('summarises what the recorder refused to keep, even with nothing left in those sections', async () => {
		mounted = mount(
			baseGeneration(),
			baseReport({
				schema_version: 2,
				artifacts: [],
				plugin_outputs: {},
				status_history: [],
				artifacts_dropped: 400,
				plugin_output_types_dropped: 2950,
				status_history_dropped: 2830,
				pipe_timers_dropped: 3056
			} as never)
		);
		await settle();

		const text = mounted.text();
		expect(text).toContain('Not recorded');
		expect(text).toContain('400 artifacts');
		expect(text).toContain('2950 plugin output types');
		expect(text).toContain('2830 status entries');
		expect(text).toContain('3056 pipe timers');
	});

	it('shows no omission summary for a report written before the counters existed', async () => {
		mounted = mount(baseGeneration(), baseReport());
		await settle();

		expect(mounted.text()).not.toContain('Not recorded');
	});

	it('reports shortened text alongside the omission summary', async () => {
		mounted = mount(
			baseGeneration({ status: 'failed' }),
			baseReport({
				schema_version: 2,
				status_history: [
					{
						at: '2026-08-14T00:00:05Z',
						pipe_id: null,
						step: 'failed',
						message: 'X'.repeat(2048),
						progress: null,
						truncated: ['message']
					}
				],
				texts_truncated: 1
			} as never)
		);
		await settle();

		const text = mounted.text();
		expect(text).toContain('Text shortened on');
		expect(text).toContain('1');
		expect(text).toContain('terminal status message');
	});

	it('shows an honest empty message when the report has no recorded entries', async () => {
		mounted = mount(baseGeneration(), baseReport());
		await settle();

		expect(mounted.text()).toContain('no recorded status, artifact, or plugin output entries');
	});

	it('serves output thumbnails through the media route with a size query, never the stored path', async () => {
		mounted = mount(
			baseGeneration({
				files: [
					{
						id: 'f1',
						generation_id: 'gen-1',
						file_path: 'generations/2026-08-14/gen-1/out.png',
						file_type: 'image',
						is_final: true,
						thumbnail_medium: 'generations/2026-08-14/gen-1/thumbs/out_medium.jpg'
					},
					{
						id: 'f2',
						generation_id: 'gen-1',
						file_path: 'generations/2026-08-14/gen-1/clip.mp4',
						file_type: 'video',
						is_final: false
					}
				] as never
			}),
			null
		);
		await settle();

		const images = Array.from(mounted.target.querySelectorAll('img')).map((img) => img.getAttribute('src'));
		expect(images).toEqual(['/api/media/generations/gen-1/out.png?size=medium']);
		const links = Array.from(mounted.target.querySelectorAll('a[href^="/api/media/generations/"]')).map((a) =>
			a.getAttribute('href')
		);
		expect(links).toEqual(['/api/media/generations/gen-1/out.png', '/api/media/generations/gen-1/clip.mp4']);
		expect(mounted.text()).toContain('2 files');
	});

	it('shows the predates-persistence empty state when there is no run report at all', async () => {
		mounted = mount(baseGeneration({ has_run_report: false }), null);
		await settle();

		expect(mounted.text()).toContain('No report recorded');
	});
});
