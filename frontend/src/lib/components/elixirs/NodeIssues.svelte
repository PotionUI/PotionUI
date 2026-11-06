<script lang="ts">
	import { SETUP_CODES } from '$lib/elixirs/model';
	import type { ValidationIssue } from '$lib/elixirs/types';

	interface Props {
		issues?: ValidationIssue[];
		limit?: number;
	}

	let { issues = [], limit = 2 }: Props = $props();

	let shown = $derived(issues.slice(0, limit));
	let hidden = $derived(Math.max(0, issues.length - limit));
</script>

{#if issues.length > 0}
	<ul class="flex flex-col gap-1 border-t border-line px-4 py-3" aria-label="Problems">
		{#each shown as issue, index (index)}
			<li class="text-sm {SETUP_CODES.has(issue.code) ? 'text-fg-muted' : issue.severity === 'error' ? 'text-danger' : 'text-warning'}">{issue.message}</li>
		{/each}
		{#if hidden > 0}
			<li class="font-mono text-sm tabular-nums text-fg-subtle">+{hidden} more</li>
		{/if}
	</ul>
{/if}
