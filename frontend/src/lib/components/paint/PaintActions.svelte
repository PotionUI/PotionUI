<script lang="ts">
	import { onDestroy } from 'svelte';
	import { guard, reportFailure } from '$lib/editors/guard';
	import { Button } from '$lib/components/ui';
	import { paintActions } from '$lib/paint/registries';
	import type { PaintSession } from '$lib/paint/session';
	import type { PaintAction } from '$lib/paint/types';

	let { session }: { session: PaintSession } = $props();

	let actions = $state.raw<PaintAction[]>(paintActions.list());
	const stop = paintActions.subscribe(() => (actions = paintActions.list()));
	onDestroy(stop);

	function run(action: PaintAction) {
		const entry = `paint:action:${action.id}`;
		guard(
			entry,
			'run',
			() => {
				const pending = action.run(session);
				if (pending && typeof pending.catch === 'function') pending.catch((error: unknown) => reportFailure(entry, 'run', error));
			},
			undefined
		);
	}
</script>

{#if actions.length > 0}
	<p class="font-mono text-xs uppercase tracking-[0.08em] text-fg-subtle">Actions</p>
	<div class="flex flex-wrap gap-1.5" data-paint-actions>
		{#each actions as action (action.id)}
			<Button variant="secondary" size="sm" onclick={() => run(action)}>{action.label}</Button>
		{/each}
	</div>
{/if}
