<script lang="ts">
	import { Alert, Button } from '$lib/components/ui';
	import { tabsStore } from '$lib/stores/tabs';
	import { toasts } from '$lib/stores/toast';
	import { pendingLongVideo, planLongVideoTab } from '$lib/utils/longVideoNotice';
	import type { GenerationTab } from '$lib/types/tabs';

	let {
		tab,
		modeNames,
		class: className = ''
	}: {
		tab: GenerationTab;
		modeNames: readonly string[];
		class?: string;
	} = $props();

	let opened = $state<string[]>([]);

	const pending = $derived(opened.includes(tab.id) ? null : pendingLongVideo(tab, modeNames));

	function openInDirector() {
		if (!pending) return;
		const plan = planLongVideoTab(tab, pending);
		opened = [...opened, tab.id];
		tabsStore.addTabWithData(plan.name, plan.tabData);
		if (plan.notes.length > 0) toasts.warning(plan.notes.join(' '), 12000);
	}
</script>

{#if pending}
	<Alert variant="signal" density="compact" icon class={className}>
		This session has a Long video setup. Long video is now part of Video Director.
		{#snippet actions()}
			<Button variant="secondary" size="sm" onclick={openInDirector}>Open in Video Director</Button>
		{/snippet}
	</Alert>
{/if}
