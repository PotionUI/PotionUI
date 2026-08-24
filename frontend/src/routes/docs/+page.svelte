<script lang="ts">
	import { onMount } from 'svelte';
	import { goto } from '$app/navigation';
	import { page } from '$app/stores';
	import { docsStore, findDocItem } from '$lib/stores/docs';
	import { EmptyState, PageHeader, Spinner } from '$lib/components/ui';
	import MasterDetailLayout from '$lib/components/master-detail/MasterDetailLayout.svelte';
	import Icon from '$lib/components/Icon.svelte';
	import DocsSidebar from './components/DocsSidebar.svelte';
	import DocsContent from './components/DocsContent.svelte';

	// Deep-link target, e.g. "user/getting-started" — passed in via ?doc= on /docs.
	$: initialDocId = $page.url.searchParams.get('doc');

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
		url.searchParams.set('doc', id);
		url.hash = '';
		void goto(url, { keepFocus: true, noScroll: true });
	}

	$: selectedItem = state.selectedId ? findDocItem(state.sections, state.selectedId) : null;
</script>

<svelte:head>
	<title>Documentation · PotionUI</title>
</svelte:head>

<div class="flex h-[100dvh] flex-col bg-canvas text-fg">
	<PageHeader sticky={false}>
		<div class="flex min-w-0 items-center gap-3">
			<span class="flex h-7 w-7 flex-shrink-0 items-center justify-center rounded bg-surface-2 border border-line-strong">
				<Icon name="book" className="h-4 w-4 text-fg-muted" />
			</span>
			<div class="min-w-0">
				<h1 class="truncate text-sm font-semibold text-fg">Documentation</h1>
				<p class="hidden text-xs text-fg-muted sm:block">
					{docCount} {docCount === 1 ? 'topic' : 'topics'}
				</p>
			</div>
		</div>
	</PageHeader>

	<div class="flex-1 min-h-0 p-4">
		<section class="h-full rounded-lg border border-line bg-surface-1 overflow-hidden">
			<MasterDetailLayout leftWidth={288} minWidth={240} maxWidth={420} storageKey="docs-list-width">
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
</div>

<style>
	.doc-sidebar-pane :global(> div) {
		height: 100%;
		min-height: 0;
	}
</style>
