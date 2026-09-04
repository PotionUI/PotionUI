<script lang="ts">
	// `.seam` / `.join-block` from console.html + shot-states.html. The join's
	// `kind` only decides the icon/tone; every other word on screen (`label`,
	// `sentence`, the chip/toggle text) comes straight off the ConsoleJoin
	// model -- consoleModel.ts already knows which sentence and which control
	// a given profile/capability combination gets (D3/D4 in PLAN.md).
	//
	// `onGeneratePreviousAndThis`/`onConvertToFreshCut` back the missing-
	// predecessor block's two actions; W1 has no runs map to check a
	// predecessor's output against, so ShotConsole never actually produces a
	// `kind: 'missing'` join yet -- these default to no-ops so the variant
	// renders (and is testable) without every caller having to wire W3 state.
	import type { ConsoleJoin } from './consoleModel';
	import { joinKindMeta } from './badgeMeta';
	import ConsoleIcon from './ConsoleIcon.svelte';

	let {
		join,
		onSetJoin,
		onGeneratePreviousAndThis = () => {},
		onConvertToFreshCut = () => {}
	}: {
		join: ConsoleJoin;
		onSetJoin: (afterShotId: string, value: 'continue' | 'cut') => void;
		onGeneratePreviousAndThis?: (beforeShotId: string) => void;
		onConvertToFreshCut?: (afterShotId: string) => void;
	} = $props();

	let meta = $derived(joinKindMeta(join.kind));
</script>

<div class="relative my-5 flex justify-center before:absolute before:-bottom-5 before:-top-5 before:left-1/2 before:z-0 before:w-px before:-translate-x-1/2 before:bg-line-strong before:content-['']">
	{#if join.kind === 'missing'}
		<div
			class="relative z-10 flex min-h-[34px] w-3/5 max-w-[680px] flex-wrap items-center gap-3 rounded-md border border-warning/40 bg-warning/10 px-3 py-1.5"
		>
			<span class="flex flex-none items-center gap-[5px] whitespace-nowrap font-mono text-[10.5px] uppercase tracking-[0.04em] text-warning">
				<ConsoleIcon name={meta.icon} class="h-[11px] w-[11px]" />
				{join.label}
			</span>
			<span class="min-w-0 flex-1 whitespace-normal text-xs text-fg">{join.sentence}</span>
			<div class="flex w-full flex-none flex-wrap gap-2">
				<button
					type="button"
					class="inline-flex h-[26px] items-center justify-center gap-1.5 rounded bg-accent px-3 text-[11.5px] font-semibold text-accent-contrast"
					onclick={() => onGeneratePreviousAndThis(join.beforeShotId)}
				>
					Generate previous + this shot
				</button>
				<button
					type="button"
					class="inline-flex h-[26px] items-center gap-1.5 rounded border border-line-strong bg-surface-2 px-2.5 text-[11.5px] text-fg"
					onclick={() => onConvertToFreshCut(join.afterShotId)}
				>
					Convert to fresh cut
				</button>
			</div>
		</div>
	{:else}
		<div
			class="relative z-10 flex min-h-[34px] w-3/5 min-w-[300px] max-w-[680px] items-center gap-3 rounded-md border border-line bg-surface-2/55 px-3 py-1.5"
		>
			<span class="flex flex-none items-center gap-[5px] whitespace-nowrap font-mono text-[10.5px] uppercase tracking-[0.04em] text-fg-muted">
				<ConsoleIcon name={meta.icon} class="h-[11px] w-[11px] {meta.iconClass}" />
				{join.label}
			</span>
			<span class="min-w-0 flex-1 truncate text-xs text-fg-muted">{join.sentence}</span>
			<div class="flex flex-none items-center gap-2">
				{#if join.control.kind === 'chip'}
					<span
						class="whitespace-nowrap font-mono text-[10px] uppercase tracking-[0.03em] {meta.chipTone === 'ready'
							? 'text-success'
							: 'text-fg-subtle'}"
					>
						{join.control.text}
					</span>
				{:else}
					<div class="inline-flex items-stretch overflow-hidden rounded border border-line-strong bg-surface-1">
						<button
							type="button"
							class="flex h-5 items-center gap-1 whitespace-nowrap border-r border-line-strong px-2 font-mono text-[10px] uppercase tracking-[0.03em] {join
								.control.value === 'continue'
								? 'bg-signal/10 text-signal'
								: 'text-fg-subtle'}"
							onclick={() => onSetJoin(join.afterShotId, 'continue')}
						>
							<ConsoleIcon name="link" class="h-[9px] w-[9px]" />
							Continue
						</button>
						<button
							type="button"
							class="flex h-5 items-center gap-1 whitespace-nowrap px-2 font-mono text-[10px] uppercase tracking-[0.03em] {join.control
								.value === 'cut'
								? 'bg-signal/10 text-signal'
								: 'text-fg-subtle'}"
							onclick={() => onSetJoin(join.afterShotId, 'cut')}
						>
							<ConsoleIcon name="scissors" class="h-[9px] w-[9px]" />
							Fresh cut
						</button>
					</div>
				{/if}
			</div>
		</div>
	{/if}
</div>
