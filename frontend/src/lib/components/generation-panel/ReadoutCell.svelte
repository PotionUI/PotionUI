<script lang="ts">
	// One label/value readout in the bar's right-hand cluster (session, save,
	// last, elapsed, queue). A cell never disappears — the caller always
	// passes a value, falling back to "none"/"empty" text upstream
	// (generation-panel.dc.html line 415) — so this component has no empty
	// state of its own.
	export let label: string;
	export let mono = true;
	export let clickable = false;
	export let disabled = false;
	export let onclick: (() => void) | undefined = undefined;
	// Overrides the button's computed accessible name — needed when the
	// visible value text is state-dependent (a session name, "None", a
	// relative timestamp) and the trigger still needs one stable name for
	// assistive tech and tests to grab onto.
	export let ariaLabel: string | undefined = undefined;
</script>

{#if clickable}
	<button
		type="button"
		class="flex flex-col items-start gap-0.5 px-[18px] text-left disabled:cursor-not-allowed disabled:opacity-50"
		{disabled}
		aria-label={ariaLabel}
		on:click={() => { if (!disabled) onclick?.(); }}
	>
		<span class="font-mono text-[9px] uppercase tracking-[0.11em] text-fg-subtle">{label}</span>
		<span class="flex items-center gap-1.5 {mono ? 'font-mono tabular-nums' : ''} text-[13px]">
			<slot />
		</span>
	</button>
{:else}
	<div class="flex flex-col items-start gap-0.5 px-[18px]">
		<span class="font-mono text-[9px] uppercase tracking-[0.11em] text-fg-subtle">{label}</span>
		<span class="flex items-center gap-1.5 {mono ? 'font-mono tabular-nums' : ''} text-[13px]">
			<slot />
		</span>
	</div>
{/if}
