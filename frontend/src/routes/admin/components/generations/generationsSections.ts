import type { LibrarySectionMeta } from '$lib/components/library/librarySection';
import type { GenerationStatusFilter } from '../generationsFilters';

export type GenerationSection = 'all' | 'completed' | 'running' | 'failed' | 'cancelled';

export const GENERATION_LIBRARY_SECTIONS: readonly LibrarySectionMeta<GenerationSection>[] = [
	{ id: 'all', label: 'All generations', icon: 'generation' },
	{ id: 'completed', label: 'Completed', icon: 'check' },
	{ id: 'running', label: 'Running', icon: 'loading' },
	{ id: 'failed', label: 'Failed', icon: 'warning' },
	{ id: 'cancelled', label: 'Cancelled', icon: 'close' }
];

const SECTION_STATUSES = new Set<string>(['completed', 'running', 'failed', 'cancelled']);

export function sectionFromStatus(status: GenerationStatusFilter): GenerationSection {
	return SECTION_STATUSES.has(status) ? (status as GenerationSection) : 'all';
}

export function statusFromSection(section: GenerationSection): GenerationStatusFilter {
	return section === 'all' ? '' : section;
}
