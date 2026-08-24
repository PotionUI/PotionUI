<script lang="ts">
	import { onMount } from 'svelte';
	import { goto } from '$app/navigation';
	import { page } from '$app/stores';
	import { docsStore, findDocItem } from '$lib/stores/docs';
	import { EmptyState, Spinner } from '$lib/components/ui';
	import MasterDetailLayout from '$lib/components/master-detail/MasterDetailLayout.svelte';
	import DocsSidebar from '../../docs/components/DocsSidebar.svelte';
	import DocsContent from '../../docs/components/DocsContent.svelte';
	import AdminTabShell from './AdminTabShell.svelte';

	// Deep-link target, e.g. "dev/backends" — passed in via ?doc= on /admin.
	export let initialDocId: string | null = null;

	$: state = $docsStore;
	$: docCount = state.sections.reduce((total, section) => total + section.items.length, 0);
	let mounted = false;

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

<div class="flex h-[calc(100dvh-var(--header-h)-2rem)] min-h-[36rem] flex-col gap-4 sm:h-[calc(100dvh-var(--header-h)-3rem)]">
	<AdminTabShell title="Documentation" icon="document" counts={[{ label: docCount === 1 ? 'topic' : 'topics', value: docCount }]} />

	<section class="flex-1 min-h-0 rounded-lg border border-line bg-surface-1 overflow-hidden">
		<MasterDetailLayout leftWidth={288} minWidth={240} maxWidth={420} storageKey="admin-docs-list-width">
			<!-- DocsSidebar owns its own header (filter input) and scroll body, so
			     it's mounted as-is rather than re-wrapped in a pane header. The
			     override below only makes it fill the pane's fixed height — its
			     internals aren't touched. -->
			<div slot="list" class="doc-sidebar-pane h-full min-h-0 flex flex-col">
				<DocsSidebar
					sections={state.sections}
					loading={state.loading}
					error={state.error}
					selectedId={state.selectedId}
					onSelect={handleSelect}
				/>
			</div>

			<div slot="detail" class="h-full min-h-0 flex flex-col overflow-y-auto">
				{#if state.loading && state.sections.length === 0}
					<div class="flex-1 flex items-center justify-center">
						<Spinner size="lg" />
					</div>
				{:else if selectedItem}
					{#key selectedItem.id}
						<DocsContent item={selectedItem} onNavigate={handleSelect} />
					{/key}
				{:else if state.error}
					<div class="flex-1 p-5 flex items-center justify-center">
						<EmptyState title="Documentation unavailable" description={state.error} icon="warning" compact />
					</div>
				{:else}
					<div class="flex-1 p-5 flex items-center justify-center">
						<EmptyState
							title="Select a topic"
							description="Choose a topic from the sidebar to view its documentation."
							icon="document"
							compact
						/>
					</div>
				{/if}
			</div>
		</MasterDetailLayout>
	</section>
</div>

<style>
	.doc-sidebar-pane :global(> div) {
		height: 100%;
		min-height: 0;
	}
</style>
