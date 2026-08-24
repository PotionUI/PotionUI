<script lang="ts">
	import { goto } from '$app/navigation';
	import { page } from '$app/stores';
	import { SegmentedControl } from '$lib/components/ui';
	import LLMConfigTab from './LLMConfigTab.svelte';
	import SessionsDebugTab from './SessionsDebugTab.svelte';

	type View = 'configuration' | 'sessions';

	// currentUser is passed through by +page.svelte's componentProps switch for
	// some tabs; this wrapper doesn't need it, so it's not declared as a prop.

	$: view = (($page.url.searchParams.get('view') as View) || 'configuration') === 'sessions'
		? 'sessions'
		: 'configuration';

	function setView(next: View) {
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
</script>

<div class="space-y-4">
	<SegmentedControl
		items={[
			{ id: 'configuration', label: 'Configuration', icon: 'cpu' },
			{ id: 'sessions', label: 'Sessions', icon: 'chat' }
		]}
		selected={view}
		onSelect={(id) => setView(id as View)}
		ariaLabel="LLM / Assistant views"
	/>

	{#if view === 'configuration'}
		<LLMConfigTab />
	{:else}
		<SessionsDebugTab />
	{/if}
</div>
