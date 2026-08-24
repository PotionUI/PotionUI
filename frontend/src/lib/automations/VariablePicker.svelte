<script lang="ts">
	/**
	 * Insert a reference to an upstream node's output into a config field.
	 *
	 * The syntax depends on how the backend reads the field, and getting it wrong
	 * produces a silently broken automation:
	 *
	 * - `template` — an action's string config, run through Jinja `render_template`.
	 *   Inserts `{{ event.path }}`, appended to whatever's already there.
	 * - `path` — a condition's `field`, resolved with `get_path`. It is exactly ONE
	 *   bare dot-path, so we *replace* the value with `event.path`. Braces here, or
	 *   appending a second path, would break it.
	 * - `expression` — `condition.jinja_expression`'s whole-expression field.
	 *   Inserts the bare path, appended (`event.size > 0`).
	 */
	import Icon from '$lib/components/Icon.svelte';
	import { Input } from '$lib/components/ui';
	import type { VariableScope, VariableRef } from '$lib/stores/automationEditor';

	export type InsertMode = 'template' | 'path' | 'expression';

	let {
		scope,
		mode,
		onInsert
	}: {
		scope: VariableScope;
		mode: InsertMode;
		onInsert: (text: string, replace: boolean) => void;
	} = $props();

	let open = $state(false);
	let filter = $state('');

	function matches(ref: VariableRef): boolean {
		const needle = filter.trim().toLowerCase();
		if (!needle) return true;
		return (
			ref.path.toLowerCase().includes(needle) ||
			(ref.description ?? '').toLowerCase().includes(needle)
		);
	}

	let eventRefs = $derived(scope.event.filter(matches));
	let upstreamGroups = $derived(
		scope.upstream
			.map((group) => ({ ...group, outputs: group.outputs.filter(matches) }))
			.filter((group) => group.outputs.length > 0 || group.dynamic)
	);

	let hasAnything = $derived(
		scope.event.length > 0 || scope.eventDynamic || scope.upstream.length > 0
	);

	function insert(ref: VariableRef) {
		// `path` fields hold exactly one dot-path, so selecting one replaces
		// the field rather than appending to it.
		if (mode === 'path') onInsert(ref.path, true);
		else if (mode === 'template') onInsert(`{{ ${ref.path} }}`, false);
		else onInsert(ref.path, false);

		open = false;
		filter = '';
	}

	function onKeydown(event: KeyboardEvent) {
		if (event.key === 'Escape' && open) {
			open = false;
			event.stopPropagation();
		}
	}
</script>

<svelte:window
	onclick={(event) => {
		if (open && !(event.target as HTMLElement)?.closest?.('[data-variable-picker]')) open = false;
	}}
/>

<div class="relative" data-variable-picker onkeydown={onKeydown} role="presentation">
	<button
		type="button"
		class="flex items-center gap-1 rounded px-1.5 py-0.5 text-2xs font-mono uppercase tracking-wide
			text-fg-subtle hover:text-fg hover:bg-surface-3 transition-colors
			disabled:opacity-40 disabled:cursor-not-allowed"
		disabled={!hasAnything}
		title={hasAnything ? 'Insert a value from an upstream node' : 'No upstream node provides data yet'}
		onclick={(event) => {
			event.stopPropagation();
			open = !open;
		}}
	>
		<Icon name="braces" className="w-3 h-3" />
		Insert
	</button>

	{#if open}
		<div
			class="absolute right-0 z-30 mt-1 w-72 rounded-xl border border-line-strong bg-surface-2
				shadow-overlay overflow-hidden"
		>
			<div class="p-2 border-b border-line">
				<!-- svelte-ignore a11y_autofocus -->
				<Input bind:value={filter} placeholder="Filter fields…" autofocus class="w-full text-xs" />
			</div>

			<div class="max-h-64 overflow-y-auto py-1">
				{#if scope.eventDynamic}
					<p class="px-3 py-2 text-2xs text-fg-subtle">
						This trigger's payload is defined at runtime — type
						<code class="font-mono text-fg-muted">event.&lt;field&gt;</code> yourself.
					</p>
				{/if}

				{#if eventRefs.length > 0}
					<p
						class="px-3 pt-1.5 pb-1 text-2xs font-mono font-semibold uppercase tracking-wide text-fg-subtle"
					>
						Event
					</p>
					{#each eventRefs as ref (ref.path)}
						{@render row(ref)}
					{/each}
				{/if}

				{#each upstreamGroups as group (group.nodeId)}
					<p
						class="px-3 pt-2 pb-1 text-2xs font-mono font-semibold uppercase tracking-wide text-fg-subtle truncate"
						title={group.nodeId}
					>
						{group.title}
					</p>
					{#if group.dynamic}
						<p class="px-3 pb-1 text-2xs text-fg-subtle">Runtime-defined output.</p>
					{/if}
					{#each group.outputs as ref (ref.path)}
						{@render row(ref)}
					{/each}
				{/each}

				{#if eventRefs.length === 0 && upstreamGroups.length === 0 && !scope.eventDynamic}
					<p class="px-3 py-3 text-2xs text-fg-subtle">
						{filter.trim() ? 'No matching fields.' : 'No upstream node provides data yet.'}
					</p>
				{/if}
			</div>
		</div>
	{/if}
</div>

{#snippet row(ref: VariableRef)}
	<button
		type="button"
		class="w-full text-left px-3 py-1.5 hover:bg-surface-3 transition-colors group"
		onclick={(event) => {
			event.stopPropagation();
			insert(ref);
		}}
	>
		<span class="flex items-baseline justify-between gap-2">
			<span class="font-mono text-xs text-fg truncate">{ref.key}</span>
			<span class="font-mono text-2xs text-fg-subtle tabular-nums flex-shrink-0">{ref.type}</span>
		</span>
		{#if ref.description}
			<span class="block text-2xs text-fg-subtle truncate">{ref.description}</span>
		{/if}
	</button>
{/snippet}
