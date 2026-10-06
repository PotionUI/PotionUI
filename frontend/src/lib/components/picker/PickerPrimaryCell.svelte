<script lang="ts" generics="Row">
	import Badge from '$lib/components/ui/Badge.svelte';
	import PickerLead from './PickerLead.svelte';
	import { MAX_FACTS, MAX_FACTS_MOBILE, pickFacts } from './pickFacts';
	import type { CollisionInfo, EntityKind } from './types';

	let {
		kind,
		row,
		collision,
		assigned = false,
		error = null,
		compact = false
	}: {
		kind: EntityKind<Row>;
		row: Row;
		collision?: CollisionInfo;
		assigned?: boolean;
		error?: string | null;
		compact?: boolean;
	} = $props();

	const facts = $derived(pickFacts(row, kind, collision, compact ? MAX_FACTS_MOBILE : MAX_FACTS));
	const badges = $derived(kind.badges?.(row) ?? []);
	const quiet = $derived(!collision);
</script>

<div class="flex min-w-0 items-center gap-3">
	<PickerLead lead={kind.lead(row)} />
	<div class="min-w-0 flex-1">
		<div class="flex min-w-0 items-center gap-2">
			<span class="truncate text-sm font-medium text-fg">{kind.getName(row)}</span>
			{#if collision}
				<span
					class="flex-shrink-0 whitespace-nowrap rounded border border-dashed border-line-strong px-1 font-mono text-2xs uppercase tracking-[0.06em] text-fg-subtle"
					data-same-name
				>
					{collision.size} same name
				</span>
			{/if}
			{#each badges as badge (badge.label)}
				<Badge size="sm" variant={badge.tone}>{badge.label}</Badge>
			{/each}
			{#if assigned}<Badge size="sm" variant="signal">assigned</Badge>{/if}
		</div>
		{#if error}
			<div class="mt-0.5 truncate text-xs text-danger" role="alert">{error}</div>
		{:else if facts.length > 0}
			{#if quiet}
				<div class="mt-0.5 truncate font-mono text-xs text-fg-subtle" data-facts>
					{facts.map((fact) => fact.value).join(' · ')}
				</div>
			{:else}
				<div class="mt-0.5 flex min-w-0 flex-wrap items-center gap-x-1.5 gap-y-0.5 overflow-hidden" data-facts>
					{#each facts as fact (fact.key)}
						{#if fact.as === 'tag'}
							<span
								class="rounded border px-1.5 font-mono text-xs {fact.emphasis
									? 'border-signal/40 bg-signal/10 text-signal'
									: 'border-line-strong text-fg-muted'}"
								data-fact={fact.key}
								data-emphasis={fact.emphasis}
							>
								{fact.value}
							</span>
						{:else}
							<span
								class="font-mono text-xs {fact.emphasis
									? 'text-fg underline decoration-dotted underline-offset-2'
									: 'text-fg-subtle'}"
								data-fact={fact.key}
								data-emphasis={fact.emphasis}
							>
								{fact.value}
							</span>
						{/if}
					{/each}
				</div>
			{/if}
		{/if}
	</div>
</div>
