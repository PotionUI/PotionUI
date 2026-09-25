<script lang="ts">
	import { goto } from '$app/navigation';
	import { page } from '$app/stores';
	import { Button } from '$lib/components/ui';
	import LibraryShell from '$lib/components/library/LibraryShell.svelte';
	import LibraryFilterBar from '$lib/components/library/LibraryFilterBar.svelte';
	import type { LibrarySectionMeta } from '$lib/components/library/librarySection';
	import { debounce } from '$lib/stores/tabPersistence';
	import LLMConfigTab from './LLMConfigTab.svelte';
	import SessionsDebugTab from './SessionsDebugTab.svelte';
	import {
		LLM_CONFIG_SORT_OPTIONS,
		llmConfigFiltersFromSearchParams,
		llmConfigFiltersToSearchParams,
		type LLMConfigFilters,
		type LLMConfigSortBy
	} from './llmConfigFilters';
	import {
		SESSIONS_SORT_OPTIONS,
		sessionsFiltersFromSearchParams,
		sessionsFiltersToSearchParams,
		type SessionsFilters
	} from './sessionsFilters';

	type LLMView = 'configuration' | 'sessions';

	const LLM_SECTIONS: readonly LibrarySectionMeta<LLMView>[] = [
		{ id: 'configuration', label: 'Configurations', icon: 'cpu' },
		{ id: 'sessions', label: 'Sessions', icon: 'chat' }
	];

	const view = $derived(
		(($page.url.searchParams.get('view') as LLMView) || 'configuration') === 'sessions' ? 'sessions' : 'configuration'
	);

	let configTab: any = $state();
	let sessionsTab: any = $state();

	let configTotal = $state(0);
	let configVisible = $state(0);
	let configDetailOpen = $state(false);

	let sessionsTotal = $state(0);
	let sessionsDetailOpen = $state(false);
	let sessionsClearingScope = $state<'session' | 'all' | 'sessions' | null>(null);

	const detailOpen = $derived(view === 'configuration' ? configDetailOpen : sessionsDetailOpen);
	const shellCount = $derived(view === 'configuration' ? configVisible : sessionsTotal);
	const sectionCounts = $derived({ configuration: configTotal, sessions: sessionsTotal });

	const llmConfigFilters = $derived(llmConfigFiltersFromSearchParams($page.url.searchParams));
	const sessionsFilters = $derived(sessionsFiltersFromSearchParams($page.url.searchParams));

	function selectSection(next: LLMView) {
		if (next === view) return;
		const url = new URL($page.url);
		url.searchParams.set('tab', 'llm');
		if (next === 'configuration') {
			url.searchParams.delete('view');
		} else {
			url.searchParams.set('view', next);
		}
		void goto(url, { keepFocus: true, noScroll: true });
	}

	let configFiltersDebounce: ReturnType<typeof setTimeout> | undefined;
	function updateConfigFilters(next: LLMConfigFilters) {
		clearTimeout(configFiltersDebounce);
		configFiltersDebounce = setTimeout(() => {
			const url = new URL($page.url);
			for (const key of ['q', 'sort_by']) url.searchParams.delete(key);
			for (const [key, value] of llmConfigFiltersToSearchParams(next)) url.searchParams.set(key, value);
			void goto(url, { replaceState: true, keepFocus: true, noScroll: true });
		}, 250);
	}

	function updateSessionsFilters(next: SessionsFilters) {
		const url = new URL($page.url);
		for (const key of ['q', 'sort_by']) url.searchParams.delete(key);
		for (const [key, value] of sessionsFiltersToSearchParams(next)) url.searchParams.set(key, value);
		void goto(url, { replaceState: true, keepFocus: true, noScroll: true });
	}

	const debouncedUpdateSessionsFilters = debounce(updateSessionsFilters, 300);
</script>

<LibraryShell
	title="LLM / Assistant"
	persistKey="admin-llm-library"
	heightClass="h-full"
	sections={LLM_SECTIONS}
	section={view}
	onSelectSection={selectSection}
	{sectionCounts}
	count={shellCount}
	{detailOpen}
>
	{#snippet toolbar()}
		{#if view === 'configuration'}
			<LibraryFilterBar
				q={llmConfigFilters.q}
				onQueryChange={(value) => updateConfigFilters({ ...llmConfigFilters, q: value })}
				searchPlaceholder="Search by name or model…"
				sortBy={llmConfigFilters.sortBy}
				sortOptions={LLM_CONFIG_SORT_OPTIONS}
				onSortChange={(value) => updateConfigFilters({ ...llmConfigFilters, sortBy: value as LLMConfigSortBy })}
			/>
		{:else}
			<LibraryFilterBar
				q={sessionsFilters.q}
				onQueryChange={(value) => debouncedUpdateSessionsFilters({ ...sessionsFilters, q: value })}
				searchPlaceholder="Search name, user, email…"
				sortBy={sessionsFilters.sortBy}
				sortOptions={SESSIONS_SORT_OPTIONS}
				onSortChange={(value) => updateSessionsFilters({ ...sessionsFilters, sortBy: value as SessionsFilters['sortBy'] })}
			/>
		{/if}
	{/snippet}

	{#snippet primary()}
		{#if view === 'configuration'}
			<Button variant="primary" size="sm" icon="plus" onclick={() => configTab?.openCreateModal()}>
				Add configuration
			</Button>
		{/if}
	{/snippet}

	{#snippet overflow(close: () => void)}
		{#if view === 'sessions'}
			<button
				type="button"
				class="block w-full px-3 py-1.5 text-left text-xs text-fg hover:bg-surface-3"
				disabled={sessionsClearingScope !== null}
				onclick={() => {
					close();
					void sessionsTab?.clearAllTraces();
				}}
			>
				Clear all traces
			</button>
			<button
				type="button"
				class="block w-full px-3 py-1.5 text-left text-xs text-danger hover:bg-surface-3"
				disabled={sessionsClearingScope !== null || sessionsTotal === 0}
				onclick={() => {
					close();
					void sessionsTab?.clearAllSessions();
				}}
			>
				Clear chat history
			</button>
		{/if}
	{/snippet}

	<div class="h-full min-h-0" class:hidden={view !== 'configuration'}>
		<LLMConfigTab
			bind:this={configTab}
			filters={llmConfigFilters}
			bind:total={configTotal}
			bind:visibleCount={configVisible}
			bind:detailOpen={configDetailOpen}
		/>
	</div>
	<div class="h-full min-h-0" class:hidden={view !== 'sessions'}>
		<SessionsDebugTab
			bind:this={sessionsTab}
			filters={sessionsFilters}
			bind:total={sessionsTotal}
			bind:detailOpen={sessionsDetailOpen}
			bind:clearingScope={sessionsClearingScope}
		/>
	</div>
</LibraryShell>
