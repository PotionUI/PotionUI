<script lang="ts">
	import Icon from './Icon.svelte';
	import Tooltip from './Tooltip.svelte';
	import portal from '$lib/actions/portal';
	import { computeFlippedMenuPosition, type FlippedMenuPosition } from '$lib/utils/menuPosition';
	import {
		dependents,
		normalizeVariableDef,
		optionText,
		whenIsBroken,
		type ChoiceVariableMode,
		type VariableOptionWhen,
		type VariablesMap
	} from '$lib/utils/variableDefs';

	let {
		ownerName,
		when,
		mode,
		variables,
		onChange
	}: {
		ownerName: string;
		when: VariableOptionWhen | undefined;
		mode: ChoiceVariableMode;
		variables: VariablesMap;
		onChange: (when: VariableOptionWhen | undefined) => void;
	} = $props();

	const POPOVER_WIDTH = 260;
	const POPOVER_HEIGHT_ESTIMATE = 260;
	const POPOVER_GAP = 4;

	let open = $state(false);
	let pickingVar = $state(false);
	let chipRef = $state<HTMLSpanElement>();
	let popoverRef = $state<HTMLDivElement>();
	let popoverPos = $state<FlippedMenuPosition>({ left: 0, top: 0, maxHeight: POPOVER_HEIGHT_ESTIMATE });

	let struckThrough = $derived(mode === 'pin' || mode === 'per-image');
	let broken = $derived(when ? whenIsBroken(when, variables) : false);

	let pickableEntries = $derived(
		Object.keys(variables).map((name) => {
			const def = normalizeVariableDef(variables[name]);
			let reason: string | null = null;
			if (name === ownerName) reason = 'this variable';
			else if (def.type !== 'choice') reason = 'text variable';
			else if (dependents(ownerName, variables, true).includes(name)) reason = `depends on $${ownerName}`;
			return { name, reason };
		})
	);

	let referencedDef = $derived(when ? normalizeVariableDef(variables[when.var]) : null);
	let referencedOptions = $derived(
		referencedDef && referencedDef.type === 'choice'
			? referencedDef.options.map((o) => optionText(o).trim()).filter((t) => t.length > 0)
			: []
	);

	function updatePosition() {
		if (!chipRef) return;
		const heightEstimate = popoverRef?.scrollHeight || POPOVER_HEIGHT_ESTIMATE;
		popoverPos = computeFlippedMenuPosition(chipRef, { width: POPOVER_WIDTH, heightEstimate, gap: POPOVER_GAP });
	}

	let popoverStyle = $derived(
		`position: fixed; left: ${popoverPos.left}px; width: ${POPOVER_WIDTH}px; max-height: ${popoverPos.maxHeight}px; overflow-y: auto; ${
			popoverPos.top !== undefined ? `top: ${popoverPos.top}px; bottom: auto;` : `bottom: ${popoverPos.bottom}px; top: auto;`
		}`
	);

	$effect(() => {
		if (!open) return;
		updatePosition();
		const raf = requestAnimationFrame(updatePosition);
		window.addEventListener('resize', updatePosition);
		window.addEventListener('scroll', updatePosition, true);
		return () => {
			cancelAnimationFrame(raf);
			window.removeEventListener('resize', updatePosition);
			window.removeEventListener('scroll', updatePosition, true);
		};
	});

	function handleWindowPointerDown(e: PointerEvent) {
		if (!open) return;
		const target = e.target as Node;
		if (chipRef?.contains(target) || popoverRef?.contains(target)) return;
		open = false;
		pickingVar = false;
	}

	function handleWindowKeydown(e: KeyboardEvent) {
		if (open && e.key === 'Escape') {
			open = false;
			pickingVar = false;
		}
	}

	function toggleOpen() {
		if (struckThrough) return;
		pickingVar = !when;
		open = !open;
	}

	function clear(e: MouseEvent) {
		e.stopPropagation();
		onChange(undefined);
		open = false;
		pickingVar = false;
	}

	function pickVariable(name: string) {
		onChange({ var: name, values: [] });
		pickingVar = false;
	}

	function toggleValue(value: string) {
		if (!when) return;
		const values = when.values.includes(value) ? when.values.filter((v) => v !== value) : [...when.values, value];
		onChange(values.length > 0 ? { var: when.var, values } : undefined);
	}
</script>

<svelte:window onpointerdown={handleWindowPointerDown} onkeydown={handleWindowKeydown} />

