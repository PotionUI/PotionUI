<script lang="ts">
	import { onMount } from 'svelte';
	import { goto } from '$app/navigation';
	import { page } from '$app/stores';
	import { docsStore, findDocItem } from '$lib/stores/docs';
	import { EmptyState, Spinner } from '$lib/components/ui';
	import { DetailHeader, DetailBody } from '$lib/components/detail';
	import LibraryShell from '$lib/components/library/LibraryShell.svelte';
	import type { LibrarySectionMeta } from '$lib/components/library/librarySection';
	import DocsSidebar from '../../docs/components/DocsSidebar.svelte';
	import DocsContent from '../../docs/components/DocsContent.svelte';

	// Deep-link target, e.g. "dev/backends" — passed in via ?doc= on /admin.
	export let initialDocId: string | null = null;

	$: state = $docsStore;
	$: docCount = state.sections.reduce((total, section) => total + section.items.length, 0);
	let mounted = false;

	const DOCS_SECTIONS: readonly LibrarySectionMeta<'all'>[] = [{ id: 'all', label: 'Documentation', icon: 'document' }];

	onMount(async () => {
		docsStore.select(initialDocId);
		await docsStore.loadTree();
		mounted = true;
	});

	// Query-only navigation keeps this component mounted. Mirror Back/Forward
	// changes into the docs store after the initial tree load has started.
	$: if (mounted && state.selectedId !== initialDocId) {
		docsStore.select(initialDocId);
	}

	function handleSelect(id: string) {
		const url = new URL($page.url);
		url.searchParams.set('tab', 'docs');
		url.searchParams.set('doc', id);
		url.hash = '';
		void goto(url, { keepFocus: true, noScroll: true });
	}

	$: selectedItem = state.selectedId ? findDocItem(state.sections, state.selectedId) : null;
</script>

<LibraryShell
	title="Documentation"
	persistKey="admin-docs-library"
	heightClass="h-full"
	sections={DOCS_SECTIONS}
	section="all"
	onSelectSection={() => {}}
	sectionCounts={{ all: docCount }}
	detailOpen
>
	{#snippet sidebarTree()}
		<div class="docs-sidebar-host">
			<DocsSidebar
				sections={state.sections}
				loading={state.loading}
				error={state.error}
				selectedId={state.selectedId}
				onSelect={handleSelect}
			/>
		</div>
	{/snippet}

	<div class="flex h-full min-h-0 flex-col">
		{#if state.loading && state.sections.length === 0}
			<div class="flex-1 flex items-center justify-center">
				<Spinner size="lg" />
			</div>
		{:else if selectedItem}
			<DetailHeader title={selectedItem.title} />
			<DetailBody>
				{#key selectedItem.id}
					<DocsContent item={selectedItem} onNavigate={handleSelect} />
				{/key}
			</DetailBody>
		{:else if state.error}
			<DetailBody>
				<div class="flex h-full items-center justify-center">
					<EmptyState title="Documentation unavailable" description={state.error} icon="warning" compact />
				</div>
			</DetailBody>
		{:else}
			<DetailBody>
				<div class="flex h-full items-center justify-center">
					<EmptyState
						title="Select a topic"
						description="Choose a topic from the sidebar to view its documentation."
						icon="document"
						compact
					/>
				</div>
			</DetailBody>
		{/if}
	</div>
</LibraryShell>

<style>
	.docs-sidebar-host :global(> div) {
		width: 100%;
		max-height: none;
		border: 0;
	}
</style>
