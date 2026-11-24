<script lang="ts">
	import { Alert } from '$lib/components/ui';
	import { hasPromptWeights } from '$lib/generation/promptWeights';

	let {
		prompts,
		supported = true,
		pictures = false,
		class: className = ''
	}: {
		prompts: ReadonlyArray<{ prompt?: string | null }>;
		supported?: boolean;
		pictures?: boolean;
		class?: string;
	} = $props();

	const show = $derived((!supported || pictures) && prompts.some((entry) => hasPromptWeights(entry.prompt)));
</script>

{#if show}
	<Alert variant="neutral" density="compact" icon class={className}>
		Prompt weights like <span class="font-mono">(word:1.3)</span>
		{#if supported}
			are ignored when pictures are attached in this mode.
		{:else}
			aren't supported by this model, so they're ignored.
		{/if}
	</Alert>
{/if}
