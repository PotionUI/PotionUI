<script lang="ts">
	import { Alert, Button } from '$lib/components/ui';

	let {
		kind,
		busy = false,
		onload,
		onkeep
	}: {
		kind: 'conflict' | 'too-large';
		busy?: boolean;
		onload?: () => void;
		onkeep?: () => void;
	} = $props();
</script>

<Alert variant="warning" icon density="compact" class="rounded-none border-x-0 border-t-0" live="polite">
	{#if kind === 'conflict'}
		<p class="font-semibold" data-edit-notice="conflict">This edit was changed somewhere else</p>
		<p class="mt-0.5 text-fg-muted">
			It was saved from another window or device after you opened it. Nothing has been replaced. Pick the
			version you want to continue with.
		</p>
		<div class="mt-2 flex flex-wrap items-center gap-2">
			<Button variant="secondary" size="sm" disabled={busy} onclick={() => onload?.()} attrs={{ 'data-edit-load-newer': '' }}>Load the newer one</Button>
			<Button variant="secondary" size="sm" disabled={busy} onclick={() => onkeep?.()} attrs={{ 'data-edit-keep-mine': '' }}>Keep mine as a copy</Button>
		</div>
	{:else}
		<p class="font-semibold" data-edit-notice="too-large">This edit is too big to save online</p>
		<p class="mt-0.5 text-fg-muted">
			Your work stays in this browser, but it is not being saved to your account. Removing some edits may let it save again.
		</p>
	{/if}
</Alert>
