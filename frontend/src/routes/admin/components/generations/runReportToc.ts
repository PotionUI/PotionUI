export interface TocSection {
	id: string;
	label: string;
}

export function buildTocSections(flags: {
	hasRouting: boolean;
	hasTimeline: boolean;
	hasArtifacts: boolean;
	hasPrompt: boolean;
	hasStatusLog: boolean;
	hasPluginOutput: boolean;
}): TocSection[] {
	const sections: TocSection[] = [{ id: 'overview', label: 'Overview' }];
	if (flags.hasRouting) sections.push({ id: 'routing', label: 'Routing' });
	if (flags.hasTimeline) sections.push({ id: 'timeline', label: 'Pipe timeline' });
	sections.push({ id: 'outputs', label: 'Outputs' });
	if (flags.hasArtifacts) sections.push({ id: 'artifacts', label: 'Artifacts' });
	if (flags.hasPrompt) sections.push({ id: 'prompt', label: 'Prompt' });
	if (flags.hasStatusLog) sections.push({ id: 'status-log', label: 'Status log' });
	if (flags.hasPluginOutput) sections.push({ id: 'plugin-output', label: 'Plugin output' });
	return sections;
}

export function pickActiveSectionId(
	orderedIds: readonly string[],
	visibleIds: ReadonlySet<string>,
	previous: string
): string {
	for (const id of orderedIds) {
		if (visibleIds.has(id)) return id;
	}
	return previous;
}
