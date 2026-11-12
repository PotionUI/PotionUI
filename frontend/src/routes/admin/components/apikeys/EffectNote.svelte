<script lang="ts">
	import Icon from '$lib/components/Icon.svelte';
	import type { EffectNote } from './accessPanel';

	let { note, testid }: { note: EffectNote | null; testid?: string } = $props();
</script>

{#if note}
	<p
		class="mt-1.5 flex items-start gap-1.5 text-sm {note.tone === 'warning' ? 'text-warning' : 'text-fg-subtle'}"
		data-testid={testid}
		role="status"
	>
		<Icon name={note.tone === 'warning' ? 'warning' : 'info'} className="mt-0.5 h-3.5 w-3.5 flex-shrink-0" />
		<span>{note.text}</span>
	</p>
{/if}
