<script lang="ts">
	import { Alert, Button } from '$lib/components/ui';
	import { tabsStore } from '$lib/stores/tabs';
	import type { Tab } from '$lib/types/tabs';

	let { tab }: { tab: Tab } = $props();

	const notice = $derived(tab.generation.cancelNotice ?? null);

	function dismiss() {
		tabsStore.updateTab(tab.id, { generation: { ...tab.generation, cancelNotice: null } });
	}
</script>

{#if notice}
	<div class="mb-3" data-testid="cancel-notice">
		<Alert variant="neutral" density="compact" icon="info" live="polite">
			<p>{notice}</p>
			{#snippet actions()}
				<Button variant="ghost" size="xs" onclick={dismiss}>Dismiss</Button>
			{/snippet}
		</Alert>
	</div>
{/if}
