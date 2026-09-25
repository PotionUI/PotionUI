import { describe, it, expect } from 'vitest';
import { buildTocSections, pickActiveSectionId } from './runReportToc';

describe('buildTocSections', () => {
	it('always lists Overview and Outputs, in order, with nothing else for a bare report', () => {
		expect(
			buildTocSections({
				hasRouting: false,
				hasTimeline: false,
				hasArtifacts: false,
				hasPrompt: false,
				hasStatusLog: false,
				hasPluginOutput: false
			})
		).toEqual([
			{ id: 'overview', label: 'Overview' },
			{ id: 'outputs', label: 'Outputs' }
		]);
	});

	it('inserts every present section in reading order', () => {
		expect(
			buildTocSections({
				hasRouting: true,
				hasTimeline: true,
				hasArtifacts: true,
				hasPrompt: true,
				hasStatusLog: true,
				hasPluginOutput: true
			})
		).toEqual([
			{ id: 'overview', label: 'Overview' },
			{ id: 'routing', label: 'Routing' },
			{ id: 'timeline', label: 'Pipe timeline' },
			{ id: 'outputs', label: 'Outputs' },
			{ id: 'artifacts', label: 'Artifacts' },
			{ id: 'prompt', label: 'Prompt' },
			{ id: 'status-log', label: 'Status log' },
			{ id: 'plugin-output', label: 'Plugin output' }
		]);
	});
});

describe('pickActiveSectionId', () => {
	const order = ['overview', 'routing', 'outputs'];

	it('picks the topmost id currently in the band', () => {
		expect(pickActiveSectionId(order, new Set(['routing', 'outputs']), 'overview')).toBe('routing');
	});

	it('keeps the previous active id when nothing is in the band', () => {
		expect(pickActiveSectionId(order, new Set(), 'routing')).toBe('routing');
	});

	it('picks the only visible id', () => {
		expect(pickActiveSectionId(order, new Set(['outputs']), 'overview')).toBe('outputs');
	});
});
