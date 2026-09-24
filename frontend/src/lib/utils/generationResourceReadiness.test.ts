import { describe, expect, it } from 'vitest';
import { firstResourceIssue, segmentResourceIssues } from './generationResourceReadiness';
import type { PromptResourceSpec } from './promptResources';

const specs: PromptResourceSpec[] = [{ field: 'references', kind: 'image', label: 'Pictures', token: '<Picture @>' }];
const formValues = { references: [{ relative_path: 'a.png' }] };

describe('segmentResourceIssues', () => {
	it('returns no issues when there are no prompt resource specs mapped', () => {
		expect(segmentResourceIssues([{ content: '@[references:gone.png]' }], [], formValues)).toEqual([]);
	});

	it('returns no issues for a resolvable marker', () => {
		expect(segmentResourceIssues([{ content: 'a cat @[references:a.png]' }], specs, formValues)).toEqual([]);
	});

	it('flags a dangling marker in an enabled segment', () => {
		const issues = segmentResourceIssues([{ content: '@[references:gone.png]' }], specs, formValues);
		expect(issues).toHaveLength(1);
		expect(issues[0]).toContain('removed from this field');
	});

	it('ignores a disabled segment', () => {
		expect(
			segmentResourceIssues([{ content: '@[references:gone.png]', enabled: false }], specs, formValues)
		).toEqual([]);
	});
});

describe('firstResourceIssue', () => {
	it('returns undefined when every segment group is clean', () => {
		expect(
			firstResourceIssue(
				[[{ content: 'a cat @[references:a.png]' }], [{ content: 'no markers here' }]],
				specs,
				formValues
			)
		).toBeUndefined();
	});

	it('returns the first issue across multiple segment groups', () => {
		const issue = firstResourceIssue(
			[[{ content: 'fine' }], [{ content: '@[references:gone.png]' }], [{ content: '@[unmapped:x]' }]],
			specs,
			formValues
		);
		expect(issue).toContain('removed from this field');
	});

	it('handles undefined segment groups', () => {
		expect(firstResourceIssue([undefined, [{ content: 'fine' }]], specs, formValues)).toBeUndefined();
	});
});
