<script lang="ts">
	import type { Snippet } from 'svelte';
	import { Button } from '$lib/components/ui';
	import LibraryShell from './LibraryShell.svelte';
	import LibraryFilterBar from './LibraryFilterBar.svelte';
	import type { LibrarySectionMeta, SortOption } from './librarySection';

	let {
		title,
		persistKey,
		sections,
		section,
		onSelectSection,
		sectionCounts = {},
		count = null,
		titleLabel = undefined,
		heightClass = 'h-full',
		q = '',
		onQueryChange = () => {},
		searchPlaceholder = 'Search',
		searchHint = null,
		sortBy = '',
		sortOptions = [],
		onSortChange = () => {},
		primaryLabel = '',
		primaryIcon = undefined,
		primaryLoading = false,
		primaryDisabled = false,
		onPrimary = () => {},
		children
	}: {
		title: string;
		persistKey: string;
		sections: readonly LibrarySectionMeta<string>[];
		section: string;
		onSelectSection: (id: string) => void;
		sectionCounts?: Record<string, number>;
		count?: number | null;
		titleLabel?: string;
		heightClass?: string;
		q?: string;
		onQueryChange?: (value: string) => void;
		searchPlaceholder?: string;
		searchHint?: string | null;
		sortBy?: string;
		sortOptions?: readonly SortOption[];
		onSortChange?: (value: string) => void;
		primaryLabel?: string;
		primaryIcon?: string;
		primaryLoading?: boolean;
		primaryDisabled?: boolean;
		onPrimary?: () => void;
		children: Snippet;
	} = $props();
</script>

<LibraryShell {title} {persistKey} {sections} {section} {onSelectSection} {sectionCounts} {count} {titleLabel} {heightClass} {children}>
	{#snippet toolbar()}
		<LibraryFilterBar {q} {onQueryChange} {searchPlaceholder} {searchHint} {sortBy} {sortOptions} {onSortChange} />
	{/snippet}
	{#snippet primary()}
		{#if primaryLabel}
			<Button variant="primary" size="sm" icon={primaryIcon} loading={primaryLoading} disabled={primaryDisabled} onclick={onPrimary}>
				{primaryLabel}
			</Button>
		{/if}
	{/snippet}
</LibraryShell>