<span bind:this={chipRef} class="relative flex w-full">
	{#if struckThrough && when}
		<Tooltip
			text={mode === 'pin'
				? 'Pinning wins regardless of this condition.'
				: 'Per-image ignores conditions — every option is used.'}
		>
			<span
				class="flex h-[22px] w-full min-w-0 items-center rounded border border-line-strong bg-surface-2 px-2 text-xs font-mono text-fg-disabled line-through"
			>
				<span class="truncate">${when.var} = {when.values.join(', ')}</span>
			</span>
		</Tooltip>
	{:else if !when}
		<button
			type="button"
			class="flex h-[22px] w-full items-center rounded border border-line-strong bg-surface-2 px-2 text-xs font-mono uppercase tracking-[0.04em] text-fg-subtle transition-colors hover:border-line-hover hover:text-fg-muted"
			aria-label={`Set condition for this option of ${ownerName}`}
			onclick={toggleOpen}
		>
			Always
		</button>
	{:else}
		<Tooltip text={`$${when.var} = ${when.values.join(', ')}`}>
			<span
				class="flex h-[22px] w-full min-w-0 items-center rounded border px-2 text-xs font-mono {broken
					? 'border-warning/35 bg-warning/10 text-warning'
					: 'border-signal/28 bg-signal/10 text-signal'}"
			>
				<button
					type="button"
					class="min-w-0 truncate"
					aria-label={`Edit condition for this option of ${ownerName}`}
					onclick={toggleOpen}
				>
					${when.var} = {when.values.join(', ')}
				</button>
				<button
					type="button"
					class="ml-auto flex-shrink-0 pl-2 opacity-70 transition-opacity hover:opacity-100"
					aria-label={`Clear condition on ${ownerName}`}
					onclick={clear}
				>
					&times;
				</button>
			</span>
		</Tooltip>
	{/if}

	{#if open}
		<div
			bind:this={popoverRef}
			use:portal
			class="z-[99999] rounded-lg border border-line-strong bg-surface-1 p-2.5 shadow-floating"
			style={popoverStyle}
			role="dialog"
			aria-label={`Condition for ${ownerName}`}
		>
			<div class="flex items-center gap-1.5 text-xs text-fg">
				<span>Only when</span>
				<button
					type="button"
					class="inline-flex items-center gap-1 rounded bg-surface-2 px-1.5 py-0.5 font-mono text-fg hover:bg-surface-3"
					onclick={() => (pickingVar = !pickingVar)}
				>
					{when ? `$${when.var}` : 'choose…'}
					<Icon name="chevron-down" className="h-3 w-3" />
				</button>
				<span>is</span>
			</div>

			{#if pickingVar}
				<div class="mt-2 space-y-0.5">
					{#each pickableEntries as entry (entry.name)}
						<button
							type="button"
							disabled={!!entry.reason}
							class="flex w-full items-center justify-between rounded px-1.5 py-1 text-left text-xs {entry.reason
								? 'cursor-not-allowed text-fg-disabled'
								: 'text-fg hover:bg-surface-2'}"
							aria-label={`Condition on $${entry.name}`}
							onclick={() => pickVariable(entry.name)}
						>
							<span class="font-mono">${entry.name}</span>
							{#if entry.reason}<span class="text-xs text-fg-subtle">{entry.reason}</span>{/if}
						</button>
					{/each}
				</div>
			{:else if when}
				<div class="mt-2 flex flex-wrap gap-1.5">
					{#each referencedOptions as value (value)}
						<button
							type="button"
							class="rounded border px-1.5 py-0.5 text-xs font-mono transition-colors {when.values.includes(value)
								? 'border-signal/40 bg-signal/15 text-signal'
								: 'border-line-strong bg-surface-2 text-fg-muted hover:border-line-hover'}"
							aria-label={`Toggle ${value}`}
							onclick={() => toggleValue(value)}
						>
							{value}
						</button>
					{/each}
					{#if referencedOptions.length === 0}
						<span class="text-xs text-fg-subtle">${when.var} has no options yet.</span>
					{/if}
				</div>
				<div class="mt-2 flex items-center justify-between gap-2">
					<span class="text-xs text-fg-subtle">Values come from ${when.var}'s options; nothing to type.</span>
					<button type="button" class="flex-shrink-0 text-xs font-medium text-fg-muted hover:text-fg" onclick={clear}>
						Clear
					</button>
				</div>
			{/if}
		</div>
	{/if}
</span>
