<script lang="ts">
	import { onMount, untrack } from 'svelte';
	import Icon from '$lib/components/Icon.svelte';
	import { Badge, Button, Input } from '$lib/components/ui';
	import { countSelected, type GroupState } from '$lib/formulas/groups';

	const EXCLUDED = 'Prompt and negative prompt · Attached media · Seed and quantity · Layout';

	let {
		states,
		presetName,
		modeLabel,
		initialName = '',
		initialSelected,
		saving,
		error = null,
		submitLabel = 'Save',
		onSave,
		onCancel
	}: {
		states: GroupState[];
		presetName: string;
		modeLabel: string;
		initialName?: string;
		initialSelected: string[];
		saving: boolean;
		error?: string | null;
		submitLabel?: string;
		onSave: (name: string, selected: Set<string>) => void;
		onCancel: () => void;
	} = $props();

	let name = $state(untrack(() => initialName));
	let selected = $state<Set<string>>(new Set(untrack(() => initialSelected)));
	let expanded = $state<Set<string>>(new Set());

	onMount(() => {
		document.getElementById('formula-name')?.focus();
	});

	let total = $derived(countSelected(states, selected));
	let canSave = $derived(name.trim().length > 0 && selected.size > 0 && !saving);

	function toggle(set: Set<string>, id: string): Set<string> {
		const next = new Set(set);
		if (next.has(id)) next.delete(id);
		else next.add(id);
		return next;
	}

	function submit(event: Event) {
		event.preventDefault();
		if (canSave) onSave(name.trim(), selected);
	}
</script>

<form class="flex min-h-0 flex-1 flex-col" onsubmit={submit}>
	<div class="flex-shrink-0 border-b border-line px-4 py-3">
		<div class="text-md font-semibold text-fg">Current form</div>
		<div class="truncate font-mono text-xs text-fg-subtle">{presetName} · {modeLabel} · never your prompt or media</div>
	</div>

	<div class="drawer-scroll min-h-0 flex-1 overflow-y-auto px-4 pb-3 pt-3" data-testid="formula-save-scroll">
		<label class="mb-1 block text-xs font-medium text-fg-muted" for="formula-name">Name</label>
		<Input id="formula-name" bind:value={name} maxlength={80} placeholder="Name this formula" autocomplete="off" />

		<div class="mb-2 mt-4 flex items-baseline justify-between gap-2 text-xs text-fg-muted">
			<span>What to keep</span>
			<span class="truncate text-right text-fg-subtle">Offered by the {presetName} preset</span>
		</div>

		<ul class="m-0 flex list-none flex-col gap-2 p-0">
			{#each states as state (state.id)}
				{@const open = expanded.has(state.id)}
				<li class="rounded-lg border border-line-strong bg-surface-2/40" data-group-id={state.id}>
					<div class="flex items-start gap-3 px-3 py-2.5">
						<input
							type="checkbox"
							class="mt-1 h-4 w-4 flex-shrink-0"
							checked={selected.has(state.id)}
							aria-label={`Keep ${state.label}`}
							onchange={() => (selected = toggle(selected, state.id))}
						/>
						<div class="min-w-0 flex-1">
							<div class="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1">
								<span class="text-sm font-semibold text-fg">{state.label}</span>
								{#if state.changed}<Badge variant="signal" size="sm">Changed from defaults</Badge>{/if}
							</div>
							<div class="truncate font-mono text-xs text-fg-subtle">{state.summary}</div>
							{#if state.description}<div class="mt-0.5 text-xs text-fg-muted">{state.description}</div>{/if}
						</div>
						<span class="flex-shrink-0 pt-0.5 font-mono text-xs tabular-nums text-fg-subtle">{state.count}</span>
						<button
							type="button"
							class="flex h-6 w-6 flex-shrink-0 items-center justify-center rounded text-fg-subtle hover:bg-surface-3 hover:text-fg"
							aria-expanded={open}
							aria-label={`${open ? 'Hide' : 'Show'} settings in ${state.label}`}
							onclick={() => (expanded = toggle(expanded, state.id))}
						>
							<Icon name="chevron-down" className="h-4 w-4 {open ? 'rotate-180' : ''}" />
						</button>
					</div>
					{#if open}
						<ul class="m-0 list-none border-t border-line px-3 py-2">
							{#each state.rows as row (row.name)}
								<li class="flex items-baseline justify-between gap-3 py-1 text-xs">
									<span class="min-w-0 truncate text-fg-muted">
										{row.label}{#if row.advanced}<span class="text-fg-subtle"> (advanced view)</span>{/if}
									</span>
									<span class="max-w-[60%] truncate font-mono tabular-nums text-fg">{row.text}</span>
								</li>
							{/each}
						</ul>
					{/if}
				</li>
			{/each}
		</ul>

		<p class="mt-3 rounded-lg border border-dashed border-line-strong px-3 py-2 text-xs text-fg-subtle">
			Not part of any formula: {EXCLUDED}.
		</p>
		{#if error}<p class="mt-2 text-xs text-danger" role="alert">{error}</p>{/if}
	</div>

	<footer class="flex min-h-[48px] flex-shrink-0 items-center justify-between gap-3 border-t border-line px-4 py-2">
		<span class="text-xs text-fg-muted">Private to you</span>
		<div class="flex gap-2">
			<Button variant="secondary" size="sm" onclick={onCancel}>Cancel</Button>
			<Button variant="primary" size="sm" type="submit" disabled={!canSave} loading={saving}>
				{submitLabel} <span class="font-mono tabular-nums">{total}</span>
			</Button>
		</div>
	</footer>
</form>
