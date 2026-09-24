import { collectResourceProblems, type PromptResourceSpec } from './promptResources';

export interface ResourceIssueSegment {
	enabled?: boolean;
	content: string;
}

export function segmentResourceIssues(
	segments: ResourceIssueSegment[] | undefined,
	specs: PromptResourceSpec[],
	formValues: Record<string, unknown>
): string[] {
	if (!specs.length || !segments?.length) return [];
	const texts = segments.filter((segment) => segment.enabled !== false).map((segment) => segment.content);
	return collectResourceProblems(texts, specs, formValues).map((problem) => problem.message);
}

export function firstResourceIssue(
	segmentGroups: (ResourceIssueSegment[] | undefined)[],
	specs: PromptResourceSpec[],
	formValues: Record<string, unknown>
): string | undefined {
	for (const segments of segmentGroups) {
		const issues = segmentResourceIssues(segments, specs, formValues);
		if (issues.length) return issues[0];
	}
	return undefined;
}
