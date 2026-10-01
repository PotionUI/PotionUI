<script lang="ts">
	import { CopyButton } from '$lib/components/ui';

	let { detail = null }: { detail?: string | null } = $props();
</script>

{#if detail}
	<details class="mt-3 w-full max-w-sm text-left select-text" data-failure-technical-details>
		<summary class="text-sm text-fg-subtle cursor-pointer select-none w-fit mx-auto">Technical details</summary>
		<div class="relative mt-1.5">
			<pre class="font-mono text-sm bg-surface-3 text-fg-muted rounded px-2 py-1.5 pr-8 overflow-x-auto whitespace-pre-wrap break-words max-h-32 overflow-y-auto text-left">{detail}</pre>
			<div class="absolute top-1 right-1">
				<CopyButton text={detail} ariaLabel="Copy technical details" size="sm" />
			</div>
		</div>
	</details>
{/if}
