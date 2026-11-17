<script lang="ts">
	import { untrack } from 'svelte';
	import CandidateStrip from '$lib/components/tool-tabs/generate/CandidateStrip.svelte';
	import FloatingPrompt from '$lib/components/tool-tabs/generate/FloatingPrompt.svelte';
	import { provideGenerateController } from '$lib/tool-tabs/image/generate/context';
	import type { GenerateController } from '$lib/tool-tabs/image/generate/controller';

	let {
		controller,
		withExtras = false,
		withResults = false,
		...rest
	}: {
		controller: GenerateController;
		withExtras?: boolean;
		withResults?: boolean;
		settings?: { open: boolean; toggle: () => void };
		running?: string | null;
		run?: { status: string; progress: number | null } | null;
		large?: boolean;
		phone?: boolean;
	} = $props();
	provideGenerateController(untrack(() => controller));
	const results = [1, 2].map((n) => ({ patch: { item_id: `p${n}` }, url: `/p${n}.png`, seed: n }));
</script>

{#snippet extrasView()}
	<span data-test-extra>extra</span>
{/snippet}

{#snippet tempView()}
	<CandidateStrip dock {results} chosen={0} onchoose={() => undefined} onagain={() => undefined} />
{/snippet}

<FloatingPrompt {...rest} extras={withExtras ? extrasView : undefined} tempRow={withResults ? tempView : undefined} hasTemp={withResults} />
